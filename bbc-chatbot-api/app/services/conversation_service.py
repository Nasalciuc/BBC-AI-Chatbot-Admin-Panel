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
                      model_used: Optional[str] = None, cost: float = 0.0,
                      live_extra: Optional[dict] = None, *, outbound: bool = True):
    """Store a message and publish it. `live_extra` rides the published copy
    only — the operator's name and photo, which the messages table has no
    column for. This push is the first copy a client gets, and both consumers
    keep the first copy of an id: on 17 Nov 2026 the bare copy came first and
    the widget signed the operator's message "Consultant", with no photo."""
    row = await db.add_message(conversation_id, role, content, model_used, cost)
    # The ONE place a stored message reaches the live stream. #220 published
    # agent, system and AI rows from their own call sites and never the
    # visitor's — five save paths, none pushed — so an operator with a live
    # stream (polling off) stopped seeing what the client wrote. Publishing
    # here covers every path at once. AI rows are excluded: they already
    # arrive as stream_end, and a bare row landing first would flash the full
    # reply before the widget's typewriter finishes. Both consumers dedupe by
    # id and keep the FIRST copy, so a later push of the same row adds
    # nothing: what the client must see goes on this one (`live_extra`).
    if row and role in ("user", "agent", "system"):
        try:
            from app.realtime.manager import manager
            await manager.push(conversation_id, {**row, **live_extra} if live_extra else row)
        except Exception as e:  # noqa: silent — the message is SAVED; a failed fan-out must never fail the save
            logger.warning(f"[sse] push after add_message failed conv={conversation_id}: {e}")
    # A chat on Facebook Messenger: what is said to the person goes out there too (app.services.messenger_channel,
    # which never raises and counts what fails). `outbound=False`: a row that must not go out (what someone wrote
    # from the Page's inbox, a note for the agent). Nothing here while the channel is off.
    if row and outbound and role in ("ai", "agent", "system"):
        from app.services import messenger_channel

        await messenger_channel.outbound(conversation_id, role, row)
    return row
