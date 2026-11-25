"""Skye and the agents on Facebook Messenger, the API's side (contracts/messenger.md).

The gateway (bbc-messenger, Go) checks Meta's signature, keeps each person's events in order and hands each one
here, signed with MESSENGER_GATEWAY_SECRET. A person's Messenger thread is an ordinary chat: visitor id
`msgr-<hash of Page and PSID>`, metadata.chat_channel "messenger", metadata.messenger {page_id, psid}, tunnel
sales, the Page's site (bbc or bct). Their message takes the same turn a widget message takes (app.api.chat.chat):
the blocklist, the chat's doors, an agent who holds the chat, the card's buttons, Skye. What that turn stores for
the person goes back in the answer to the gateway, which sends it; an agent's message from the panel (and anything
else stored outside a turn) goes out through the gateway's POST /v1/send.

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
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.flags import flag
from app.infra.redis_client import get_redis, redis_failed

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
TARGET_S = 30 * 86400
GATEWAY_TIMEOUT_S = 40.0
SIGNED_SKEW_S = 300

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
}
NOT_SENT_OTHER = "Not sent on Messenger. Try again in a minute; if it keeps failing, tell an admin."

MESSENGER_HEALTH: dict[str, int] = {
    "events": 0,             # events the gateway handed over, signed
    "turns": 0,              # messages answered by the chat's turn
    "replies": 0,            # replies given back to the gateway
    "duplicates": 0,         # deliveries made again, answered with the replies kept
    "still_answering": 0,    # deliveries made again while the first was being answered (409, the gateway waits)
    "turns_failed": 0,       # events that failed: the gateway delivers them again
    "turns_refused": 0,      # turns the chat refused (blocklist, a limit): kept, no reply
    "kept_unanswered": 0,    # messages kept without a reply: another app owns the thread, or the Page's inbox spoke
    "inbox_replies": 0,      # messages written from the Page's inbox, kept in the chat
    "attachments": 0,        # messages with no text, answered with ATTACHMENT_REPLY
    "referrals": 0,          # where a person came from (an m.me link, an ad), kept
    "no_dedupe": 0,          # events answered with Redis down: a delivery made again could be answered twice
    "bad_signature": 0,      # calls refused: not the gateway's
    "sent": 0,               # messages sent out of a turn (an agent's, mostly)
    "send_failed": 0,        # of those, refused or lost; by why below
    "window_closed": 0,
    "unavailable": 0,
    "thread_owned": 0,
    "gateway_down": 0,       # the gateway did not answer
    "no_target": 0,          # a Messenger chat whose Page and PSID could not be read: nothing sent
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
    secret = _secret()
    if not secret or not unix_header or not signature:
        return False
    try:
        unix = int(unix_header.strip())
    except ValueError:  # noqa: silent — a time that is not a number is a call that is not the gateway's: refused
        return False
    if abs((now if now is not None else time.time()) - unix) > SIGNED_SKEW_S:
        return False
    return hmac.compare_digest(sign(secret, unix, body), signature.strip())


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


async def outbound(conversation_id: str, role: str, row: dict) -> None:
    """A row stored for the person in some chat (conversation_service.add_message): in a Messenger turn it joins
    the turn's answer; outside one, a Messenger chat's row goes out through the gateway, after the chat's previous
    one. Never raises: the row is saved whatever happens here."""
    try:
        turn = _TURN.get()
        if turn is not None and turn.open and turn.conversation_id == conversation_id:
            turn.rows.append(row)
            return
        if not enabled():
            return
        prev = _LAST.get(conversation_id)
        task = asyncio.create_task(_deliver_after(prev, conversation_id, role, row))
        _LAST[conversation_id] = task
        task.add_done_callback(lambda t, cid=conversation_id: _LAST.pop(cid, None) if _LAST.get(cid) is t else None)
    except Exception as e:
        MESSENGER_HEALTH["send_failed"] += 1
        logger.error(f"[messenger] conv {conversation_id}: a row not handed on: {type(e).__name__}: {e}")


async def drain(conversation_id: str) -> None:
    """Wait for a chat's deliveries (tests; a shutdown that wants them out)."""
    task = _LAST.get(conversation_id)
    if task is not None:
        await asyncio.gather(task, return_exceptions=True)


async def _deliver_after(prev: Optional[asyncio.Task], conversation_id: str, role: str, row: dict) -> None:
    if prev is not None:
        await asyncio.gather(prev, return_exceptions=True)
    try:
        await _deliver(conversation_id, role, row)
    except Exception as e:
        MESSENGER_HEALTH["send_failed"] += 1
        logger.error(f"[messenger] conv {conversation_id}: delivery broke: {type(e).__name__}: {e}")


async def _deliver(conversation_id: str, role: str, row: dict) -> None:
    text = str(row.get("content") or "").strip()
    if not text:
        return
    target = await _target(conversation_id, role)
    if not target:
        return
    result = await send(target["page_id"], target["psid"], [{"text": text}])
    if result.get("sent"):
        MESSENGER_HEALTH["sent"] += 1
        return
    MESSENGER_HEALTH["send_failed"] += 1
    kind = str((result.get("failed") or {}).get("kind") or "")
    if kind in ("window_closed", "unavailable", "thread_owned"):
        MESSENGER_HEALTH[kind] += 1
    logger.warning(f"[messenger] conv {conversation_id}: a {role} message not sent on Messenger ({kind or 'no answer'})")
    if role == "agent":
        # The agent sees it in the chat, and only there: the note never goes out.
        from app.services.conversation_service import add_message

        await add_message(conversation_id, "system", NOT_SENT.get(kind, NOT_SENT_OTHER), model_used="template",
                          outbound=False)


async def _target(conversation_id: str, role: str) -> Optional[dict]:
    """The Page and PSID a chat answers to: kept in Redis by each event; an agent's message to a chat Redis no
    longer knows reads the chat's metadata (rows of the bot and the system stay quiet then: they come from a turn)."""
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

    conv = await db.get_conversation_simple(conversation_id)
    meta = (conv or {}).get("metadata") or {}
    t = meta.get("messenger") if isinstance(meta, dict) and meta.get("chat_channel") == "messenger" else None
    if not (isinstance(t, dict) and t.get("page_id") and t.get("psid")):
        if conv is None:
            MESSENGER_HEALTH["no_target"] += 1
        return None
    await _keep_target(conversation_id, t["page_id"], t["psid"])
    return {"page_id": t["page_id"], "psid": t["psid"]}


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
    """The gateway's POST /v1/send: {"sent": n, "failed": {"kind", …}?}; {"sent": 0, "failed": {"kind":
    "gateway"}} when the gateway did not answer (counted)."""
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
    try:
        async with httpx.AsyncClient(timeout=GATEWAY_TIMEOUT_S) as client:
            res = await client.post(url, content=body, headers=headers)
        if res.status_code == 200:
            out = res.json()
            if isinstance(out, dict):
                return out
        logger.error(f"[messenger] the gateway refused a send: {res.status_code}")
    except Exception as e:
        logger.error(f"[messenger] the gateway did not answer a send: {type(e).__name__}: {e}")
    MESSENGER_HEALTH["gateway_down"] += 1
    return {"sent": 0, "failed": {"kind": "gateway"}}


# ── one event ─────────────────────────────────────────────────────


class StillAnswering(Exception):
    """The same event is being answered by an earlier delivery: the gateway tries again in a moment (409)."""


async def handle(event: InboundEvent) -> dict:
    """One event from the gateway: {"replies": [{"text", "quick_replies"?}, …]}, the replies to send in order. A
    delivery made again gets the replies given the first time; one made while the first is still answered raises
    StillAnswering; a failure raises, and the gateway delivers the event again."""
    MESSENGER_HEALTH["events"] += 1
    key = f"msgr:event:{event.key()}"
    state, kept = await _claim(key)
    if state == "pending":
        MESSENGER_HEALTH["still_answering"] += 1
        raise StillAnswering()
    if state == "done":
        MESSENGER_HEALTH["duplicates"] += 1
        return {"replies": kept}
    try:
        replies = await _answer(event)
    except Exception:
        await _forget(key)
        raise
    await _remember(key, replies)
    MESSENGER_HEALTH["replies"] += len(replies)
    return {"replies": replies}


async def _claim(key: str) -> tuple[str, list]:
    """("new", []) for an event not seen, ("pending", []) while it is being answered, ("done", replies) after."""
    r = get_redis()
    if r is None:
        MESSENGER_HEALTH["no_dedupe"] += 1
        return "new", []
    raw: Optional[str] = None
    kept: Any = []
    try:
        if await r.set(key, "pending", nx=True, ex=PENDING_S):
            return "new", []
        raw = await r.get(key)
    except Exception as e:
        redis_failed("messenger dedupe", e)
        MESSENGER_HEALTH["no_dedupe"] += 1
        logger.warning(f"[messenger] an event answered without its dedupe: {type(e).__name__}: {e}")
        return "new", []
    if raw is None:
        return "new", []
    if raw == "pending":
        return "pending", []
    try:
        kept = json.loads(raw)
    except ValueError as e:
        logger.warning(f"[messenger] kept replies not readable, answered as none: {e}")
        kept = []
    return "done", kept if isinstance(kept, list) else []


async def _remember(key: str, replies: list) -> None:
    r = get_redis()
    if r is None:
        return
    try:
        await r.set(key, json.dumps(replies), ex=ANSWERED_S)
    except Exception as e:
        redis_failed("messenger dedupe", e)
        logger.warning(f"[messenger] an event's replies not kept: {type(e).__name__}: {e}")


async def _forget(key: str) -> None:
    r = get_redis()
    if r is None:
        return
    try:
        await r.delete(key)
    except Exception as e:
        redis_failed("messenger dedupe", e)
        logger.warning(f"[messenger] a failed event's mark not cleared: {type(e).__name__}: {e}")


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
    from app.services.conversation_service import add_message

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
            # leaves the thread to them a while.
            MESSENGER_HEALTH["inbox_replies"] += 1
            if event.text.strip():
                await add_message(cid, "agent", event.text.strip(), outbound=False)
            await db.patch_conversation_presence(cid, {"messenger_inbox_at": datetime.now(timezone.utc).isoformat()})
            return []
        text = _text_of(event)
        if event.standby or _inbox_spoke(meta):
            MESSENGER_HEALTH["kept_unanswered"] += 1
            await add_message(cid, "user", text)
            return []
        if event.kind == "message" and not event.text.strip():
            # Rule 4: what they sent is kept first; then Skye asks for words.
            MESSENGER_HEALTH["attachments"] += 1
            await add_message(cid, "user", text)
            await add_message(cid, "ai", ATTACHMENT_REPLY, model_used="template")
            return _replies(turn, None, None)
        button = event.quick_reply if event.quick_reply in _buttons() else None
        resp = await _turn(cid, vid, text, button, event.site)
        return _replies(turn, resp, button, await _say_queued(cid, resp))


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
    """The widget's turn for one Messenger message. A turn the chat refuses (the blocklist, a limit) is kept as the
    chat kept it and gets no reply."""
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


def _replies(turn: _Turn, resp, button: Optional[str], say_queued: bool = False) -> list[dict]:
    """What the turn said to the person, in order: the rows it stored, then its answer when that was not stored
    (a queued line once, a door's or a fallback's words), the buttons on the last one."""
    texts = [str(r.get("content") or "").strip() for r in turn.rows]
    texts = [t for t in texts if t]
    message = str(getattr(resp, "message", "") or "").strip() if resp is not None else ""
    if message and not getattr(resp, "streaming", False) and message not in texts:
        if getattr(resp, "type", None) != "queued" or say_queued:
            texts.append(message)
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


def health() -> dict:
    return {**MESSENGER_HEALTH, "enabled": enabled()}
