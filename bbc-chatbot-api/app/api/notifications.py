"""Admin API — Notifications (stale / pending conversations + assigned tasks)."""

from fastapi import APIRouter, Depends

from app.db import supabase as db
from app.security.auth import get_current_user

router = APIRouter()


@router.get("/notifications")
async def get_notifications(current_user: dict = Depends(get_current_user)):
    """Return stale conversations + active assigned tasks for current user.
    Owner/admin see all conversations. Sales/support see only their own."""
    role = current_user.get("role", "sales")
    user_id = current_user.get("id")
    agent_id = None if role in ("owner", "admin", "dev") else user_id
    stale = await db.get_pending_conversations(agent_id=agent_id)
    assigned_tasks = await db.get_assigned_tasks(user_id) if user_id else []
    return {
        "success": True,
        "stale_conversations": stale,
        "assigned_tasks": assigned_tasks,
        "count": len(stale) + len(assigned_tasks),
    }
