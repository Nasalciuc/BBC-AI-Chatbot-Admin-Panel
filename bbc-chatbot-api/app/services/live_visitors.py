"""The API's side of "On the site now" (contracts/live.md; the Go service bbc-live does the rest).

The API is the authority: it hands the panel its live token (the heartbeat), tells the widget where
bbc-live is (GET /chat/live), checks and sends an agent's hello (POST /api/live/send), and at the
visitor's tap starts the chat as the agent's (/chat/init with live_tap). bbc-live never sees the
database; this module never holds a widget's poll.

Two switches in app.flags, both off at merge: LIVE_VISITORS (the list and the beats) and
AGENT_FIRST_MESSAGE (claims, hellos, taps; it needs the first).

Nothing here logs a name, an e-mail or a phone; a hello's text is the agent's own words.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.flags import flag
from app.infra.redis_client import get_redis, redis_failed
from app.services import live_tokens

logger = logging.getLogger(__name__)

LIST_ROLES = {"sales", "owner", "admin", "dev", "supervisor"}
HELLO_ROLES = {"sales"}
HELLO_LINES = {"sales", "all"}
MAX_TEXT = 300
HELLO_TTL = 900          # the hello, the tap token and "one hello a visitor" (send.lua ARGV[5])
AFTER_S = 20             # the widget beats from 20 s on the page
BEAT_S = 15

VISITOR_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

PASS_NEW_PER_ADDRESS = 20   # new visitor ids one address gets passes for in an hour (pass.lua ARGV[3])

LIVE_HEALTH: dict = {"tokens": 0, "sent": 0, "refused": 0, "send_failed": 0, "taps": 0, "taps_refused": 0,
                     "tap_failed": 0, "release_failed": 0, "redis_failed": 0, "passes": 0, "passes_refused": 0,
                     "pass_failed": 0}

_LUA_DIR = Path(__file__).with_name("live_lua")


def _lua(name: str) -> str:
    return (_LUA_DIR / name).read_text(encoding="utf-8")


PASS_LUA = _lua("pass.lua")
# How long a visitor id stays bound to the pass it was handed: the pass's life and the grace in which it still
# proves a renewal. Bound for less, an id would be free to anyone for the last PASS_GRACE of a pass that still
# renews, and two browsers could hold passes for one id (CodeRabbit #273).
PASS_BINDING_S = live_tokens.PASS_TTL + live_tokens.PASS_GRACE
SEND_LUA = _lua("send.lua")
TAKE_HELLO_LUA = _lua("take_hello.lua")
RELEASE_LUA = _lua("release.lua")


def list_enabled() -> bool:
    return flag("LIVE_VISITORS", False) and live_tokens.enabled()


def hello_enabled() -> bool:
    return list_enabled() and flag("AGENT_FIRST_MESSAGE", False)


def _url() -> str:
    from config.settings import settings
    return (settings.live_service_url or "").strip().rstrip("/")


def may_say_hello(user: dict) -> bool:
    return (user.get("role") or "") in HELLO_ROLES and (user.get("tunnel_scope") or user.get("role")) in HELLO_LINES


def heartbeat_live(user: Optional[dict]) -> Optional[dict]:
    """What the panel needs to ask bbc-live, for the roles that see the list; None otherwise."""
    if not user or (user.get("role") or "") not in LIST_ROLES or not list_enabled():
        return None
    token = live_tokens.agent_token(user)
    if not token:
        return None
    LIVE_HEALTH["tokens"] += 1
    expires = datetime.now(timezone.utc) + timedelta(seconds=live_tokens.AGENT_TTL)
    return {"url": _url(), "token": token, "expires_at": expires.isoformat(),
            "can_say_hello": hello_enabled() and may_say_hello(user)}


def widget_config() -> dict:
    """Whether the widget beats, and where (without the pass: widget_pass)."""
    if not list_enabled():
        return {"enabled": False}
    return {"enabled": True, "url": _url(), "after_s": AFTER_S, "beat_s": BEAT_S,
            "hello": hello_enabled()}


_PASS_REFUSALS = {-1: "the id has a pass already", -2: "too many new visitors from one address"}


async def widget_pass(visitor_id: Optional[str], old_pass: Optional[str], ip: Optional[str]) -> dict:
    """POST /chat/live: widget_config() and, while it is on, the visitor's pass to bbc-live (b1).

    A visitor id gets a pass without proof once (pass.lua); after that only by showing its own pass, which
    the widget keeps and shows on every page. {"enabled": False} whenever no pass is handed: the widget then
    beats nowhere. {"enabled": False, "later": True} when Redis can't bind the id for now: the widget asks again
    later instead of ending the page's live work (CodeRabbit #273). Every refusal and failure is counted in
    LIVE_HEALTH."""
    config = widget_config()
    if not config["enabled"]:
        return config
    off = {"enabled": False}
    for_now = {"enabled": False, "later": True}
    if not VISITOR_ID.fullmatch(visitor_id or ""):
        LIVE_HEALTH["passes_refused"] += 1
        return off
    proof, reason = (None, "missing")
    if old_pass:
        proof, reason = live_tokens.read_pass(old_pass, grace=live_tokens.PASS_GRACE)
    proven = proof is not None and proof.get("v") == visitor_id
    # The pass is made before the id is bound to it, so the binding (PASS_BINDING_S from the eval) outlasts the
    # pass's last renewing instant; a binding refused or failed drops it unseen (CodeRabbit #273).
    token = live_tokens.beat_pass(visitor_id)
    if not token:
        LIVE_HEALTH["pass_failed"] += 1
        logger.error("[live] no pass handed: the token could not be made")
        return off
    r = get_redis()
    if r is None:
        from config.settings import settings

        LIVE_HEALTH["pass_failed"] += 1
        # Cooling down after a failure: for now. Not configured at all: no pass on this deploy.
        return for_now if settings.redis_url else off
    hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    keys = [f"live:passed:{visitor_id}", f"rl:livepass:{live_tokens.address_key(ip)}:{hour}"]
    result = 0
    try:
        result = int(await r.eval(PASS_LUA, len(keys), *keys, "1" if proven else "0",
                                  str(PASS_BINDING_S), str(PASS_NEW_PER_ADDRESS)))
    except Exception as e:
        redis_failed("live pass", e)
        LIVE_HEALTH["pass_failed"] += 1
        logger.error(f"[live] no pass handed for now: {type(e).__name__}: {e}")
        return for_now
    if result != 1:
        LIVE_HEALTH["passes_refused"] += 1
        logger.info(f"[live] pass refused: {_PASS_REFUSALS.get(result, 'refused')} (shown pass: {reason})")
        return off
    LIVE_HEALTH["passes"] += 1
    expires = datetime.now(timezone.utc) + timedelta(seconds=live_tokens.PASS_TTL)
    return {**config, "pass": token, "pass_expires_at": expires.isoformat()}


class SendRefused(Exception):
    """A hello that is not sent, with the reason the panel shows (status, code)."""

    def __init__(self, status: int, code: str, detail: str):
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


def clean_text(text: str) -> str:
    return " ".join(_CONTROL.sub(" ", text or "").split())


def first_name(name: Optional[str]) -> str:
    parts = (name or "").split()
    return parts[0][:40] if parts else "Your travel agent"


async def send_hello(user: dict, user_db: Optional[dict], visitor_id: str, text: str) -> dict:
    """The agent's hello to a visitor they hold in bbc-live. Raises SendRefused."""
    from app.ai.output_guard import foreign_contact_kinds
    from app.services.agent_presence import _may_take_chats

    if not hello_enabled():
        raise SendRefused(404, "off", "Saying hello first is switched off.")
    if not may_say_hello(user) or not _may_take_chats(user_db):
        raise SendRefused(403, "not_for_you", "Saying hello first is for agents of the sales line who take chats.")
    if not VISITOR_ID.fullmatch(visitor_id or ""):
        raise SendRefused(400, "visitor", "Unknown visitor.")
    words = clean_text(text)
    if not words or len(words) > MAX_TEXT:
        raise SendRefused(422, "length", f"Write 1 to {MAX_TEXT} characters.")
    kinds = foreign_contact_kinds(words)
    if kinds:
        LIVE_HEALTH["refused"] += 1
        raise SendRefused(422, "contact", "No phone number, e-mail or link in a first message: the chat carries them.")
    r = get_redis()
    if r is None:
        raise SendRefused(503, "redis", "Couldn't reach the live service. Try again.")
    agent_id = str(user.get("id"))
    hello_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    tap = live_tokens.tap_token(visitor_id, agent_id, hello_id)
    if not tap:
        raise SendRefused(503, "not_configured", "The live service isn't configured.")
    agent_name = first_name((user_db or {}).get("name") or user.get("name"))
    message = {"id": hello_id, "kind": "hello", "agent": agent_name, "text": words,
               "sent_at": now.isoformat(), "expires_at": (now + timedelta(seconds=HELLO_TTL)).isoformat(),
               "tap": tap}
    hello = {"id": hello_id, "visitor_id": visitor_id, "agent_id": agent_id, "text": words,
             "sent_at": now.isoformat()}
    keys = [f"live:claim:{visitor_id}", f"live:dismissed:{visitor_id}", f"live:inbox:{visitor_id}",
            f"live:hello:{hello_id}", f"live:said:{visitor_id}"]
    result = 0
    try:
        result = await r.eval(SEND_LUA, len(keys), *keys, agent_id, visitor_id,
                              json.dumps(message, separators=(",", ":")), json.dumps(hello, separators=(",", ":")),
                              str(HELLO_TTL))
    except Exception as e:
        redis_failed("live send", e)
        LIVE_HEALTH["send_failed"] += 1
        logger.error(f"[live] hello to {visitor_id} not sent: {type(e).__name__}: {e}")
        raise SendRefused(503, "redis", "Couldn't send the hello. Try again.") from e
    refusals = {
        -1: (409, "not_held", "This visitor isn't yours any more: take them again in the list."),
        -2: (409, "declined", "The visitor said not now."),
        -3: (409, "already", "This visitor got a hello already."),
    }
    if int(result) != 1:
        LIVE_HEALTH["refused"] += 1
        status, code, detail = refusals.get(int(result), (409, "refused", "The hello wasn't sent."))
        raise SendRefused(status, code, detail)
    LIVE_HEALTH["sent"] += 1
    from app.db import supabase as db
    await db.log_audit(agent_id, user.get("email"), "live_hello_sent", "live_visitors", visitor_id,
                       {"hello_id": hello_id, "text": words, "length": len(words)})
    return {"hello_id": hello_id, "sent_at": now.isoformat(), "expires_at": message["expires_at"]}


async def take_tap(visitor_id: Optional[str], tap: Optional[str]) -> Optional[dict]:
    """The hello a widget's tap answers: {"agent_id", "text", "hello_id", "sent_at"}, taken once; None when
    the tap doesn't hold (off, expired, someone else's, already taken, Redis down), counted."""
    if not tap:
        return None
    LIVE_HEALTH["taps"] += 1
    if not hello_enabled():
        LIVE_HEALTH["taps_refused"] += 1
        return None
    payload, reason = live_tokens.read_tap(tap)
    if payload is None or payload.get("v") != visitor_id:
        LIVE_HEALTH["taps_refused"] += 1
        logger.info(f"[live] tap refused: {reason if payload is None else 'another visitor'}")
        return None
    r = get_redis()
    if r is None:
        LIVE_HEALTH["tap_failed"] += 1
        return None
    raw = None
    try:
        raw = await r.eval(TAKE_HELLO_LUA, 1, f"live:hello:{payload['h']}")
    except Exception as e:
        redis_failed("live tap", e)
        LIVE_HEALTH["tap_failed"] += 1
        logger.error(f"[live] tap of {visitor_id} not read: {type(e).__name__}: {e}")
        return None
    if raw:
        # The claim's place goes back to the agent at once. The hello is taken already: a release that fails
        # must not lose it (the claim runs out in 10 min by itself).
        try:
            await r.eval(RELEASE_LUA, 2, f"live:claim:{visitor_id}", f"live:claims:{payload['a']}",
                         payload["a"], visitor_id)
        except Exception as e:
            redis_failed("live release", e)
            LIVE_HEALTH["release_failed"] += 1
            logger.warning(f"[live] the claim on {visitor_id} not released, it runs out: {type(e).__name__}: {e}")
    hello = None
    if raw:
        try:
            hello = json.loads(raw)
        except ValueError as e:
            logger.error(f"[live] the hello for {visitor_id} is not JSON: {e}")
    if not isinstance(hello, dict) or hello.get("agent_id") != payload["a"] or hello.get("visitor_id") != visitor_id:
        LIVE_HEALTH["taps_refused"] += 1
        return None
    return {"agent_id": payload["a"], "text": str(hello.get("text") or ""), "hello_id": payload["h"],
            "sent_at": hello.get("sent_at")}


async def start_with_the_agent(conv_id: str, visitor_id: Optional[str], tap: str) -> dict:
    """/chat/init with live_tap: the visitor tapped an agent's hello, so the chat becomes the agent's, with the
    hello as its first message. {"agent": first name, "text": the hello}, or {} when the tap doesn't hold or the
    agent can't take the chat: the chat then stays an ordinary one, counted, never a failure for the visitor.

    Order: the agent may still take chats; the claim; the hello stored; then the presence fields. A hello that
    cannot be stored after the claim gives the chat back (unassigned, AI mode), so {} always means ordinary; a
    chat that cannot be given back stays the agent's, and the answer says so."""
    from app.db import supabase as db
    from app.services.agent_presence import _may_take_chats
    from app.services.conversation_service import add_message

    hello = await take_tap(visitor_id, tap)
    if not hello:
        return {}
    agent_id = hello["agent_id"]
    agent: dict = {}
    try:
        # The hello may be 15 minutes old: an agent who no longer takes chats (switched off, deactivated) gets
        # no chat from it; the visitor's chat then starts as any other, never waiting on someone who can't answer.
        agent = await db.get_user_by_id(agent_id) or {}
        if not _may_take_chats(agent):
            LIVE_HEALTH["taps_refused"] += 1
            logger.warning(f"[live] conv {conv_id}: the tapped hello's agent no longer takes chats")
            return {}
        won = (await db.claim_conversation_if_unassigned(conv_id, agent_id)).get("won")
    except Exception as e:
        LIVE_HEALTH["tap_failed"] += 1
        logger.error(f"[live] conv {conv_id}: starting with the agent failed: {type(e).__name__}: {e}")
        return {}
    if not won:
        LIVE_HEALTH["tap_failed"] += 1
        logger.warning(f"[live] conv {conv_id}: the tapped hello's agent could not take it")
        return {}
    row = None
    try:
        row = await add_message(conv_id, "agent", hello["text"], live_extra={
            "agent_name": agent.get("name") or "Consultant", "agent_avatar_url": agent.get("avatar_url")})
    except Exception as e:
        logger.error(f"[live] conv {conv_id}: the hello was not stored: {type(e).__name__}: {e}")
    if not row:
        LIVE_HEALTH["tap_failed"] += 1
        if await db.release_live_claim(conv_id, agent_id):
            logger.error(f"[live] conv {conv_id}: the hello was not stored; the chat given back")
            return {}
        # Not given back: the write failed (the chat is still the agent's, in human mode) or it is no longer
        # theirs. Only a chat that is not the agent's starts as an ordinary one; theirs, or one that can't be read,
        # stays theirs, so the visitor and the panel agree on who answers (CodeRabbit #273).
        LIVE_HEALTH["release_failed"] += 1
        now_conv = await db.get_conversation_simple(conv_id)
        if now_conv is not None and now_conv.get("assigned_agent_id") != agent_id:
            logger.error(f"[live] conv {conv_id}: the hello was not stored and the chat is no longer the agent's")
            return {}
        logger.error(f"[live] conv {conv_id}: the hello was not stored and the chat could not be given back: "
                     "it stays the agent's")
    now = datetime.now(timezone.utc).isoformat()
    await db.patch_conversation_presence(conv_id, {
        "engaged_agent_id": agent_id, "agent_assigned_at": now,
        "live_hello": {"id": hello["hello_id"], "agent_id": agent_id, "sent_at": hello.get("sent_at"),
                       "tapped_at": now},
    })
    return {"agent": first_name(agent.get("name")), "text": hello["text"]}
