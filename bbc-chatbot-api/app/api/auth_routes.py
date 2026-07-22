"""Auth endpoints — login + invite."""

import logging
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from config.settings import settings
from app.db import supabase as db
from app.security.auth import get_current_user
from app.security.rate_limiter import check_rate_limit

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
        "exp": datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expiry_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user: dict


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
async def login(req: LoginRequest, _rate: None = Depends(check_rate_limit)):
    """Authenticate user with email + password, return JWT."""
    user = await db.get_user_by_email(req.email.lower().strip())
    if not user:
        raise HTTPException(401, "Invalid email or password")

    # Check password
    stored_hash = user.get("password_hash")
    if not stored_hash:
        raise HTTPException(401, "Account not activated — contact admin")

    if not bcrypt.checkpw(req.password.encode("utf-8"), stored_hash.encode("utf-8")):
        raise HTTPException(401, "Invalid email or password")

    # Check active
    if not user.get("is_active", True):
        raise HTTPException(403, "Account disabled — contact admin")

    token = _issue_jwt(user)

    logger.info(f"Login OK | email={user['email']} role={user.get('role')}")

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


class CrmExchangeRequest(BaseModel):
    token: str


@router.post("/sso/crm-exchange", response_model=LoginResponse)
async def crm_sso_exchange(req: CrmExchangeRequest, _rate: None = Depends(check_rate_limit)):
    """Verify a short-lived JWT signed by the CRM (docs/chatbot-sso.md) and, if it
    maps to a known active BBC user (by email), issue our own normal session JWT.

    role/tunnel_scope ALWAYS come from OUR users table — never trusted from the CRM
    token (they send none today; even if added later, ignored). Fails closed."""
    if not settings.chat_sso_secret or not settings.chat_sso_secret.strip():
        raise HTTPException(503, "CRM SSO not configured")

    try:
        payload = jwt.decode(
            req.token,
            settings.chat_sso_secret,
            algorithms=["HS256"],
            options={"require": ["exp", "iat"]},
        )
    except jwt.ExpiredSignatureError:
        logger.warning("CRM SSO exchange: token expired")
        raise HTTPException(401, "SSO token expired — please reload from the CRM")
    except jwt.InvalidTokenError:
        # Covers bad signature, malformed, and ImmatureSignatureError (nbf in future).
        logger.warning("CRM SSO exchange: invalid token")
        raise HTTPException(401, "Invalid SSO token")

    if payload.get("iss") != "crm":
        logger.warning(f"CRM SSO exchange: bad issuer {payload.get('iss')!r}")
        raise HTTPException(401, "Invalid SSO token issuer")

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


@router.post("/invite")
async def invite_user(req: InviteRequest, current_user: dict = Depends(get_current_user)):
    """Create new user with temporary password. Owner/admin only."""
    if current_user.get("role") not in ("owner", "admin", "dev"):
        raise HTTPException(403, "Only owner/admin can invite users")

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

            await db.invalidate_active_invite_tokens(existing["id"], purpose="set_password")
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

    await db.invalidate_active_invite_tokens(user["id"], purpose="set_password")
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
        raise HTTPException(400, "Invite link expired or invalid")

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


class SelfUpdateRequest(BaseModel):
    name: str | None = None
    phone: str | None = None
    avatar_url: str | None = None
    # SECURITY: role / tunnel_scope / is_active / email intentionally ABSENT.


@router.patch("/me")
async def update_own_profile(
    payload: SelfUpdateRequest,
    current_user: dict = Depends(get_current_user),
):
    """Self-service profile update — any authenticated role.

    Whitelisted fields only. Re-issues JWT so claims reflect changes on refresh.
    """
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
