"""One writer at a time per conversation: inside this process, and across processes.

The lead writers (update_lead_from_entities, the card buttons) and the after-closing
send used asyncio.Lock objects that live in this process. That is correct while the
API is one process (Dockerfile --workers 1, one Railway replica) and wrong the day
there are two: each process would hold its own lock and both would write.

This keeps the in-process lock (cheap, exact, always there) and adds a Redis lock
under it when Redis answers:

    SET lock:<kind>:<conversation_id> <random token> NX PX <ttl>
    ... the work ...
    EVAL "delete the key only if it still holds my token"

The token is random (secrets), so no process can release a lock another one holds,
and the TTL frees a lock whose holder died. A conversation id is a UUID: nothing
personal is in the key.

When Redis is not configured, is cooling down after a failure, or another process
holds the key longer than WAIT_S, the work goes on under the in-process lock alone,
which is exactly the behaviour before this change, and it is counted in /health
(`locks`). A write is never dropped for want of a lock: losing the client's contact
would cost more than a score computed once from a stale read. LOCK_REDIS=false
turns the Redis half off without a deploy.
"""

from __future__ import annotations

import asyncio
import logging
import random
import secrets
import time
import weakref
from typing import Any, Optional

from app.flags import flag
from app.infra.redis_client import get_redis, redis_failed

logger = logging.getLogger(__name__)

TTL_MS = 15_000
WAIT_S = 10.0

RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""

LOCK_HEALTH: dict[str, int] = {
    "redis_held": 0,          # held in Redis too
    "local_only": 0,          # Redis off, absent or cooling down: the in-process lock alone
    "redis_errors": 0,        # a SET or a release that Redis did not answer
    "wait_timeouts": 0,       # another process held the key past WAIT_S; the work went on
    "expired_before_release": 0,  # the work outlived TTL_MS; the key was gone or someone else's
    "max_wait_ms": 0,
}

_LOCAL: dict[str, "weakref.WeakValueDictionary[str, asyncio.Lock]"] = {}
_RELEASES: set = set()  # releases started by a sync release(), kept alive until they finish


def local_locks(kind: str) -> "weakref.WeakValueDictionary[str, asyncio.Lock]":
    """The in-process locks of one kind; a lock nobody holds or waits on goes away."""
    locks = _LOCAL.get(kind)
    if locks is None:
        locks = _LOCAL[kind] = weakref.WeakValueDictionary()
    return locks


def _local(kind: str, conversation_id: str) -> asyncio.Lock:
    locks = local_locks(kind)
    lock = locks.get(conversation_id)
    if lock is None:
        lock = locks[conversation_id] = asyncio.Lock()
    return lock


def _key(kind: str, conversation_id: str) -> str:
    return f"lock:{kind}:{conversation_id}"


class ConversationLock:
    """The lock of one kind of work on one conversation.

    Used as `async with lock:`. The asyncio.Lock calls (`await acquire()`, `release()`,
    `locked()`) work too, for the writers that hold it that way; a sync release() lets
    the Redis key go a moment later, in the background."""

    def __init__(self, kind: str, conversation_id: str):
        self._kind = kind
        self._cid = conversation_id
        self._local = _local(kind, conversation_id)
        self._token: Optional[str] = None

    def locked(self) -> bool:
        return self._local.locked()

    async def acquire(self) -> bool:
        await self._local.acquire()
        try:
            self._token = await _acquire_redis(self._kind, self._cid)
        except BaseException:
            # Cancelled while waiting for the key: the in-process lock must not stay held.
            self._local.release()
            raise
        return True

    def release(self) -> None:
        token, self._token = self._token, None
        if token:
            task = asyncio.get_running_loop().create_task(_release_redis(self._kind, self._cid, token))
            _RELEASES.add(task)
            task.add_done_callback(_RELEASES.discard)
        self._local.release()

    async def __aenter__(self) -> "ConversationLock":
        await self.acquire()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        token, self._token = self._token, None
        try:
            if token:
                await _release_redis(self._kind, self._cid, token)
        finally:
            self._local.release()


def conversation_lock(kind: str, conversation_id: str) -> ConversationLock:
    return ConversationLock(kind, conversation_id)


async def _acquire_redis(kind: str, conversation_id: str) -> Optional[str]:
    """The token when the Redis lock is ours, None when the work goes on without it."""
    if not flag("LOCK_REDIS", True):
        LOCK_HEALTH["local_only"] += 1
        return None
    r = get_redis()
    if r is None:
        LOCK_HEALTH["local_only"] += 1
        return None
    key = _key(kind, conversation_id)
    token = secrets.token_hex(16)
    started = time.monotonic()
    delay = 0.02
    while True:
        try:
            got = await r.set(key, token, nx=True, px=TTL_MS)
        except Exception as e:
            redis_failed("lock", e)
            LOCK_HEALTH["redis_errors"] += 1
            logger.warning(f"[lock] {kind} {conversation_id}: Redis did not answer, in-process lock only "
                           f"({type(e).__name__}: {e})")
            return None
        if got:
            waited_ms = int((time.monotonic() - started) * 1000)
            LOCK_HEALTH["redis_held"] += 1
            LOCK_HEALTH["max_wait_ms"] = max(LOCK_HEALTH["max_wait_ms"], waited_ms)
            return token
        if time.monotonic() - started >= WAIT_S:
            LOCK_HEALTH["wait_timeouts"] += 1
            logger.warning(f"[lock] {kind} {conversation_id}: held by another process for over {WAIT_S:.0f}s, "
                           f"the work goes on under the in-process lock")
            return None
        await asyncio.sleep(delay + random.uniform(0, delay))
        delay = min(delay * 2, 0.25)


async def _release_redis(kind: str, conversation_id: str, token: str) -> None:
    r = get_redis(even_cooling_down=True)
    if r is None:
        return
    try:
        deleted = await r.eval(RELEASE_LUA, 1, _key(kind, conversation_id), token)
    except Exception as e:
        redis_failed("unlock", e)
        LOCK_HEALTH["redis_errors"] += 1
        logger.warning(f"[lock] {kind} {conversation_id}: release not confirmed, the key expires in "
                       f"{TTL_MS // 1000}s ({type(e).__name__}: {e})")
        return
    if not deleted:
        LOCK_HEALTH["expired_before_release"] += 1
        logger.warning(f"[lock] {kind} {conversation_id}: the work outlived the {TTL_MS // 1000}s lock; "
                       f"another process may have written alongside it")
