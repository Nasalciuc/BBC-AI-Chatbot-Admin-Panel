"""Routing engine — casino-fair assignment to free operators.

Algorithm: Fisher-Yates shuffle (CSPRNG) + sort by chats_served_today.
Least-served-today agent wins. Equal counts: random tiebreak.
Daily counter resets lazily (date check, no cron needed).
"""
import secrets
import logging
from datetime import date
from typing import Optional
from config.settings import settings
from app.db import supabase as db

logger = logging.getLogger(__name__)


async def route_returning_visitor(
    tunnel: str,
    visitor_email: Optional[str],
    visitor_phone: Optional[str],
) -> Optional[dict]:
    """Try to route returning visitor to their last operator.
    Returns routing dict if same operator is available and free.
    Returns None to fall through to casino-fair routing."""
    last_agent_id = await db.get_last_agent_for_visitor(visitor_email, visitor_phone)
    if not last_agent_id:
        return None  # new visitor or no previous agent

    try:
        agents = await db.get_available_agents(
            tunnel=tunnel,
            timeout_seconds=settings.agent_timeout_seconds,
        )
        available_map = {a["id"]: a for a in agents}

        if last_agent_id not in available_map:
            logger.info(
                f"[routing] Returning visitor: "
                f"last agent {last_agent_id[:8]}... offline → casino"
            )
            return None

        count = await db.get_agent_active_count(last_agent_id)
        if count >= settings.max_concurrent_chats:
            logger.info(
                f"[routing] Returning visitor: "
                f"last agent {last_agent_id[:8]}... busy ({count} chats) → casino"
            )
            return None

        agent = available_map[last_agent_id]
        await db.increment_chats_served(agent)
        agent_name = agent.get("name") or agent.get("email", "A specialist")
        logger.info(
            f"[routing] Returning visitor → same agent {agent_name} "
            f"(tunnel={tunnel})"
        )
        return {
            "agent_id": last_agent_id,
            "mode": "human",
            "agent_name": agent_name,
        }
    except Exception as e:
        logger.error(f"route_returning_visitor error: {e} → casino fallback")
        return None


async def route_conversation(tunnel: str, visitor=None) -> dict:
    """Pick a FREE operator using casino-fair distribution.

    If visitor has email/phone and was served before, tries to route back
    to the same operator first (returning visitor affinity).
    Falls back to casino-fair if same operator is unavailable.

    FREE = 0 active human conversations, online (heartbeat within timeout),
    tunnel match, below max_concurrent_chats.

    Returns: {"agent_id": uuid|None, "mode": "human"|"ai", "agent_name": str|None}
    """
    try:
        # Step 0: Returning visitor — try same operator first
        visitor_email = getattr(visitor, 'email', None) if visitor else None
        visitor_phone = getattr(visitor, 'phone', None) if visitor else None
        if visitor_email or visitor_phone:
            result = await route_returning_visitor(
                tunnel=tunnel,
                visitor_email=visitor_email,
                visitor_phone=visitor_phone,
            )
            if result:
                return result

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
