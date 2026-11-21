"""The shared async Redis client for the code that coordinates through Redis.

Locks on conversations, who is at the desk, the live list of visitors, the panel's
"opened recently": all of it is coordination, never the truth. The truth stays in
Supabase. So a Redis that does not answer must cost a request milliseconds, not
seconds, and must not take the request down with it:

  * one client per process, made on first use from REDIS_URL, with short socket
    timeouts (Redis and the API share Railway's private network);
  * after a failure every caller skips Redis for COOLDOWN_S and does what it did
    before Redis was there (an in-process lock, the database), counted in /health
    (`redis`), instead of each request waiting out its own timeout.

The older modules (ban_store, typing_indicator, response_cache, proactive) keep
their own clients; they are not moved here.

The keys and the values every service may rely on are listed in
contracts/redis-keys.md (the Go live service reads some of them).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

COOLDOWN_S = 30.0
SOCKET_TIMEOUT_S = 1.0

REDIS_HEALTH: dict[str, Any] = {
    "configured": False,
    "errors": 0,
    "skipped_cooling_down": 0,
    "last_error": None,
}

_client: Any = None
_down_until = 0.0


def get_redis(*, even_cooling_down: bool = False) -> Any:
    """The client, or None: Redis not configured, or cooling down after a failure.

    even_cooling_down: a caller that must tidy up what it set (a lock it holds)
    tries anyway; whatever it does not reach expires on its own."""
    global _client
    from config.settings import settings

    if not settings.redis_url:
        REDIS_HEALTH["configured"] = False
        return None
    REDIS_HEALTH["configured"] = True
    if not even_cooling_down and time.monotonic() < _down_until:
        REDIS_HEALTH["skipped_cooling_down"] += 1
        return None
    if _client is None:
        import redis.asyncio as aioredis

        _client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=SOCKET_TIMEOUT_S,
            socket_connect_timeout=SOCKET_TIMEOUT_S,
            health_check_interval=30,
        )
    return _client


def redis_failed(where: str, err: BaseException) -> None:
    """A Redis call failed: every caller skips Redis for COOLDOWN_S. The caller logs."""
    global _down_until
    _down_until = time.monotonic() + COOLDOWN_S
    REDIS_HEALTH["errors"] += 1
    REDIS_HEALTH["last_error"] = f"{where}: {type(err).__name__}"


def set_client_for_tests(client: Any) -> None:
    """A fake client (tests/fake_redis.py), or None to forget it. Clears the cooldown."""
    global _client, _down_until
    _client = client
    _down_until = 0.0
