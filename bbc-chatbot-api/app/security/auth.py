"""Auth — protects all admin /api/* endpoints.
/health remains public for Railway healthcheck.
/api/chat is public (customer widget).

Accepts BOTH auth schemes:
  1. Basic Auth: Authorization: Basic base64(user:pass) — checked against API_USER/API_PASS
  2. Bearer Token: Authorization: Bearer <jwt> — V1 accepts any non-empty token,
     V2 will validate JWT against Supabase.

If API_USER and API_PASS are both empty, auth is DISABLED (dev mode).
"""

import base64
import logging
import secrets

import jwt as _jwt
from fastapi import HTTPException, Request, status
from config.settings import settings

logger = logging.getLogger(__name__)


def get_current_user(request: Request) -> dict:
    """Extract and verify credentials from Authorization header.

    Returns a user dict: {id, role, name}.
    """
    # Dev mode: no auth configured → allow everything
    if not settings.api_user or not settings.api_pass:
        return {"id": "dev", "role": "owner", "name": "dev"}

    auth = request.headers.get("Authorization", "")

    # ── Basic Auth ────────────────────────────────────────────
    if auth.startswith("Basic "):
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

        return {"id": "admin", "role": "owner", "name": user}

    # ── Bearer Token ──────────────────────────────────────────
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Empty Bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # V1.5: Try JWT decode first
        if settings.jwt_secret:
            try:
                payload = _jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
                return {
                    "id": payload.get("sub", "unknown"),
                    "email": payload.get("email", ""),
                    "role": payload.get("role", "sales"),
                    "name": payload.get("name", ""),
                    "tunnel_scope": payload.get("tunnel_scope", "sales"),
                    "phone": payload.get("phone", ""),
                }
            except _jwt.ExpiredSignatureError:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token expired — please login again",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            except _jwt.InvalidTokenError:
                pass  # Fall through to API_PASS check below

        # Fallback: Bearer token = API_PASS (backward compatible)
        if settings.api_pass and secrets.compare_digest(token, settings.api_pass):
            return {"id": "admin", "role": "owner", "name": "admin"}

        logger.warning(f"Invalid Bearer token | path={request.url.path}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ── No auth header ────────────────────────────────────────
    logger.warning(f"No Authorization header | path={request.url.path}")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


# Keep old name as alias for backwards compatibility in tests
verify_credentials = get_current_user
