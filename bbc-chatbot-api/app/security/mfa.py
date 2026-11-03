"""A second factor for the privileged roles (PR-13).

TOTP (pyotp) for MFA_REQUIRED_ROLES (owner, admin, dev by default):

  password ok, role privileged, MFA_ENFORCE on
    ├─ enrolled      → a 5-minute "mfa_pending" token; POST /api/auth/mfa
    │                  with a code (or a recovery code) gives the session.
    └─ not enrolled  → a 15-minute "mfa_enroll" token that can do ONE thing:
                       enrol (QR + confirm). Nobody is locked out at merge.

Limited tokens carry a `scope` claim; get_current_user refuses any token
that has one, so neither can reach a normal route.

The secret is encrypted with MFA_ENCRYPTION_KEY (Fernet; the key is derived
from the setting, so any long random string works). No key configured, or
migration 043 not applied → MFA is unavailable: login works as before and
/health/detail.mfa says so. The CRM SSO path is untouched — the CRM does
the authentication there.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.flags import flag

logger = logging.getLogger(__name__)

SCOPE_PENDING = "mfa_pending"
SCOPE_ENROLL = "mfa_enroll"
PENDING_TTL = timedelta(minutes=5)
ENROLL_TTL = timedelta(minutes=15)
RECOVERY_CODES = 10
ISSUER = "BBC Admin"

MFA_HEALTH = {
    "unavailable": False,
    "challenged": 0,
    "enroll_required": 0,
    "enrolled": 0,
    "passed": 0,
    "failed": 0,
    "recovery_used": 0,
    "new_place_alerts": 0,
}


# ── policy ────────────────────────────────────────────────────

def required_roles() -> set[str]:
    raw = str(flag("MFA_REQUIRED_ROLES", "owner,admin,dev"))
    return {r.strip() for r in raw.split(",") if r.strip()}


def _fernet():
    from config.settings import settings

    raw = (getattr(settings, "mfa_encryption_key", "") or "").strip()
    if len(raw) < 16:
        return None
    from cryptography.fernet import Fernet

    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    return Fernet(key)


def available(user: dict) -> bool:
    """MFA can be used for this user: a key is configured and the row has
    043's columns (a select * that lacks them means 043 is not applied)."""
    ok = _fernet() is not None and "mfa_enrolled_at" in (user or {})
    MFA_HEALTH["unavailable"] = not ok
    return ok


def applies_to(user: dict) -> bool:
    return (
        bool(flag("MFA_ENFORCE", True))
        and (user or {}).get("role") in required_roles()
        and available(user)
    )


def is_enrolled(user: dict) -> bool:
    return bool((user or {}).get("mfa_enrolled_at")) and bool((user or {}).get("totp_secret_enc"))


# ── secrets ───────────────────────────────────────────────────

def new_secret() -> str:
    import pyotp
    return pyotp.random_base32()


def encrypt(secret: str) -> str:
    f = _fernet()
    if f is None:
        raise RuntimeError("MFA_ENCRYPTION_KEY not configured")
    return f.encrypt(secret.encode()).decode()


def decrypt(token: str) -> Optional[str]:
    f = _fernet()
    if f is None or not token:
        return None
    from cryptography.fernet import InvalidToken

    try:
        return f.decrypt(token.encode()).decode()
    except InvalidToken as e:
        logger.error(f"[mfa] a stored TOTP secret does not decrypt with the current key: {type(e).__name__}")
        return None


def provisioning_uri(secret: str, email: str) -> str:
    import pyotp
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def qr_svg(uri: str) -> str:
    import io

    import qrcode
    import qrcode.image.svg

    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def verify_code(secret: str, code: str) -> bool:
    import pyotp

    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != 6:
        return False
    # One step either side: a phone a little out of sync still works.
    return pyotp.TOTP(secret).verify(code, valid_window=1)


# ── recovery codes ────────────────────────────────────────────

def new_recovery_codes() -> list[str]:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    out = []
    for _ in range(RECOVERY_CODES):
        raw = "".join(secrets.choice(alphabet) for _ in range(10))
        out.append(f"{raw[:5]}-{raw[5:]}")
    return out


def hash_recovery(code: str) -> str:
    import bcrypt
    return bcrypt.hashpw(_norm(code).encode(), bcrypt.gensalt(rounds=10)).decode()


def recovery_matches(code: str, code_hash: str) -> bool:
    import bcrypt
    try:
        return bcrypt.checkpw(_norm(code).encode(), code_hash.encode())
    except ValueError:  # noqa: silent — a malformed stored hash simply does not match
        return False


def _norm(code: str) -> str:
    return "".join(ch for ch in (code or "").lower() if ch.isalnum())


def looks_like_recovery(code: str) -> bool:
    return len(_norm(code)) == 10


# ── limited tokens ────────────────────────────────────────────

def issue_limited(user: dict, scope: str) -> str:
    import jwt
    from config.settings import settings

    ttl = PENDING_TTL if scope == SCOPE_PENDING else ENROLL_TTL
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["id"],
        "scope": scope,
        "token_version": int(user.get("token_version") or 0),
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_limited(token: str, scopes: tuple[str, ...]) -> Optional[dict]:
    import jwt
    from config.settings import settings

    if not token or not settings.jwt_secret:
        return None
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:  # noqa: silent — an invalid limited token is a plain 401 for the caller
        return None
    return payload if payload.get("scope") in scopes else None


def _reset_for_tests() -> None:
    MFA_HEALTH.update(unavailable=False, challenged=0, enroll_required=0, enrolled=0,
                      passed=0, failed=0, recovery_used=0, new_place_alerts=0)
