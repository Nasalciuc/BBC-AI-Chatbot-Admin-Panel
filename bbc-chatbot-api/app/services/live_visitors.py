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

LIVE_HEALTH: dict = {"tokens": 0, "sent": 0, "refused": 0, "send_failed": 0, "taps": 0, "taps_refused": 0,
                     "tap_failed": 0, "redis_failed": 0}

_LUA_DIR = Path(__file__).with_name("live_lua")


def _lua(name: str) -> str:
    return (_LUA_DIR / name).read_text(encoding="utf-8")


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
    """GET /chat/live: whether the widget beats, and where."""
    if not list_enabled():
        return {"enabled": False}
    return {"enabled": True, "url": _url(), "after_s": AFTER_S, "beat_s": BEAT_S,
            "hello": hello_enabled()}


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
        if raw:
            # The claim's place goes back to the agent at once; a release that fails runs out in 10 min.
            await r.eval(RELEASE_LUA, 2, f"live:claim:{visitor_id}", f"live:claims:{payload['a']}",
                         payload["a"], visitor_id)
    except Exception as e:
        redis_failed("live tap", e)
        LIVE_HEALTH["tap_failed"] += 1
        logger.error(f"[live] tap of {visitor_id} not read: {type(e).__name__}: {e}")
        return None
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
    agent can't take the chat: the chat then stays an ordinary one, counted, never a failure for the visitor."""
    from app.db import supabase as db
    from app.services.conversation_service import add_message

    hello = await take_tap(visitor_id, tap)
    if not hello:
        return {}
    agent_id = hello["agent_id"]
    agent: dict = {}
    try:
        won = (await db.claim_conversation_if_unassigned(conv_id, agent_id)).get("won")
        if not won:
            LIVE_HEALTH["tap_failed"] += 1
            logger.warning(f"[live] conv {conv_id}: the tapped hello's agent could not take it")
            return {}
        now = datetime.now(timezone.utc).isoformat()
        await db.patch_conversation_presence(conv_id, {
            "engaged_agent_id": agent_id, "agent_assigned_at": now,
            "live_hello": {"id": hello["hello_id"], "agent_id": agent_id, "sent_at": hello.get("sent_at"),
                           "tapped_at": now},
        })
        agent = await db.get_user_by_id(agent_id) or {}
        await add_message(conv_id, "agent", hello["text"], live_extra={
            "agent_name": agent.get("name") or "Consultant", "agent_avatar_url": agent.get("avatar_url")})
    except Exception as e:
        LIVE_HEALTH["tap_failed"] += 1
        logger.error(f"[live] conv {conv_id}: starting with the agent failed: {type(e).__name__}: {e}")
        return {}
    return {"agent": first_name(agent.get("name")), "text": hello["text"]}
