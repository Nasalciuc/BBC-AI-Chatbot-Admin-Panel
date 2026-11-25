"""An agent's own bot in "On the site now" (the owner, 25 Nov 2026: "endpoint unde alt agent poate conecta propriul bot
și parcă ar lucra de pe propriul endpoint, ... să nu fie limita de trei saluturi").

An agent takes a key in the panel (POST /api/live/bot-key); their bot then works as them, by that key
(app/api/live_bot.py):

- the visitors on the site now, as the panel lists them for that agent, with the hello the panel has ready in their name;
- the hello in the agent's name, without a claim first, so without the three visitors an agent holds at a time
  (bbc-live MaxClaims): bot_send.lua checks and sends in one step, and holds the visitor as "bot:<agent id>" while
  the hello is out;
- the visitor's tap starts the chat as the agent's, as a hello from the panel does (assigned, human mode: Skye stays
  quiet), marked live_hello.bot;
- in those chats the bot reads the visitor and answers as the agent, through the panel's own send.

The key is a JWT signed with the panel's secret, scope "live_bot": no route of the panel takes it as a session (any
scope is refused there), and it is good while it is the agent's newest key (live:botkey:<agent>, deleted on "revoke"),
the agent's token_version is the one it was issued with (a role change or a deactivation ends it) and no "sign out
everywhere" came after it. Every call reads the agent again: an agent who may no longer say hello or take chats has a
key that answers 403. Nothing here logs what a visitor wrote, a name or a contact.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from app.infra.redis_client import get_redis, redis_failed
from app.services import live_tokens, live_visitors
from app.services.live_visitors import SendRefused

logger = logging.getLogger(__name__)

KEY_SCOPE = "live_bot"
KEY_TTL_S = 90 * 86_400          # a key's life; a new one replaces it before
KEY_REDIS = "live:botkey:"       # + agent id: the id of their newest key
HOLDER = "bot:"                  # + agent id: who holds a visitor while the bot's hello is out (live:claim:<vid>)
ONCE_S = 86_400                  # one bot's hello a visitor this long, whoever's bot
AGENT_CACHE_S = 15.0             # the agent's row, read again at most this often per process
ALEX_GAVE_UP_S = 30              # the panel's: Skye's card unanswered this long, the visitor is listed
FORM_QUIET_S = 120               # the panel's: a step in the quote form holds the hello this long
LIVE_WINDOW_S = 45               # bbc-live's Window: a beat in the last 45 s is on the site now
LIST_LIMIT = 500                 # bbc-live's ListLimit
CHATS_LIMIT = 50
MAX_MESSAGE = 2000               # the panel's own send (AgentMessageBody)

_STEP_KINDS = frozenset({"search", "quote_opened", "quote_step", "quote_invalid", "quote_submitting", "quote_failed",
                         "quote_submitted", "contact"})
_ALEX_CARDS = frozenset({"shown", "dismissed", "engaged"})  # bbc-live's alexCards
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_ROLES = {"user": "visitor", "agent": "agent", "ai": "ai", "system": "system"}

BOT_HEALTH: dict = {"keys_issued": 0, "keys_revoked": 0, "key_refused": 0, "agent_unreadable": 0, "sent": 0,
                    "refused": 0, "send_failed": 0, "list_failed": 0, "messages_sent": 0, "messages_refused": 0,
                    "read_failed": 0}

_LUA_DIR = Path(__file__).with_name("live_lua")
LIST_LUA = (_LUA_DIR / "list.lua").read_text(encoding="utf-8")
BOT_SEND_LUA = (_LUA_DIR / "bot_send.lua").read_text(encoding="utf-8")

# bot_send.lua's answers (contracts/live/bot_send.lua).
_REFUSALS = {
    -1: (410, "gone", "The visitor is no longer on the site."),
    -2: (409, "in_a_chat", "The visitor is in a chat already."),
    -3: (409, "declined", "The visitor said not now."),
    -4: (409, "taken", "Someone holds this visitor to say hello."),
    -5: (409, "already", "This visitor got a hello already."),
    -6: (409, "bot_already", "A bot said hello to this visitor today."),
}
# Why the bot may not greet a visitor now (why_not), in words.
_WHY_NOT = {
    "test_alex": "The visitor is on the agent-first test's Alex side: nobody greets them.",
    "not_listed": "The visitor isn't in the list: Skye's card hasn't gone unanswered for them.",
    "in_a_chat": _REFUSALS[-2][2],
    "declined": _REFUSALS[-3][2],
    "taken": _REFUSALS[-4][2],
    "already": _REFUSALS[-5][2],
    "bot_already": _REFUSALS[-6][2],
    "filling_form": "The visitor is filling in the quote form: the hello waits until they stop for two minutes.",
}

# The step clock (the panel's stepClock): per visitor, their steps as this process last saw them, and since when.
_steps: dict[str, tuple[str, float]] = {}
# The agents whose keys were read lately: id -> (row, when read).
_agents: dict[str, tuple[dict, float]] = {}


class BotRefused(Exception):
    """A key or a call the bot's endpoints refuse, with the status and the code they answer."""

    def __init__(self, status: int, code: str, detail: str):
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


# ── the key ─────────────────────────────────────────────────────────


def _secret() -> str:
    from config.settings import settings
    return (settings.jwt_secret or "").strip()


def _may_have_a_bot(row: Optional[dict]) -> bool:
    """Who may connect a bot: whoever may say hello from the panel (the sales line, taking chats)."""
    from app.services.agent_presence import _may_take_chats

    if not isinstance(row, dict):
        return False
    return live_visitors.may_say_hello(row) and _may_take_chats(row)


async def issue_key(user: dict) -> dict:
    """A new key for the agent's bot, replacing the one before. {"key", "expires_at"}: the key is shown once."""
    import jwt
    from app.db import supabase as db

    if not live_visitors.hello_enabled():
        raise BotRefused(404, "off", "Saying hello first is switched off.")
    row = await db.get_user_by_id(str(user.get("id") or ""))
    if not _may_have_a_bot(row):
        raise BotRefused(403, "not_for_you", "A bot is for agents of the sales line who take chats.")
    if not _secret():
        raise BotRefused(503, "not_configured", "Keys can't be made here (JWT not configured).")
    r = get_redis()
    if r is None:
        raise BotRefused(503, "redis", "Couldn't reach the live service. Try again.")
    now = int(time.time())
    key_id = uuid.uuid4().hex
    agent_id = str(row["id"])
    try:
        await r.set(KEY_REDIS + agent_id, key_id, ex=KEY_TTL_S)
    except Exception as e:
        redis_failed("live bot key", e)
        logger.error(f"[live-bot] a key for {agent_id} not made: {type(e).__name__}: {e}")
        raise BotRefused(503, "redis", "Couldn't make the key. Try again.") from e
    key = jwt.encode({"sub": agent_id, "scope": KEY_SCOPE, "jti": key_id,
                      "token_version": int(row.get("token_version") or 0), "iat": now, "exp": now + KEY_TTL_S},
                     _secret(), algorithm="HS256")
    _agents.pop(agent_id, None)
    BOT_HEALTH["keys_issued"] += 1
    await db.log_audit(agent_id, user.get("email"), "live_bot_key_issued", "users", agent_id, {"key_id": key_id})
    expires = datetime.fromtimestamp(now + KEY_TTL_S, tz=timezone.utc).isoformat()
    return {"key": key, "expires_at": expires}


async def key_status(user: dict) -> dict:
    """Whether the agent has a key now, and until when: {"active", "expires_at"}."""
    r = get_redis()
    if r is None:
        raise BotRefused(503, "redis", "Couldn't reach the live service. Try again.")
    ttl = -2
    try:
        ttl = int(await r.ttl(KEY_REDIS + str(user.get("id") or "")))
    except Exception as e:
        redis_failed("live bot key", e)
        logger.error(f"[live-bot] a key's state not read: {type(e).__name__}: {e}")
        raise BotRefused(503, "redis", "Couldn't read the key. Try again.") from e
    if ttl < 0:
        return {"active": False, "expires_at": None}
    return {"active": True, "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()}


async def revoke_key(user: dict) -> dict:
    """The agent's key stops working at once: {"revoked": whether there was one}."""
    from app.db import supabase as db

    r = get_redis()
    if r is None:
        raise BotRefused(503, "redis", "Couldn't reach the live service. Try again.")
    agent_id = str(user.get("id") or "")
    gone = 0
    try:
        gone = int(await r.delete(KEY_REDIS + agent_id))
    except Exception as e:
        redis_failed("live bot key", e)
        logger.error(f"[live-bot] the key of {agent_id} not revoked: {type(e).__name__}: {e}")
        raise BotRefused(503, "redis", "Couldn't revoke the key. Try again.") from e
    _agents.pop(agent_id, None)
    if gone:
        BOT_HEALTH["keys_revoked"] += 1
        await db.log_audit(agent_id, user.get("email"), "live_bot_key_revoked", "users", agent_id, {})
    return {"revoked": bool(gone)}


def _refuse_key(code: str, detail: str) -> BotRefused:
    BOT_HEALTH["key_refused"] += 1
    return BotRefused(401, code, detail)


async def _agent_row(agent_id: str) -> Optional[dict]:
    from app.db import supabase as db

    cached = _agents.get(agent_id)
    if cached and time.monotonic() - cached[1] < AGENT_CACHE_S:
        return cached[0]
    row = await db.get_user_by_id(agent_id)
    if isinstance(row, dict):
        _agents[agent_id] = (row, time.monotonic())
    return row


async def agent_for(authorization: Optional[str]) -> dict:
    """The agent a bot's call works as, from its `Authorization: Bearer <key>`: {"id", "email", "role",
    "tunnel_scope", "name", "first_name"}. Raises BotRefused: 401 for a key that isn't one (any more), 403 for an agent
    who may no longer say hello, 503 when the key or the agent can't be read."""
    import jwt

    from app.security.ban_store import _MEM_REVOKED

    secret = _secret()
    if not secret:
        raise BotRefused(503, "not_configured", "Bot keys aren't configured here.")
    raw = (authorization or "").strip()
    if not raw.startswith("Bearer "):
        raise _refuse_key("key", "Send the bot's key as Authorization: Bearer <key>.")
    claims: dict = {}
    try:
        claims = jwt.decode(raw[7:].strip(), secret, algorithms=["HS256"],
                            options={"require": ["sub", "jti", "iat", "exp"]})
    except jwt.ExpiredSignatureError as e:
        raise _refuse_key("key_expired", "The key ran out: take a new one in the panel.") from e
    except jwt.InvalidTokenError as e:
        raise _refuse_key("key", "Not a bot key.") from e
    if claims.get("scope") != KEY_SCOPE:
        raise _refuse_key("key", "Not a bot key.")
    agent_id, key_id = str(claims["sub"]), str(claims["jti"])
    r = get_redis()
    if r is None:
        raise BotRefused(503, "redis", "Couldn't reach the live service. Try again.")
    newest, revoked = None, None
    try:
        pipe = r.pipeline(transaction=False)
        pipe.get(KEY_REDIS + agent_id)
        pipe.get(f"revoked_before:{agent_id}")
        newest, revoked = await pipe.execute()
    except Exception as e:
        redis_failed("live bot key", e)
        logger.error(f"[live-bot] a key not checked: {type(e).__name__}: {e}")
        raise BotRefused(503, "redis", "Couldn't check the key. Try again.") from e
    if newest != key_id:
        raise _refuse_key("key_revoked", "The key was revoked or replaced: take a new one in the panel.")
    marks = [m for m in (_MEM_REVOKED.get(agent_id), _int(revoked)) if m]
    if marks and int(claims["iat"]) < max(marks):
        raise _refuse_key("key_revoked", "The agent's sessions were ended after this key was made: take a new one.")
    row = await _agent_row(agent_id)
    if row is None:
        BOT_HEALTH["agent_unreadable"] += 1
        logger.error(f"[live-bot] the agent {agent_id} of a bot key not read")
        raise BotRefused(503, "agent_unreadable", "Couldn't read the agent. Try again.")
    if int(row.get("token_version") or 0) != int(claims.get("token_version") or 0):
        raise _refuse_key("key_revoked", "The agent's role or access changed after this key was made: take a new one.")
    if not _may_have_a_bot(row):
        BOT_HEALTH["key_refused"] += 1
        raise BotRefused(403, "not_for_you", "The agent may not say hello or take chats now.")
    return {"id": agent_id, "email": row.get("email") or "", "role": row.get("role") or "",
            "tunnel_scope": row.get("tunnel_scope") or row.get("role") or "", "name": row.get("name") or "",
            "avatar_url": row.get("avatar_url"), "first_name": live_visitors.first_name(row.get("name"))}


# ── the list, as bbc-live gives it ──────────────────────────────────


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):  # noqa: silent — a field bbc-live left empty or unreadable reads as 0, as its Go does
        return 0


def read_listed(raw: Any, now: int) -> list[dict]:
    """list.lua's answer (four entries a visitor) as the visitors bbc-live's GET /v1/live lists, under the same names
    and in its order, the longest on the site first (bbc-live internal/live/store.go Live; the panel's LiveVisitor).
    Raises ValueError for an answer of another shape."""
    if not isinstance(raw, list) or len(raw) % 4:
        raise ValueError(f"list reply of {len(raw) if isinstance(raw, list) else type(raw).__name__} entries")
    out: list[dict] = []
    for i in range(0, len(raw), 4):
        vid, fields, holder, dismissed = raw[i], raw[i + 1], raw[i + 2], raw[i + 3]
        if not isinstance(fields, list):
            continue
        h = {str(fields[j]): str(fields[j + 1]) for j in range(0, len(fields) - 1, 2)}
        first = _int(h.get("first_at"))
        v: dict = {
            "visitor_id": str(vid), "first_at": first, "last_at": _int(h.get("last_at")),
            "seconds_on_site": max(now - first, 0) if first else 0,
            "page": h.get("page", ""), "page_type": h.get("page_type") or None, "source": h.get("source", ""),
            "device": h.get("device") or None, "site": h.get("site") or None,
            "chat_open": h.get("chat_open") == "1", "has_chat": h.get("has_chat") == "1",
            "claimed_by": str(holder or ""), "dismissed": _int(dismissed) == 1,
            "tz": h.get("tz") or None, "returning": h.get("returning") == "1",
            "activity": [s for s in (h.get("activity") or "").split(";") if s],
            "trip": None, "alex_at": _int(h.get("alex_at")) or None,
            "alex_card": None, "alex_card_s": None, "alex_trigger": None,
        }
        trip: dict = {k: h[k] for k in ("from", "to", "depart", "return", "cabin") if h.get(k)}
        if _int(h.get("pax")):
            trip["pax"] = _int(h.get("pax"))
        v["trip"] = trip or None
        card_at = _int(h.get("alex_card_at"))
        if card_at > 0 and h.get("alex_card") in _ALEX_CARDS:
            v["alex_card"], v["alex_card_s"] = h["alex_card"], max(now - card_at, 0)
            v["alex_trigger"] = h.get("alex_trigger") or None
        out.append(v)
    out.sort(key=lambda row: row["first_at"])
    return out


async def _listed(r) -> list[dict]:
    now = int(time.time())
    raw = await r.eval(LIST_LUA, 1, "live:visitors", str(now - LIVE_WINDOW_S), str(LIST_LIMIT))
    return read_listed(raw, now)


async def _marks(r, rows: list[dict]) -> None:
    """On each row, whether a hello went to them lately (`said`, live:said: one a visitor while it is on the screen) and
    whether a bot's did today (`bot_said`, live:bot:): what bot_send.lua refuses, so the list says it before a call does
    (CodeRabbit #287)."""
    if not rows:
        return
    pipe = r.pipeline(transaction=False)
    for v in rows:
        pipe.exists(f"live:said:{v['visitor_id']}")
        pipe.exists(f"live:bot:{v['visitor_id']}")
    marks = await pipe.execute()
    for i, v in enumerate(rows):
        v["said"], v["bot_said"] = bool(marks[2 * i]), bool(marks[2 * i + 1])


async def _one(r, vid: str) -> Optional[dict]:
    """One visitor as the list shows them, with who holds them, their "not now" and the hellos that went to them."""
    pipe = r.pipeline(transaction=False)
    pipe.hgetall(f"live:v:{vid}")
    pipe.get(f"live:claim:{vid}")
    pipe.exists(f"live:dismissed:{vid}")
    fields, holder, dismissed = await pipe.execute()
    if not fields:
        return None
    flat = [x for kv in fields.items() for x in kv]
    v = read_listed([vid, flat, holder or "", dismissed], int(time.time()))[0]
    await _marks(r, [v])
    return v


# ── who the bot may greet: whom the panel lists for the agent and lets them greet ───────────


def _kinds(v: dict) -> list[str]:
    return [str(s).split(":")[0] for s in (v.get("activity") or [])]


def note_steps(rows: list[dict], now: float, *, whole: bool = True) -> None:
    """The step clock after a list (the panel's stepClock): steps that changed start it again, the same steps keep
    their time. `whole`: the rows are the whole list, so a visitor no longer in it is forgotten; otherwise only these
    rows are noted (one visitor, before a hello)."""
    global _steps
    fresh: dict[str, tuple[str, float]] = {} if whole else dict(_steps)
    for v in rows:
        line = ";".join(v.get("activity") or [])
        was = _steps.get(v["visitor_id"])
        fresh[v["visitor_id"]] = was if was is not None and was[0] == line else (line, now)
    _steps = fresh


def filling_form(v: dict, now: float, steps: dict) -> bool:
    """In the middle of the site's quote form (the panel's fillingForm): the newest step opened it or moved on in it,
    seen come less than FORM_QUIET_S ago. Steps the clock hasn't seen yet just came."""
    kinds = _kinds(v)
    if not kinds or kinds[-1] not in ("quote_opened", "quote_step"):
        return False
    seen = steps.get(v["visitor_id"])
    return seen is None or seen[0] != ";".join(v.get("activity") or []) or now - seen[1] < FORM_QUIET_S


def alex_couldnt(v: dict) -> bool:
    """The panel's alexCouldnt: Skye's card didn't start a chat with them: "Not now" to it, or no answer for
    ALEX_GAVE_UP_S."""
    if v.get("has_chat"):
        return False
    if v.get("alex_card") == "dismissed":
        return True
    return v.get("alex_card") == "shown" and (v.get("alex_card_s") or 0) >= ALEX_GAVE_UP_S


def alex_waiting(v: dict, now_s: float) -> bool:
    """The panel's alexWaiting: the agent-first test's card still waits for an agent's hello."""
    return bool(v.get("alex_at")) and v["alex_at"] > now_s


def state_of(v: dict, agent_id: str) -> str:
    """Who the visitor is to the agent (the panel's rowState): in_chat, declined, my_bot, bot, mine, taken, free."""
    holder = v.get("claimed_by") or ""
    if v.get("has_chat"):
        return "in_chat"
    if v.get("dismissed"):
        return "declined"
    if holder.startswith(HOLDER):
        return "my_bot" if holder == HOLDER + agent_id else "bot"
    if holder:
        return "mine" if holder == agent_id else "taken"
    return "free"


def listed_for(v: dict, agent_id: str, now_s: float) -> bool:
    """The panel's list for this agent: whom Skye's card didn't start a chat with, whom the card still waits on, and the
    ones the agent or their bot holds."""
    holder = v.get("claimed_by") or ""
    return (alex_couldnt(v) or (alex_waiting(v, now_s) and not v.get("has_chat"))
            or holder in (agent_id, HOLDER + agent_id))


def why_not(v: dict, agent_id: str, now: float, steps: dict) -> Optional[str]:
    """Why the agent's bot may not greet this visitor now (a _WHY_NOT key), or None when it may: a visitor the panel
    lists for the agent, nobody holding them but the agent themself, not in a chat, no "not now", not in the middle of
    the quote form (the owner's rules for a hello from the panel)."""
    from app.services import agent_first_test

    if agent_first_test.side(v.get("visitor_id")) == agent_first_test.ALEX:
        return "test_alex"
    if not listed_for(v, agent_id, now):
        return "not_listed"
    state = state_of(v, agent_id)
    if state in ("in_chat", "declined"):
        return state
    if state == "my_bot":
        return "already"
    if state not in ("free", "mine"):
        return "taken"
    if v.get("bot_said"):
        return "bot_already"
    if v.get("said"):
        return "already"
    if filling_form(v, now, steps):
        return "filling_form"
    return None


# ── the words: the panel's ready hello, in the agent's name (bbc-admin-app src/features/live/view.ts) ─────────


def _day(iso: Optional[str]) -> str:
    """"Dec 20" (the panel's day(): en-US, the short month and the day's number); "" for no day."""
    try:
        d = datetime.strptime(iso or "", "%Y-%m-%d")
    except ValueError:  # noqa: silent — no day on the page: the hello names none, as the panel's does
        return ""
    return f"{_MONTHS[d.month - 1]} {d.day}"


def anz_zone(tz: Optional[str]) -> bool:
    return bool(tz) and (str(tz).startswith("Australia/") or tz in ("Pacific/Auckland", "Pacific/Chatham", "NZ", "NZ-CHAT"))


def days_to_flight(depart: Optional[str], now_ms: int) -> Optional[int]:
    """Whole days from today to the page's departure day, by the calendar (UTC days); None without a day."""
    try:
        day = datetime.strptime(depart or "", "%Y-%m-%d").date()
    except ValueError:  # noqa: silent — no departure day on the page: no "soon" group, as in the panel
        return None
    today = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).date()
    return (day - today).days


def stuck_in_form(v: dict) -> bool:
    quote = [k for k in _kinds(v) if k.startswith("quote_")]
    return bool(quote) and quote[-1] in ("quote_opened", "quote_step", "quote_invalid", "quote_failed")


def group_of(v: dict, now_ms: int) -> Optional[str]:
    """The panel's groupOf: stuck in the form, Australia or New Zealand on a computer, flying in 15 to 45 days on a
    computer, came back; None for the rest."""
    if stuck_in_form(v):
        return "form"
    computer = v.get("device") == "computer"
    if computer and anz_zone(v.get("tz")):
        return "anz"
    days = days_to_flight((v.get("trip") or {}).get("depart"), now_ms)
    if computer and days is not None and 15 <= days <= 45:
        return "soon"
    if v.get("returning") is True:
        return "back"
    return None


def _who(name: str) -> str:
    return f"Hi, I'm {name} from the travel team." if name else "Hi, I am from the travel team."


def group_hello(group: str, v: dict, name: str) -> str:
    """The panel's groupHello, word for word."""
    who = _who(name)
    trip = v.get("trip") or {}
    to = trip.get("to")
    if group == "form":
        quote = [k for k in _kinds(v) if k.startswith("quote_")]
        if quote and quote[-1] == "quote_failed":
            return f"{who} Did the quote form give you trouble? I can take your request right here."
        return f"{who} Need a hand with the quote form? I can take your trip details right here, it only takes a minute."
    if group == "anz":
        if to:
            return f"{who} Planning the long trip to {to}? I can find business class options for your dates and send them right here."
        return f"{who} Planning a long trip? I can find business class options for your dates and send them right here."
    if group == "soon":
        when = _day(trip.get("depart"))
        return (f"{who} Flying{f' to {to}' if to else ''}{f' on {when}' if when else ''}? A good moment to book "
                "business class. Want me to check options for your dates?")
    return f"{who} Welcome back! Want me to pick up your search{f' for {to}' if to else ''} and check business class options for you?"


def suggested_hello(v: dict, name: str) -> str:
    """The panel's suggestedHello, word for word: what the page says and what the visitor just did."""
    who = _who(name)
    known = [k for k in _kinds(v) if k in _STEP_KINDS]
    last = known[-1] if known else ""
    if last == "quote_failed":
        return f"{who} Did the quote form give you trouble? I can take your request right here."
    if last == "quote_invalid":
        return f"{who} Need a hand with the quote form? I can take your trip details right here."
    if last == "quote_submitted" or v.get("page_type") == "thank_you":
        return f"{who} Thanks for your request. Anything you'd like me to know while I prepare your options?"
    to = (v.get("trip") or {}).get("to")
    if to:
        return f"{who} Looking at flights to {to}? I can check business class fares for your dates."
    return f"{who} Can I help you find a business class fare?"


def ready_hello(v: dict, now_ms: int, name: str) -> tuple[str, Optional[str]]:
    """The hello the panel has ready for this agent and its group (None when the page's words are used: helloDraft)."""
    group = group_of(v, now_ms)
    return (group_hello(group, v, name) if group else suggested_hello(v, name)), group


def _words(text: str, most: int) -> str:
    """The bot's words held to what a client may read from it: 1 to `most` characters, no contact or link, no price,
    no long dash (the owner's rules for a bot)."""
    from app.ai.output_guard import foreign_contact_kinds, has_price, no_long_dash

    words = live_visitors.clean_text(no_long_dash(text) or "")
    if not words or len(words) > most:
        raise BotRefused(422, "length", f"Write 1 to {most} characters.")
    if foreign_contact_kinds(words):
        raise BotRefused(422, "contact", "No phone number, e-mail or link from a bot: the agent gives them.")
    if has_price(words):
        raise BotRefused(422, "price", "No price from a bot: the agent names one.")
    return words


# ── what the bot does ───────────────────────────────────────────────


async def visitors(agent: dict) -> list[dict]:
    """The visitors the panel lists for the agent now, each with whom they are to the agent (`state`), whether the bot
    may greet them now (`may_greet`, `why_not`) and the hello the panel has ready in the agent's name."""
    if not live_visitors.hello_enabled():
        raise BotRefused(404, "off", "Saying hello first is switched off.")
    r = get_redis()
    if r is None:
        raise BotRefused(503, "redis", "Couldn't reach the live service. Try again.")
    rows: list[dict] = []
    mine: list[dict] = []
    now = time.time()
    try:
        rows = await _listed(r)
        mine = [v for v in rows if listed_for(v, agent["id"], now)]
        await _marks(r, mine)
    except Exception as e:
        redis_failed("live bot list", e)
        BOT_HEALTH["list_failed"] += 1
        logger.error(f"[live-bot] the list was not read: {type(e).__name__}: {e}")
        raise BotRefused(503, "redis", "Couldn't read who is on the site. Try again.") from e
    note_steps(rows, now)
    out: list[dict] = []
    for v in mine:
        reason = why_not(v, agent["id"], now, _steps)
        if reason == "test_alex":
            continue
        words, group = ready_hello(v, int(now * 1000), agent["first_name"])
        out.append({
            "visitor_id": v["visitor_id"], "state": state_of(v, agent["id"]), "may_greet": reason is None,
            "why_not": reason, "group": group, "ready_hello": words, "seconds_on_site": v["seconds_on_site"],
            "site": v["site"], "device": v["device"], "source": v["source"], "page": v["page"],
            "page_type": v["page_type"], "trip": v["trip"], "activity": v["activity"], "returning": v["returning"],
            "tz": v["tz"], "alex_card": v["alex_card"], "alex_card_s": v["alex_card_s"],
        })
    return out


async def greet(agent: dict, visitor_id: str, *, text: Optional[str] = None, group: Optional[str] = None) -> dict:
    """The bot's hello to one visitor, in the agent's name: `text`, or the hello the panel has ready (with its group).
    Raises BotRefused: why_not's codes, bot_send.lua's (_REFUSALS), the words' own; every hello sent, refused or lost
    is counted in BOT_HEALTH."""
    from app.db import supabase as db

    if not live_visitors.hello_enabled():
        raise BotRefused(404, "off", "Saying hello first is switched off.")
    if not live_visitors.VISITOR_ID.fullmatch(visitor_id or ""):
        raise BotRefused(400, "visitor", "Unknown visitor.")
    words = ""
    kept_group: Optional[str] = None
    if text is not None:
        try:
            words = _words(text, live_visitors.MAX_TEXT)
        except BotRefused:
            BOT_HEALTH["refused"] += 1
            raise
        kept_group = group if group in live_visitors.HELLO_GROUPS else None
    r = get_redis()
    if r is None:
        BOT_HEALTH["send_failed"] += 1
        raise BotRefused(503, "redis", "Couldn't reach the live service. Try again.")
    agent_id = agent["id"]
    hello_id = str(uuid.uuid4())
    message: dict = {}
    result = 0
    try:
        v = await _one(r, visitor_id)
        if v is None:
            raise BotRefused(*_REFUSALS[-1])
        now = time.time()
        note_steps([v], now, whole=False)
        reason = why_not(v, agent_id, now, _steps)
        if reason:
            raise BotRefused(409, reason, _WHY_NOT[reason])
        if text is None:
            words, kept_group = ready_hello(v, int(now * 1000), agent["first_name"])
        tap = live_tokens.tap_token(visitor_id, agent_id, hello_id)
        if not tap:
            BOT_HEALTH["send_failed"] += 1
            raise BotRefused(503, "not_configured", "The live service isn't configured.")
        sent = datetime.now(timezone.utc)
        expires = sent + timedelta(seconds=live_visitors.HELLO_TTL)
        # The widget's message is an agent's (contracts/live_inbox_message.json): the visitor sees the agent.
        message = {"id": hello_id, "kind": "hello", "agent": agent["first_name"], "text": words,
                   "sent_at": sent.isoformat(), "expires_at": expires.isoformat(), "tap": tap}
        hello: dict = {"id": hello_id, "visitor_id": visitor_id, "agent_id": agent_id, "text": words,
                       "sent_at": sent.isoformat(), "bot": True}
        if kept_group:
            hello["group"] = kept_group
        keys = [f"live:v:{visitor_id}", f"live:claim:{visitor_id}", f"live:dismissed:{visitor_id}",
                f"live:inbox:{visitor_id}", f"live:hello:{hello_id}", f"live:said:{visitor_id}", f"live:bot:{visitor_id}"]
        # ARGV[7]: a visitor the agent holds from the panel is theirs to greet by their bot too.
        result = int(await r.eval(BOT_SEND_LUA, len(keys), *keys, HOLDER + agent_id, visitor_id,
                                  json.dumps(message, separators=(",", ":")), json.dumps(hello, separators=(",", ":")),
                                  str(live_visitors.HELLO_TTL), str(ONCE_S), agent_id))
    except BotRefused as e:
        if e.status < 500:
            BOT_HEALTH["refused"] += 1
        raise
    except Exception as e:
        redis_failed("live bot send", e)
        BOT_HEALTH["send_failed"] += 1
        logger.error(f"[live-bot] hello to {visitor_id} not sent: {type(e).__name__}: {e}")
        raise BotRefused(503, "redis", "Couldn't send the hello. Try again.") from e
    if result != 1:
        BOT_HEALTH["refused"] += 1
        raise BotRefused(*_REFUSALS.get(result, (409, "refused", "The hello wasn't sent.")))
    BOT_HEALTH["sent"] += 1
    from app.services import agent_first_test
    await agent_first_test.note_hello(visitor_id)  # the owner's test: the agent's hello, by their bot
    await db.log_audit(agent_id, agent.get("email"), "live_hello_sent", "live_visitors", visitor_id,
                       {"hello_id": hello_id, "text": words, "length": len(words), "group": kept_group, "bot": True})
    return {"hello_id": hello_id, "text": words, "group": kept_group, "sent_at": message["sent_at"],
            "expires_at": message["expires_at"]}


def _bots_chat(conv: Optional[dict], agent_id: str) -> bool:
    """A chat the bot may read and answer: started by its hello, still the agent's, in human mode, open."""
    from app.db.supabase import OPEN_STATUSES

    if not isinstance(conv, dict):
        return False
    hello = (conv.get("metadata") or {}).get("live_hello") or {}
    return (isinstance(hello, dict) and hello.get("bot") is True and conv.get("assigned_agent_id") == agent_id
            and conv.get("mode") == "human" and conv.get("status") in OPEN_STATUSES)


async def chats(agent: dict) -> list[dict]:
    """The chats the bot's hellos started that are still the agent's, newest first, with whether the visitor wrote
    last (`waiting`)."""
    from app.db import supabase as db

    rows = await db.list_live_bot_chats(agent["id"], CHATS_LIMIT)
    if rows is None:
        BOT_HEALTH["read_failed"] += 1
        raise BotRefused(503, "db", "Couldn't read the chats. Try again.")
    out: list[dict] = []
    for c in rows:
        hello = c.get("live_hello") if isinstance(c.get("live_hello"), dict) else {}
        user_at, agent_at = c.get("last_user_message_at"), c.get("last_agent_message_at")
        out.append({"conversation_id": c.get("id"), "visitor_id": c.get("visitor_id"), "status": c.get("status"),
                    "hello_id": hello.get("id"), "started_at": hello.get("tapped_at") or c.get("created_at"),
                    "updated_at": c.get("updated_at"), "last_visitor_at": user_at, "last_agent_at": agent_at,
                    "waiting": bool(user_at) and (not agent_at or str(user_at) > str(agent_at))})
    return out


async def _the_chat(agent: dict, conversation_id: str) -> dict:
    from app.db import supabase as db

    conv = await db.get_conversation_simple(conversation_id)
    if conv is None:
        BOT_HEALTH["read_failed"] += 1
        raise BotRefused(404, "chat", "No such chat, or it couldn't be read.")
    if not _bots_chat(conv, agent["id"]):
        raise BotRefused(403, "not_the_bots", "Not a chat this bot's hello started, or no longer the agent's.")
    return conv


async def messages(agent: dict, conversation_id: str, after: Optional[str] = None) -> list[dict]:
    """The chat's messages, after `after` (an ISO time) when given, oldest first: {"id", "role" (visitor, agent, ai,
    system), "text", "at"}."""
    from app.db import supabase as db

    since: Optional[str] = None
    if after:
        try:
            since = datetime.fromisoformat(after.strip().replace("Z", "+00:00")).isoformat()
        except ValueError as e:
            raise BotRefused(422, "after", "`after` is an ISO time, as `at` gives it.") from e
    await _the_chat(agent, conversation_id)
    rows = await db.get_messages_after(conversation_id, since)
    return [{"id": m.get("id"), "role": _ROLES.get(str(m.get("role")), str(m.get("role"))),
             "text": m.get("content") or "", "at": m.get("created_at")} for m in rows if isinstance(m, dict)]


async def say(agent: dict, conversation_id: str, text: str) -> dict:
    """The bot's answer in its chat, as the agent's own message from the panel (the same send: the widget, the
    response clock, the follow-up task): {"id", "text", "at"}."""
    from app.api.conversations import AgentMessageBody, send_agent_message

    await _the_chat(agent, conversation_id)
    try:
        words = _words(text, MAX_MESSAGE)
    except BotRefused:
        BOT_HEALTH["messages_refused"] += 1
        raise
    from fastapi import HTTPException

    user = {k: agent[k] for k in ("id", "email", "role", "tunnel_scope", "name")}
    out: Any = None
    try:
        out = await send_agent_message(conversation_id, AgentMessageBody(content=words), user=user)
    except HTTPException as e:
        BOT_HEALTH["messages_refused"] += 1
        raise BotRefused(e.status_code, "refused", str(e.detail)) from e
    msg = (out or {}).get("data") if isinstance(out, dict) else None
    if not isinstance(msg, dict):
        BOT_HEALTH["send_failed"] += 1
        logger.error(f"[live-bot] conv {conversation_id}: the bot's answer was not stored")
        raise BotRefused(503, "not_stored", "The answer wasn't stored. Try again.")
    BOT_HEALTH["messages_sent"] += 1
    return {"id": msg.get("id"), "text": msg.get("content") or words, "at": msg.get("created_at")}
