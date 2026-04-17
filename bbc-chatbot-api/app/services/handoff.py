"""Handoff service — unified logic for human ↔ AI transitions.

H1: is_agent_effectively_offline / fall_back_to_ai
    Used when a conversation is in human mode but the assigned agent
    has gone silent (heartbeat timeout or agent_silent_timeout exceeded).
    Falls back to AI so the visitor isn't stuck.

H3: perform_handoff_to_agent
    Single function that sets mode='human', assigns the agent, and
    emits the 3 system messages (connecting + joined + welcome).
    Replaces all ad-hoc handoff code scattered across chat.py and agent.py.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from config.settings import settings
from app.db import supabase as db
from app.services.conversation_service import add_message
from app.realtime.manager import manager

logger = logging.getLogger(__name__)


# ── H1: Agent staleness detection + AI fallback ──────────────

async def is_agent_effectively_offline(conversation_id: str) -> bool:
    """Check if the agent assigned to this conversation is effectively offline.

    Returns True if:
      - No agent assigned, OR
      - Agent's last_seen_at is older than agent_timeout_seconds, OR
      - Agent hasn't sent a message in agent_silent_timeout_seconds
    """
    conv = await db.get_conversation_simple(conversation_id)
    if not conv:
        return False

    agent_id = conv.get("assigned_agent_id")
    if not agent_id:
        return True  # no agent at all — effectively offline

    # Check heartbeat freshness
    agent = await db.get_user_by_id(agent_id)
    if not agent:
        return True

    last_seen = agent.get("last_seen_at")
    if not last_seen:
        return True

    # Parse last_seen (ISO format from Supabase)
    try:
        if isinstance(last_seen, str):
            # Handle both Z suffix and +00:00
            last_seen_dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
        else:
            last_seen_dt = last_seen
    except (ValueError, TypeError):
        return True

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.agent_timeout_seconds)
    if last_seen_dt < cutoff:
        return True  # heartbeat expired

    # Check agent_silent_timeout: has the agent actually responded?
    last_agent_msg = await db.get_last_agent_message_time(conversation_id)
    if last_agent_msg is None:
        # Agent was assigned but never sent a message.
        # Check if the conversation was assigned more than silent_timeout ago.
        conv_updated = conv.get("updated_at")
        if conv_updated:
            try:
                updated_dt = datetime.fromisoformat(
                    conv_updated.replace("Z", "+00:00") if isinstance(conv_updated, str) else conv_updated
                )
                silent_cutoff = datetime.now(timezone.utc) - timedelta(
                    seconds=settings.agent_silent_timeout_seconds
                )
                if updated_dt < silent_cutoff:
                    return True
            except (ValueError, TypeError):
                pass
        return False

    return False


async def fall_back_to_ai(conversation_id: str) -> None:
    """Revert a human-mode conversation back to AI.
    Clears agent assignment, sets mode='ai', and emits a system message."""
    await db.update_conversation(conversation_id, {
        "mode": "ai",
        "assigned_agent_id": None,
    })
    msg = await add_message(
        conversation_id,
        "system",
        "Your specialist is no longer available. "
        "I'll continue assisting you — how can I help?",
    )
    if msg:
        await manager.push(conversation_id, msg)
    logger.info(f"[handoff] Conv {conversation_id}: agent offline → fell back to AI")


# ── H3: Unified handoff to agent ─────────────────────────────

async def perform_handoff_to_agent(
    conversation_id: str,
    agent_id: str,
    agent_name: str = "A specialist",
    tunnel: str = "sales",
    emit_messages: bool = True,
) -> dict:
    """Assign conversation to an agent and emit system messages.

    Returns dict with the 3 system message rows (or empty dict if
    emit_messages=False — used by claim/reassign which have their own UX).
    """
    await db.update_conversation(conversation_id, {
        "mode": "human",
        "assigned_agent_id": agent_id,
    })

    if not emit_messages:
        return {}

    connecting = settings.connecting_message
    joined = settings.joined_message_template.format(agent_name=agent_name)
    welcome = (
        settings.welcome_message_sales
        if tunnel == "sales"
        else settings.welcome_message_support
    )
    qr = (
        settings.quick_replies_sales
        if tunnel == "sales"
        else settings.quick_replies_support
    )

    row1 = await add_message(conversation_id, "system", connecting)
    row2 = await add_message(conversation_id, "system", joined)
    row3 = await add_message(conversation_id, "system", welcome)

    # Push to SSE — no-op if widget isn't connected
    for row in (row1, row2, row3):
        if row:
            await manager.push(conversation_id, row)

    return {
        "connecting": row1,
        "joined": row2,
        "welcome": row3,
        "quick_replies": qr,
    }
