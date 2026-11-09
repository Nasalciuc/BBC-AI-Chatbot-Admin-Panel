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

    async def ban(self, ip: str, jail: str, seconds: int) -> tuple[int, bool]:
        """Ban ip; each repeat within 30d doubles, capped at a week.

        Returns (effective seconds, created). A scanner's parallel requests
        can all pass is_banned before the first ban lands; only the one that
        claims the ban (SET NX) counts the repeat, sets the length and gets
        created=True, so the others neither double the ban nor write a
        second row or alert.

        A Redis failure before the claim raises: the caller cannot know a ban
        was set, and (0, False) would read as "another request holds it"
        (rule 7). After the claim the ban stands; if the repeat count or the
        new length is not confirmed (a lost reply may still have applied),
        its length is what Redis reads back, or the base length when it
        can't."""
        if not ip:
            return 0, False
        if self._redis is not None:
            try:
                claimed = await self._redis.set(f"ban:{ip}", jail, nx=True, ex=max(1, int(seconds)))
                if not claimed:
                    ttl = await self._redis.ttl(f"ban:{ip}")
                    return max(0, int(ttl or 0)), False
            except Exception as e:
                logger.warning(f"[ban-store] ban redis failed, no ban set: {type(e).__name__}")
                raise
            try:
                n = await self._redis.incr(f"bancount:{ip}")
                await self._redis.expire(f"bancount:{ip}", 30 * 24 * 3600)
                eff = min(seconds * (2 ** (int(n) - 1)), _MAX_BAN)
                if eff != seconds:
                    await self._redis.expire(f"ban:{ip}", eff)
                return eff, True
            except Exception as e:
                logger.warning(f"[ban-store] ban set, repeat count or length not confirmed: {type(e).__name__}")
                try:
                    ttl = int(await self._redis.ttl(f"ban:{ip}") or 0)
                except Exception as e2:
                    logger.warning(f"[ban-store] ban length unreadable, base length recorded: {type(e2).__name__}")
                    ttl = 0
                return (ttl if ttl > 0 else int(seconds)), True
        now = time.time()
        exp = self._mem.get(ip)
        if exp is not None and exp > now:
            return int(exp - now), False
        n = self._counts.get(ip, 0) + 1
        self._counts[ip] = n
        eff = min(seconds * (2 ** (n - 1)), _MAX_BAN)
        self._mem[ip] = now + eff
        return eff, True

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

    async def note_auth_email(self, ip: str, email: str, window: int) -> int:
        """Record a failed login's email for ip; the distinct emails seen from
        it within window (0 on a Redis failure — fail-open)."""
        address = (email or "").strip().lower()
        if self._redis is not None:
            try:
                key = f"authmail:{ip}"
                await self._redis.sadd(key, address)
                await self._redis.expire(key, window)
                return int(await self._redis.scard(key))
            except Exception as e:
                logger.warning(f"[ban-store] auth-email redis failed: {type(e).__name__}")
                return 0
        now = time.time()
        seen = self._auth_emails.setdefault(ip, {})
        for k in [k for k, t in seen.items() if t < now - window]:
            seen.pop(k, None)
        seen[address] = now
        return len(seen)

    async def unban(self, ip: str) -> bool:
        """False when the live ban could not be lifted: the caller must not
        record an unban that did not happen (the identity stays blocked)."""
        if self._redis is not None:
            try:
                await self._redis.delete(f"ban:{ip}")
            except Exception as e:
                logger.warning(f"[ban-store] unban redis failed: {type(e).__name__}")
                return False
            return True
        self._mem.pop(ip, None)
        return True

    async def revoke_sessions(self, user_id: str) -> None:
        ts = int(time.time())
        # Always in memory too (one process, ADR-5): the read path is sync and
        # goes through response_cache's client, which may be down even when
        # this async one works — a revocation must never be silently missed
        # (review fix).
        _MEM_REVOKED[user_id] = ts
        if self._redis is not None:
            try:
                await self._redis.set(f"revoked_before:{user_id}", ts)
            except Exception as e:
                logger.warning(f"[ban-store] revoke redis failed (memory still holds it): {type(e).__name__}")


# Session revocation read path is SYNC (auth.get_current_user runs under a
# threadpool Depends), so it uses a sync Redis client or the memory dict.
_MEM_REVOKED: dict[str, int] = {}


def revoked_before_sync(user_id: str) -> Optional[int]:
    """The latest revocation mark for the user, from Redis and from memory."""
    marks = [v for v in (_MEM_REVOKED.get(user_id),) if v]
    try:
        from app.services.response_cache import _get_redis
        r = _get_redis()
        if r is not None:
            v = r.get(f"revoked_before:{user_id}")
            if v:
                marks.append(int(v))
    except Exception as e:
        logger.debug(f"[ban-store] revoked_before_sync redis read failed: {type(e).__name__}")
    return max(marks) if marks else None


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
