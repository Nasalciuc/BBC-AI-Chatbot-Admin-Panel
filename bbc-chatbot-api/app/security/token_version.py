"""token_version — bump a user's version to invalidate their live sessions.

A role change or a deactivation used to wait out the 24h token expiry. The
version is a claim in the JWT; every request compares it to the users row,
cached 60s. A mismatch is a 401, so a bump logs the user out within a minute.

This is naturally inert until something bumps: a missing column, a missing
claim and a fresh user all read as 0, so 0 == 0 passes. A user whose row is
gone reads as NO_USER, which no token matches: deleting the row ends the
sessions, unbumped ones included. A failed read is not 0: it fails closed (the
request is a 401) unless this process already holds a version for the user. That entry cannot predate a bump, because a bump drops
it (invalidate_cache) in this same process (ADR-5, one process), and a read
that was in flight while a bump landed is neither cached nor allowed to fall
back on the dropped entry. The read is sync
(get_current_user runs under a threadpool Depends) and cached, so it adds at
most one cheap query per user per minute.

A read that hits a dead connection (the server ended it, a socket the edge
closed) is tried once more on a fresh one before it fails closed: with no
version cached yet — the first request after a deploy — that one transport
error was a 401, and the panel signs out on a 401.
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
# Moved by every invalidate_cache. A read that started before a bump's write
# may return the old version after the bump dropped the entry: that read
# answers its own request, and is never cached for the next ones.
_generation = 0
_missing_column = False

# retried: reads that hit a dead connection and were tried once more (a
# second failure also counts in read_failed).
TOKEN_VERSION_HEALTH: dict[str, int] = {
    "read_failed": 0, "refused_unreadable": 0, "bump_failed": 0, "retried": 0,
}

# What _read answers for a user whose row does not exist. Every token is
# issued for a users row (auth_routes._issue_jwt), and claims are >= 0.
NO_USER = -1


def current_version(user_id: str) -> Optional[int]:
    """The user's stored token_version, cached for 60s.

    None when it cannot be read and nothing is cached for the user: the
    caller must refuse, not guess 0 (a guessed 0 validates a revoked token).
    """
    if not user_id:
        return 0
    now = time.time()
    with _lock:
        hit = _cache.get(user_id)
        if hit and now - hit[1] < _TTL:
            return hit[0]
        seen = _generation
    version = _read(user_id)
    with _lock:
        bumped = _generation != seen  # a bump landed while this read was in flight
        if version is not None and not bumped:
            _cache[user_id] = (version, now)
    if version is None:
        # Keep serving the last version read in this process, unless a bump
        # dropped it meanwhile; with none, refuse.
        if hit is not None and not bumped:
            return hit[0]
        TOKEN_VERSION_HEALTH["refused_unreadable"] += 1
        return None
    return version


def _read(user_id: str) -> Optional[int]:
    global _missing_column
    from app.db.supabase import _is_disconnect, get_client

    for attempt in (1, 2):
        try:
            res = (
                get_client().table("users").select("token_version").eq("id", user_id).limit(1).execute()
            )
            rows = getattr(res, "data", None) or []
            if not rows:
                return NO_USER
            return int(rows[0].get("token_version") or 0)
        except Exception as e:
            text = str(e).lower()
            if "token_version" in text and ("does not exist" in text or "42703" in text or "could not find" in text):
                if not _missing_column:
                    _missing_column = True
                    logger.warning("[token-version] column missing — apply 039; check is inert until then")
                return 0
            if attempt == 1 and _is_disconnect(e):
                # A SELECT: safe to send again. httpx drops the dead socket,
                # so the second try goes out on a fresh connection.
                TOKEN_VERSION_HEALTH["retried"] += 1
                logger.warning(f"[token-version] read hit a dead connection ({type(e).__name__}) — retrying once")
                continue
            TOKEN_VERSION_HEALTH["read_failed"] += 1
            logger.warning(f"[token-version] read failed: {type(e).__name__}: {e}")
            return None
    return None  # not reached: the second attempt returns or fails above


def matches(user_id: str, claim: Optional[int]) -> bool:
    """True when the token's version claim matches the stored version."""
    version = current_version(user_id)
    return version is not None and version != NO_USER and int(claim or 0) == version


def invalidate_cache(user_id: str) -> None:
    global _generation
    with _lock:
        _cache.pop(user_id, None)
        _generation += 1


def _reset_for_tests() -> None:
    global _missing_column
    with _lock:
        _cache.clear()
    _missing_column = False
    for k in TOKEN_VERSION_HEALTH:
        TOKEN_VERSION_HEALTH[k] = 0
