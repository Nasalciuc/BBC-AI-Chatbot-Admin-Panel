"""Skye and the agents on Facebook Messenger, the API's side (contracts/messenger.md).

The gateway (bbc-messenger, Go) checks Meta's signature, keeps each person's events in order and hands each one
here, signed with MESSENGER_GATEWAY_SECRET. A person's Messenger thread is an ordinary chat: visitor id
`msgr-<hash of Page and PSID>`, metadata.chat_channel "messenger", metadata.messenger {page_id, psid}, tunnel
sales, the Page's site (bbc or bct). Their message takes the same turn a widget message takes (app.api.chat.chat):
the blocklist, the chat's doors, an agent who holds the chat, the card's buttons, Skye. What that turn stores for
the person goes back in the answer to the gateway, which sends it; an agent's message from the panel (and anything
else stored outside a turn) goes out through the gateway's POST /v1/send.

An event is kept in Redis (the inbox) from the moment it is taken until it is answered: one whose turn failed is
answered before the person's next event, or a few minutes later by run_messenger_recover (the scheduler's minute),
whatever became of the gateway's own tries, and its replies go out through /v1/send, once: a send the API makes is
written down before it starts, and one a stopped process left is never made again.

MESSENGER_CHANNEL (app.flags, off at merge), with MESSENGER_GATEWAY_URL and MESSENGER_GATEWAY_SECRET set: off,
the inbound route answers 503 and nothing goes out.

The owner's defaults until they decide otherwise (25 Nov 2026): Skye answers at any hour and asks for a contact as
on the site; a name only from what the person writes (Meta's profile name is never read); an agent's message after
Meta's 24 hours is not sent, and the agent is told to reach the person by SMS or email. Nothing here logs what a
person wrote, their PSID, a name, an e-mail or a phone.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import hmac
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Coroutine, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from app.flags import flag
from app.infra.redis_client import get_redis, redis_failed
from app.services.conversation_lock import RELEASE_LUA, RENEW_LUA

logger = logging.getLogger(__name__)

SITES = ("bbc", "bct")
# A thread goes on in the chat it had while it was written to this lately; older, a new chat starts.
THREAD_DAYS = 7
# Someone wrote from the Page's inbox this lately: Skye leaves the thread to them (their message is kept).
INBOX_QUIET = timedelta(minutes=30)
# "Connecting you with a specialist" is said once in this long, not after each message to an agent.
QUEUED_EVERY_S = 1800
# An event being answered, then the replies given for it: what a delivery made again by the gateway gets.
PENDING_S = 120
ANSWERED_S = 86400
# How many times a claim found let go of between SET NX and GET is taken again before the event reads as being answered.
CLAIM_TRIES = 3
TARGET_S = 30 * 86400
# The gateway waits for Meta, with its retries, within its own budget (bbc-messenger SendBudget, 60 s): never longer.
GATEWAY_TIMEOUT_S = 80.0
SIGNED_SKEW_S = 300
_SIGNATURE = re.compile(r"sha256=[0-9a-f]{64}")

# The events taken and not answered yet: kept until answered (a sorted set by when they were taken, each event's
# JSON beside it), so one whose turn failed is answered later (run_messenger_recover).
INBOX = "msgr:inbox"
INBOX_KEEP_S = 2 * 86400
# Longer than the gateway's own tries of an event the API keeps (bbc-messenger forward: Retries, each call up to
# APITimeout): the recovery never races a delivery still on its way.
RECOVER_AFTER_S = 600
# One try a minute: an event still failing after an hour of them is given up (counted, logged without its words).
RECOVER_TRIES = 60
RECOVER_BATCH = 20
# Held by one pass at a time under its own token, renewed every RECOVER_LOCK_S / 3 while the pass runs and only while
# it is still that pass's (compare and expire), and let go the same way (compare and delete): a turn or a send that
# takes long never lets another pass in beside it, and a pass never frees another's.
RECOVER_LOCK = "msgr:recover:lock"
RECOVER_LOCK_S = 300
# A pass starts nothing new past RECOVER_BUDGET_S, nor once its lock is found lost; the agents' notes get
# TELL_BUDGET_S of it (each one cut there: its note's id makes saving it again harmless) and TELL_BATCH of them at
# most, the outbox gone through in parts (OUTBOX_CURSOR: the part the passes are on; OUTBOX_SEEN: the deliveries
# looked at in it, by their ids, until the part is gone through), so the events always get their part and every
# delivery its turn.
RECOVER_BUDGET_S = 120.0
TELL_BUDGET_S = 30.0
TELL_BATCH = 20
OUTBOX_CURSOR = "msgr:outbox:cursor"
OUTBOX_SEEN = "msgr:outbox:seen"
# A send of an event's replies by the API (the recovery's, or an earlier event's answered before a later one) is
# written down before it starts (when, and by which process: taken by one sender only) and once it is over ("done"):
# from then on they may be with the person, and they are never sent again. One begun more than SENDING_S ago and not
# over is taken for a process that stopped in it (a send waits for the gateway GATEWAY_TIMEOUT_S at most), unless
# that process is still alive (it knows whether its send is over).
SENDING_S = GATEWAY_TIMEOUT_S + 10
# The ids of the rows an event stores are made from the event's key and the row's part (uuid5 in this namespace):
# a delivery made again, or the recovery, stores each once, whatever became of an earlier insert.
ROW_IDS = uuid.UUID("6f1d3c52-8a4e-4b7f-9d2a-5c0e7b13a948")
# What a shutdown gives the deliveries still on their way (app.main), then the notes of those it stopped.
DRAIN_S = 8.0
NOTES_S = 2.0
# Agents' messages on their way to Messenger, written down before they are handed on (whatever the chat: a site
# chat's is crossed out once looked up) until they went out or their agent was told why not (a hash: a field a
# delivery, its JSON). A process that stops before either leaves them there, and the next one's recovery minute
# tells each agent what the shutdown would have; a note that could not be saved stays there too (untold), and the
# minute saves it. Given up after OUTBOX_KEEP_S (counted).
OUTBOX = "msgr:outbox"
OUTBOX_KEEP_S = 2 * 86400
# This process, as the deliveries it writes down name it, and how long it is taken for alive after it last said so
# (each recovery minute, each delivery written down): a process gone that long left its deliveries unfinished.
BOOT = uuid.uuid4().hex
ALIVE_S = 300

# What a message with no text but an attachment is kept as, and what Skye says to it.
_ATTACHMENTS = {"image": "[photo]", "audio": "[voice message]", "video": "[video]", "file": "[file]",
                "location": "[location]"}
ATTACHMENT_REPLY = "Thanks! I can only read text here. Could you type your question?"

# What the agent sees in the chat when their message did not reach the person on Messenger (never sent there).
NOT_SENT = {
    "window_closed": ("Not sent on Messenger: the client last wrote over 24 hours ago, and Messenger allows no "
                      "reply until they write again. Reach them by SMS or email."),
    "unavailable": ("Not sent on Messenger: the client can't be reached there (they may have blocked the Page). "
                    "Reach them by SMS or email."),
    "thread_owned": "Not sent on Messenger: the chat is being answered from the Page's inbox now.",
    "unknown": ("Maybe not sent on Messenger: Messenger did not confirm it. If the client doesn't reply, send it "
                "again."),
}
NOT_SENT_OTHER = "Not sent on Messenger. Try again in a minute; if it keeps failing, tell an admin."
NOT_SENT_RESTART = "Not sent on Messenger: the server was restarting. Send it again in a minute."
PARTLY_SENT = ("Only part of this message reached the client on Messenger: the first {sent} of {parts} pieces. Send "
               "them the rest, not the whole message again.")
PARTLY_UNCONFIRMED = ("Part of this message reached the client on Messenger: the first {sent} of {parts} pieces, and "
                      "piece {next} may have too (Messenger did not confirm it). Ask the client what they got before "
                      "you send the rest, never the whole message again.")

MESSENGER_HEALTH: dict[str, int] = {
    "events": 0,             # events the gateway handed over, signed
    "turns": 0,              # messages answered by the chat's turn
    "replies": 0,            # replies given back to the gateway
    "duplicates": 0,         # deliveries made again, answered with the replies kept
    "still_answering": 0,    # deliveries made again while the first was being answered (409, the gateway waits)
    "turns_failed": 0,       # events that failed: the gateway delivers them again, the recovery answers them later
    "turns_refused": 0,      # turns the chat refused (blocklist, a limit): kept, no reply
    "kept_unanswered": 0,    # messages kept without a reply: another app owns the thread, or the Page's inbox spoke
    "inbox_replies": 0,      # messages written from the Page's inbox, kept in the chat
    "attachments": 0,        # messages with no text, answered with ATTACHMENT_REPLY
    "referrals": 0,          # where a person came from (an m.me link, an ad), kept
    "no_dedupe": 0,          # events answered with Redis down: a delivery made again could be answered twice
    "saved_once": 0,         # rows a delivery made again found saved already: not saved twice
    "not_kept": 0,           # events not put in the inbox (Redis down): one that fails is not answered later
    "recovered": 0,          # events answered by the recovery, their replies sent
    "recover_waiting": 0,    # recovery tries that failed again: the next minute tries again
    "recover_given_up": 0,   # events still failing after RECOVER_TRIES, or unreadable: given up
    "answered_first": 0,     # a person's earlier events still in the inbox, answered and sent before their later one
    "recover_unsure": 0,     # events whose replies a process was sending when it stopped: maybe out, never sent again
    "claim_lost": 0,         # turns that ended after another delivery or pass took their event (their claim ran out):
                             # their answer never written over the other's, and a recovery's never sent beside it
    "answer_unkept": 0,      # answers Redis did not take as their turn ended: held by their process and written at
                             # its next minute, never a second turn; the API sends none until it is written
    "sent_late": 0,          # replies a process knew never went out, of an event another process let go of meanwhile
                             # (taking it, cut off from Redis, for one stopped): sent by it once Redis answered
    "late_dropped": 0,       # such replies never sent: a later event of the person's was taken meanwhile, and its
                             # replies may be out already (never sent after them)
    "answer_dropped": 0,     # answers still not taken by Redis when their process stopped: their event may be answered
                             # again once its claim runs out
    "bad_signature": 0,      # calls refused: not the gateway's
    "sent": 0,               # messages sent out of a turn (an agent's, mostly)
    "send_failed": 0,        # of those, refused or lost; by why below
    "window_closed": 0,
    "unavailable": 0,
    "thread_owned": 0,
    "unconfirmed": 0,        # Meta did not confirm it: maybe with the person
    "partly_sent": 0,        # a long one cut short: its first pieces went out
    "gateway_down": 0,       # the gateway did not answer
    "no_target": 0,          # a Messenger chat whose Page and PSID could not be read: nothing sent
    "target_unread": 0,      # an agent's message whose chat could not be read: kept for the minute, never dropped
    "off_skipped": 0,        # rows that waited for the chat's previous one while the channel was turned off: not sent
    "cut_at_shutdown": 0,    # rows a shutdown did not wait for: their agents are told
    "outbox_unsaved": 0,     # agents' messages not written down (Redis down): one a shutdown cuts may go untold
    "told_later": 0,         # agents told by the recovery minute: a stopped process's message, or a note not saved
    "outbox_given_up": 0,    # notes still not saved after OUTBOX_KEEP_S, or unreadable: given up
}


def enabled() -> bool:
    from config.settings import settings

    return bool(flag("MESSENGER_CHANNEL", False)) and bool(settings.messenger_gateway_url) \
        and bool(settings.messenger_gateway_secret)


def _secret() -> bytes:
    from config.settings import settings

    return (settings.messenger_gateway_secret or "").encode()


def sign(secret: bytes, unix: int, body: bytes) -> str:
    """Every call between the gateway and the API, both ways: "sha256=" + hex(HMAC-SHA256(the shared secret, unix
    seconds + "." + the raw body)) (bbc-messenger internal/gateway.Sign)."""
    mac = hmac.new(secret, f"{unix}.".encode() + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def valid_signed(unix_header: Optional[str], signature: Optional[str], body: bytes, *,
                 now: Optional[float] = None) -> bool:
    """A call the gateway signed, its time near ours. Only what the gateway sends is read (unix seconds, a
    lower-case hex signature): anything else is refused here, never raised from the arithmetic or the comparison."""
    secret = _secret()
    if not secret or not unix_header or not signature:
        return False
    unix_header, signature = unix_header.strip(), signature.strip()
    if not (unix_header.isascii() and unix_header.isdigit() and len(unix_header) <= 12):
        return False
    if not _SIGNATURE.fullmatch(signature):
        return False
    unix = int(unix_header)
    if abs((now if now is not None else time.time()) - unix) > SIGNED_SKEW_S:
        return False
    return hmac.compare_digest(sign(secret, unix, body).encode(), signature.encode())


def visitor_id_for(page_id: str, psid: str) -> str:
    """One person on one Page: the chat's visitor id (the PSID is the Page's id for them, never shown or logged)."""
    return "msgr-" + hashlib.sha256(f"{page_id}:{psid}".encode()).hexdigest()[:40]


# ── the event ─────────────────────────────────────────────────────


class Referral(BaseModel):
    ref: Optional[str] = Field(default=None, max_length=250)
    source: Optional[str] = Field(default=None, max_length=40)
    type: Optional[str] = Field(default=None, max_length=40)
    ad_id: Optional[str] = Field(default=None, max_length=64)


class InboundEvent(BaseModel):
    """One event as the gateway hands it over (bbc-messenger internal/meta.Inbound, plus the Page's site)."""
    kind: Literal["message", "postback", "referral", "echo"]
    page_id: str = Field(..., pattern=r"^[A-Za-z0-9_-]{1,64}$")
    psid: str = Field(..., pattern=r"^[A-Za-z0-9_-]{1,64}$")
    site: Literal["bbc", "bct"]
    mid: str = Field(default="", max_length=256)
    timestamp: int = 0
    text: str = Field(default="", max_length=20000)
    quick_reply: str = Field(default="", max_length=1000)
    postback: str = Field(default="", max_length=1000)
    postback_title: str = Field(default="", max_length=200)
    referral: Optional[Referral] = None
    attachments: list[str] = Field(default_factory=list, max_length=20)
    standby: bool = False

    def key(self) -> str:
        """What makes a delivery made again the same event (the gateway's Key)."""
        return self.mid or f"{self.kind}:{self.page_id}:{self.psid}:{self.timestamp}"


# ── what a turn says to the person ───────────────────────────────


@dataclass
class _Turn:
    """The rows a turn stores for the person, given back to the gateway in its answer instead of sent one by one.
    Shut once the answer is made: a row stored later (a task the turn started) goes out on its own."""
    conversation_id: str
    rows: list[dict] = field(default_factory=list)
    open: bool = True


_TURN: contextvars.ContextVar[Optional[_Turn]] = contextvars.ContextVar("messenger_turn", default=None)


@contextlib.contextmanager
def _collecting(conversation_id: str):
    turn = _Turn(conversation_id)
    token = _TURN.set(turn)
    try:
        yield turn
    finally:
        turn.open = False
        _TURN.reset(token)


# One delivery after another for each chat in this process: an agent's "joined" line and their message never
# cross on the way out.
_LAST: dict[str, asyncio.Task] = {}
# A chat's rows for the person stored one at a time in this process (in_order), from before the insert to their
# place in the chat's order: they go out in the order the database stored them, whatever an insert's answer took to
# come back. [the lock, how many hold or wait for it]
_ORDER: dict[str, list] = {}


@contextlib.asynccontextmanager
async def in_order(conversation_id: str):
    """One of a chat's rows stored and given its place (conversation_service.add_message) while no other of its rows
    is: the order of the places is the order of the stores."""
    slot = _ORDER.setdefault(conversation_id, [asyncio.Lock(), 0])
    slot[1] += 1
    try:
        async with slot[0]:
            yield
    finally:
        slot[1] -= 1
        if not slot[1] and _ORDER.get(conversation_id) is slot:
            del _ORDER[conversation_id]
# Set by a shutdown (drain_all): no delivery starts after it.
_CLOSING = False


def place(conversation_id: str, role: str, row: dict,
          entry: Optional[str] = None) -> Optional[Coroutine[Any, Any, None]]:
    """A row stored for the person in some chat, given its place without awaiting anything (db.add_message calls it
    back the moment the row is stored, conversation_service.add_message): a chat's rows go out in the order they were
    stored. In a Messenger turn it joins the turn's answer; outside one, a Messenger chat's row goes out through the
    gateway, after the chat's previous one. An agent's comes written down already (write_down, before it was stored:
    `entry`, None when Redis could not take it), whatever the chat (where it goes is looked up after), until it went
    out or they were told: a stop at any point leaves it for the next process's minute. What is left to do and must
    be awaited (an entry crossed out, an agent told during a shutdown) comes back, to await (never raising); None when
    there is nothing. Never raises: the row is saved whatever happens here."""
    try:
        turn = _TURN.get()
        if turn is not None and turn.open and turn.conversation_id == conversation_id:
            turn.rows.append(row)
            return _quietly(_cross_out(entry), conversation_id) if entry else None  # the turn's answer carries it
        if not enabled():
            return _quietly(_cross_out(entry), conversation_id) if entry else None
        if _CLOSING:
            return _quietly(_not_started(conversation_id, role, entry), conversation_id)
        prev = _LAST.get(conversation_id)
        task = asyncio.create_task(_deliver_after(prev, conversation_id, role, row, entry))
        _LAST[conversation_id] = task
        task.add_done_callback(lambda t, cid=conversation_id: _LAST.pop(cid, None) if _LAST.get(cid) is t else None)
    except Exception as e:
        MESSENGER_HEALTH["send_failed"] += 1
        logger.error(f"[messenger] conv {conversation_id}: a row not handed on: {type(e).__name__}: {e}")
    return None


async def _quietly(work: Coroutine[Any, Any, None], conversation_id: str) -> None:
    """What place() left to do, awaited by its caller: never raises."""
    try:
        await work
    except Exception as e:
        MESSENGER_HEALTH["send_failed"] += 1
        logger.error(f"[messenger] conv {conversation_id}: a row's hand-off not finished: {type(e).__name__}: {e}")


async def outbound(conversation_id: str, role: str, row: dict, entry: Optional[str] = None) -> None:
    """place(), and what it leaves, awaited."""
    follow = place(conversation_id, role, row, entry)
    if follow is not None:
        await follow


async def _not_started(conversation_id: str, role: str, entry: Optional[str]) -> None:
    """A row the process going did not start (nothing starts once a shutdown began): on a Messenger chat the agent
    is told to send it again; a site chat's written-down delivery is crossed out. A chat that could not be read
    leaves it written down, for the next process's minute to look up and tell."""
    try:
        target = await _target(conversation_id, role)
    except TargetUnread as e:
        logger.warning(f"[messenger] conv {conversation_id}: a {role} message not started at shutdown, its chat not "
                       f"read: left written down for the next process ({type(e).__name__})")
        return
    if target:
        MESSENGER_HEALTH["cut_at_shutdown"] += 1
        if role == "agent":
            await _tell(entry or await write_down(conversation_id), conversation_id, NOT_SENT_RESTART)
        return
    await _cross_out(entry)


async def drain(conversation_id: str) -> None:
    """Wait for a chat's deliveries (tests)."""
    task = _LAST.get(conversation_id)
    if task is not None:
        await asyncio.gather(task, return_exceptions=True)


async def drain_all(budget_s: float = DRAIN_S) -> dict:
    """A shutdown (app.main): no delivery starts from now on, the ones on their way get budget_s to go out, and those
    still waiting then are stopped; each agent whose message did not go out is told so in the chat (not sent, or
    maybe not, when it was with the gateway already), in NOTES_S. A note not saved by then is the next process's to
    write: its delivery is still written down (OUTBOX). {"left": how many chats had one stopped}."""
    global _CLOSING
    _CLOSING = True
    tasks = [t for t in _LAST.values() if not t.done()]
    if not tasks:
        return {"left": 0}
    _, pending = await asyncio.wait(tasks, timeout=budget_s)
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.wait(pending, timeout=NOTES_S)
        logger.warning(f"[messenger] shutdown: {len(pending)} chat(s) had a message not sent on Messenger")
    return {"left": len(pending)}


async def _deliver_after(prev: Optional[asyncio.Task], conversation_id: str, role: str, row: dict,
                         entry: Optional[str] = None) -> None:
    """A row on its way out, a link in its chat's chain (_LAST): it ends only after the chat's previous one, whatever
    came of it (nothing to send, its chat not read, a break), so the chat's next row, which waits for this one only,
    never goes before the previous one. A shutdown that stops it waits for nothing (no delivery starts after it)."""
    await _deliver_one(prev, conversation_id, role, row, entry)
    if prev is not None:
        await asyncio.gather(prev, return_exceptions=True)


async def _deliver_one(prev: Optional[asyncio.Task], conversation_id: str, role: str, row: dict,
                       entry: Optional[str] = None) -> None:
    # Where it goes (once known: a site chat's row goes nowhere), for an agent's its entry in the outbox (written down
    # before this started), and how the send ended once it did ("note": "" sent, else what the agent is told): what a
    # stop tells the agent depends on them.
    state: dict[str, Any] = {"sending": False, "known": False, "target": None, "entry": entry, "note": None}
    try:
        try:
            state["target"] = await _target(conversation_id, role)
        except TargetUnread as e:
            # Where it goes is not known now: never crossed out on a guess. Kept for the recovery minute (unsure),
            # which looks the chat up again, this process alive or not: on Messenger, its agent is told it was not
            # sent; a site chat's is crossed out without a word.
            MESSENGER_HEALTH["send_failed"] += 1
            logger.warning(f"[messenger] conv {conversation_id}: a {role} message not sent, its chat not read: kept "
                           f"for the recovery minute ({type(e).__name__})")
            await _mark_entry(entry, conversation_id, "unsure", row=str(row.get("id") or "") or None)
            return
        state["known"] = True
        if not state["target"]:
            await _cross_out(entry)
            return
        if prev is not None:
            await asyncio.gather(prev, return_exceptions=True)
        if not enabled():
            # Turned off while this one waited for the chat's previous row: nothing goes out, as for a row stored
            # with the channel off.
            MESSENGER_HEALTH["off_skipped"] += 1
            await _cross_out(state["entry"])
            return
        await _deliver(conversation_id, role, row, state)
    except asyncio.CancelledError:
        # A shutdown that could not wait for it (drain_all): on a Messenger chat its agent is told, as for a send
        # that failed (maybe sent, when it was with the gateway already). Stopped before the note is saved, it stays
        # written down for the next process; a site chat's is crossed out. A send whose end was known before the
        # stop says that end, nothing else: sent, nothing to tell; refused, its own note.
        if state["note"] is not None:
            if state["note"] and role == "agent":
                await _tell(state["entry"], conversation_id, state["note"])
            elif not state["note"]:
                await _cross_out(state["entry"])
            raise
        unread = False
        if not state["known"]:
            try:
                state["target"] = await _target(conversation_id, role)
            except TargetUnread as e:
                unread = True
                logger.warning(f"[messenger] conv {conversation_id}: a {role} message stopped before where it goes "
                               f"was read, and it could not be read now: left written down for the next process "
                               f"({type(e).__name__})")
        if unread:
            raise
        if state["sending"] or state["target"]:
            MESSENGER_HEALTH["cut_at_shutdown"] += 1
            if role == "agent":
                await _tell(state["entry"], conversation_id,
                            NOT_SENT["unknown"] if state["sending"] else NOT_SENT_RESTART)
        else:
            await _cross_out(state["entry"])
        raise
    except Exception as e:
        logger.error(f"[messenger] conv {conversation_id}: delivery broke: {type(e).__name__}: {e}")
        if state["note"] == "":
            return  # sent before it broke: nothing to tell
        MESSENGER_HEALTH["send_failed"] += 1
        if role == "agent" and state["target"]:
            note = state["note"] or (NOT_SENT["unknown"] if state["sending"] else NOT_SENT_OTHER)
            await _tell(state["entry"], conversation_id, note)


async def _deliver(conversation_id: str, role: str, row: dict, state: dict) -> None:
    text = str(row.get("content") or "").strip()
    entry, target = state["entry"], state["target"]
    if not text:
        await _cross_out(entry)
        return
    if entry is not None and not await _mark_entry(entry, conversation_id, "sending"):
        # Written down, its going to the gateway must be too, or a stop after the call would leave it "not sent"
        # when it may be with the client: never sent then, and the agent is told to send it again.
        MESSENGER_HEALTH["send_failed"] += 1
        logger.warning(f"[messenger] conv {conversation_id}: a {role} message not sent: its send not written down")
        state["note"] = NOT_SENT_OTHER
        await _tell(entry, conversation_id, NOT_SENT_OTHER)
        return
    state["sending"] = True
    result = await send(target["page_id"], target["psid"], [{"text": text}])
    state["sending"] = False
    if result.get("sent"):
        MESSENGER_HEALTH["sent"] += 1
        state["note"] = ""  # its end known before anything else is awaited: a stop now tells nothing
        await _cross_out(entry)
        return
    MESSENGER_HEALTH["send_failed"] += 1
    kind = str((result.get("failed") or {}).get("kind") or "")
    parts_sent = _count(result.get("parts_sent"))
    parts = max(_count(result.get("parts")), parts_sent)
    if kind in ("window_closed", "unavailable", "thread_owned"):
        MESSENGER_HEALTH[kind] += 1
    elif kind == "unknown":
        MESSENGER_HEALTH["unconfirmed"] += 1
    if parts_sent:
        MESSENGER_HEALTH["partly_sent"] += 1
    state["note"] = _not_sent_note(kind, parts_sent, parts)
    logger.warning(f"[messenger] conv {conversation_id}: a {role} message not sent on Messenger "
                   f"({kind or 'no answer'}, {parts_sent} of {parts} pieces out)")
    if role == "agent":
        # The agent sees it in the chat, and only there: the note never goes out.
        await _tell(entry, conversation_id, state["note"])


def _not_sent_note(kind: str, parts_sent: int, parts: int) -> str:
    """What the agent is told about their message: part of it out, they send the rest, never the whole message
    again; a piece Meta did not confirm may be with the client, so they ask before sending it again."""
    if parts_sent and kind == "unknown":
        return PARTLY_UNCONFIRMED.format(sent=parts_sent, parts=parts, next=parts_sent + 1)
    if parts_sent:
        return PARTLY_SENT.format(sent=parts_sent, parts=parts)
    return NOT_SENT.get(kind, NOT_SENT_OTHER)


def _count(value: Any) -> int:
    """A count from the gateway's answer (0 when there is none, or it is not one)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


async def _tell_agent(conversation_id: str, note: str, note_id: Optional[str] = None) -> bool:
    """A line in the chat for the agent alone: stored, never sent (add_message never raises). Whether it was
    stored (a note with an id stored before counts: it is there)."""
    from app.services.conversation_service import add_message

    return bool(await add_message(conversation_id, "system", note, model_used="template", outbound=False,
                                  message_id=note_id))


def _note_id(entry: str) -> str:
    """The id of the note about a written-down delivery: one note whoever writes it (this process, the next one's
    minute)."""
    return str(uuid.uuid5(ROW_IDS, f"outbox:{entry}"))


async def _tell(entry: Optional[str], conversation_id: str, note: str) -> None:
    """The agent told about their message, once: the note's id is the delivery's. Saved, the delivery is crossed
    out; not saved, it stays written down as untold and the recovery minute saves the note."""
    if await _tell_agent(conversation_id, note, _note_id(entry) if entry else None):
        await _cross_out(entry)
    else:
        logger.error(f"[messenger] conv {conversation_id}: the agent's note not saved"
                     f"{' (left for the recovery minute)' if entry else ''}")
        await _mark_entry(entry, conversation_id, "untold", note)


async def write_down(conversation_id: str, row_id: Optional[str] = None) -> Optional[str]:
    """An agent's message on its way, written down until it went out or its agent was told: its id. `row_id`: the
    row it is written down for, before that row is stored (conversation_service.add_message): the recovery minute
    tells the agent only once it finds the row stored (a stop before the insert, or an insert that failed, leaves
    nothing to tell). The id comes back too when the write's answer was lost: it may be there, so it is taken as
    there (marked "sending" before the message goes, crossed out after). None only when Redis was not there to take
    it (counted: a shutdown that cuts it then may leave its agent untold)."""
    r = get_redis()
    if r is None:
        MESSENGER_HEALTH["outbox_unsaved"] += 1
        return None
    entry = uuid.uuid4().hex
    data: dict[str, Any] = {"cid": conversation_id, "boot": BOOT, "state": "waiting", "at": time.time()}
    if row_id:
        data["row"] = row_id
    try:
        await r.set(f"msgr:alive:{BOOT}", "1", ex=ALIVE_S)
        await r.hset(OUTBOX, entry, json.dumps(data))
    except Exception as e:
        redis_failed("messenger outbox", e)
        MESSENGER_HEALTH["outbox_unsaved"] += 1
        logger.warning(f"[messenger] conv {conversation_id}: an agent's message maybe not written down (taken as "
                       f"written): {type(e).__name__}: {e}")
    return entry


async def unsure(entry: Optional[str], conversation_id: str, row_id: str) -> None:
    """An agent's message written down (write_down) whose row may be stored or not (its insert failed, and so did
    the one made again under its id): nothing was handed on. Left to the recovery minute, which looks the row up
    even while this process lives: stored, its agent is told it was not sent; not stored, it is crossed out without
    a word. Not written so (Redis failing), it stays "waiting" with its row's id, for the next process's minute."""
    if entry is not None and not await _mark_entry(entry, conversation_id, "unsure", row=row_id):
        logger.warning(f"[messenger] conv {conversation_id}: an agent's message whose save is not known left as written "
                       f"down before (the next process's minute looks its row up)")


# A written-down delivery's own state is written through a Redis cooldown (another call's failure, process wide):
# skipped, a message may go out with its entry still saying "waiting", or stay written down once it went out.


async def _mark_entry(entry: Optional[str], conversation_id: str, state: str, note: Optional[str] = None,
                      row: Optional[str] = None) -> bool:
    """Where a written-down delivery is: "sending" (with the gateway: maybe out from now on), "untold" (its note,
    kept here, not saved: the recovery minute saves it), "unsure" (its row stored or not, not known: the minute looks
    it up). True once written; False when it could not be (no entry, Redis gone or failing: logged)."""
    r = get_redis(even_cooling_down=True) if entry is not None else None
    if r is None:
        return False
    data: dict[str, Any] = {"cid": conversation_id, "boot": BOOT, "state": state, "at": time.time()}
    if note:
        data["note"] = note
    if row:
        data["row"] = row
    try:
        await r.hset(OUTBOX, entry, json.dumps(data))
        return True
    except Exception as e:
        redis_failed("messenger outbox", e)
        logger.warning(f"[messenger] conv {conversation_id}: a delivery's state not written down ({state}): "
                       f"{type(e).__name__}: {e}")
        return False


async def _cross_out(entry: Optional[str]) -> None:
    """A written-down delivery done with: it went out, or its agent was told."""
    r = get_redis(even_cooling_down=True) if entry is not None else None
    if r is None:
        return
    try:
        await r.hdel(OUTBOX, entry)
    except Exception as e:
        redis_failed("messenger outbox", e)
        logger.warning(f"[messenger] a delivery done with not crossed out (a stopped process's note may follow): "
                       f"{type(e).__name__}: {e}")


async def _target(conversation_id: str, role: str) -> Optional[dict]:
    """The Page and PSID a chat answers to: kept in Redis by each event; an agent's message to a chat Redis no
    longer knows reads the chat's metadata (rows of the bot and the system stay quiet then: they come from a turn).
    None: the chat is not on Messenger, or there is no such chat. The chat not read: TargetUnread (counted), never a
    None (rule 7)."""
    r = get_redis()
    if r is not None:
        try:
            raw = await r.get(f"msgr:conv:{conversation_id}")
            if raw:
                t = json.loads(raw)
                if isinstance(t, dict) and t.get("page_id") and t.get("psid"):
                    return t
        except Exception as e:
            redis_failed("messenger target", e)
            logger.warning(f"[messenger] conv {conversation_id}: target not read from Redis: {type(e).__name__}: {e}")
    if role != "agent":
        return None
    from app.db import supabase as db

    try:
        conv = await db.get_conversation_metadata(conversation_id)
    except Exception as e:
        MESSENGER_HEALTH["target_unread"] += 1
        raise TargetUnread(conversation_id) from e
    meta = (conv or {}).get("metadata") or {}
    t = meta.get("messenger") if isinstance(meta, dict) and meta.get("chat_channel") == "messenger" else None
    if not (isinstance(t, dict) and t.get("page_id") and t.get("psid")):
        if conv is None:
            MESSENGER_HEALTH["no_target"] += 1
        return None
    await _keep_target(conversation_id, t["page_id"], t["psid"])
    return {"page_id": t["page_id"], "psid": t["psid"]}


async def _on_messenger(conversation_id: str) -> Optional[bool]:
    """Whether a chat is on Messenger, as the recovery minute needs it for a delivery a stopped process left: None
    when it can't be told now (Redis or the chat not read: rule 7), never a guess; False for no such chat."""
    r = get_redis()
    if r is not None:
        try:
            if await r.get(f"msgr:conv:{conversation_id}"):
                return True
        except Exception as e:
            redis_failed("messenger target", e)
            logger.warning(f"[messenger] conv {conversation_id}: target not read from Redis: {type(e).__name__}: {e}")
            return None
    from app.db import supabase as db

    try:
        conv = await db.get_conversation_metadata(conversation_id)
    except Exception as e:
        logger.warning(f"[messenger] conv {conversation_id}: the chat not read, looked at again later: "
                       f"{type(e).__name__}: {e}")
        return None
    if conv is None:
        return False  # no such chat: nothing goes there
    meta = conv.get("metadata") or {}
    return isinstance(meta, dict) and meta.get("chat_channel") == "messenger"


async def _row_stored(row_id: str) -> Optional[bool]:
    """Whether an agent's message written down before it was stored is in the chat: None when it can't be told now
    (the read failed: rule 7), never a guess."""
    from app.db import supabase as db

    try:
        return await db.get_message(row_id) is not None
    except Exception as e:
        logger.warning(f"[messenger] a written-down message's row not read: {type(e).__name__}: {e}")
        return None


async def _keep_target(conversation_id: str, page_id: str, psid: str) -> None:
    r = get_redis()
    if r is None:
        return
    try:
        await r.set(f"msgr:conv:{conversation_id}", json.dumps({"page_id": page_id, "psid": psid}), ex=TARGET_S)
    except Exception as e:
        redis_failed("messenger target", e)
        logger.warning(f"[messenger] conv {conversation_id}: target not kept: {type(e).__name__}: {e}")


async def send(page_id: str, psid: str, messages: list[dict], tag: Optional[str] = None) -> dict:
    """The gateway's POST /v1/send: {"sent": n, "parts": m, "parts_sent": k, "failed": {"kind", …}?} (a long
    message is several on Messenger: parts_sent of them went out). With no answer from the gateway (counted):
    {"sent": 0, "failed": {"kind": "gateway"}} only when the call is known to have gone nowhere (no connection was
    ever had to write it on, or it was turned away with a 3xx or 4xx: the gateway refuses a call before it sends
    anything, and so does a proxy before it); "unknown" for anything else (a write that failed or ran out of time,
    whose bytes may have reached the gateway whole all the same; a lost connection, the wait over, any 5xx, which the
    gateway never answers itself and a proxy may answer once the gateway had the call, an answer that is not one):
    what went out is not known, and it is never sent again."""
    import httpx
    from config.settings import settings

    payload: dict[str, Any] = {"page_id": page_id, "psid": psid, "messages": messages}
    if tag:
        payload["tag"] = tag
    body = json.dumps(payload).encode()
    unix = int(time.time())
    headers = {"Content-Type": "application/json", "X-Gateway-Time": str(unix),
               "X-Gateway-Signature": sign(_secret(), unix, body)}
    url = settings.messenger_gateway_url.rstrip("/") + "/v1/send"
    kind = "unknown"
    try:
        async with httpx.AsyncClient(timeout=GATEWAY_TIMEOUT_S) as client:
            res = await client.post(url, content=body, headers=headers)
        if res.status_code == 200:
            out = res.json()
            if isinstance(out, dict):
                return out
            logger.error("[messenger] the gateway answered a send with something that is not one")
        else:
            # Turned away (the gateway's own refusals are a 4xx, before it sends anything): nothing went out. A 5xx
            # is never the gateway's (whatever it says, a proxy's: maybe after the gateway had it).
            kind = "gateway" if 300 <= res.status_code < 500 else "unknown"
            logger.error(f"[messenger] a send answered {res.status_code} ({kind})")
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
        # No connection to write it on: nothing went out. Never a write that failed (WriteError, WriteTimeout): what
        # was handed to the socket before it may still reach the gateway whole.
        kind = "gateway"
        logger.error(f"[messenger] the gateway did not take a send: {type(e).__name__}: {e}")
    except Exception as e:
        logger.error(f"[messenger] the gateway did not answer a send: {type(e).__name__}: {e}")
    MESSENGER_HEALTH["gateway_down"] += 1
    return {"sent": 0, "failed": {"kind": kind}}


# ── one event ─────────────────────────────────────────────────────


class TargetUnread(Exception):
    """Where a chat's rows go could not be told now (Redis did not know it, and the chat could not be read): its
    agent's message is kept for later, never dropped as a site chat's would be."""


class StillAnswering(Exception):
    """The same event is being answered by an earlier delivery: the gateway tries again in a moment (409)."""


class EventFailed(Exception):
    """An event that could not be answered now: the gateway delivers it again. `kept`: it is in the inbox, and the
    API answers it later itself (run_messenger_recover) if no delivery does first (503 with X-Messenger-Kept)."""

    def __init__(self, kept: bool):
        super().__init__("kept for later" if kept else "not kept")
        self.kept = kept


class AnswerNotKept(StillAnswering):
    """A recovery turn whose answer could not be kept as the API's to send (`why`, _remember): not written (held by
    this process, written at its next minute, then sent), or another delivery or pass took the event while it ran
    (theirs). Never sent from here now: the event waits."""

    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


async def handle(event: InboundEvent, *, by: Literal["gateway", "api"] = "gateway", client: Any = None) -> dict:
    """One event: {"replies": [{"text", "quick_replies"?}, …]}, the replies to send in order. `by` is who sends
    them: the gateway (the inbound route) or the API itself (run_messenger_recover). A delivery made again gets the
    replies given the first time, none when the other way sent them; one made while the first is still answered
    raises StillAnswering; a failure raises EventFailed, and the gateway delivers the event again. `client`: the
    recovery's Redis client. The event's mark and its place in the inbox are read and written through it, a cooldown
    begun since or not, and Redis failing raises: a kept event is never answered without its mark (its replies kept
    from a turn whose send never reached the gateway would be made again), it is left for a later pass."""
    if by == "gateway":
        MESSENGER_HEALTH["events"] += 1
    key = f"msgr:event:{event.key()}"
    state, kept = await _claim(key, client)
    if state == "pending":
        MESSENGER_HEALTH["still_answering"] += 1
        raise StillAnswering()
    if state == "done":
        if kept.get("by", "gateway") != by:
            return {"replies": []}
        MESSENGER_HEALTH["duplicates"] += 1
        return {"replies": kept.get("replies") or []}
    token = str(kept.get("token") or "")
    in_inbox = await _keep_event(event, client)
    replies: list[dict] = []
    try:
        async with _claim_kept(key, token):
            # Never before an earlier event of theirs still in the inbox (its turn failed): it is answered and sent
            # first, now. Redis down (this one not kept, not_kept): there is no inbox to look in.
            if in_inbox:
                await _answer_earlier(event)
            replies = await _answer(event)
    except StillAnswering:
        await _forget(key, token)
        raise
    except Exception as e:
        await _forget(key, token)
        raise EventFailed(in_inbox) from e
    if by == "gateway" and in_inbox and replies:
        kept_now = await _give_to_gateway(key, event.key(), replies, token)
        if kept_now == "theirs":
            # The recovery's send of them began first (this event's mark lost meanwhile): its to send, and to let go
            # of. The gateway gets none.
            MESSENGER_HEALTH["duplicates"] += 1
            return {"replies": []}
        if kept_now == "unkept":
            # Redis did not say whether the right to send them is this delivery's: none given (another delivery's,
            # or the API's own send, may hold them), the event kept. The gateway delivers it again (409), and gets
            # them once Redis says the right is this one's (_UNGIVEN), or none, another's standing.
            raise StillAnswering()
    else:
        kept_now = await _remember(key, replies, by, token, client)
    if kept_now == "lost":
        # Another delivery or pass took the event while this turn ran (its claim ran out): theirs to give or send,
        # never given or sent from here beside theirs.
        if by == "api":
            raise AnswerNotKept(kept_now)
        return {"replies": []}
    if by == "api" and kept_now != "kept":
        # The recovery sends only an answer kept as the API's to send: a send known not to have gone out is made
        # again from it, never from a second turn. Not written (Redis failing): this process holds it, writes it at
        # its next minute, and the event waits for it.
        raise AnswerNotKept(kept_now)
    if by == "gateway" and not (kept_now == "kept" and in_inbox and replies
                                and (await _later_kept(event)) is not False):
        # Kept and given to the gateway with a later event of the person's waiting in the inbox (or one that could not
        # be looked up), it stays there, its replies on their way through the gateway (_send_state), so the later one
        # never goes before them; the recovery lets go of it once the gateway's send is over.
        await _release_event(event.key(), _who(event))
    MESSENGER_HEALTH["replies"] += len(replies)
    return {"replies": replies}


# A kept event's replies given back to the gateway, which takes the right to send them, and their answer kept, in one
# step, only while the delivery still holds the event's claim (its token, or nothing): 1; 2 when the API's own send of
# them began first, and the gateway gets none: over ("done"), an answer with none is kept; still on its way, this
# turn's answer is kept as the API's to send, should that send go nowhere (never an empty one, which would let the
# event go unsent); 0 when another delivery or pass holds the event, or answered it (nothing written). Run again for
# the same delivery (its right, ARGV[5], found there: a run whose answer was lost took it), 1 again.
GIVE_TO_GATEWAY_LUA = """
local s = redis.call('get', KEYS[2])
if s == ARGV[5] then
  return 1
end
local v = redis.call('get', KEYS[1])
if v and v ~= ARGV[1] then
  return 0
end
if s and string.sub(s, 1, 7) ~= 'gateway' then
  if s == 'done' then
    redis.call('set', KEYS[1], ARGV[3], 'EX', ARGV[4])
  else
    redis.call('set', KEYS[1], ARGV[7], 'EX', ARGV[4])
  end
  return 2
end
redis.call('set', KEYS[2], ARGV[5], 'EX', ARGV[6])
redis.call('set', KEYS[1], ARGV[2], 'EX', ARGV[4])
return 1
"""


async def _give_to_gateway(key: str, k: str, replies: list, token: str) -> str:
    """A kept event's replies given back to the gateway with the right to send them (rule 7: why, never a bare bool):
    "kept" (the gateway sends them: a pass of the recovery never sends beside it, and reads the right as on its way
    while the gateway may still be sending them, SENDING_S, then as over); "theirs" (the API's own send of them began
    first: the gateway gets none); "lost" (another delivery or pass took the event while this turn ran: theirs, and
    this delivery gets none, counted); "unkept" (Redis failing: whether the right is this delivery's is not known, so
    the gateway gets none now, and the giving is held to make again, _UNGIVEN, counted); "no_redis"."""
    r = get_redis(even_cooling_down=True)
    if r is None:
        return "no_redis"
    answer = json.dumps({"replies": replies, "by": "gateway"})
    right = f"{SENT_BY_GATEWAY}:{time.time()}"
    try:
        given = await _give(r, key, k, replies, token, answer, right)
    except Exception as e:
        redis_failed("messenger inbox", e)
        _UNKEPT[key] = (token, answer)
        _UNGIVEN[key] = (k, replies, right)
        MESSENGER_HEALTH["answer_unkept"] += 1
        logger.warning(f"[messenger] a kept event's replies not given to the gateway, Redis not saying whether their "
                       f"right is this delivery's (held, given when it does): {type(e).__name__}: {e}")
        return "unkept"
    if not given:
        MESSENGER_HEALTH["claim_lost"] += 1
        logger.warning("[messenger] a delivery's turn ended after another delivery or pass took the event: it gives "
                       "the gateway no replies beside theirs")
        return "lost"
    return "theirs" if int(given) == 2 else "kept"


async def _give(r, key: str, k: str, replies: list, token: str, answer: str, right: str) -> int:
    """GIVE_TO_GATEWAY_LUA for one delivery (its token, answer and right): 1, 2 or 0. Redis failing raises."""
    return int(await r.eval(GIVE_TO_GATEWAY_LUA, 2, key, _sending_key(k), token, answer,
                            json.dumps({"replies": [], "by": "gateway"}), ANSWERED_S, right, INBOX_KEEP_S,
                            json.dumps({"replies": replies, "by": "api"})))


# Kept events whose giving to the gateway Redis did not answer (the right and the answer maybe written, maybe not):
# each with its key, replies and right, beside its answer in _UNKEPT. The gateway got none: one delivery's replies
# beside another's, or beside the API's own send, could go twice. Made again (GIVE_TO_GATEWAY_LUA, the same right) at
# the process's next minute, or when the event is delivered again: whatever came of the first, a delivery made again
# then reads the event's answer from Redis (this one's, the API's, or another's). Never given from here meanwhile:
# the delivery waits (409).
_UNGIVEN: dict[str, tuple[str, list, str]] = {}


async def _later_kept(event: InboundEvent) -> Optional[bool]:
    """Whether a later event of this person's is in the inbox (its turn failed): it waits there behind this one. None
    when it can't be told (Redis failing, logged; or not there): never taken for "none waiting" (rule 7), so this one
    stays in the inbox as if one waited, and a later one still waits for the gateway's send of its replies."""
    r = get_redis(even_cooling_down=True)
    if r is None:
        return None
    k, who = event.key(), _who(event)
    try:
        mine = await r.zscore(INBOX, k)
        if mine is None:
            return False
        for m in await r.zrangebyscore(who, mine, "+inf"):
            if m != k and await r.zscore(INBOX, m) is not None:
                return True
    except Exception as e:
        redis_failed("messenger inbox", e)
        logger.warning(f"[messenger] a person's later events not looked up (the event kept): {type(e).__name__}: {e}")
        return None
    return False


async def _answer_earlier(event: InboundEvent) -> None:
    """The person's events taken before this one and not answered yet (in the inbox: their turn failed), answered
    now, oldest first, and their replies sent through the gateway before this one's turn (as the recovery sends
    them), so this one never comes before them and none of theirs is lost if this one fails. One being answered
    elsewhere, or answered with its replies still to send (the gateway could not be reached, or a send is on its way):
    this one waits (StillAnswering: the gateway tries again in a moment). One that fails again: this one fails too,
    and waits in the inbox behind it.

    This one is in the inbox: Redis answered a moment ago. A cooldown since (another call's failure, process wide)
    never reads as "nothing earlier": they are looked up all the same, and Redis failing raises (this one fails, kept,
    and is delivered again)."""
    r = get_redis(even_cooling_down=True)
    if r is None:
        raise RuntimeError("redis not there to look up the person's earlier events")
    for k in await _earlier_kept(r, event):
        earlier = await _kept_event(r, k)
        if earlier is None:
            continue  # unreadable: the recovery gives it up
        sending = await _send_state(r, k)
        if sending == "on_its_way":
            MESSENGER_HEALTH["still_answering"] += 1
            raise StillAnswering()  # its replies are being sent: this one comes after them
        if sending != "none":
            await _send_ended(k, _who(earlier), sending)
            continue
        key = f"msgr:event:{k}"
        state, kept = await _claim(key, r)
        if state == "pending":
            MESSENGER_HEALTH["still_answering"] += 1
            raise StillAnswering()
        if state == "done":
            if kept.get("by", "gateway") == "api":
                raise StillAnswering()  # the recovery sends its replies: this one comes after them
            # Answered through the gateway already: only its place in the inbox was left.
            await _release_event(k, _who(earlier))
            continue
        token = str(kept.get("token") or "")
        try:
            async with _claim_kept(key, token):
                replies = await _answer(earlier)
        except Exception:
            await _forget(key, token)
            raise
        MESSENGER_HEALTH["answered_first"] += 1
        # Its answer kept as the API's to send first, then its send written down (one sender only: SET NX, so the
        # recovery, finding it kept, never sends it beside this). Not written (Redis failing): held by this process
        # and written at its next minute, the recovery sends it then, without a second turn, and this one waits.
        # Another pass holding the event now: theirs to send.
        if await _remember(key, replies, "api", token, r) != "kept":
            raise StillAnswering()
        mark = await _begin_send(r, earlier, k, replies) if replies else None
        if replies and mark is None:
            raise StillAnswering()  # another send of them began: this one comes after it
        if mark is not None and not await _send_kept(r, earlier, k, replies, mark):
            raise StillAnswering()  # never reached the gateway: kept, the recovery sends it, and this one waits
        await _release_event(k, _who(earlier))


async def _claim(key: str, client: Any = None) -> tuple[str, dict]:
    """("new", {"token"}) for an event not seen: its claim, written under a token of this turn's own ("pending:<id>"),
    is renewed, cleared and answered only while it still holds that token, so a turn whose claim ran out never
    clears or answers over the delivery or pass that took the event since ("" with no Redis: nothing written).
    ("pending", {}) while it is being answered, ("done", {"replies", "by"}) after: also for an answer this process
    holds (Redis did not take it as its turn ended, _UNKEPT), written now, never a second turn. With Redis down an
    event is answered without its mark (counted); with `client` given (a person's earlier event, answered for a later
    one that is kept, or the recovery's event), Redis failing raises: never answered without its mark."""
    r = client if client is not None else get_redis()
    if key in _UNKEPT:
        state, held = await _held_answer(r, key, raising=client is not None)
        if state != "claim":
            return state, held
    if r is None:
        MESSENGER_HEALTH["no_dedupe"] += 1
        return "new", {"token": ""}
    # Kept whatever the SET's answer: one lost may have written it, and then it is this turn's.
    token = f"pending:{uuid.uuid4().hex}"
    raw: Optional[str] = None
    kept: Any = None
    try:
        # The turn starts only on a claim taken: one let go of between SET NX and GET (it ran out, or its turn failed)
        # is taken again, never answered as new without it (another delivery could take it and run a second turn).
        for _ in range(CLAIM_TRIES):
            if await r.set(key, token, nx=True, ex=PENDING_S):
                return "new", {"token": token}
            raw = await r.get(key)
            if raw is not None:
                break
    except Exception as e:
        redis_failed("messenger dedupe", e)
        if client is not None:
            logger.warning(f"[messenger] a kept event's mark not read: {type(e).__name__}: {e}")
            raise
        MESSENGER_HEALTH["no_dedupe"] += 1
        logger.warning(f"[messenger] an event answered without its dedupe: {type(e).__name__}: {e}")
        return "new", {"token": token}
    if raw is None:
        # Taken and let go of each time it was looked at: answered as being answered (the gateway tries again).
        return "pending", {}
    if raw == "pending" or str(raw).startswith("pending:"):
        return "pending", {}
    try:
        kept = json.loads(raw)
    except ValueError as e:
        logger.warning(f"[messenger] kept replies not readable, answered as none: {e}")
        kept = None
    if isinstance(kept, list):
        kept = {"replies": kept, "by": "gateway"}
    return "done", kept if isinstance(kept, dict) else {"replies": [], "by": "gateway"}


# What an event's mark and its place in the inbox say once it was answered is written through a cooldown too
# (another call's failure, process wide, while Redis may answer): skipped, the event would stay in the inbox marked
# for no one, and the recovery would answer it, and send its replies, a second time. Redis down, each fails (logged).


# An event's answer written in place of its claim, only while the claim is still the turn's own token, or gone with no
# other delivery or pass holding the event since: 1; 0 when another's claim or answer is there (never written over).
KEEP_ANSWER_LUA = """
local v = redis.call('get', KEYS[1])
if v == false or v == ARGV[1] then
  redis.call('set', KEYS[1], ARGV[2], 'EX', ARGV[3])
  return 1
end
return 0
"""


# Events whose answer Redis did not take when their turn ended, each with its turn's claim and the answer: this process
# writes it at its next minute (_settle_own), or when the event is asked for again (_claim), and never runs a second
# turn of it meanwhile: a delivery made again gets the same replies from here, and the API sends none until it is
# written (a send known not to have gone out is made again from the answer written, never from a new turn).
_UNKEPT: dict[str, tuple[str, str]] = {}


async def _remember(key: str, replies: list, by: str, token: str, client: Any = None) -> str:
    """An event's answer kept in place of its turn's claim (`token`, from _claim), and why not (rule 7): "kept";
    "lost" when another delivery or pass holds the event now (this turn's claim ran out, and that one took it, or
    answered it): theirs stands, never written over (counted); "unkept" when Redis did not take it: held by this
    process and written later (_UNKEPT, counted); "no_redis" when there is none to keep it in."""
    r = client if client is not None else get_redis(even_cooling_down=True)
    if r is None:
        return "no_redis"
    answer = json.dumps({"replies": replies, "by": by})
    try:
        kept = await r.eval(KEEP_ANSWER_LUA, 1, key, token, answer, ANSWERED_S)
    except Exception as e:
        redis_failed("messenger dedupe", e)
        _UNKEPT[key] = (token, answer)
        _UNGIVEN.pop(key, None)  # this turn's answer, written as an answer: never an older delivery's giving
        MESSENGER_HEALTH["answer_unkept"] += 1
        logger.warning(f"[messenger] an event's answer not written, held to write later (never a second turn): "
                       f"{type(e).__name__}: {e}")
        return "unkept"
    if not kept:
        MESSENGER_HEALTH["claim_lost"] += 1
        logger.warning("[messenger] an event's turn ended after another delivery or pass took the event: its answer "
                       "is not written over theirs")
        return "lost"
    return "kept"


async def _write_held(r, key: str) -> bool:
    """An answer this process holds (_UNKEPT) written now, in place of its turn's claim or of nothing: True; False when
    another delivery or pass holds the event since (theirs stands: the held one is dropped, counted). One whose giving
    to the gateway Redis did not answer (_UNGIVEN) is given again, with the same right, never beside the API's own
    send of the replies (GIVE_TO_GATEWAY_LUA): True also when that send holds them. Redis failing raises, and it stays
    held."""
    token, answer = _UNKEPT[key]
    giving = _UNGIVEN.get(key)
    if giving is not None:
        kept: Any = await _give(r, key, giving[0], giving[1], token, answer, giving[2])
    else:
        kept = await r.eval(KEEP_ANSWER_LUA, 1, key, token, answer, ANSWERED_S)
    if _UNKEPT.get(key) == (token, answer):
        del _UNKEPT[key]  # only the one written: a newer turn's, held meanwhile, stays
    if giving is not None and _UNGIVEN.get(key) == giving:
        del _UNGIVEN[key]
    if not kept:
        MESSENGER_HEALTH["claim_lost"] += 1
        logger.warning("[messenger] an answer held by this process not written: another delivery or pass took the "
                       "event meanwhile")
    return bool(kept)


async def _held_answer(r, key: str, raising: bool) -> tuple[str, dict]:
    """An event whose answer this process holds (_UNKEPT), asked for again (a delivery made again, the recovery, a
    later event of the person's): ("done", the answer), written now, never a second turn; still given from here while
    Redis fails, unless `raising` (the recovery's and an earlier event's own client: Redis failing raises, as for their
    mark). ("claim", {}) once another delivery or pass is found holding the event: claimed as any event then. One whose
    giving to the gateway Redis did not answer (_UNGIVEN) is never given from here: given again first, its event's
    answer is then read from Redis ("claim"); Redis failing still, ("pending", {}): the delivery waits (409)."""
    answer = _UNKEPT[key][1]
    giving = key in _UNGIVEN
    if r is not None:
        try:
            if not await _write_held(r, key) or giving:
                return "claim", {}
        except Exception as e:
            redis_failed("messenger dedupe", e)
            logger.warning(f"[messenger] an answer held by this process still not written: {type(e).__name__}: {e}")
            if raising:
                raise
    if giving:
        return "pending", {}
    return "done", json.loads(answer)


async def _forget(key: str, token: str) -> None:
    """A failed turn's claim cleared, so the next delivery is answered: only while it is still this turn's (`token`),
    never another delivery's or pass's that took the event since, nor its answer."""
    r = get_redis(even_cooling_down=True) if token else None
    if r is None:
        return
    try:
        await r.eval(RELEASE_LUA, 1, key, token)
    except Exception as e:
        redis_failed("messenger dedupe", e)
        logger.warning(f"[messenger] a failed event's mark not cleared: {type(e).__name__}: {e}")


def _who(event: InboundEvent) -> str:
    """The inbox's events of one person (their visitor id: a hash of the Page and the PSID)."""
    return f"{INBOX}:who:{visitor_id_for(event.page_id, event.psid)}"


def _last_key(event: InboundEvent) -> str:
    """The Meta time of the latest event of one person's taken (_keep_event), answered since or not."""
    return f"{INBOX}:last:{visitor_id_for(event.page_id, event.psid)}"


# The person's latest event taken: its Meta time kept only when later than the one there (ARGV[1]), for INBOX_KEEP_S.
LAST_TAKEN_LUA = """
local v = tonumber(redis.call('get', KEYS[1]) or '0')
if tonumber(ARGV[1]) > v then
  redis.call('set', KEYS[1], ARGV[1], 'EX', ARGV[2])
else
  redis.call('expire', KEYS[1], ARGV[2])
end
return 1
"""


async def _keep_event(event: InboundEvent, client: Any = None) -> bool:
    """The event in the inbox until it is answered, and among its person's there, by when it was first taken: True
    once it is there; False when Redis could not take it (counted: one that fails then is not answered later). With
    `client` given (the recovery's: the event is in the inbox already), Redis failing raises."""
    r = client if client is not None else get_redis()
    if r is None:
        MESSENGER_HEALTH["not_kept"] += 1
        return False
    k = event.key()
    try:
        # First, so an event taken is never one a late send of an earlier one's replies could pass (_send_late).
        await r.eval(LAST_TAKEN_LUA, 1, _last_key(event), event.timestamp, INBOX_KEEP_S)
        await r.set(f"{INBOX}:{k}", event.model_dump_json(), ex=INBOX_KEEP_S)
        taken = await r.zscore(INBOX, k)
        if taken is None:
            taken = time.time()
            await r.zadd(INBOX, {k: taken})
        await r.zadd(_who(event), {k: taken})
        await r.expire(_who(event), INBOX_KEEP_S)
        return True
    except Exception as e:
        redis_failed("messenger inbox", e)
        if client is not None:
            logger.warning(f"[messenger] a kept event's place in the inbox not written: {type(e).__name__}: {e}")
            raise
        MESSENGER_HEALTH["not_kept"] += 1
        logger.warning(f"[messenger] an event not kept in the inbox: {type(e).__name__}: {e}")
        return False


async def _release_event(k: str, who: Optional[str] = None) -> None:
    """Out of the inbox, and out of its person's there when they are known: answered (or given up). A send's mark of
    its replies stays until it expires (INBOX_KEEP_S): a turn of the same event that ends after this (one whose pass
    lost its claim) never takes a send of them again (_begin_send)."""
    r = get_redis(even_cooling_down=True)
    if r is None:
        return
    try:
        pipe = r.pipeline(transaction=False)
        pipe.zrem(INBOX, k)
        if who:
            pipe.zrem(who, k)
        pipe.delete(f"{INBOX}:{k}", f"{INBOX}:tries:{k}")
        await pipe.execute()
    except Exception as e:
        redis_failed("messenger inbox", e)
        logger.warning(f"[messenger] an answered event not taken out of the inbox: {type(e).__name__}: {e}")


def _sending_key(k: str) -> str:
    return f"{INBOX}:sending:{k}"


# Kept events whose send is known to have sent nothing (the call never reached the gateway) and whose mark could not
# be cleared then (Redis failing), each with the mark this process wrote: cleared by it at its next recovery minute,
# or when it next looks at the event, however long Redis took to answer, so their replies go again. Another process
# meeting such a mark waits while this one is alive (_send_state); one that finds this process gone takes the mark
# for a send begun: maybe out, never sent by it (counted). A mark written whose answer was lost is one of these too
# (nothing was sent: _begin_send). Each is kept with its event and its replies: a process cut off from Redis past
# ALIVE_S while it runs, whose event another process let go of meanwhile (taking it for one stopped), sends them itself
# once Redis answers, under the same mark (no one else sends while it stands).
_UNSENT: dict[str, tuple[str, str, list]] = {}
# Kept events whose send ended (out, refused, or maybe out) and whose end could not be written then (Redis failing),
# each with its mark and its person: this process lets go of them at its next recovery minute (out of the inbox,
# never sent again). With _UNSENT, every old mark of a live process is one it knows of and settles itself: another
# process waits on it while that process is alive (_send_state).
_ENDED: dict[str, tuple[str, Optional[str]]] = {}
# The right to send a kept event's replies, taken by its delivery from the gateway (handle, "gateway:<when>"), only
# while that delivery holds the event's claim: the gateway sends them, and the API never does.
SENT_BY_GATEWAY = "gateway"


def _drop_unsent(k: str, entry: Optional[tuple]) -> None:
    """An entry of _UNSENT let go of, only while it is still the one looked at: a newer one, written by a send of this
    process that sent nothing meanwhile, stays (it is that send's to settle)."""
    if entry is not None and _UNSENT.get(k) is entry:
        del _UNSENT[k]


async def _send_state(r, k: str) -> str:
    """Where the API's send of a kept event's replies is (SENDING_S): "none" (never begun, or known to have sent
    nothing), "on_its_way" (begun lately: its process may still be at it; or before that by another process still
    alive, which may know that nothing went out, for as long as its minutes could still clear it: ALIVE_S), "begun"
    (begun and never over: its process stopped in it, or this one, which is not at it), "done" (over, and the event
    not taken out of the inbox after). Redis failing raises."""
    known = _UNSENT.get(k)  # what this process knew of the send before the read: never one it writes meanwhile
    raw = await r.get(_sending_key(k))
    if raw is None or raw == "done":
        _drop_unsent(k, known)
        return "none" if raw is None else "done"
    if raw == SENT_BY_GATEWAY or str(raw).startswith(f"{SENT_BY_GATEWAY}:"):
        # The gateway's right to send them, taken as its delivery's replies went back to it ("gateway:<when>"): on
        # its way while the gateway may still be sending them (SENDING_S, past the gateway's own 60 seconds), then
        # over, sent or not: never sent again by the API. A later event of the person's waits for it until then.
        _drop_unsent(k, known)
        at = 0.0
        try:
            at = float(str(raw).partition(":")[2] or 0)
        except ValueError as e:
            logger.warning(f"[messenger] the gateway's right to send not readable, taken for one over: {e}")
        return "on_its_way" if time.time() - at < SENDING_S else "done"
    ended = _ENDED.get(k)
    if ended is not None and ended[0] == raw:
        _ENDED.pop(k, None)
        return "done"  # this process's own send, over: only its end was not written
    if (_UNSENT.get(k) or ("",))[0] == raw:
        # This process's own send of them, which sent nothing: the mark was left only because clearing it failed.
        await r.eval(RELEASE_LUA, 1, _sending_key(k), raw)
        _UNSENT.pop(k, None)
        return "none"
    head, _, owner = str(raw).partition(":")
    since = 0.0  # unreadable: taken for one begun long ago
    try:
        since = float(head)
    except ValueError as e:
        logger.warning(f"[messenger] a send's mark not readable, taken for one begun long ago: {e}")
    if time.time() - since < SENDING_S:
        return "on_its_way"
    if owner and owner != BOOT and await r.exists(f"msgr:alive:{owner}"):
        # Its process lives: an old mark of its own is one it knows of (_UNSENT, _ENDED), and its minutes settle it
        # (_settle_own), however long Redis fails them. Never taken for a send begun while it is alive.
        return "on_its_way"
    return "begun"


async def _begin_send(r, event: InboundEvent, k: str, replies: list[dict]) -> Optional[str]:
    """A send of a kept event's replies written down before it starts, by one sender only (SET NX): its mark ("<when>:
    <this process>"), from which on they are never sent again; None when another send of them was begun (that one's
    to finish: this one sends nothing)."""
    mark = f"{time.time()}:{BOOT}"
    try:
        taken = await r.set(_sending_key(k), mark, nx=True, ex=INBOX_KEEP_S)
    except Exception as e:
        # Its answer lost: the mark may be there, and nothing was sent. This process clears it later if it is.
        redis_failed("messenger inbox", e)
        _UNSENT[k] = (mark, event.model_dump_json(), replies)
        logger.warning(f"[messenger] a send's mark maybe written, nothing sent (cleared later if it was): "
                       f"{type(e).__name__}: {e}")
        raise
    return mark if taken else None


async def _send_kept(r, event: InboundEvent, k: str, replies: list[dict], mark: str) -> bool:
    """A kept event's replies sent through the gateway, the send written down already (_begin_send: `mark`). True
    once the event is done with: they went out, or may have, or Meta refused them (counted, never sent again); False
    when the call never reached the gateway (nothing went out: the mark is cleared, and they go again later; Redis
    failing then, this process clears it later, _UNSENT)."""
    result = await send(event.page_id, event.psid, replies)
    failed = result.get("failed") or {}
    kind = str(failed.get("kind") or "")
    if failed and kind == "gateway":
        try:
            # The same replies go again from the event's answer: written back first where it went meanwhile.
            await _answer_back(r, k, replies)
            await r.eval(RELEASE_LUA, 1, _sending_key(k), mark)
        except Exception as e:
            redis_failed("messenger inbox", e)
            _UNSENT[k] = (mark, event.model_dump_json(), replies)
            logger.warning(f"[messenger] a kept event's send that sent nothing, its mark not cleared (cleared later): "
                           f"{type(e).__name__}: {e}")
        return False
    try:
        await r.set(_sending_key(k), "done", ex=INBOX_KEEP_S)
    except Exception as e:
        # Over, and its end not written: this process lets go of the event at its next minute (_settle_own).
        redis_failed("messenger inbox", e)
        _ENDED[k] = (mark, _who(event))
        logger.warning(f"[messenger] a kept event's send over, its end not written (let go of at the next minute): "
                       f"{type(e).__name__}: {e}")
    if failed:
        MESSENGER_HEALTH["send_failed"] += 1
        if kind == "unknown":
            MESSENGER_HEALTH["unconfirmed"] += 1
        logger.warning(f"[messenger] a kept event's replies not sent ({kind or 'no kind'})")
    return True


async def _answer_back(r, k: str, replies: list) -> None:
    """A kept event's answer written back as the API's to send, only where there is none (it went meanwhile: its day
    over, or Redis let it go), before a send known not to have gone out has its mark cleared: the same replies go
    again from it, never from a second turn. Never over another's claim or answer. Redis failing raises."""
    await r.eval(KEEP_ANSWER_LUA, 1, f"msgr:event:{k}", "", json.dumps({"replies": replies, "by": "api"}),
                 ANSWERED_S)


async def _settle_own(r) -> None:
    """This process's own marks it could not settle then (Redis failing). An answer Redis did not take (_UNKEPT) is
    written, in place of its turn's claim or of nothing, so the recovery sends it without a second turn (not written
    still, its claim is kept, so no other delivery or pass answers the event again meanwhile); a send that
    sent nothing (_UNSENT) has its mark cleared, each only while it is still the one this process wrote, and its
    replies go again (its event let go of meanwhile, the mark still this one's: sent from here, _send_late); a send
    that ended (_ENDED) has its event let go of, out of the inbox: never sent again. Redis failing still: left for the
    next minute (logged)."""
    unwritten = False
    for key in list(_UNKEPT):
        try:
            await _write_held(r, key)
        except Exception as e:
            redis_failed("messenger dedupe", e)
            logger.warning(f"[messenger] an answer held by this process still not written: {type(e).__name__}: {e}")
            unwritten = True
            await _keep_held_claim(r, key)
    if unwritten:
        return
    for k, (mark, kept_event, replies) in list(_UNSENT.items()):
        try:
            if await r.zscore(INBOX, k) is not None:
                # Still kept: its answer written back where it went meanwhile, its mark cleared (only while it is this
                # one's), and the recovery sends the same replies again.
                await _answer_back(r, k, replies)
                await r.eval(RELEASE_LUA, 1, _sending_key(k), mark)
                _UNSENT.pop(k, None)
            elif await r.get(_sending_key(k)) == mark:
                await _send_late(r, k, kept_event, replies, mark)
            else:
                _UNSENT.pop(k, None)  # the mark ran out, or is another's: not this one's to send
        except Exception as e:
            redis_failed("messenger inbox", e)
            logger.warning(f"[messenger] a send's mark that sent nothing still not cleared: {type(e).__name__}: {e}")
            return
    for k, (_mark, who) in list(_ENDED.items()):
        try:
            pipe = r.pipeline(transaction=False)
            pipe.zrem(INBOX, k)
            if who:
                pipe.zrem(who, k)
            pipe.delete(f"{INBOX}:{k}", f"{INBOX}:tries:{k}")
            await pipe.execute()
        except Exception as e:
            redis_failed("messenger inbox", e)
            logger.warning(f"[messenger] an event whose send ended still not let go of: {type(e).__name__}: {e}")
            return
        _ENDED.pop(k, None)


async def _keep_held_claim(r, key: str) -> None:
    """The claim of an event whose answer this process holds and Redis still did not take, given a new expiry (a
    write refused may still let an expiry through): no other delivery or pass takes the event and answers it a second
    time meanwhile. Only while it is still the turn's own; failing, it runs out as any claim (logged)."""
    token = (_UNKEPT.get(key) or ("",))[0]
    if not token:
        return
    try:
        await r.eval(RENEW_LUA, 1, key, token, int(PENDING_S * 1000))
    except Exception as e:
        redis_failed("messenger dedupe", e)
        logger.warning(f"[messenger] the claim of an event whose answer is held not renewed: {type(e).__name__}: {e}")


async def write_held(budget_s: float = NOTES_S) -> dict:
    """A shutdown (app.main, after drain_all): the answers this process holds (Redis did not take them as their turn
    ended, _UNKEPT) written now, within budget_s, so the next process never answers their events a second time. One
    still not written goes with the process, counted (answer_dropped) and logged: its event may be answered again once
    its claim runs out. {"written", "dropped"}."""
    written = 0
    r = get_redis(even_cooling_down=True) if _UNKEPT else None

    async def write_all() -> None:
        nonlocal written
        for key in list(_UNKEPT):
            if await _write_held(r, key):
                written += 1

    if r is not None:
        try:
            await asyncio.wait_for(write_all(), budget_s)
        except Exception as e:
            redis_failed("messenger dedupe", e)
            logger.warning(f"[messenger] shutdown: the answers held by this process not all written: "
                           f"{type(e).__name__}: {e}")
    dropped = len(_UNKEPT)
    if dropped:
        MESSENGER_HEALTH["answer_dropped"] += dropped
        logger.error(f"[messenger] shutdown: {dropped} answer(s) Redis did not take go with this process: their events "
                     f"may be answered again once their claim runs out")
        _UNKEPT.clear()
        _UNGIVEN.clear()
    return {"written": written, "dropped": dropped}


# A late send's event put back in the inbox, and among its person's there, before its replies go (its JSON, ARGV[5];
# ARGV[4], its place: before any taken after it), its send's mark made a new one (ARGV[7]: on its way from now, for
# every process, this one too), only while the mark is still this process's (ARGV[1]): 0 when it is not; -1 when an
# event of the person's later than it (ARGV[2], Meta's time) was taken since, whose replies may be out already (never
# sent after them); 1 once it is back, so a later one taken now waits behind it.
REKEEP_LUA = """
if redis.call('get', KEYS[1]) ~= ARGV[1] then
  return 0
end
if tonumber(redis.call('get', KEYS[2]) or '0') > tonumber(ARGV[2]) then
  return -1
end
redis.call('set', KEYS[1], ARGV[7], 'EX', ARGV[6])
redis.call('set', KEYS[5], ARGV[5], 'EX', ARGV[6])
redis.call('zadd', KEYS[3], ARGV[4], ARGV[3])
redis.call('zadd', KEYS[4], ARGV[4], ARGV[3])
redis.call('expire', KEYS[4], ARGV[6])
return 1
"""


async def _send_late(r, k: str, kept_event: str, replies: list, mark: str) -> None:
    """A kept event's replies this process knows never went out, whose event another process let go of meanwhile: it
    took this one's mark for a send a stopped process began (this one, cut off from Redis past ALIVE_S, could not say
    it was alive), maybe out, and sent nothing. No one sends them while the mark is this one's, so they go from here,
    under it, once (sent_late), in the person's order: the event is put back in the inbox first, so a later event of
    theirs waits behind it, and then let go of again; a later one taken since (its replies maybe out already), they
    are never sent after it: dropped, counted (late_dropped). Never reaching the gateway again: held, the event in
    the inbox, and its mark cleared at the next minute, so the recovery sends them. Redis failing raises (the caller
    logs): held, unless they went out already."""
    try:
        event = InboundEvent.model_validate_json(kept_event)
    except ValidationError as e:
        _UNSENT.pop(k, None)
        logger.error(f"[messenger] replies to send late, their event not readable ({e.error_count()} errors): given up")
        return
    who = _who(event)
    place = event.timestamp / 1000 if event.timestamp else time.time()
    fresh = f"{time.time()}:{BOOT}"
    back = int(await r.eval(REKEEP_LUA, 5, _sending_key(k), _last_key(event), INBOX, who, f"{INBOX}:{k}", mark,
                            event.timestamp, k, place, kept_event, INBOX_KEEP_S, fresh))
    _UNSENT.pop(k, None)  # its mark this one's no more, or on its way under the new one: never cleared as unsent
    if back == 0:
        return  # the mark ran out, or is another's: not this one's to send
    if back < 0:
        MESSENGER_HEALTH["late_dropped"] += 1
        logger.warning("[messenger] replies known never to have gone out, a later event of the person's taken since: "
                       "never sent after its replies (dropped)")
        return
    result = await send(event.page_id, event.psid, replies)
    failed = result.get("failed") or {}
    kind = str(failed.get("kind") or "")
    if failed and kind == "gateway":
        # In the inbox again: its mark cleared at the next minute, and the recovery sends them, in the person's order.
        _UNSENT[k] = (fresh, kept_event, replies)
        logger.warning("[messenger] replies to send late never reached the gateway: sent by the recovery from the inbox")
        return
    if failed:
        MESSENGER_HEALTH["send_failed"] += 1
        if kind == "unknown":
            MESSENGER_HEALTH["unconfirmed"] += 1
        logger.warning(f"[messenger] replies sent late not sent ({kind or 'no kind'})")
    else:
        MESSENGER_HEALTH["sent_late"] += 1
    try:
        await r.set(_sending_key(k), "done", ex=INBOX_KEEP_S)
    except Exception as e:
        # Over, and its end not written: this process lets go of the event at its next minute (_settle_own).
        redis_failed("messenger inbox", e)
        _ENDED[k] = (fresh, who)
        logger.warning(f"[messenger] replies sent late, their end not written (let go of at the next minute): "
                       f"{type(e).__name__}: {e}")
        return
    await _release_event(k, who)


@contextlib.asynccontextmanager
async def _claim_kept(key: str, token: str):
    """An event's claim kept while its turn runs: renewed every PENDING_S / 3, only while it still holds the turn's
    own token (compare and expire). A turn longer than PENDING_S never lets a second turn of the same event begin (a
    delivery made again, another recovery pass); its process gone, the claim expires as before. A claim found
    another's (this one ran out while Redis failed, and the event was taken since) is never renewed as this turn's."""
    r = get_redis(even_cooling_down=True) if token else None
    keeper = asyncio.create_task(_keep_pending(r, key, token)) if r is not None else None
    try:
        yield
    finally:
        if keeper is not None:
            keeper.cancel()
            await asyncio.gather(keeper, return_exceptions=True)


async def _keep_pending(r, key: str, token: str) -> None:
    while True:
        await asyncio.sleep(PENDING_S / 3)
        try:
            if not await r.eval(RENEW_LUA, 1, key, token, int(PENDING_S * 1000)):
                return  # no longer this turn's claim: nothing to keep
        except Exception as e:
            redis_failed("messenger dedupe", e)
            logger.warning(f"[messenger] an event's pending mark not renewed (tried again): {type(e).__name__}: {e}")


async def _send_ended(k: str, who: Optional[str], sending: str) -> None:
    """A kept event whose replies a send has had ("done", or "begun" by a process that stopped in it): never sent
    again, out of the inbox. One begun and never over may be with the person, or not (counted)."""
    if sending == "begun":
        MESSENGER_HEALTH["recover_unsure"] += 1
        logger.warning("[messenger] a kept event's replies were being sent when their process stopped: maybe with "
                       "the person, never sent again")
    await _release_event(k, who)


async def _earlier_kept(r, event: InboundEvent) -> list[str]:
    """The keys of this person's events taken before this one and not answered yet, oldest first. Redis failing
    raises (logged): the event fails and is delivered again, never answered past an earlier one it could not look
    up."""
    k, who = event.key(), _who(event)
    out: list[str] = []
    try:
        mine = await r.zscore(INBOX, k)
        for m in await r.zrangebyscore(who, "-inf", "+inf" if mine is None else mine):
            if m == k:
                continue
            if await r.zscore(INBOX, m) is None:
                await r.zrem(who, m)  # answered or given up: only its place here was left
                continue
            out.append(m)
    except Exception as e:
        redis_failed("messenger inbox", e)
        logger.warning(f"[messenger] a person's earlier events not looked up: {type(e).__name__}: {e}")
        raise
    return out


async def _kept_event(r, k: str) -> Optional[InboundEvent]:
    """An event kept in the inbox, as the gateway handed it over; None when it is gone, or there and unreadable
    (logged: the recovery gives it up). Redis failing raises (logged): one not read is never taken for one gone."""
    try:
        raw = await r.get(f"{INBOX}:{k}")
    except Exception as e:
        redis_failed("messenger inbox", e)
        logger.warning(f"[messenger] a kept event not read: {type(e).__name__}: {e}")
        raise
    if not raw:
        return None
    try:
        return InboundEvent.model_validate_json(raw)
    except ValidationError as e:
        logger.error(f"[messenger] a kept event not readable ({e.error_count()} errors)")
        return None


def _text_of(event: InboundEvent) -> str:
    """What the person said, as the chat keeps it: their text, the button they tapped, or what they sent."""
    text = (event.text or "").strip()
    if text:
        return text
    if event.kind == "postback":
        return (event.postback_title or "Get Started").strip()
    kinds = [_ATTACHMENTS.get(a, "[attachment]") for a in event.attachments]
    return " ".join(dict.fromkeys(kinds)) or "[attachment]"


def _referral_meta(ref: Optional[Referral]) -> dict:
    """Where the person came from, for the lead and the CRM, as a UTM tag says it on the site."""
    if ref is None:
        return {}
    out: dict[str, Any] = {"messenger_referral": {k: v for k, v in ref.model_dump().items() if v}}
    out["utm_medium"] = "messenger_ads" if (ref.source or "").upper() == "ADS" else "messenger"
    if ref.ref:
        out["utm_campaign"] = ref.ref[:120]
    return out


async def _answer(event: InboundEvent) -> list[dict]:
    from app.db import supabase as db

    vid = visitor_id_for(event.page_id, event.psid)
    with _collecting("") as turn:
        if event.kind == "referral":
            # Opened the thread from a link or an ad without writing yet: kept for their chat.
            MESSENGER_HEALTH["referrals"] += 1
            await _keep_referral(vid, event.referral)
            conv = await _thread(vid, create=False)
            if conv:
                await db.patch_conversation_presence(conv["id"], _referral_meta(event.referral))
            return []
        conv = await _thread(vid, create=True, event=event)
        if not conv:
            raise RuntimeError("the thread's chat could not be found or made")
        cid = conv["id"]
        turn.conversation_id = cid
        await _keep_target(cid, event.page_id, event.psid)
        meta = conv.get("metadata") if isinstance(conv.get("metadata"), dict) else {}
        if event.kind == "echo":
            # Someone wrote from the Page's inbox: kept so the chat in the panel is whole, never sent back, and Skye
            # leaves the thread to them a while. That while is what keeps her out of their thread: not saved, the
            # event fails and is delivered again (the echo is not stored twice).
            if event.text.strip():
                await _save_once(event, "echo", cid, "agent", event.text.strip(), outbound=False)
            if not await db.patch_conversation_presence(cid, {"messenger_inbox_at": datetime.now(timezone.utc).isoformat()}):
                raise RuntimeError("the inbox's quiet time was not saved")
            MESSENGER_HEALTH["inbox_replies"] += 1
            return []
        text = _text_of(event)
        if event.standby or _inbox_spoke(meta):
            await _save_once(event, "user", cid, "user", text)
            MESSENGER_HEALTH["kept_unanswered"] += 1
            return []
        if event.kind == "message" and not event.text.strip():
            # Rule 4: what they sent is kept first; then Skye asks for words.
            await _save_once(event, "user", cid, "user", text)
            await _save_once(event, "prompt", cid, "ai", ATTACHMENT_REPLY, model_used="template")
            MESSENGER_HEALTH["attachments"] += 1
            return await _replies(turn, None, None)
        button = event.quick_reply if event.quick_reply in _buttons() else None
        resp = await _turn(cid, vid, text, button, event.site)
        return await _replies(turn, resp, button, await _say_queued(cid, resp))


def row_id(event: InboundEvent, part: str) -> str:
    """The id of a row this event stores: the same for every delivery of the event, another for each part."""
    return str(uuid.uuid5(ROW_IDS, f"{event.key()}:{part}"))


async def _save_once(event: InboundEvent, part: str, cid: str, role: str, text: str, **kw) -> dict:
    """A row this event stores, stored once whatever the deliveries: its id is the event's and the part's
    (row_id), so an insert made again finds the row an earlier one stored, its answer lost included, and stores
    nothing (counted); that row is still said this time (it joins the turn as a new one does). Not stored, or not
    known to be (the row could not be read): raises, and the event is delivered again."""
    from app.db.supabase import StoredBefore
    from app.services.conversation_service import add_message

    row = await add_message(cid, role, text, message_id=row_id(event, part), **kw)
    if not row:
        raise RuntimeError(f"the event's {part} row was not saved")
    if isinstance(row, StoredBefore):
        MESSENGER_HEALTH["saved_once"] += 1
    return row


async def _thread(vid: str, *, create: bool, event: Optional[InboundEvent] = None) -> Optional[dict]:
    """The person's chat: the one they had while it was written to in the last THREAD_DAYS (closed or not: the turn
    reopens it, or answers after the sale, as for the widget's chat), else a new one."""
    from app.db import supabase as db
    from app.models.chat import VisitorInfo
    from app.services.conversation_service import get_or_create_conversation

    latest = await db.latest_conversation_of_visitor(vid)
    if latest is None:
        raise RuntimeError("the thread's chat could not be read")
    if latest and _recent(latest):
        return latest
    if not create or event is None:
        return None
    meta: dict[str, Any] = {
        "site": event.site, "chat_channel": "messenger", "visitor_id": vid,
        "messenger": {"page_id": event.page_id, "psid": event.psid},
        "utm_source": "messenger", "utm_medium": "messenger",
        **_referral_meta(event.referral or await _kept_referral(vid)),
    }
    return await get_or_create_conversation(None, "sales", VisitorInfo(), visitor_id=vid, metadata=meta)


def _recent(conv: dict) -> bool:
    """Written to in the last THREAD_DAYS: the latest of the chat's own change, the person's last message and its
    start."""
    times: list[datetime] = []
    for at in (conv.get("updated_at"), conv.get("last_user_message_at"), conv.get("created_at")):
        if not at:
            continue
        try:
            when = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
        except ValueError as e:
            logger.warning(f"[messenger] conv {conv.get('id')}: a time that is not one ({e}), left out")
            continue
        times.append(when if when.tzinfo else when.replace(tzinfo=timezone.utc))
    return bool(times) and datetime.now(timezone.utc) - max(times) < timedelta(days=THREAD_DAYS)


def _inbox_spoke(meta: dict) -> bool:
    at = meta.get("messenger_inbox_at")
    if not at:
        return False
    try:
        when = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError as e:
        logger.warning(f"[messenger] an inbox time that is not one ({e}): Skye answers")
        return False
    return datetime.now(timezone.utc) - when < INBOX_QUIET


async def _keep_referral(vid: str, ref: Optional[Referral]) -> None:
    r = get_redis()
    if r is None or ref is None:
        return
    try:
        await r.set(f"msgr:ref:{vid}", ref.model_dump_json(), ex=THREAD_DAYS * 86400)
    except Exception as e:
        redis_failed("messenger referral", e)
        logger.warning(f"[messenger] a referral not kept: {type(e).__name__}: {e}")


async def _kept_referral(vid: str) -> Optional[Referral]:
    r = get_redis()
    if r is None:
        return None
    try:
        raw = await r.get(f"msgr:ref:{vid}")
        return Referral.model_validate_json(raw) if raw else None
    except Exception as e:
        redis_failed("messenger referral", e)
        logger.warning(f"[messenger] a kept referral not read: {type(e).__name__}: {e}")
        return None


def _buttons() -> frozenset:
    from app.pipeline.card_buttons import BUTTONS

    return frozenset(BUTTONS)


def _gateway_request():
    """The request the chat's turn reads: no address, no browser (the person is on Messenger, not the site)."""
    from starlette.requests import Request

    return Request({"type": "http", "method": "POST", "path": "/api/channels/messenger/inbound",
                    "headers": [], "query_string": b"", "client": None})


async def _turn(cid: str, vid: str, text: str, button: Optional[str], site: str):
    """The widget's turn for one Messenger message. A turn the chat refuses (the blocklist, a limit: a 4xx) is kept
    as the chat kept it and gets no reply; one it could not take now (a 5xx: the message not saved) raises, and the
    event is delivered again."""
    from fastapi import HTTPException

    from app.api.chat import chat
    from app.models.chat import ChatRequest, VisitorInfo
    from app.security.visitor_token import issue

    MESSENGER_HEALTH["turns"] += 1
    req = ChatRequest(message=text[:2000], conversation_id=cid, visitor_id=vid, tunnel="sales",
                      visitor=VisitorInfo(), metadata={"site": site}, button=button)
    try:
        return await chat(req, _gateway_request(), None, issue(vid))
    except HTTPException as e:
        if e.status_code >= 500:
            raise
        MESSENGER_HEALTH["turns_refused"] += 1
        logger.warning(f"[messenger] conv {cid}: the turn was refused ({e.status_code})")
        return None


async def _say_queued(cid: str, resp) -> bool:
    """Whether "connecting you with a specialist" goes out now: once in QUEUED_EVERY_S, never "your message has
    been sent" after each message to an agent."""
    from app.api.chat import QUEUED_FIRST

    if resp is None or getattr(resp, "type", None) != "queued" or getattr(resp, "message", None) != QUEUED_FIRST:
        return False
    r = get_redis()
    if r is None:
        return True
    try:
        return bool(await r.set(f"msgr:queued:{cid}", "1", nx=True, ex=QUEUED_EVERY_S))
    except Exception as e:
        redis_failed("messenger queued", e)
        logger.warning(f"[messenger] conv {cid}: the queued line's mark not read: {type(e).__name__}: {e}")
        return True


def _answer_stored(turn: _Turn, resp) -> bool:
    """The turn stored its answer: an AI row among what it stored, or the row its answer names."""
    mid = getattr(resp, "message_id", None)
    return any(r.get("role") == "ai" or (mid and r.get("id") == mid) for r in turn.rows)


async def _replies(turn: _Turn, resp, button: Optional[str], say_queued: bool = False) -> list[dict]:
    """What the turn said to the person, in order: the rows it stored, then its answer when that was not stored (a
    queued line once, an answer whose row could not be stored). Told by what was stored, never by comparing texts:
    a stored answer went through the output guard and may read otherwise than what the turn returned. What goes
    out without a row goes through the guard too. The buttons on the last one."""
    from app.ai.output_guard import guard_ai_text

    texts = [str(r.get("content") or "").strip() for r in turn.rows]
    texts = [t for t in texts if t]
    message = str(getattr(resp, "message", "") or "").strip() if resp is not None else ""
    if message and not getattr(resp, "streaming", False) and not _answer_stored(turn, resp):
        if getattr(resp, "type", None) != "queued" or say_queued:
            guarded = str(await guard_ai_text(message, conversation_id=turn.conversation_id) or "").strip()
            if guarded:
                texts.append(guarded)
    replies: list[dict] = [{"text": t} for t in texts]
    quick = _quick_replies(resp, button)
    if replies and quick:
        replies[-1]["quick_replies"] = quick
    return replies


def _quick_replies(resp, button: Optional[str]) -> list[dict]:
    """The answer's buttons as Messenger quick replies (a text one each, its kind as the payload the gateway brings
    back), and right after the person chose SMS or e-mail, their own number or address from their profile in one
    tap (Meta shows it only when the profile has one; they can always type it)."""
    out: list[dict] = []
    for b in (getattr(resp, "buttons", None) or []) if resp is not None else []:
        kind, label = (b or {}).get("kind"), str((b or {}).get("label") or "").strip()
        if kind in _buttons() and label:
            out.append({"content_type": "text", "title": label[:20], "payload": kind})
    for q in (getattr(resp, "quick_replies", None) or []) if resp is not None else []:
        q = str(q or "").strip()
        if q:
            out.append({"content_type": "text", "title": q[:20], "payload": "say"})
    if not out and button == "sms":
        out.append({"content_type": "user_phone_number"})
    elif not out and button == "email":
        out.append({"content_type": "user_email"})
    return out[:13]


# ── the events whose turn failed ──────────────────────────────────


async def run_messenger_recover() -> dict:
    """The scheduler's minute (app.services.scheduler). Each event kept in the inbox longer than RECOVER_AFTER_S
    (its turn failed, and the gateway's tries are over) is answered now, and its replies go out through the gateway;
    each agent whose message a stopped process left written down, or whose note was not saved, is told (OUTBOX).
    Every process says first that it is alive, and settles the marks of its own sends it could not settle then
    (_settle_own); then one at a time (a lock in Redis, under the pass's own token,
    renewed while the pass runs and let go only while it is still its own) does the work, starting nothing new past
    RECOVER_BUDGET_S or once the lock is lost, the agents' notes first within TELL_BUDGET_S so the events always get
    their part. An event still failing after RECOVER_TRIES runs, or unreadable, is given up: counted, and logged
    without its words."""
    out: dict[str, Any] = {"state": "empty", "answered": 0, "waiting": 0, "given_up": 0, "told": 0}
    if not enabled():
        return {**out, "state": "off"}
    r = get_redis()
    if r is None:
        return {**out, "state": "no_redis"}
    token = uuid.uuid4().hex
    try:
        await r.set(f"msgr:alive:{BOOT}", "1", ex=ALIVE_S)
        # Every process's own, lock or not: another process waits on these marks while this one is alive.
        await _settle_own(r)
        if not await r.set(RECOVER_LOCK, token, nx=True, ex=RECOVER_LOCK_S):
            return {**out, "state": "busy"}
    except Exception as e:
        redis_failed("messenger recover", e)
        logger.warning(f"[messenger] the recovery's lock not taken: {type(e).__name__}: {e}")
        return {**out, "state": "redis_failed"}
    started = time.monotonic()
    held = {"lost": False}
    keeper = asyncio.create_task(_keep_lock(r, token, held))
    try:
        out["told"] = await _tell_left(r, started + TELL_BUDGET_S)
        keys = await r.zrangebyscore(INBOX, "-inf", time.time() - RECOVER_AFTER_S)
        if keys or out["told"]:
            out["state"] = "ran"
        # Oldest first; a person whose event still waits has their later ones wait too, in order.
        waiting: set[str] = set()
        for k in keys[:RECOVER_BATCH]:
            if time.monotonic() - started >= RECOVER_BUDGET_S or held["lost"]:
                break  # the rest next minute (or the pass that holds the lock now)
            out[await _recover_one(r, k, waiting)] += 1
    except Exception as e:
        redis_failed("messenger recover", e)
        logger.error(f"[messenger] the recovery stopped: {type(e).__name__}: {e}")
        out["state"] = "failed"
    finally:
        keeper.cancel()
        await asyncio.gather(keeper, return_exceptions=True)
        try:
            await r.eval(RELEASE_LUA, 1, RECOVER_LOCK, token)
        except Exception as e:
            redis_failed("messenger recover", e)
            logger.warning(f"[messenger] the recovery's lock not released (it expires): {type(e).__name__}: {e}")
    return out


async def _keep_lock(r, token: str, held: dict) -> None:
    """The recovery's lock kept the pass's own while it runs: renewed every RECOVER_LOCK_S / 3, only while it still
    holds the pass's token. Found gone (it expired while Redis failed, and maybe another pass holds it now), the pass
    starts nothing new: what it is on goes on (a send's mark keeps any reply from going twice). Stopped by the pass."""
    while True:
        await asyncio.sleep(RECOVER_LOCK_S / 3)
        try:
            if not await r.eval(RENEW_LUA, 1, RECOVER_LOCK, token, int(RECOVER_LOCK_S * 1000)):
                held["lost"] = True
                logger.warning("[messenger] the recovery's lock was lost: the pass starts nothing new")
                return
        except Exception as e:
            redis_failed("messenger recover", e)
            logger.warning(f"[messenger] the recovery's lock not renewed (tried again): {type(e).__name__}: {e}")


async def _tell_left(r, until: float) -> int:
    """The agents' messages written down that no live process is on (theirs stopped: its alive mark is gone), the
    notes not saved (untold) and the messages whose save is not known (unsure): each agent told now, once (the
    note's id is the delivery's), when the chat is on Messenger (one left before that was looked up is looked up
    now) and, for one written down before its row was stored, once the row is found stored. How many were told. One
    whose note still is not saved after OUTBOX_KEEP_S, or whose chat or row could not be read by then, or
    unreadable, is given up (counted).

    A part at a time: the outbox is gone through by a cursor (OUTBOX_CURSOR), TELL_BATCH of its deliveries looked up
    or told at most and none started past `until` (time.monotonic), each one's work cut there (its note may be saved
    after all: its id makes saving it again harmless). Redis gives a part of any size, and not always the same one
    for a cursor (HSCAN's COUNT is a hint; a small hash comes whole), so the deliveries looked at in the part the
    passes are on are kept by their ids (OUTBOX_SEEN): a part cut short is gone on with from those not looked at yet,
    whatever left it meanwhile, and the cursor moves on once none is left. The ones that keep failing, or hang, never
    keep the others from their turn."""
    told = 0
    try:
        raw_cursor = await r.get(OUTBOX_CURSOR)
        head = str(raw_cursor or "").partition(":")[0]  # an older "<cursor>:<delivery>" reads as its cursor
        cursor = int(head) if head.isdigit() else 0
        after, entries = await r.hscan(OUTBOX, cursor=cursor, count=TELL_BATCH)
        seen = set(await r.smembers(OUTBOX_SEEN))
        alive: dict[str, bool] = {}
        worked, cut = 0, False
        looked: list[str] = []
        for entry, raw in entries.items():
            if entry in seen:
                continue
            if worked >= TELL_BATCH or time.monotonic() >= until:
                cut = True
                break
            looked.append(entry)  # looked at from here, whatever comes of it (one cut in its work too)
            try:
                d = json.loads(raw)
                cid, boot, state, at = str(d["cid"]), str(d["boot"]), d.get("state"), float(d.get("at") or 0)
            except (ValueError, KeyError, TypeError) as e:
                MESSENGER_HEALTH["outbox_given_up"] += 1
                logger.error(f"[messenger] a written-down delivery not readable ({type(e).__name__}): given up")
                await r.hdel(OUTBOX, entry)
                continue
            if state not in ("untold", "unsure"):
                if boot not in alive:
                    alive[boot] = boot == BOOT or bool(await r.exists(f"msgr:alive:{boot}"))
                if alive[boot]:
                    continue  # on its way in a live process
            worked += 1
            try:
                told += await asyncio.wait_for(_tell_one(r, entry, d, cid, state, at),
                                               max(0.0, until - time.monotonic()))
            except asyncio.TimeoutError as e:
                logger.warning(f"[messenger] conv {cid}: a written-down delivery cut at the notes' time, looked at "
                               f"again when the outbox comes round ({type(e).__name__})")
                cut = True
                break
        if cut:
            if looked:
                await r.sadd(OUTBOX_SEEN, *looked)
                await r.expire(OUTBOX_SEEN, OUTBOX_KEEP_S)
            await r.set(OUTBOX_CURSOR, str(cursor), ex=OUTBOX_KEEP_S)
        else:
            # The part gone through: the next one (0: the outbox from its start), with nothing looked at in it yet.
            await r.set(OUTBOX_CURSOR, str(after), ex=OUTBOX_KEEP_S)
            await r.delete(OUTBOX_SEEN)
    except Exception as e:
        redis_failed("messenger outbox", e)
        logger.error(f"[messenger] the written-down deliveries not gone through: {type(e).__name__}: {e}")
    return told


async def _tell_one(r, entry: str, d: dict, cid: str, state: Any, at: float) -> int:
    """One written-down delivery no live process is on, or whose save is not known: its agent told (1), or not now
    (0: looked at again later, given up after OUTBOX_KEEP_S)."""
    if state in ("waiting", "unsure"):
        # Written down before where it goes was looked up, and before its row was stored: a site chat's says
        # nothing, nor one whose row never was (the stop came before the insert, or the insert failed); a chat or a
        # row not read now is looked at again next pass (given up after OUTBOX_KEEP_S).
        on = await _on_messenger(cid)
        if on is True and d.get("row"):
            on = await _row_stored(str(d["row"]))
        if on is False:
            await r.hdel(OUTBOX, entry)
            return 0
        if on is None:
            if time.time() - at > OUTBOX_KEEP_S:
                await r.hdel(OUTBOX, entry)
                MESSENGER_HEALTH["outbox_given_up"] += 1
                logger.error(f"[messenger] conv {cid}: a written-down delivery whose chat or row could not be read "
                             f"for {OUTBOX_KEEP_S // 3600} h: given up")
            return 0
    # Stored and never handed on (unsure), with the gateway when its process stopped (sending: maybe out), or not
    # started (waiting).
    note = d.get("note") or {"sending": NOT_SENT["unknown"], "unsure": NOT_SENT_OTHER}.get(str(state), NOT_SENT_RESTART)
    if await _tell_agent(cid, str(note), _note_id(entry)):
        await r.hdel(OUTBOX, entry)
        MESSENGER_HEALTH["told_later"] += 1
        return 1
    if time.time() - at > OUTBOX_KEEP_S:
        await r.hdel(OUTBOX, entry)
        MESSENGER_HEALTH["outbox_given_up"] += 1
        logger.error(f"[messenger] conv {cid}: an agent's note still not saved after {OUTBOX_KEEP_S // 3600} h: "
                     f"given up")
    return 0


async def _recover_one(r, k: str, waiting: set[str]) -> str:
    """One kept event: "answered" (its replies sent, or none to send, or a send of them had before: never made
    again), "waiting" (the next minute tries again; its person's later events wait behind it, `waiting`), or
    "given_up"."""
    raw = await r.get(f"{INBOX}:{k}")
    event: Optional[InboundEvent] = None
    if raw:
        try:
            event = InboundEvent.model_validate_json(raw)
        except ValidationError as e:
            logger.error(f"[messenger] a kept event not readable ({e.error_count()} errors): given up")
    who = _who(event) if event is not None else None
    if who is not None and who in waiting:
        return "waiting"  # behind an earlier one of theirs this minute
    # Its replies sent already (the inbox not cleared after), or being sent, or left half sent by a process that
    # stopped: never sent again.
    sending = await _send_state(r, k)
    if sending == "on_its_way":
        MESSENGER_HEALTH["recover_waiting"] += 1
        if who is not None:
            waiting.add(who)
        return "waiting"
    if sending != "none":
        await _send_ended(k, who, sending)
        return "answered"
    tries = await r.incr(f"{INBOX}:tries:{k}")
    await r.expire(f"{INBOX}:tries:{k}", INBOX_KEEP_S)
    if event is None or tries > RECOVER_TRIES:
        await _release_event(k, who)
        MESSENGER_HEALTH["recover_given_up"] += 1
        logger.error(f"[messenger] a kept event given up after {tries} tries "
                     f"({event.kind if event else 'gone or unreadable'})")
        return "given_up"
    try:
        # Through this pass's client: a cooldown begun since (another call's failure, process wide) never reads as an
        # event with no mark, and Redis failing stops the pass, the event left for the next one.
        out = await handle(event, by="api", client=r)
    except StillAnswering as e:
        MESSENGER_HEALTH["recover_waiting"] += 1
        waiting.add(who)
        logger.info(f"[messenger] a kept event waits: it, or an earlier one of its person's, is being answered, or "
                    f"its answer is still to be written ({type(e).__name__}, try {tries})")
        return "waiting"
    except EventFailed as e:
        MESSENGER_HEALTH["recover_waiting"] += 1
        waiting.add(who)
        cause = e.__cause__
        logger.warning(f"[messenger] a kept event still not answered (try {tries}): "
                       f"{type(cause).__name__ if cause else type(e).__name__}")
        return "waiting"
    replies = out.get("replies") or []
    if replies:
        mark = await _begin_send(r, event, k, replies)
        if mark is None or not await _send_kept(r, event, k, replies, mark):
            # Another send of them began since this one looked (another pass, or an earlier event's turn answering
            # it): that one's to finish. Or this one never reached the gateway: kept as answered, the next minute sends
            # the same replies, without a second turn. The person's later events wait for them either way.
            MESSENGER_HEALTH["recover_waiting"] += 1
            waiting.add(who)
            return "waiting"
    await _release_event(k, who)
    MESSENGER_HEALTH["recovered"] += 1
    return "answered"


def health() -> dict:
    return {**MESSENGER_HEALTH, "enabled": enabled()}
