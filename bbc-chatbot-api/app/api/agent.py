"""Agent presence — heartbeat endpoint."""
import logging
from fastapi import APIRouter, Depends
from app.db import supabase as db
from app.security.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


async def _cleanup_stale_conversations() -> int:
    """Revert conversations from offline agents back to AI mode.
    Called on every heartbeat — each online agent helps clean up."""
    from config.settings import settings
    stale = await db.get_stale_agent_conversations(settings.agent_timeout_seconds)
    count = 0
    for conv in stale:
        await db.update_conversation(conv["id"], {
            "mode": "ai",
            "assigned_agent_id": None,
        })
        logger.info(f"[fallback] Conv {conv['id']}: agent offline → AI mode")
        count += 1
    return count


@router.post("/agent/heartbeat")
async def heartbeat(user: dict = Depends(get_current_user)):
    """Agent pings every 30s to signal online presence.
    Also cleans up conversations from offline agents."""
    user_id = user.get("id")
    if not user_id:
        return {"success": False, "error": "No user ID in token"}
    await db.update_user_last_seen(user_id)
    cleaned = await _cleanup_stale_conversations()
    return {"success": True, "cleaned": cleaned}


@router.get("/agent/status")
async def agent_status(user: dict = Depends(get_current_user)):
    """Get all agents with online/offline status."""
    from config.settings import settings
    agents = await db.get_all_agents_status(
        timeout_seconds=settings.agent_timeout_seconds
    )
    return {"success": True, "data": agents}
