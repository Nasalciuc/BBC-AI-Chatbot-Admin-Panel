"""CRM-signed SSO token verification — ONE home, two audiences.

Extracted verbatim from /sso/crm-exchange so the presence gate cannot
drift from the login path: same secret, same algorithm, same required
claims, same issuer check, same messages.

One verification implementation is right. One AUDIENCE would have been
wrong: the presence gate mints a token per check, machine-to-machine,
high frequency — and without a purpose claim every one of those is also
a full login credential for that agent's panel account. Anything that
merely OBSERVES one (CRM logs, an APM span, a proxy, a retry queue, an
error report) could exchange it for a session. So `expected_purpose` is
a REQUIRED argument: neither call site can forget to say what the token
is for.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import jwt
from fastapi import HTTPException

from config.settings import settings

logger = logging.getLogger(__name__)

# The claim that names what a token may be used for. `purpose` is ours;
# `aud` is read as an alias so the CRM may use either.
PURPOSE_LOGIN = "login"
PURPOSE_PRESENCE = "presence"


def verify_crm_token(
    token: str,
    *,
    expected_purpose: str,
    max_age_seconds: Optional[int] = None,
) -> dict:
    """Verify a CRM-signed SSO JWT and return its payload.

    expected_purpose:
      "login"    — accepts a token with purpose/aud == "login" OR ABSENT.
                   Absent is the CRM's current shape and must keep
                   working; any OTHER value is refused, which is what
                   stops a presence token from becoming a session.
      "presence" — REQUIRES purpose/aud == "presence". The endpoint is
                   new, so there is no legacy to protect and no reason
                   to accept a login token replayed from a browser URL.

    max_age_seconds: reject a token minted longer ago than this, even if
    `exp` is still in the future — a per-check token has no business
    living for fifteen minutes.

    Raises HTTPException exactly as the login exchange always has:
      503 — the shared secret is not configured
      401 — expired / invalid / wrong issuer / wrong purpose / too old
    """
    if not settings.chat_sso_secret or not settings.chat_sso_secret.strip():
        raise HTTPException(503, "CRM SSO not configured")

    try:
        payload = jwt.decode(
            token,
            settings.chat_sso_secret,
            algorithms=["HS256"],
            # verify_aud off because WE check the audience below, against
            # the purpose the call site declared. PyJWT's own check needs
            # an `audience=` argument and would reject any token carrying
            # `aud` before our stricter rule ever ran.
            options={"require": ["exp", "iat"], "verify_aud": False},
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

    purpose = payload.get("purpose") or payload.get("aud")
    if expected_purpose == PURPOSE_LOGIN:
        # Absent = the CRM's shape today. A token stamped for anything
        # else is NOT a login credential, whatever else it is.
        if purpose is not None and purpose != PURPOSE_LOGIN:
            logger.warning(f"CRM SSO exchange: token purpose {purpose!r} is not login")
            raise HTTPException(401, "SSO token is not valid for login")
    elif purpose != expected_purpose:
        logger.warning(
            f"CRM SSO exchange: token purpose {purpose!r} != {expected_purpose!r}"
        )
        raise HTTPException(
            401,
            f"SSO token must carry purpose='{expected_purpose}'",
        )

    if max_age_seconds is not None:
        iat = payload.get("iat")
        try:
            age = (
                datetime.now(timezone.utc)
                - datetime.fromtimestamp(float(iat), tz=timezone.utc)
            ).total_seconds()
        except (TypeError, ValueError, OSError, OverflowError):
            logger.warning("CRM SSO exchange: unreadable iat")
            raise HTTPException(401, "Invalid SSO token")
        # Negative age = the other side's clock runs fast; tolerate it.
        if age > max_age_seconds:
            logger.warning(f"CRM SSO exchange: token older than {max_age_seconds}s")
            raise HTTPException(401, "SSO token expired — please reload from the CRM")

    return payload
