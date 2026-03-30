"""Routing engine — casino-fair assignment to free operators.

Algorithm: Fisher-Yates shuffle (CSPRNG) + sort by chats_served_today.
Least-served-today agent wins. Equal counts: random tiebreak.
Daily counter resets lazily (date check, no cron needed).
"""
import secrets
import logging
from datetime import date
from config.settings import settings
from app.db import supabase as db

logger = logging.getLogger(__name__)


async def route_conversation(tunnel: str) -> dict:
    """Pick a FREE operator using casino-fair distribution.

    FREE = 0 active human conversations, online (heartbeat within timeout),
    tunnel match, below max_concurrent_chats.

    Returns: {"agent_id": uuid|None, "mode": "human"|"ai", "agent_name": str|None}
    """
    try:
        agents = await db.get_available_agents(
            tunnel=tunnel,
            timeout_seconds=settings.agent_timeout_seconds,
        )
        if not agents:
            logger.info(f"[routing] No agents online for tunnel={tunnel} → AI")
            return {"agent_id": None, "mode": "ai", "agent_name": None}

        # Filter: only FREE agents (0 active conversations — strict 1:1 rule)
        free_agents = []
        for agent in agents:
            count = await db.get_agent_active_count(agent["id"])
            if count < settings.max_concurrent_chats:
                free_agents.append(agent)

        if not free_agents:
            logger.info(f"[routing] All agents busy for tunnel={tunnel} → AI")
            return {"agent_id": None, "mode": "ai", "agent_name": None}

        # Lazy daily reset: stale date → treat served count as 0
        today = date.today().isoformat()
        for a in free_agents:
            if a.get("chats_served_date") != today:
                a["chats_served_today"] = 0

        # Casino step 1: Fisher-Yates shuffle with CSPRNG (random tiebreak)
        for i in range(len(free_agents) - 1, 0, -1):
            j = secrets.randbelow(i + 1)
            free_agents[i], free_agents[j] = free_agents[j], free_agents[i]

        # Casino step 2: stable sort by least served today
        free_agents.sort(key=lambda a: a.get("chats_served_today", 0))
        selected = free_agents[0]

        # Increment daily counter (1 DB call — uses data already in memory)
        await db.increment_chats_served(selected)

        agent_name = selected.get("name") or selected.get("email", "A specialist")
        logger.info(
            f"[routing] Assigned to {agent_name} "
            f"(served {selected.get('chats_served_today', 0)} today) "
            f"for tunnel={tunnel}"
        )
        return {"agent_id": selected["id"], "mode": "human", "agent_name": agent_name}

    except Exception as e:
        logger.error(f"[routing] Unexpected error: {e} → fallback AI")
        return {"agent_id": None, "mode": "ai", "agent_name": None}
