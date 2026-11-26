"""Conversation service — business logic for conversations."""
import logging
import uuid
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
                      live_extra: Optional[dict] = None, *, outbound: bool = True,
                      message_id: Optional[str] = None):
    """Store a message and publish it. `live_extra` rides the published copy
    only — the operator's name and photo, which the messages table has no
    column for. This push is the first copy a client gets, and both consumers
    keep the first copy of an id: on 17 Nov 2026 the bare copy came first and
    the widget signed the operator's message "Consultant", with no photo.
    `message_id` (db.add_message): a row stored before under that id comes back
    as it was (a StoredBefore) and is published and handed on as a new one is;
    its consumers keep the first copy of an id."""
    from app.services import messenger_channel

    # An agent's message to a chat that may be on Messenger is written down
    # BEFORE it is stored, under the id its row gets: a stop at any point after
    # (a deploy, a cancelled request) leaves it to the next process's recovery
    # minute, which tells the agent it was not sent once it finds the row (none:
    # nothing to tell). Nothing while the channel is off.
    entry: Optional[str] = None
    if outbound and role == "agent" and messenger_channel.enabled():
        message_id = message_id or str(uuid.uuid4())
        entry = await messenger_channel.write_down(conversation_id, message_id)
    row = await db.add_message(conversation_id, role, content, model_used, cost,
                               **({"message_id": message_id} if message_id else {}))
    if not row:
        await messenger_channel.not_stored(entry)
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
        await messenger_channel.outbound(conversation_id, role, row, entry)
    return row
