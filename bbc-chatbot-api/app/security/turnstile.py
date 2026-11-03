"""Invisible Cloudflare Turnstile at /chat/init (PR-12, owner's decision ⚑1).

TURNSTILE_ENABLED off at merge. On: the widget sends the token Cloudflare's
script produced; the server checks it with siteverify — always server-side,
a client-side pass proves nothing.

Cloudflare down or slow (3 s) → the visitor passes and it is counted: a
captcha outage must never close the chat. success=false → 403.
The API does not sit behind Cloudflare for this to work. Privacy: Cloudflare
joins the processors list and the invisible mode is named in the policy.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException

from app.flags import flag

logger = logging.getLogger(__name__)

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

TURNSTILE_HEALTH = {"checked": 0, "passed": 0, "failed": 0, "unavailable": 0, "missing": 0}


async def _siteverify(secret: str, token: str, remote_ip: Optional[str]) -> Optional[bool]:
    """True/False from Cloudflare, or None when it could not be asked."""
    import httpx

    data = {"secret": secret, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.post(SITEVERIFY_URL, data=data)
        if r.status_code != 200:
            logger.warning(f"[turnstile] siteverify HTTP {r.status_code}")
            return None
        return bool(r.json().get("success"))
    except Exception as e:
        logger.warning(f"[turnstile] siteverify unavailable: {type(e).__name__}: {e}")
        return None


async def verify_or_raise(token: Optional[str], remote_ip: Optional[str]) -> None:
    if not flag("TURNSTILE_ENABLED", False):
        return
    from config.settings import settings

    secret = (getattr(settings, "turnstile_secret", "") or "").strip()
    if not secret:
        TURNSTILE_HEALTH["unavailable"] += 1
        logger.warning("[turnstile] enabled but no TURNSTILE_SECRET — letting the visitor through")
        return
    TURNSTILE_HEALTH["checked"] += 1
    if not token:
        TURNSTILE_HEALTH["missing"] += 1
        TURNSTILE_HEALTH["failed"] += 1
        raise HTTPException(status_code=403, detail="Please reload the page and try again")
    ok = await _siteverify(secret, token, remote_ip)
    if ok is None:
        TURNSTILE_HEALTH["unavailable"] += 1
        return
    if not ok:
        TURNSTILE_HEALTH["failed"] += 1
        raise HTTPException(status_code=403, detail="Please reload the page and try again")
    TURNSTILE_HEALTH["passed"] += 1


def _reset_for_tests() -> None:
    TURNSTILE_HEALTH.update(checked=0, passed=0, failed=0, unavailable=0, missing=0)
