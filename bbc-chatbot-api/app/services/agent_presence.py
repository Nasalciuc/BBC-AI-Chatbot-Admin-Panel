"""Who is at the desk: the heartbeat's presence write, to Redis on every beat and to
Supabase at most every few seconds.

Every open panel beats every 5 s (use-heartbeat.ts), and each beat was an UPDATE on
users: 2.6 million a week (14-21 Nov), two thirds of every request this API serves,
and the loudest caller of the 20-thread pool the Supabase client runs on. When
Supabase slows down, those threads fill up and ops gets "DB executor saturated"
(19 times between 17 and 21 Nov).

Now a beat:
  * goes to Redis every time: ZADD presence:operators <unix seconds> <user id>, for
    the people who may take chats (an operator role, active, chat on); anyone else
    is taken out. "Present" for the code that asks Redis (the follow-up lottery, the
    live service) is a score inside queue_presence_window_seconds, the window the
    shared queue uses. contracts/redis-keys.md lists the key.
  * goes to Supabase at most every HEARTBEAT_DB_EVERY_S (15 s), and at once when
    the operator opens another conversation (viewing_conversation_id: the sweep's
    120 s courtesy reads it) or after the process starts. Nothing that reads
    users.last_seen_at changes: the shortest window among them is the sweep's 30 s
    "fresh" test, and a write every 15 s with a beat every 5 s keeps the stored
    time at most 20 s old.
  * answers with the users row the last write returned while writes are skipped:
    the heartbeat reads role, readiness and scope from it. forget(user_id) drops it
    (db.update_user calls it), so Ready or a role change shows on the next beat.

HEARTBEAT_DB_EVERY_S=0 writes every beat, as before this change.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from app.flags import flag
from app.infra.redis_client import get_redis, redis_failed

logger = logging.getLogger(__name__)

PRESENCE_KEY = "presence:operators"
_KEEP_S = 24 * 3600  # scores older than a day are dropped as beats come in

AGENT_PRESENCE_HEALTH: dict[str, int] = {
    "beats": 0,
    "db_writes": 0,
    "db_skipped": 0,        # the row of the last write reused: one UPDATE fewer
    "db_write_failed": 0,
    "redis_written": 0,
    "redis_failed": 0,
}

# user id -> (the row the last write returned, monotonic time of the write, viewing id written)
_LAST_WRITE: dict[str, tuple[dict, float, Optional[str]]] = {}


def forget(user_id: str) -> None:
    """The user's row changed: the next beat writes and reads it again."""
    _LAST_WRITE.pop(user_id, None)


def _may_take_chats(row: Optional[dict]) -> bool:
    from app.db.supabase import _OPERATOR_ROLES

    if not isinstance(row, dict):
        return False
    return (
        (row.get("role") or "") in _OPERATOR_ROLES
        and bool(row.get("is_active", True))
        and bool(row.get("chat_enabled", True))
    )


async def beat(user_id: str, viewing_conversation_id: Optional[str] = None) -> Optional[dict]:
    """One heartbeat. Returns the users row as last written, or None when it could
    not be written or read (the heartbeat then reads the row itself)."""
    from app.db import supabase as db

    AGENT_PRESENCE_HEALTH["beats"] += 1
    viewing = viewing_conversation_id or None
    every_s = max(0, int(flag("HEARTBEAT_DB_EVERY_S", 15)))
    now = time.monotonic()
    last = _LAST_WRITE.get(user_id)
    row: Optional[dict] = None
    if last and every_s and now - last[1] < every_s and last[2] == viewing:
        row = last[0]
        AGENT_PRESENCE_HEALTH["db_skipped"] += 1
    else:
        written = await db.update_user_last_seen(user_id, viewing_conversation_id=viewing)
        if isinstance(written, dict) and written.get("id") == user_id:
            row = written
            _LAST_WRITE[user_id] = (written, now, viewing)
            AGENT_PRESENCE_HEALTH["db_writes"] += 1
        else:
            # The write failed or matched nobody (the writer logged why). The next beat writes again;
            # meanwhile the panel is beating, so Redis still hears it, judged on the last row known.
            AGENT_PRESENCE_HEALTH["db_write_failed"] += 1
            if last:
                _LAST_WRITE[user_id] = (last[0], float("-inf"), last[2])
    await _to_redis(user_id, row if row is not None else (last[0] if last else None))
    return row


async def _to_redis(user_id: str, row: Optional[dict]) -> None:
    if row is None:
        return  # nothing known about the person yet: Redis keeps what it had
    r = get_redis()
    if r is None:
        return
    stamp = int(time.time())
    try:
        pipe = r.pipeline(transaction=False)
        if _may_take_chats(row):
            pipe.zadd(PRESENCE_KEY, {user_id: stamp})
        else:
            pipe.zrem(PRESENCE_KEY, user_id)
        pipe.zremrangebyscore(PRESENCE_KEY, "-inf", stamp - _KEEP_S)
        await pipe.execute()
        AGENT_PRESENCE_HEALTH["redis_written"] += 1
    except Exception as e:
        redis_failed("presence", e)
        AGENT_PRESENCE_HEALTH["redis_failed"] += 1
        logger.warning(f"[presence] {user_id}: beat not written to Redis ({type(e).__name__}: {e})")


async def present_operator_ids(window_s: Optional[int] = None) -> Optional[set[str]]:
    """The people who may take chats and whose panel beat inside the window.

    None when Redis cannot say (not configured, cooling down, or the read failed):
    the caller asks the database instead. An empty set is an answer: nobody is there."""
    from config.settings import settings

    r = get_redis()
    if r is None:
        return None
    window = int(window_s if window_s is not None else settings.queue_presence_window_seconds)
    try:
        ids = await r.zrangebyscore(PRESENCE_KEY, int(time.time()) - window, "+inf")
    except Exception as e:
        redis_failed("presence read", e)
        logger.warning(f"[presence] who is at the desk not read from Redis ({type(e).__name__}: {e})")
        return None
    return {str(i) for i in ids}


def _reset_for_tests() -> None:
    _LAST_WRITE.clear()
    for k in AGENT_PRESENCE_HEALTH:
        AGENT_PRESENCE_HEALTH[k] = 0
