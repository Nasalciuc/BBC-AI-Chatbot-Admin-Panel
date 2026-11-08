"""Admin API — Notifications: conversations waiting, and the tasks the panel
alerts about (Settings → Alerts: sound, browser notification, or both)."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from app.db import supabase as db
from app.security.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

# A supervisor hears about a teammate's task this long after it went late.
TEAM_LATE_AFTER = timedelta(minutes=30)


async def _task_alerts(user: dict) -> dict:
    """The reader's open tasks due by the end of today (for the menu count,
    the CRM dock band and the reminders), and for a supervisor or manager the
    tasks they see that are 30 minutes late or more. Raises."""
    from app.api.tasks import _can_assign, _reach
    from app.services import tasks_service
    from app.services.follow_ups import zone
    from app.services.tasks_view import task_row

    reach = await _reach(user)
    rules = await tasks_service.load_rules()
    tz = zone(rules["time_zone"]) or timezone.utc
    now = datetime.now(timezone.utc)
    end_of_today = datetime.combine(now.astimezone(tz).date() + timedelta(days=1), datetime.min.time(), tzinfo=tz)

    def due_of(t: dict):
        return datetime.fromisoformat(str(t["due_at"]).replace("Z", "+00:00")) if t.get("due_at") else None

    mine_open, _ = await db.list_follow_ups(assignee_ids=[reach["id"]])
    soon = [t for t in mine_open if (d := due_of(t)) and d < end_of_today]
    team_open: list[dict] = []
    late: list[dict] = []
    if _can_assign(reach):
        team_open, _ = await db.list_follow_ups(assignee_ids=reach["members"], with_unowned=True)
        late = [t for t in team_open if t.get("assignee_id") and t["assignee_id"] != reach["id"]
                and (d := due_of(t)) and d <= now - TEAM_LATE_AFTER][:20]
    convs, leads = await db.get_follow_up_context([t["conversation_id"] for t in [*soon, *late] if t.get("conversation_id")])
    names = await db.get_user_names() if late else {}

    def row(t: dict) -> dict:
        cid = t.get("conversation_id") or ""
        # Titles carry the client's first name; task_row masks it for a supervisor.
        r = task_row(t, conv=convs.get(cid), lead=leads.get(cid), names=names, wrote_at=None, now=now, tz=tz, masked=reach["masked"])
        return {k: r[k] for k in ("id", "title", "kind", "due_at", "state", "conversation_id", "client", "route", "assignee_name")}

    out: dict = {"set_up": True, "mine": [row(t) for t in soon], "team_late": [row(t) for t in late],
                 "counts": {"late": 0, "today": 0, "unowned": sum(1 for t in team_open if not t.get("assignee_id"))}}
    for r in out["mine"]:
        if r["state"] in ("late", "today"):
            out["counts"][r["state"]] += 1
    return out


@router.get("/notifications")
async def get_notifications(current_user: dict = Depends(get_current_user)):
    """Return stale conversations + the reader's tasks for alerts.
    Owner/admin see all conversations. Sales/support see only their own."""
    role = current_user.get("role", "sales")
    user_id = current_user.get("id")
    agent_id = None if role in ("owner", "admin", "dev", "qa") else user_id
    stale = await db.get_pending_conversations(agent_id=agent_id)
    tasks: dict = {"set_up": True, "mine": [], "team_late": [], "counts": {"late": 0, "today": 0, "unowned": 0}}
    try:
        tasks = await _task_alerts(current_user)
    except db.TasksNotSetUp:  # noqa: silent — migration 051 not applied yet: _tasks_call logged and counted it; the bell still shows the waiting conversations
        tasks["set_up"] = False
    except Exception as e:
        # The waiting conversations still come; the tasks part says it failed.
        logger.error(f"[notifications] tasks part failed: {e}", exc_info=True)
        tasks["failed"] = True
    return {
        "success": True,
        "stale_conversations": stale,
        "tasks": tasks,
        "count": len(stale) + len(tasks.get("mine") or []),
    }
