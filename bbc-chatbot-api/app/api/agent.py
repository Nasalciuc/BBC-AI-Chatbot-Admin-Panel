"""Agent presence — heartbeat endpoint."""
import logging
from fastapi import APIRouter, Depends
from app.db import supabase as db
from app.security.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/agent/heartbeat")
async def heartbeat(user: dict = Depends(get_current_user)):
    """Agent pings every 30s to signal online presence.
    Updates last_seen_at in users table."""
    user_id = user.get("id")
    if not user_id:
        return {"success": False, "error": "No user ID in token"}
    await db.update_user_last_seen(user_id)
    return {"success": True}


@router.get("/agent/status")
async def agent_status(user: dict = Depends(get_current_user)):
    """Get all agents with online/offline status."""
    from config.settings import settings
    agents = await db.get_all_agents_status(
        timeout_seconds=settings.agent_timeout_seconds
    )
    return {"success": True, "data": agents}
