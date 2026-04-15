"""Admin API — Notifications (stale / pending conversations)."""

from fastapi import APIRouter

from app.db import supabase as db

router = APIRouter()


@router.get("/notifications")
async def get_notifications():
    """Return conversations waiting for agent pickup."""
    stale = await db.get_pending_conversations()
    return {
        "success": True,
        "stale_conversations": stale,
        "count": len(stale),
    }
