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
    "command_errors": 0,
    "client_errors": 0,       # the client could not be made from REDIS_URL: tried again after COOLDOWN_S
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

        try:
            _client = aioredis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_timeout=SOCKET_TIMEOUT_S,
                socket_connect_timeout=SOCKET_TIMEOUT_S,
                health_check_interval=30,
            )
        except Exception as e:
            # A REDIS_URL redis-py cannot read (an option of the wrong type): every caller goes on without Redis,
            # as when it is down, and the client is not made again before COOLDOWN_S (each heartbeat would try,
            # and log, again: CodeRabbit #273). The URL is not logged: it can carry the password.
            logger.error(f"[redis] the client could not be made from REDIS_URL: {type(e).__name__}")
            _client_failed(e)
            return None
    return _client


def _client_failed(err: BaseException) -> None:
    """The client could not be made: counted apart from the commands, and the cooldown starts, as when Redis
    is away."""
    global _down_until
    REDIS_HEALTH["errors"] += 1
    REDIS_HEALTH["client_errors"] += 1
    REDIS_HEALTH["last_error"] = f"client: {type(err).__name__}"
    _down_until = time.monotonic() + COOLDOWN_S


def _unreachable(err: BaseException) -> bool:
    """A failure of the connection (Redis away or slow), not of one command."""
    from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError

    return isinstance(err, (RedisConnectionError, RedisTimeoutError, OSError, TimeoutError))


def redis_failed(where: str, err: BaseException) -> None:
    """A Redis call failed. When Redis is unreachable every caller skips it for COOLDOWN_S; a command Redis
    refused (a wrong type, a script error) is counted without turning Redis off for the other callers, the
    locks above all. The caller logs."""
    global _down_until
    REDIS_HEALTH["errors"] += 1
    REDIS_HEALTH["last_error"] = f"{where}: {type(err).__name__}"
    if _unreachable(err):
        _down_until = time.monotonic() + COOLDOWN_S
    else:
        REDIS_HEALTH["command_errors"] += 1


def set_client_for_tests(client: Any) -> None:
    """A fake client (tests/fake_redis.py), or None to forget it. Clears the cooldown."""
    global _client, _down_until
    _client = client
    _down_until = 0.0
