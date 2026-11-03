"""token_version — bump a user's version to invalidate their live sessions.

A role change or a deactivation used to wait out the 24h token expiry. The
version is a claim in the JWT; every request compares it to the users row,
cached 60s. A mismatch is a 401, so a bump logs the user out within a minute.

This is naturally inert until something bumps: a missing column, a missing
claim and a fresh user all read as 0, so 0 == 0 passes. The read is sync
(get_current_user runs under a threadpool Depends) and cached, so it adds at
most one cheap query per user per minute.
"""

from __future__ import annotations

import logging
import time
from threading import Lock
from typing import Optional

logger = logging.getLogger(__name__)

_TTL = 60.0
_cache: dict[str, tuple[int, float]] = {}
_lock = Lock()
_missing_column = False


def current_version(user_id: str) -> int:
    """The user's stored token_version, cached for 60s. 0 on any trouble."""
    if not user_id:
        return 0
    now = time.time()
    with _lock:
        hit = _cache.get(user_id)
        if hit and now - hit[1] < _TTL:
            return hit[0]
    version = _read(user_id)
    with _lock:
        _cache[user_id] = (version, now)
    return version


def _read(user_id: str) -> int:
    global _missing_column
    try:
        from app.db.supabase import get_client

        res = (
            get_client().table("users").select("token_version").eq("id", user_id).limit(1).execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            return 0
        return int(rows[0].get("token_version") or 0)
    except Exception as e:
        text = str(e).lower()
        if "token_version" in text and ("does not exist" in text or "42703" in text or "could not find" in text):
            if not _missing_column:
                _missing_column = True
                logger.warning("[token-version] column missing — apply 039; check is inert until then")
            return 0
        logger.warning(f"[token-version] read failed, treating as 0: {type(e).__name__}: {e}")
        return 0


def matches(user_id: str, claim: Optional[int]) -> bool:
    """True when the token's version claim matches the stored version."""
    return int(claim or 0) == current_version(user_id)


def invalidate_cache(user_id: str) -> None:
    with _lock:
        _cache.pop(user_id, None)


def _reset_for_tests() -> None:
    global _missing_column
    with _lock:
        _cache.clear()
    _missing_column = False
