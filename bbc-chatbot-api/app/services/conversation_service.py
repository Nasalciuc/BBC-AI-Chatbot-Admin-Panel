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

    kw: dict = {"message_id": message_id} if message_id else {}
    placed = outbound and role in ("ai", "agent", "system") and channel_on
    if placed:
        kw["on_stored"] = hand_on

    async def store() -> Optional[dict]:
        stored = await db.add_message(conversation_id, role, content, model_used, cost, **kw)
        if not stored and entry is not None:
            # Its insert may have landed with its answer lost: asked again under the same id, the row stored before
            # comes back (StoredBefore), or it is stored now. Still none: stored or not is not known, and the recovery
            # minute looks the row up (this process alive or not): stored, the agent is told it was not sent.
            stored = await db.add_message(conversation_id, role, content, model_used, cost, **kw)
            if not stored:
                await messenger_channel.unsure(entry, conversation_id, message_id)
        return stored

    async def follow_on() -> None:
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
    async def publish(stored: Optional[dict]) -> None:
        if not stored or role not in ("user", "agent", "system"):
            return
        try:
            from app.realtime.manager import manager
            await manager.push(conversation_id, {**stored, **live_extra} if live_extra else stored)
        except Exception as e:  # noqa: silent — the message is SAVED; a failed fan-out must never fail the save
            logger.warning(f"[sse] push after add_message failed conv={conversation_id}: {e}")

    row: Optional[dict] = None
    if placed:
        # An agent's message, a "joined" line, a Messenger turn's own rows: stored, placed (or left unsure), what its
        # place left to do done, and published, as one piece of work that holds the chat's order to its end. A caller
        # cancelled meanwhile (a request, a turn, a turn's part stopped by its timeout) only stops waiting for it: its
        # insert may land all the same, and then takes its place before the chat's next row may go, the retry under
        # its id and the unsure mark included (never left "waiting"), and reaches the live stream once, before the
        # chat's next row does. A turn's row joins the turn's answer while the turn is on, and nothing once it stopped
        # with no answer (its event is answered again).
        async def saved_in_order() -> Optional[dict]:
            stored = await store()
            await follow_on()
            await publish(stored)
            return stored

        row = await messenger_channel.save_in_order(conversation_id, saved_in_order())
    else:
        row = await store()  # no place to take: only stored, and published
        await publish(row)
    return row
