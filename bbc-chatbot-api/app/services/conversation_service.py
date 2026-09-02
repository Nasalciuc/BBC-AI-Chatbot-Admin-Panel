"""Conversation service — business logic for conversations."""
import logging
from typing import Optional, Any
from app.db import supabase as db

logger = logging.getLogger(__name__)


async def get_or_create_conversation(
    conversation_id: Optional[str], tunnel: str, visitor: Any,
    visitor_id: Optional[str] = None,
    metadata: Optional[dict] = None,
):
    return await db.get_or_create_conversation(
        conversation_id, tunnel, visitor, visitor_id=visitor_id, metadata=metadata,
    )


async def add_message(conversation_id: str, role: str, content: str,
                      model_used: Optional[str] = None, cost: float = 0.0):
    row = await db.add_message(conversation_id, role, content, model_used, cost)
    # The ONE place a stored message reaches the live stream. #220 published
    # agent, system and AI rows from their own call sites and never the
    # visitor's — five save paths, none pushed — so an operator with a live
    # stream (polling off) stopped seeing what the client wrote. Publishing
    # here covers every path at once. AI rows are excluded: they already
    # arrive as stream_end, and a bare row landing first would flash the full
    # reply before the widget's typewriter finishes. Double publishes from
    # call sites that still push explicitly are harmless — both consumers
    # dedupe by id.
    if row and role in ("user", "agent", "system"):
        try:
            from app.realtime.manager import manager
            await manager.push(conversation_id, row)
        except Exception as e:  # noqa: silent — the message is SAVED; a failed fan-out must never fail the save
            logger.warning(f"[sse] push after add_message failed conv={conversation_id}: {e}")
    return row
