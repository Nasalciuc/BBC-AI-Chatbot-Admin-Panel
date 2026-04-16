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



async def _assign_pending_conversations(
    user_id: str,
    tunnel_scope: str,
    agent_name: str = "A specialist",
) -> int:
    """If agent is idle (0 active convs), auto-assign oldest unassigned AI conv.
    Sends system messages + SSE push to notify widget of the handoff."""
    from config.settings import settings
    count = await db.get_agent_active_count(user_id)
    if count >= settings.max_concurrent_chats:
        return 0
    tunnels = ["sales", "support"] if tunnel_scope == "all" else [tunnel_scope]
    for t in tunnels:
        conv = await db.get_oldest_unassigned_conversation(t)
        if conv:
            await db.update_conversation(conv["id"], {
                "assigned_agent_id": user_id,
                "mode": "human",
            })

            # Notify widget via system messages + SSE push
            from app.services.conversation_service import add_message
            from app.realtime.manager import manager

            joined = settings.heartbeat_joined_template.format(agent_name=agent_name)
            welcome = (
                settings.heartbeat_welcome_sales
                if conv.get("tunnel") == "sales"
                else settings.heartbeat_welcome_support
            )

            row1 = await add_message(conv["id"], "system", joined)
            row2 = await add_message(conv["id"], "system", welcome)

            # Push to SSE stream — no-op if widget not currently connected
            if row1:
                await manager.push(conv["id"], row1)
            if row2:
                await manager.push(conv["id"], row2)

            logger.info(
                f"[heartbeat-assign] Conv {conv['id']} → {user_id} "
                f"({agent_name}), SSE pushed"
            )
            return 1
    return 0


@router.post("/agent/heartbeat")
async def heartbeat(user: dict = Depends(get_current_user)):
    """Agent pings every 30s to signal online presence.
    Also cleans up conversations from offline agents.
    Management roles (owner/admin/dev) update presence but do NOT auto-receive conversations."""
    user_id = user.get("id")
    if not user_id:
        return {"success": False, "error": "No user ID in token"}
    await db.update_user_last_seen(user_id)
    cleaned = await _cleanup_stale_conversations()
    role = user.get("role", "")
    assigned = 0
    if role not in db._MANAGEMENT_ROLES:
        agent_name = user.get("name") or user.get("email", "A specialist")
        assigned = await _assign_pending_conversations(
            user_id,
            user.get("tunnel_scope", "sales"),
            agent_name,
        )
    return {"success": True, "cleaned": cleaned, "assigned": assigned}


@router.get("/agent/status")
async def agent_status(user: dict = Depends(get_current_user)):
    """Get all agents with online/offline status."""
    from config.settings import settings
    agents = await db.get_all_agents_status(
        timeout_seconds=settings.agent_timeout_seconds
    )
    return {"success": True, "data": agents}
