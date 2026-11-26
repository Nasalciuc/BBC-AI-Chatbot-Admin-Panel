"""Conversation service — business logic for conversations."""
import asyncio
import contextlib
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
    # nothing to tell). Nothing while the channel is off (asked once: a
    # message written down is always handed on, place() crosses it out if the
    # channel went off meanwhile).
    channel_on = messenger_channel.enabled()
    entry: Optional[str] = None
    if outbound and role == "agent" and channel_on:
        message_id = message_id or str(uuid.uuid4())
        entry = await messenger_channel.write_down(conversation_id, message_id)
    # A chat on Facebook Messenger: what is said to the person goes out there too (app.services.messenger_channel,
    # which never raises and counts what fails). `outbound=False`: a row that must not go out (what someone wrote
    # from the Page's inbox, a note for the agent). Nothing here while the channel is off. The row's place in the
    # chat's order is taken the moment it is stored (db.add_message calls back before it awaits anything else, the
    # activity clocks included), and a chat's rows are stored one at a time in this process (in_order): they go out
    # in the order they were stored, however long an insert's answer, or what follows it, takes.
    follows: list = []

    def hand_on(stored: dict) -> None:
        if not follows:  # once, whichever insert stored it
            follows.append(messenger_channel.place(conversation_id, role, stored, entry))

    unknown: list = []
    kw: dict = {"message_id": message_id} if message_id else {}
    if outbound and role in ("ai", "agent", "system") and channel_on:
        kw["on_stored"] = hand_on
        if entry is not None:
            kw["on_unknown"] = lambda: unknown.append(True)
    ordered = messenger_channel.in_order(conversation_id) if "on_stored" in kw else contextlib.nullcontext()
    async with ordered:
        try:
            row = await db.add_message(conversation_id, role, content, model_used, cost, **kw)
        except asyncio.CancelledError:
            if unknown and not follows:
                # Cancelled, its insert's answer an error (stored with its answer lost, or refused): never placed.
                # Marked unsure before the chat's next row may go, so the recovery minute looks its row up while
                # this process lives: stored, the agent is told it was not sent; not stored, crossed out. Cancelled
                # before its insert's end was known: left written down, for the next process's minute.
                await messenger_channel.unsure(entry, conversation_id, message_id)
            # Stored and placed while the cancel waited: what its place left to do (its entry crossed out, its agent
            # told) is done before the cancel goes on, in the chat's order still, never left unawaited.
            for follow in follows:
                if follow is not None:
                    await follow
            raise
        if not row and entry is not None:
            # Its insert may have landed with its answer lost: asked again under the same id, the row stored before
            # comes back (StoredBefore), or it is stored now. Still none: stored or not is not known, and the recovery
            # minute looks the row up (this process alive or not): stored, the agent is told it was not sent.
            row = await db.add_message(conversation_id, role, content, model_used, cost, **kw)
            if not row:
                await messenger_channel.unsure(entry, conversation_id, message_id)
    for follow in follows:
        if follow is not None:
            await follow
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
    return row
