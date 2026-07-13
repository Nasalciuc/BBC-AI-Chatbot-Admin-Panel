"""Auth — protects all admin /api/* endpoints.
/health remains public for Railway healthcheck.
/api/chat is public (customer widget). /api/cron uses CRON_SECRET (not this).

Auth contract (hardened for a CRM-embedded, shared-team session):
  1. Bearer JWT (the ONLY real user path): Authorization: Bearer <jwt>, signed
     with JWT_SECRET (HS256). Role + tunnel_scope come from the token claims —
     never defaulted to owner. This is what /api/auth/login issues.
  2. Basic Auth (ops escape hatch): Authorization: Basic base64(user:pass),
     checked against API_USER/API_PASS. Returns owner. Independent of JWT_SECRET.
  3. Debug bypass: ONLY when debug=True AND jwt_secret is empty — explicit,
     logged, never silent. Never happens in production (debug=False there).

Production requires JWT_SECRET. The Bearer path fails CLOSED (500) if it is
missing, so a misconfigured server can never silently accept unverified tokens.
Removed (were unsafe): silent allow-all when API_USER/API_PASS empty; "any
Bearer == API_PASS → owner"; the `if jwt_secret:` conditional that let JWT
decode be skipped.
"""

import base64
import logging
import secrets

import jwt as _jwt
from fastapi import HTTPException, Request, status
from config.settings import settings

logger = logging.getLogger(__name__)


def get_current_user(request: Request) -> dict:
    """Extract and verify credentials from the Authorization header.

    Returns a user dict: {id, email, role, name, tunnel_scope, phone}.
    Raises 401 when auth is missing/invalid, 500 when the server is
    misconfigured (no JWT_SECRET in production).
    """
    # Explicit dev bypass — ONLY when opted in (debug + no secret). Never silent.
    if settings.debug and not settings.jwt_secret:
        logger.warning(
            "AUTH DEV BYPASS active (debug=True, no jwt_secret). NEVER in production."
        )
        return {"id": "dev", "role": "owner", "name": "dev", "tunnel_scope": "all"}

    auth = request.headers.get("Authorization", "")

    # ── Basic Auth (ops escape hatch — independent of JWT_SECRET) ──
    if auth.startswith("Basic ") and settings.api_user and settings.api_pass:
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            user, pwd = decoded.split(":", 1)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Malformed Basic credentials",
                headers={"WWW-Authenticate": "Basic"},
            )

        correct_user = secrets.compare_digest(
            user.encode("utf-8"),
            settings.api_user.encode("utf-8"),
        )
        correct_pass = secrets.compare_digest(
            pwd.encode("utf-8"),
            settings.api_pass.encode("utf-8"),
        )

        if not (correct_user and correct_pass):
            logger.warning(f"Invalid Basic credentials | user={user}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )

        return {"id": "admin", "role": "owner", "name": user, "tunnel_scope": "all"}

    # ── Bearer JWT (the only accepted token path) ──────────────
    if auth.startswith("Bearer "):
        # Fail closed: JWT is the only real path — a missing secret must never
        # silently accept unverified tokens.
        if not settings.jwt_secret:
            logger.error(
                "jwt_secret not configured in non-debug mode — refusing admin request"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Server auth misconfigured",
            )

        token = auth[7:].strip()
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Empty Bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            payload = _jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        except _jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token expired — please login again",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except _jwt.InvalidTokenError:
            logger.warning(f"Invalid Bearer token | path={request.url.path}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return {
            "id": payload.get("sub", "unknown"),
            "email": payload.get("email", ""),
            "role": payload.get("role", "sales"),
            "name": payload.get("name", ""),
            "tunnel_scope": payload.get("tunnel_scope", "sales"),
            "phone": payload.get("phone", ""),
        }

    # ── No / unsupported auth header ───────────────────────────
    logger.warning(f"No/invalid Authorization | path={request.url.path}")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


# Keep old name as alias for backwards compatibility in tests
verify_credentials = get_current_user
