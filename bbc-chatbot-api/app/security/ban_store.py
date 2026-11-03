"""Ban state backing the ASGI guard (PR-11).

Redis (redis.asyncio) when REDIS_URL is set — shared across replicas and it
survives a restart; else an in-process dict (one worker, ADR-5), which is
enough for the shadow period. A ban doubles on each repeat of the same
identity within 30 days, capped at a week. Session revocation (revoked_before)
also lives here; the sync auth path reads it through revoked_before_sync.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

_MAX_BAN = 7 * 24 * 3600
_store: Optional["BanStore"] = None


class BanStore:
    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._mem: dict[str, float] = {}      # ip -> expires_at (epoch)
        self._counts: dict[str, int] = {}     # ip -> ban count (memory mode)
        self._strikes: dict[str, list[float]] = {}  # key -> hit times (memory mode)
        self._auth_emails: dict[str, dict[str, float]] = {}  # ip -> {email: last seen}

    async def is_banned(self, ip: str) -> bool:
        if not ip:
            return False
        if self._redis is not None:
            try:
                return bool(await self._redis.exists(f"ban:{ip}"))
            except Exception as e:
                logger.debug(f"[ban-store] is_banned redis failed: {type(e).__name__}")
                return False
        exp = self._mem.get(ip)
        if exp is None:
            return False
        if exp < time.time():
            self._mem.pop(ip, None)
            return False
        return True

    async def ban(self, ip: str, jail: str, seconds: int) -> int:
        """Ban ip; each repeat within 30d doubles, capped at a week. Returns the
        effective seconds."""
        if not ip:
            return 0
        if self._redis is not None:
            try:
                n = await self._redis.incr(f"bancount:{ip}")
                await self._redis.expire(f"bancount:{ip}", 30 * 24 * 3600)
                eff = min(seconds * (2 ** (int(n) - 1)), _MAX_BAN)
                await self._redis.setex(f"ban:{ip}", eff, jail)
                return eff
            except Exception as e:
                logger.debug(f"[ban-store] ban redis failed: {type(e).__name__}")
                return 0
        n = self._counts.get(ip, 0) + 1
        self._counts[ip] = n
        eff = min(seconds * (2 ** (n - 1)), _MAX_BAN)
        self._mem[ip] = time.time() + eff
        return eff

    async def strike(self, key: str, window: int) -> int:
        """Count one strike for key inside a sliding-ish fixed window; returns
        the count so far (0 on a Redis failure — fail-open)."""
        if self._redis is not None:
            try:
                n = await self._redis.incr(f"strike:{key}")
                if int(n) == 1:
                    await self._redis.expire(f"strike:{key}", window)
                return int(n)
            except Exception as e:
                logger.debug(f"[ban-store] strike redis failed: {type(e).__name__}")
                return 0
        now = time.time()
        hits = [t for t in self._strikes.get(key, []) if t > now - window]
        hits.append(now)
        self._strikes[key] = hits
        return len(hits)

    async def unban(self, ip: str) -> None:
        if self._redis is not None:
            try:
                await self._redis.delete(f"ban:{ip}")
            except Exception as e:
                logger.debug(f"[ban-store] unban redis failed: {type(e).__name__}")
            return
        self._mem.pop(ip, None)

    async def revoke_sessions(self, user_id: str) -> None:
        ts = int(time.time())
        if self._redis is not None:
            try:
                await self._redis.set(f"revoked_before:{user_id}", ts)
            except Exception as e:
                logger.debug(f"[ban-store] revoke redis failed: {type(e).__name__}")
            return
        _MEM_REVOKED[user_id] = ts


# Session revocation read path is SYNC (auth.get_current_user runs under a
# threadpool Depends), so it uses a sync Redis client or the memory dict.
_MEM_REVOKED: dict[str, int] = {}


def revoked_before_sync(user_id: str) -> Optional[int]:
    try:
        from app.services.response_cache import _get_redis
        r = _get_redis()
        if r is not None:
            v = r.get(f"revoked_before:{user_id}")
            return int(v) if v else None
    except Exception as e:
        logger.debug(f"[ban-store] revoked_before_sync failed: {type(e).__name__}")
    return _MEM_REVOKED.get(user_id)


def get_store() -> Optional[BanStore]:
    """The ban store, or None when fail-open (no Redis AND nothing in memory is
    acceptable — memory store is always available, so this returns a store)."""
    global _store
    if _store is not None:
        return _store
    redis_client = None
    try:
        from config.settings import settings
        if settings.redis_url:
            import redis.asyncio as aioredis
            redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    except Exception as e:
        logger.warning(f"[ban-store] redis.asyncio unavailable, using memory: {type(e).__name__}")
        redis_client = None
    _store = BanStore(redis_client)
    return _store


def _reset_for_tests() -> None:
    global _store
    _store = None
    _MEM_REVOKED.clear()
