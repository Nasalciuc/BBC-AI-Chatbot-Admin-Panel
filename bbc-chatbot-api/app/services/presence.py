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


async def record_presence_tick(user_id: str, db) -> None:
    try:
        now = datetime.now(timezone.utc)
        last = _last_tick.get(user_id)
        if last and (now - last).total_seconds() < _TICK_INTERVAL:
            return
        _last_tick[user_id] = now
        today = now.date().isoformat()
        row = await db.get_presence_day(user_id, today)
        if not row:
            await db.upsert_presence_day(user_id, today, {
                "first_seen_at": now.isoformat(),
                "last_seen_at": now.isoformat(),
                "minutes_online": 0,
                "heartbeat_ticks": 1,
            })
            return
        prev = _parse_ts(row["last_seen_at"])
        elapsed = (now - prev).total_seconds()
        gained = min(elapsed, _GAP_CAP) / 60.0 if elapsed > _TICK_INTERVAL else 0
        await db.upsert_presence_day(user_id, today, {
            "last_seen_at": now.isoformat(),
            "minutes_online": row["minutes_online"] + round(gained),
            "heartbeat_ticks": row["heartbeat_ticks"] + 1,
        })
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
