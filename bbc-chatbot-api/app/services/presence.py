"""Agent presence + activity tracking. All writes fire-and-forget."""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_last_tick: dict[str, datetime] = {}
_TICK_INTERVAL = 60
_GAP_CAP = 120


def _parse_ts(raw) -> datetime:
    if isinstance(raw, str):
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return raw


# What the tick could not do, for /health: a tick that is skipped loses at most
# its own minute or two (the next one counts the gap up to _GAP_CAP); a tick
# that guesses loses the day.
PRESENCE_HEALTH: dict = {"ticks": 0, "read_failed": 0, "write_failed": 0, "started_twice": 0}


def _is_duplicate_day(exc: Exception) -> bool:
    """Another request started the same day first (two tabs, one second)."""
    text = f"{type(exc).__name__}: {exc}"
    return "23505" in text or "duplicate key" in text


async def record_presence_tick(user_id: str, db) -> None:
    try:
        now = datetime.now(timezone.utc)
        last = _last_tick.get(user_id)
        if last and (now - last).total_seconds() < _TICK_INTERVAL:
            return
        _last_tick[user_id] = now
        today = now.date().isoformat()
        PRESENCE_HEALTH["ticks"] += 1
        try:
            row = await db.read_presence_day(user_id, today)
        except Exception as e:
            # A read that failed is not a day that has not started. Writing
            # "first seen now, 0 minutes" here reset days already counted.
            PRESENCE_HEALTH["read_failed"] += 1
            logger.warning(f"[presence] tick skipped {user_id}: the day could not be read ({type(e).__name__}: {e})")
            return
        try:
            if not row:
                await db.insert_presence_day(user_id, today, {
                    "first_seen_at": now.isoformat(),
                    "last_seen_at": now.isoformat(),
                    "minutes_online": 0,
                    "heartbeat_ticks": 1,
                })
                return
            prev = _parse_ts(row["last_seen_at"])
            elapsed = (now - prev).total_seconds()
            gained = min(elapsed, _GAP_CAP) / 60.0 if elapsed > _TICK_INTERVAL else 0
            # first_seen_at is never written again: the day started once.
            await db.update_presence_day(user_id, today, {
                "last_seen_at": now.isoformat(),
                "minutes_online": (row.get("minutes_online") or 0) + round(gained),
                "heartbeat_ticks": (row.get("heartbeat_ticks") or 0) + 1,
            })
        except Exception as e:
            if not row and _is_duplicate_day(e):
                # The day exists now: the other request's row stands, and the
                # next tick adds to it.
                PRESENCE_HEALTH["started_twice"] += 1
                logger.info(f"[presence] {user_id}: the day was started by another request")
                return
            PRESENCE_HEALTH["write_failed"] += 1
            logger.warning(f"[presence] tick not saved {user_id}: {type(e).__name__}: {e}")
    except Exception as e:
        logger.warning(f"[presence] tick failed {user_id}: {e}")


async def log_activity(
    db,
    user_id: str,
    conversation_id: str,
    action: str,
    handoff_reason: str | None = None,
    response_seconds: int | None = None,
) -> None:
    try:
        if not user_id:
            return
        await db.insert_activity_log(
            user_id, conversation_id, action, handoff_reason, response_seconds
        )
    except Exception as e:
        logger.warning(f"[activity] {user_id}/{action}: {e}")


async def log_ready_change(db, user_id: str, is_ready: bool) -> None:
    try:
        await db.insert_ready_log(user_id, is_ready)
    except Exception as e:
        logger.warning(f"[presence] ready {user_id}: {e}")
