"""Auth endpoints — login + invite."""

import logging
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from typing import Optional
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from config.settings import settings
from app.db import supabase as db
from app.security.auth import get_current_user
from app.security.client_ip import client_ip
from app.security.crm_token import PURPOSE_LOGIN, verify_crm_token
from app.security import login_guard
from app.security.rate_limiter import check_rate_limit
from app.services.storage import upload_avatar

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _issue_jwt(user: dict) -> str:
    """Build JWT with the same claims as login."""
    if not settings.jwt_secret:
        raise HTTPException(500, "JWT not configured")
    payload = {
        "sub": user["id"],
        "email": user["email"],
        "name": user.get("name", ""),
        "role": user.get("role", "sales"),
        "tunnel_scope": user.get("tunnel_scope", "sales"),
        "avatar_url": user.get("avatar_url") or None,
        "phone": user.get("phone") or None,
        "token_version": int(user.get("token_version") or 0),
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expiry_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


class LoginRequest(BaseModel):
    email: str
    password: str
    # Honeypot (PR-3): a hidden field no human ever fills. A value means a bot
    # filled the form. Counted here (alert-only); PR-11 feeds it to the jail.
    company_fax: Optional[str] = None


class LoginResponse(BaseModel):
    # Empty while a second factor is pending (PR-13): the panel then reads
    # `mfa` ("required" → ask for a code, "enroll" → enrol first) and sends
    # `mfa_token` to /api/auth/mfa or /api/auth/mfa/enroll/*.
    token: str = ""
    user: dict
    mfa: Optional[str] = None
    mfa_token: Optional[str] = None


def _public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user.get("name", ""),
        "role": user.get("role", "sales"),
        "tunnel_scope": user.get("tunnel_scope", "sales"),
        "phone": user.get("phone", ""),
        "avatar_url": user.get("avatar_url") or None,
    }


def _login_place(request: Request, ip: str) -> str:
    """Country when the edge says it (CF-IPCountry), else the network the IP
    sits in (/24 for IPv4, /48 for IPv6). No ASN database is configured."""
    cc = (request.headers.get("cf-ipcountry") or "").strip().upper()
    if cc and cc not in ("XX", "T1"):
        return f"country:{cc}"
    if not ip or ip == "unknown":
        return ""
    if ":" in ip:
        return "net:" + ":".join(ip.split(":")[:3]) + "::/48"
    parts = ip.split(".")
    return "net:" + ".".join(parts[:3]) + ".0/24" if len(parts) == 4 else ""


async def _complete_login(user: dict, request: Request, ip: str) -> LoginResponse:
    """The session token, plus the new-place alert (PR-13). Never logs PII."""
    from app.security import mfa as _mfa
    from app.security.mfa_store import note_login_place

    place = _login_place(request, ip)
    is_new = await note_login_place(user["id"], place) if _mfa.available(user) else None
    if is_new:
        _mfa.MFA_HEALTH["new_place_alerts"] += 1
        try:
            from app.services.email import send_ops_alert_email
            await send_ops_alert_email(
                "login from a new place",
                f"user={user['id']} role={user.get('role')} place={place}",
            )
        except Exception as e:  # noqa: silent — the alert is best-effort, the login stands
            logger.warning(f"[mfa] new-place alert failed: {type(e).__name__}")
    token = _issue_jwt(user)
    logger.info(f"Login OK | user={user['id']} role={user.get('role')}")
    return LoginResponse(token=token, user=_public_user(user))


class InviteRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    email: str
    role: str = "sales"
    tunnel_scope: str = "sales"
    phone: Optional[str] = None


class SetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=512)
    password: str = Field(..., min_length=8, max_length=128)


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request, _rate: None = Depends(check_rate_limit)):
    """Authenticate user with email + password, return JWT."""
    _email = req.email.lower().strip()
    _ip = client_ip(request, prefer_cf=False) or "unknown"

    if (req.company_fax or "").strip():
        from app.security.decoys import DECOY_HEALTH
        DECOY_HEALTH["form_honeypot"] = DECOY_HEALTH.get("form_honeypot", 0) + 1
        logger.warning(f"[decoy] login honeypot filled from {_ip}")
        from app.security.ban_guard import note_login_failure
        await note_login_failure(_ip, _email, form_trap=True)

    # Progressive backoff on the email+IP pair. A generic 429 either way, so a
    # throttle never tells an attacker whether the email exists.
    _wait = login_guard.retry_after(_email, _ip)
    if _wait:
        raise HTTPException(
            429, "Too many attempts — please wait and try again.",
            headers={"Retry-After": str(_wait)},
        )

    async def _reject() -> None:
        await login_guard.record_failure(_email, _ip)
        from app.security.ban_guard import note_login_failure
        await note_login_failure(_ip, _email)

    user = await db.get_user_by_email(_email)
    if not user:
        await _reject()
        raise HTTPException(401, "Invalid email or password")

    # Check password
    stored_hash = user.get("password_hash")
    if not stored_hash:
        await _reject()
        raise HTTPException(401, "Invalid email or password")

    if not bcrypt.checkpw(req.password.encode("utf-8"), stored_hash.encode("utf-8")):
        await _reject()
        raise HTTPException(401, "Invalid email or password")

    # Check active
    if not user.get("is_active", True):
        raise HTTPException(403, "Account disabled — contact admin")

    login_guard.clear(_email, _ip)

    # PR-13: the privileged roles need a second factor. Not enrolled yet →
    # a token that can only enrol (nobody is locked out at merge).
    from app.security import mfa as _mfa
    if _mfa.applies_to(user):
        if _mfa.is_enrolled(user):
            _mfa.MFA_HEALTH["challenged"] += 1
            return LoginResponse(user=_public_user(user), mfa="required",
                                 mfa_token=_mfa.issue_limited(user, _mfa.SCOPE_PENDING))
        _mfa.MFA_HEALTH["enroll_required"] += 1
        return LoginResponse(user=_public_user(user), mfa="enroll",
                             mfa_token=_mfa.issue_limited(user, _mfa.SCOPE_ENROLL))

    return await _complete_login(user, request, _ip)


# ── Second factor (PR-13) ─────────────────────────────────────

class MfaCodeRequest(BaseModel):
    mfa_token: str = Field(..., min_length=20, max_length=2048)
    code: str = Field(..., min_length=6, max_length=32)


class MfaEnrollStartRequest(BaseModel):
    mfa_token: Optional[str] = Field(default=None, max_length=2048)


class MfaEnrollConfirmRequest(BaseModel):
    mfa_token: Optional[str] = Field(default=None, max_length=2048)
    code: str = Field(..., min_length=6, max_length=12)


async def _user_for(token: Optional[str], request: Request, scopes: tuple[str, ...]) -> dict:
    """The user a limited token names — or, for enrolment from the profile
    page, the caller's full session."""
    from app.security import mfa as _mfa
    from app.security.token_version import matches

    payload = _mfa.decode_limited(token or "", scopes)
    if payload is None:
        if not token:
            # Enrolment from a full session (Profile → Security).
            current = get_current_user(request)
            user = await db.get_user_by_id(current["id"])
            if user:
                return user
        raise HTTPException(401, "This step expired — please sign in again")
    if not matches(payload["sub"], payload.get("token_version")):
        raise HTTPException(401, "This step expired — please sign in again")
    user = await db.get_user_by_id(payload["sub"])
    if not user or not user.get("is_active", True):
        raise HTTPException(401, "This step expired — please sign in again")
    return user


async def _mfa_failed(user: dict, ip: str) -> None:
    from app.security import mfa as _mfa
    _mfa.MFA_HEALTH["failed"] += 1
    # Counted with the password failures: the same email+IP backoff.
    await login_guard.record_failure((user.get("email") or "").lower(), ip)
    raise HTTPException(401, "That code did not work")


@router.post("/mfa", response_model=LoginResponse)
async def mfa_verify(req: MfaCodeRequest, request: Request, _rate: None = Depends(check_rate_limit)):
    """Second step of a login: a TOTP code, or one of the recovery codes."""
    from app.security import mfa as _mfa
    from app.security.mfa_store import unused_recovery_codes, use_recovery_code

    ip = client_ip(request, prefer_cf=False) or "unknown"
    user = await _user_for(req.mfa_token, request, (_mfa.SCOPE_PENDING,))
    wait = login_guard.retry_after((user.get("email") or "").lower(), ip)
    if wait:
        raise HTTPException(429, "Too many attempts — please wait and try again.",
                            headers={"Retry-After": str(wait)})
    secret = _mfa.decrypt(user.get("totp_secret_enc") or "")
    if not secret or not _mfa.is_enrolled(user):
        raise HTTPException(401, "This step expired — please sign in again")

    ok = _mfa.verify_code(secret, req.code)
    if not ok and _mfa.looks_like_recovery(req.code):
        for row in await unused_recovery_codes(user["id"]):
            if _mfa.recovery_matches(req.code, row.get("code_hash", "")):
                ok = await use_recovery_code(row["id"])
                if ok:
                    _mfa.MFA_HEALTH["recovery_used"] += 1
                break
    if not ok:
        await _mfa_failed(user, ip)
    _mfa.MFA_HEALTH["passed"] += 1
    login_guard.clear((user.get("email") or "").lower(), ip)
    return await _complete_login(user, request, ip)


@router.post("/mfa/enroll/start")
async def mfa_enroll_start(req: MfaEnrollStartRequest, request: Request, _rate: None = Depends(check_rate_limit)):
    """A new TOTP secret for this account: the QR (SVG) and the otpauth link
    for manual entry. Not active until /mfa/enroll/confirm sees a code."""
    from app.security import mfa as _mfa
    from app.security.mfa_store import set_pending_secret

    user = await _user_for(req.mfa_token, request, (_mfa.SCOPE_ENROLL,))
    if not _mfa.available(user):
        raise HTTPException(503, "Two-factor sign-in is not available yet")
    if _mfa.is_enrolled(user):
        # Never re-enrol in place: clearing mfa_enrolled_at would drop the
        # second factor until the confirm. Resetting a lost device is an
        # owner action, not a self-service one.
        raise HTTPException(409, "Two-factor sign-in is already set up")
    secret = _mfa.new_secret()
    if not await set_pending_secret(user["id"], _mfa.encrypt(secret)):
        raise HTTPException(503, "Two-factor sign-in is not available yet")
    uri = _mfa.provisioning_uri(secret, user.get("email") or "")
    return {"success": True, "data": {"otpauth_uri": uri, "qr_svg": _mfa.qr_svg(uri)}, "count": 1}


@router.post("/mfa/enroll/confirm")
async def mfa_enroll_confirm(req: MfaEnrollConfirmRequest, request: Request, _rate: None = Depends(check_rate_limit)):
    """The first code from the app turns MFA on. Returns the 10 recovery
    codes ONCE (only their hashes are stored) and, on the login path, the
    session token."""
    from app.security import mfa as _mfa
    from app.security.mfa_store import mark_enrolled, replace_recovery_codes

    ip = client_ip(request, prefer_cf=False) or "unknown"
    user = await _user_for(req.mfa_token, request, (_mfa.SCOPE_ENROLL,))
    secret = _mfa.decrypt(user.get("totp_secret_enc") or "")
    if not secret:
        raise HTTPException(409, "Start the set-up again")
    if not _mfa.verify_code(secret, req.code):
        await _mfa_failed(user, ip)
    codes = _mfa.new_recovery_codes()
    if not await replace_recovery_codes(user["id"], [_mfa.hash_recovery(c) for c in codes]):
        raise HTTPException(503, "Could not save the recovery codes — try again")
    if not await mark_enrolled(user["id"]):
        raise HTTPException(503, "Could not finish the set-up — try again")
    _mfa.MFA_HEALTH["enrolled"] += 1
    out: dict = {"recovery_codes": codes}
    if req.mfa_token:
        session = await _complete_login(user, request, ip)
        out.update(token=session.token, user=session.user)
    return {"success": True, "data": out, "count": 1}


class CrmExchangeRequest(BaseModel):
    token: str


@router.post("/sso/crm-exchange", response_model=LoginResponse)
async def crm_sso_exchange(req: CrmExchangeRequest, _rate: None = Depends(check_rate_limit)):
    """Verify a short-lived JWT signed by the CRM (docs/chatbot-sso.md) and, if it
    maps to a known active BBC user (by email), issue our own normal session JWT.

    role/tunnel_scope ALWAYS come from OUR users table — never trusted from the CRM
    token (they send none today; even if added later, ignored). Fails closed."""
    # Shared with the presence gate (app/security/crm_token.py): one
    # verification policy, not two copies drifting apart. The purpose is
    # named explicitly so a token minted for a presence CHECK can never
    # be exchanged for a session — absent stays accepted, which is the
    # CRM's shape today.
    payload = verify_crm_token(req.token, expected_purpose=PURPOSE_LOGIN)

    email = payload.get("email")
    if not email:
        # Expected until the CRM team adds `email` to their payload (they've confirmed
        # it's a one-line change). Fail closed with a clear, actionable message.
        logger.warning("CRM SSO exchange: token has no email claim yet — cannot map to a BBC user")
        raise HTTPException(
            400,
            "SSO token is missing the 'email' claim. The CRM integration needs to include "
            "the user's email in the JWT payload before auto-login can work.",
        )

    user = await db.get_user_by_email(email.lower().strip())
    if not user:
        logger.warning(f"CRM SSO exchange: unknown email {email}")
        raise HTTPException(401, "No matching BBC account for this user")
    if not user.get("is_active", True):
        logger.warning(f"CRM SSO exchange: inactive account {email}")
        raise HTTPException(403, "Account disabled — contact admin")

    token = _issue_jwt(user)  # role/tunnel_scope from OUR db, not the CRM payload
    logger.info(f"CRM SSO exchange login | email={user['email']} role={user.get('role')}")

    return LoginResponse(
        token=token,
        user={
            "id": user["id"],
            "email": user["email"],
            "name": user.get("name", ""),
            "role": user.get("role", "sales"),
            "tunnel_scope": user.get("tunnel_scope", "sales"),
            "phone": user.get("phone", ""),
            "avatar_url": user.get("avatar_url") or None,
        },
    )


# Staff accounts live only on the company's own domains. An invite or
# reactivation to any other domain is refused (PR-4 domain gate).
_STAFF_EMAIL_DOMAINS = ("buybusinessclass.com", "businessclass-tickets.com")


def _staff_email_ok(email: str) -> bool:
    domain = (email or "").lower().strip().split("@")[-1]
    return any(domain == d or domain.endswith("." + d) for d in _STAFF_EMAIL_DOMAINS)


@router.post("/invite")
async def invite_user(req: InviteRequest, current_user: dict = Depends(get_current_user)):
    """Create new user with temporary password. Owner/admin only."""
    if current_user.get("role") not in ("owner", "admin", "dev"):
        raise HTTPException(403, "Only owner/admin can invite users")
    if not _staff_email_ok(req.email):
        from app.flags import flag
        if flag("ROLE_GATES_ENFORCE", False):
            raise HTTPException(400, "Staff accounts must use a company email address")
        logger.warning(f"[domain-gate] would refuse non-company invite email: {req.email}")
    if req.role == "owner" and current_user.get("role") != "owner":
        raise HTTPException(403, "Only an owner can grant the owner role")

    # Check email not taken — or reactivate if inactive
    from app.services.email import generate_invite_token, send_invite_email
    existing = await db.get_user_by_email(req.email.lower().strip())
    if existing:
        if not existing.get("is_active", True):
            # User exists but is inactive — reactivate
            is_requester_owner = current_user.get("role") == "owner"
            existing_role = existing.get("role", "sales")

            # Admin cannot reactivate an owner
            if existing_role == "owner" and not is_requester_owner:
                raise HTTPException(403, "Only an owner can reactivate another owner")

            # Owner can change role; admin keeps original role
            update_payload = {
                "is_active": True,
                "password_hash": None,
                "name": req.name,
                "role": req.role if is_requester_owner else existing_role,
                "tunnel_scope": req.tunnel_scope if is_requester_owner else existing.get("tunnel_scope", "sales"),
            }
            if req.phone:
                update_payload["phone"] = req.phone

            updated = await db.update_user(existing["id"], update_payload)
            if not updated:
                raise HTTPException(500, "Failed to reactivate user")

            # A double-clicked Invite button minted twins 240ms apart and
            # the second invalidated the first — the one already in Kate's
            # inbox. Reuse a token created seconds ago instead.
            _recent = await db.find_recent_invite_token(existing["id"], "set_password", 5)
            if _recent:
                logger.info(f"Invite de-duplicated for {req.email} (token <5s old)")
                return {"success": True, "data": {
                    "id": existing["id"], "email": req.email, "name": req.name,
                    "role": update_payload["role"], "reactivated": True,
                    "email_sent": True, "deduplicated": True,
                }}
            token = generate_invite_token()
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.invite_link_expiry_minutes)
            token_row = await db.create_invite_token({
                "user_id": existing["id"],
                "token": token,
                "purpose": "set_password",
                "expires_at": expires_at.isoformat(),
                "created_by": current_user.get("id"),
            })
            if not token_row:
                raise HTTPException(500, "Failed to generate invite token")
            # AFTER the new token exists: invalidating first meant a failed
            # create left the operator with no valid link at all.
            await db.invalidate_active_invite_tokens(
                existing["id"], purpose="set_password", except_token=token
            )

            invite_url = f"{settings.admin_panel_url.rstrip('/')}{settings.invite_link_path}?token={token}"
            email_sent = await send_invite_email(
                req.email,
                req.name,
                invite_url,
                settings.invite_link_expiry_minutes,
            )
            if not email_sent:
                logger.warning(f"Reactivation email failed for {req.email} — user still reactivated")

            logger.info(
                f"User REACTIVATED | email={req.email} "
                f"role={update_payload['role']} "
                f"by={current_user.get('email', 'admin')}"
            )

            return {
                "success": True,
                "data": {
                    "id": existing["id"],
                    "email": req.email,
                    "name": req.name,
                    "role": update_payload["role"],
                    "reactivated": True,
                    "email_sent": email_sent,
                },
            }
        # Active user without password = invite not completed → allow reinvite
        if not existing.get("password_hash"):
            user_id = existing["id"]
            await db.invalidate_active_invite_tokens(user_id, purpose="set_password")
            token = generate_invite_token()
            expires_at = datetime.now(timezone.utc) + timedelta(
                minutes=settings.invite_link_expiry_minutes
            )
            token_row = await db.create_invite_token({
                "user_id": user_id,
                "token": token,
                "purpose": "set_password",
                "expires_at": expires_at.isoformat(),
                "created_by": current_user.get("id"),
            })
            if not token_row:
                raise HTTPException(500, "Failed to generate invite token")

            invite_url = (
                f"{settings.admin_panel_url.rstrip('/')}"
                f"{settings.invite_link_path}?token={token}"
            )
            email_sent = await send_invite_email(
                req.email.lower().strip(),
                existing.get("name") or req.name,
                invite_url,
                settings.invite_link_expiry_minutes,
            )
            logger.info(f"Reinvite sent to {req.email} (email_sent={email_sent})")
            return {
                "success": True,
                "data": {
                    "id": user_id,
                    "email": req.email.lower().strip(),
                    "reinvited": True,
                    "email_sent": email_sent,
                },
            }

        raise HTTPException(409, "Email already in use by an active user")

    user = await db.create_user({
        "email": req.email.lower().strip(),
        "name": req.name,
        "role": req.role,
        "tunnel_scope": req.tunnel_scope,
        "password_hash": None,
        "is_active": True,
        "is_ready": False,
        **({"phone": req.phone} if req.phone else {}),
    })

    if not user:
        raise HTTPException(500, "Failed to create user")

    logger.info(f"User invited | email={req.email} role={req.role} by={current_user.get('email', 'admin')}")

    # No dedup check here: the user row was created microseconds ago, so a
    # prior token cannot exist. The double-click case is the RESEND path.
    token = generate_invite_token()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.invite_link_expiry_minutes)
    token_row = await db.create_invite_token({
        "user_id": user["id"],
        "token": token,
        "purpose": "set_password",
        "expires_at": expires_at.isoformat(),
        "created_by": current_user.get("id"),
    })
    if not token_row:
        raise HTTPException(500, "Failed to generate invite token")
    await db.invalidate_active_invite_tokens(
        user["id"], purpose="set_password", except_token=token
    )

    invite_url = f"{settings.admin_panel_url.rstrip('/')}{settings.invite_link_path}?token={token}"
    email_sent = await send_invite_email(
        req.email,
        req.name,
        invite_url,
        settings.invite_link_expiry_minutes,
    )
    if not email_sent:
        logger.warning(f"Invite email failed for {req.email} — user still created")

    return {"success": True, "data": {"id": user["id"], "email": user["email"], "name": user["name"], "role": user["role"], "email_sent": email_sent}}


@router.post("/set-password")
async def set_password(req: SetPasswordRequest, _rate: None = Depends(check_rate_limit)):
    """One-time invite token activation.
    Token is valid only if unused and not expired (invite_link_expiry_minutes)."""
    token_row = await db.consume_valid_invite_token(req.token, purpose="set_password")
    if not token_row:
        # WHY it failed decides what the page can offer. One message for
        # four different causes made every failure look like the
        # operator's mistake.
        reason = await db.diagnose_invite_token(req.token, purpose="set_password")
        detail = {
            "expired": "This invite link has expired. Request a new one below.",
            "used": "This invite link was already used. Sign in with your password, "
                    "or use 'Forgot password'.",
            "superseded": "A newer invite was sent for this account — please use "
                          "the most recent email.",
            "unknown": "This invite link isn't valid. Request a new one below.",
        }[reason]
        raise HTTPException(400, detail=f"{reason}: {detail}")

    password_hash = bcrypt.hashpw(
        req.password.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")

    user_id = token_row.get("user_id")
    if not user_id:
        raise HTTPException(400, "Invite token is malformed")

    updated = await db.update_user(user_id, {"password_hash": password_hash, "is_active": True})
    if not updated:
        raise HTTPException(500, "Failed to set password")

    return {"success": True, "data": {"user_id": user_id}}


class RequestInviteRequest(BaseModel):
    email: str


# email → last re-issue timestamp. In-process is enough: the point is to
# stop a frustrated operator hammering the button, not to defend a border.
_REINVITE_LAST: dict[str, datetime] = {}
_REINVITE_COOLDOWN_SECONDS = 600
_REINVITE_TASKS: set = set()   # keeps background re-issues from being GC'd


@router.post("/request-invite")
async def request_invite(req: RequestInviteRequest, _rate: None = Depends(check_rate_limit)):
    """An operator whose link died asks for a new one themselves.

    Answers the SAME way whether or not the account exists — a dead invite
    page must not become an account-enumeration oracle."""
    from app.services.email import generate_invite_token, send_invite_email

    generic = {
        "success": True,
        "data": {"message": "If that address has a pending invite, a new link is on its way."},
    }
    email = (req.email or "").lower().strip()
    if not email or "@" not in email:
        return generic

    now = datetime.now(timezone.utc)
    # Bounded: the keys are attacker-supplied, so old entries are dropped
    # rather than accumulating one per address forever.
    if len(_REINVITE_LAST) > 512:
        cutoff = now - timedelta(seconds=_REINVITE_COOLDOWN_SECONDS)
        for k in [k for k, v in _REINVITE_LAST.items() if v < cutoff]:
            _REINVITE_LAST.pop(k, None)
    last = _REINVITE_LAST.get(email)
    if last and (now - last).total_seconds() < _REINVITE_COOLDOWN_SECONDS:
        logger.info(f"Re-invite throttled for {email}")
        return generic
    _REINVITE_LAST[email] = now

    async def _reissue() -> None:
        """Off the response path ON PURPOSE: doing the lookup, the insert
        and the SMTP round-trip inline made an existing address answer
        seconds slower than an unknown one — identical bodies, and a
        stopwatch as the enumeration oracle."""
        try:
            user = await db.get_user_by_email(email)
            # Only accounts still waiting to be activated may be re-invited:
            # one with a password uses "forgot password", and a DEACTIVATED
            # account must never be able to resurrect its own access.
            if not user or user.get("password_hash") or not user.get("is_active", True):
                return
            token = generate_invite_token()
            expires_at = datetime.now(timezone.utc) + timedelta(
                minutes=settings.invite_link_expiry_minutes
            )
            token_row = await db.create_invite_token({
                "user_id": user["id"],
                "token": token,
                "purpose": "set_password",
                "expires_at": expires_at.isoformat(),
                "created_by": user["id"],
            })
            if not token_row:
                logger.error(f"Re-invite token creation failed for {email}")
                return
            await db.invalidate_active_invite_tokens(
                user["id"], purpose="set_password", except_token=token
            )
            invite_url = f"{settings.admin_panel_url.rstrip('/')}{settings.invite_link_path}?token={token}"
            await send_invite_email(
                email, user.get("name") or "", invite_url,
                settings.invite_link_expiry_minutes,
            )
            logger.info(f"Re-invite sent for {email}")
        except Exception as e:  # noqa: BLE001 — a background task must not vanish loudly
            logger.error(f"Re-invite failed for {email}: {e}")

    import asyncio

    _task = asyncio.create_task(_reissue())
    _REINVITE_TASKS.add(_task)
    _task.add_done_callback(_REINVITE_TASKS.discard)
    return generic


async def _self_update_trap(user: dict, request: Request, extra: list[str]) -> None:
    from app.security import behavior as bh
    from app.security.ban_store import get_store
    from app.services.email import send_ops_alert_email

    ip = client_ip(request, prefer_cf=False) or ""
    enforce = bh.enforced("BEHAVIOR_TRAPS_ENFORCE")
    bh.BEHAVIOR_HEALTH["operator_traps"]["auth_me_fields"] = bh.BEHAVIOR_HEALTH["operator_traps"].get("auth_me_fields", 0) + 1
    bh.record("auth_me_fields", ip=ip, enforced_now=enforce)
    fields = ",".join(f[:30] for f in extra[:5])
    try:
        await send_ops_alert_email(
            "operator trap: PATCH /api/auth/me",
            f"user={user.get('id')} fields={fields} ip={ip} {'revoked+banned' if enforce else 'counted'}",
        )
    except Exception as e:  # noqa: silent — the alert is best-effort
        logger.warning(f"[trap] alert failed: {type(e).__name__}")
    if not enforce:
        return
    store = get_store()
    if store is not None:
        await store.revoke_sessions(user["id"])
        if ip:
            await store.ban(ip, "auth_me_fields", 24 * 3600)
    raise HTTPException(status_code=401, detail="Session no longer valid — please login again")


class SelfUpdateRequest(BaseModel):
    name: str | None = None
    phone: str | None = None
    avatar_url: str | None = None
    # SECURITY: role / tunnel_scope / is_active / email intentionally ABSENT.


@router.patch("/me")
async def update_own_profile(
    payload: SelfUpdateRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Self-service profile update — any authenticated role.

    Whitelisted fields only. Re-issues JWT so claims reflect changes on refresh.
    """
    # PR-15 operator trap: the panel only ever sends name/phone/avatar_url.
    # Anything else (role, is_active, tunnel_scope…) is someone testing
    # whether the whitelist holds. Shadow: counted + alert; enforced
    # (BEHAVIOR_TRAPS_ENFORCE): sessions revoked, IP banned, 401.
    try:
        _raw = await request.json()
    except Exception as e:  # noqa: silent — pydantic already parsed the body; a re-read failure just skips the trap
        logger.debug(f"[trap] PATCH /me body re-read skipped: {type(e).__name__}")
        _raw = {}
    _extra = sorted(set(_raw) - {"name", "phone", "avatar_url"}) if isinstance(_raw, dict) else []
    if _extra:
        await _self_update_trap(current_user, request, _extra)

    updates: dict = {}

    if payload.name is not None:
        name = payload.name.strip()
        if not name or len(name) > 100:
            raise HTTPException(status_code=422, detail="Invalid name")
        updates["name"] = name

    if payload.phone is not None:
        phone = payload.phone.strip()
        if len(phone) > 30:
            raise HTTPException(status_code=422, detail="Invalid phone")
        updates["phone"] = phone

    if payload.avatar_url is not None:
        url = payload.avatar_url.strip()
        if url and (len(url) > 500 or not url.startswith(("http://", "https://"))):
            raise HTTPException(status_code=422, detail="Invalid avatar URL")
        updates["avatar_url"] = url or None

    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")

    user_id = current_user["id"]
    updated = await db.update_user(user_id, updates)
    if not updated:
        raise HTTPException(status_code=500, detail="Update failed")

    token = _issue_jwt(updated)

    return {
        "user": {
            "id": updated["id"],
            "email": updated["email"],
            "name": updated.get("name"),
            "phone": updated.get("phone"),
            "avatar_url": updated.get("avatar_url"),
            "role": updated.get("role"),
            "tunnel_scope": updated.get("tunnel_scope"),
        },
        "token": token,
    }


@router.post("/me/avatar")
async def upload_own_avatar(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """Upload a profile photo (any authenticated role).

    Stores the image in Supabase Storage (`avatars` bucket), saves the public
    URL on the user, and re-issues the JWT so claims stay in sync with `/me`.
    """
    # Early reject when size is known and over the limit (still re-check after
    # read — clients can lie about Content-Length).
    known_size = getattr(file, "size", None)
    if known_size is not None and known_size > settings.avatar_max_bytes:
        raise HTTPException(status_code=422, detail="Image too large (max 2MB)")

    data = await file.read(settings.avatar_max_bytes + 1)
    if len(data) > settings.avatar_max_bytes:
        raise HTTPException(status_code=422, detail="Image too large (max 2MB)")
    if not data:
        raise HTTPException(status_code=422, detail="Empty file")

    # PR-15: the declared type was the only check. The first bytes must agree
    # with it (PNG, JPEG, WEBP), and the file must be at least 1 KB.
    from app.security import behavior as bh
    _declared = (file.content_type or "").split(";")[0].strip().lower()
    # An unsupported declared type keeps its 422 from upload_avatar; a
    # supported one must be what it says.
    if _declared in ("image/png", "image/jpeg", "image/webp") and not bh.upload_matches(_declared, data):
        bh.BEHAVIOR_HEALTH["upload_mismatch"] += 1
        bh.record("upload_mismatch", ip=current_user.get("id", ""))
        logger.warning(f"[trap] avatar upload type mismatch user={current_user.get('id')} declared={(file.content_type or '')[:40]}")
        raise HTTPException(status_code=415, detail="That file is not the image type it says it is")

    try:
        url = await upload_avatar(
            data=data,
            content_type=file.content_type or "",
            user_id=current_user["id"],
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except RuntimeError as e:
        # Bucket missing / Storage hiccup — FE keeps the URL fallback.
        raise HTTPException(status_code=502, detail=str(e)) from e

    updated = await db.update_user(current_user["id"], {"avatar_url": url})
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to save avatar")

    token = _issue_jwt(updated)
    return {"avatar_url": url, "token": token}
