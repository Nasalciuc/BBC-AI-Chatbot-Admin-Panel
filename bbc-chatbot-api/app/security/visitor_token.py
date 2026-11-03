"""Visitor token (PR-12): proof that a chat request comes from a widget the
server started.

Today the widget generates its own visitor_id and the server believes it.
/chat/init and /chat/start now also return a token the server signed:

    v1.<visitor_id>.<issued_at>.<expires_at>.<hmac>

HMAC-SHA256 over the first four parts with VISITOR_TOKEN_SECRET; 24 h life.
Every chat route asks for it in X-Visitor-Token (SSE, which cannot send
headers, as ?vt=). The widget refreshes it through POST /chat/token.

Rollout: VISITOR_TOKEN_ENFORCE is off for the first 7 days — a missing or bad
token is only counted (widgets cached before the change send none). On, a
missing, forged, expired or someone-else's token is a 401.
No VISITOR_TOKEN_SECRET configured → nothing is issued or checked, and
/health/detail says so.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Optional

from fastapi import HTTPException

from app.flags import flag

logger = logging.getLogger(__name__)

TOKEN_TTL = 24 * 3600
REFRESH_GRACE = 7 * 24 * 3600   # an expired token can still be traded in for a week

VISITOR_TOKEN_HEALTH = {
    "issued": 0,
    "ok": 0,
    "missing": 0,
    "invalid": 0,
    "expired": 0,
    "mismatch": 0,
    "refused": 0,
    "disabled": False,
}


def _secret() -> bytes:
    from config.settings import settings
    return (getattr(settings, "visitor_token_secret", "") or "").encode()


def _sign(body: str, secret: bytes) -> str:
    return hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()


def enabled() -> bool:
    on = bool(_secret())
    VISITOR_TOKEN_HEALTH["disabled"] = not on
    return on


def issue(visitor_id: str, *, now: Optional[int] = None) -> Optional[str]:
    """A token for visitor_id, or None when no secret is configured."""
    secret = _secret()
    if not secret or not visitor_id or "." in visitor_id:
        return None
    iat = int(now if now is not None else time.time())
    body = f"v1.{visitor_id}.{iat}.{iat + TOKEN_TTL}"
    VISITOR_TOKEN_HEALTH["issued"] += 1
    return f"{body}.{_sign(body, secret)}"


def parse(token: str, *, now: Optional[int] = None, grace: int = 0) -> tuple[Optional[str], str]:
    """(visitor_id, "ok") for a valid token, else (None|visitor_id, reason).

    reason ∈ ok | missing | invalid | expired. With grace>0 an expired token
    within the grace window still parses as "ok" (the refresh path)."""
    if not token:
        return None, "missing"
    secret = _secret()
    parts = token.split(".")
    if len(parts) != 5 or parts[0] != "v1" or not secret:
        return None, "invalid"
    _, vid, iat_s, exp_s, sig = parts
    body = ".".join(parts[:4])
    if not hmac.compare_digest(sig, _sign(body, secret)):
        return None, "invalid"
    try:
        exp = int(exp_s)
        int(iat_s)
    except ValueError:  # noqa: silent — a signed token with non-numeric times is malformed, reported as invalid
        return None, "invalid"
    t = int(now if now is not None else time.time())
    if t > exp + grace:
        return vid, "expired"
    return vid, "ok"


def check(token: Optional[str], visitor_id: Optional[str]) -> str:
    """Count the outcome for a request that claims visitor_id; raise 401 when
    VISITOR_TOKEN_ENFORCE is on and the token does not prove it.

    Returns the outcome (ok | missing | invalid | expired | mismatch | disabled)."""
    if not enabled():
        return "disabled"
    vid, reason = parse(token or "")
    if reason == "ok" and visitor_id and vid != visitor_id:
        reason = "mismatch"
    VISITOR_TOKEN_HEALTH[reason] = VISITOR_TOKEN_HEALTH.get(reason, 0) + 1
    if reason != "ok" and flag("VISITOR_TOKEN_ENFORCE", False):
        VISITOR_TOKEN_HEALTH["refused"] += 1
        logger.info(f"[visitor-token] refused: {reason}")
        raise HTTPException(status_code=401, detail="Session expired — please reopen the chat")
    return reason


def _reset_for_tests() -> None:
    VISITOR_TOKEN_HEALTH.update(issued=0, ok=0, missing=0, invalid=0, expired=0,
                                mismatch=0, refused=0, disabled=False)
