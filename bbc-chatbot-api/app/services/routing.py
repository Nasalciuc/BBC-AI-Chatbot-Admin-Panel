"""Routing engine — auto-assign new conversations to available agents."""
import logging
from config.settings import settings
from app.db import supabase as db

logger = logging.getLogger(__name__)


async def route_conversation(tunnel: str) -> dict:
    """Pick the best available agent for a new conversation.

    Strategy: least loaded agent (fewest active conversations) who is
    online and has matching tunnel_scope, below max_concurrent_chats.

    Returns: {"agent_id": uuid|None, "mode": "human"|"ai"}
    """
    agents = await db.get_available_agents(
        tunnel=tunnel,
        timeout_seconds=settings.agent_timeout_seconds,
    )
    if not agents:
        logger.info(f"[routing] No agents online for tunnel={tunnel} → AI mode")
        return {"agent_id": None, "mode": "ai"}

    # Pick agent with fewest active conversations, under capacity
    best_agent = None
    best_count = settings.max_concurrent_chats

    for agent in agents:
        count = await db.get_agent_active_count(agent["id"])
        if count < best_count:
            best_count = count
            best_agent = agent

    if best_agent:
        logger.info(
            f"[routing] Routed to {best_agent['name']} "
            f"({best_count} active) for tunnel={tunnel}"
        )
        return {"agent_id": best_agent["id"], "mode": "human"}

    logger.info(f"[routing] All agents at max capacity for tunnel={tunnel} → AI mode")
    return {"agent_id": None, "mode": "ai"}
