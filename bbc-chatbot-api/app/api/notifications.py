"""Admin API — Notifications (stale / pending conversations)."""

from fastapi import APIRouter, Depends

from app.db import supabase as db
from app.security.auth import get_current_user

router = APIRouter()


@router.get("/notifications")
async def get_notifications(current_user: dict = Depends(get_current_user)):
    """Return stale conversations for current agent.
    Owner/admin see all. Sales/support see only their own."""
    role = current_user.get("role", "sales")
    agent_id = None if role in ("owner", "admin", "dev") else current_user.get("id")
    stale = await db.get_pending_conversations(agent_id=agent_id)
    return {
        "success": True,
        "stale_conversations": stale,
        "count": len(stale),
    }
