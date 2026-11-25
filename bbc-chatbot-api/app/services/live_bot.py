"""Skye's own hello in "On the site now" (the owner, 25 Nov 2026: "botul singur va apăsa ca din partea de agent și va
lucra ca să facă leaduri"). It does by itself what an agent does there: the ready-written hello to a visitor Skye's
card didn't start a chat with; the visitor's answer starts a chat that is Skye's, and she works for the contact as in
any of her chats.

- Who (the owner: only who ignored the card): Skye's card showed and got no answer for CARD_GAVE_UP_S (the panel lists
  them from then on), no chat, no "not now" to a hello, nobody holds them, and they have not sent the quote form or
  reached out. Not on a thank-you or a legal page, and not while they move through the site's quote form (the panel's
  rule since 25 Nov: a step seen less than FORM_QUIET_S ago). Never the agent-first test's Alex side: it stays the
  comparison (it is not listed either).
- What (the owner: the ready-written hello): the group's, as the panel has it ready for an agent, or the page's
  (bbc-admin-app src/features/live/view.ts groupOf, groupHello and suggestedHello, said as Skye). POST
  /api/live/bot/hello may name its own words, held to the client's rules.
- When (the owner: at once, agents online or not): every RUN_EVERY_S in the scheduler, while LIVE_BOT is on (app.flags,
  off at merge). One of Skye's hellos a visitor a day (ONCE_S), one hello of anyone's while it is on the screen.
- Then: the tap starts the chat with the hello as Skye's first message, in AI mode (start_with_skye); agents take it
  from the queue like any of her chats.

The script (contracts/live/bot_send.lua) checks again and sends in one step: the run and the endpoint never send
twice, and an agent who claims the visitor first keeps them. The hello holds the visitor as Skye for its life, so the
panel says so and no agent claims them meanwhile. Nothing here logs what a visitor typed; the hello's words are Skye's.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from app.flags import flag
from app.infra.redis_client import get_redis, redis_failed
from app.services import live_tokens, live_visitors
from app.services.live_visitors import SendRefused

logger = logging.getLogger(__name__)

BOT_ID = "skye"           # the holder and sender in bbc-live's keys; an agent's is a user id (a UUID), never this
BOT_NAME = "Skye"         # proactive.PERSONA, the name the widget shows over her messages
MODEL_USED = "live_bot"   # Skye's hello stored in the chat: words ready-written, no model call
CARD_GAVE_UP_S = 30       # no answer to Skye's card this long: the panel lists the visitor (view.ts ALEX_GAVE_UP_S)
FORM_QUIET_S = 120        # the panel's FORM_QUIET_S: a step in the quote form holds the hello this long
ONCE_S = 86_400           # one of Skye's hellos a visitor this long
RUN_EVERY_S = 5
PER_RUN = 20              # at most this many hellos a run
LIVE_WINDOW_S = 45        # bbc-live's Window: a beat in the last 45 s is on the site now
LIST_LIMIT = 500          # bbc-live's ListLimit

_STOP_PAGES = frozenset({"thank_you", "legal"})
# The steps the panel has words for (view.ts stepText); an unknown one is no step.
_STEP_KINDS = frozenset({"search", "quote_opened", "quote_step", "quote_invalid", "quote_submitting", "quote_failed",
                         "quote_submitted", "contact"})
_ALEX_CARDS = frozenset({"shown", "dismissed", "engaged"})  # bbc-live's alexCards
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

BOT_HEALTH: dict = {"runs": 0, "run_failed": 0, "sent": 0, "refused": 0, "send_failed": 0, "key_refused": 0,
                    "taps": 0, "chats": 0, "start_failed": 0}

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
    -6: (409, "skye_already", "Skye said hello to this visitor today."),
}
# Why Skye greets nobody now (why_not), in words for POST /api/live/bot/hello.
_WHY_NOT = {
    "test_alex": "The visitor is on the agent-first test's Alex side: nobody greets them.",
    "card": f"Skye's card hasn't gone unanswered {CARD_GAVE_UP_S} s for this visitor.",
    "in_a_chat": _REFUSALS[-2][2],
    "declined": _REFUSALS[-3][2],
    "taken": _REFUSALS[-4][2],
    "page": "The visitor is on a thank-you or a legal page.",
    "reached_out": "The visitor sent the quote form or clicked a contact link.",
    "filling_form": "The visitor is filling in the quote form.",
}

# The step clock (the panel's stepClock): per visitor, their steps as this process last saw them, and since when.
_steps: dict[str, tuple[str, float]] = {}


def run_enabled() -> bool:
    """The scheduler's run: the live list and its hellos on, and LIVE_BOT (app.flags, off at merge)."""
    return live_visitors.hello_enabled() and bool(flag("LIVE_BOT", False))


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
        v: dict = {
            "visitor_id": str(vid), "first_at": _int(h.get("first_at")), "last_at": _int(h.get("last_at")),
            "page": h.get("page", ""), "page_type": h.get("page_type") or None, "source": h.get("source", ""),
            "device": h.get("device") or None, "site": h.get("site") or None,
            "chat_open": h.get("chat_open") == "1", "has_chat": h.get("has_chat") == "1",
            "claimed_by": str(holder or ""), "dismissed": _int(dismissed) == 1,
            "tz": h.get("tz") or None, "returning": h.get("returning") == "1",
            "activity": [s for s in (h.get("activity") or "").split(";") if s],
            "trip": None, "alex_card": None, "alex_card_s": None, "alex_trigger": None,
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


async def _one(r, vid: str) -> Optional[dict]:
    """One visitor's visit as the list shows it; who holds them and their "not now" are the script's to check."""
    fields = await r.hgetall(f"live:v:{vid}")
    if not fields:
        return None
    flat = [x for kv in fields.items() for x in kv]
    return read_listed([vid, flat, "", 0], int(time.time()))[0]


# ── who Skye says hello to ──────────────────────────────────────────


def _kinds(v: dict) -> list[str]:
    return [str(s).split(":")[0] for s in (v.get("activity") or [])]


def note_steps(rows: list[dict], now: float, *, whole: bool = True) -> None:
    """The step clock after a list (the panel's stepClock): steps that changed start it again, the same steps keep
    their time. `whole`: the rows are the whole list, so a visitor no longer in it is forgotten; otherwise only these
    rows are noted (POST /api/live/bot/hello, one visitor)."""
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


def why_not(v: dict, now: float, steps: dict) -> Optional[str]:
    """Why Skye says no hello to this visitor now (a _WHY_NOT key), or None when she may (the owner's who, above)."""
    from app.services import agent_first_test

    if agent_first_test.side(v.get("visitor_id")) == agent_first_test.ALEX:
        return "test_alex"
    if v.get("alex_card") != "shown" or (v.get("alex_card_s") or 0) < CARD_GAVE_UP_S:
        return "card"
    if v.get("has_chat"):
        return "in_a_chat"
    if v.get("dismissed"):
        return "declined"
    if v.get("claimed_by"):
        return "taken"
    if v.get("page_type") in _STOP_PAGES:
        return "page"
    kinds = _kinds(v)
    if "quote_submitted" in kinds or "contact" in kinds:
        return "reached_out"
    if (kinds and kinds[-1] == "quote_submitting") or filling_form(v, now, steps):
        return "filling_form"
    return None


# ── the words: the panel's ready hello, said as Skye (bbc-admin-app src/features/live/view.ts) ──────────


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


def group_hello(group: str, v: dict, name: str = BOT_NAME) -> str:
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


def suggested_hello(v: dict, name: str = BOT_NAME) -> str:
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


def ready_hello(v: dict, now_ms: int) -> tuple[str, Optional[str]]:
    """The hello Skye sends and its group (None when the page's words are used: the panel's helloDraft)."""
    group = group_of(v, now_ms)
    return (group_hello(group, v) if group else suggested_hello(v)), group


# ── the hello ───────────────────────────────────────────────────────


def _words(text: str) -> str:
    """Words named by the endpoint, held to what a client may read: 1 to 300 characters, no contact or link, no price,
    no long dash (the owner's rules for Skye)."""
    from app.ai.output_guard import foreign_contact_kinds, has_price, no_long_dash

    words = live_visitors.clean_text(no_long_dash(text) or "")
    if not words or len(words) > live_visitors.MAX_TEXT:
        raise SendRefused(422, "length", f"Write 1 to {live_visitors.MAX_TEXT} characters.")
    if foreign_contact_kinds(words):
        raise SendRefused(422, "contact", "No phone number, e-mail or link in a first message: the chat carries them.")
    if has_price(words):
        raise SendRefused(422, "price", "No price in a first message: only an agent names one.")
    return words


async def greet(visitor_id: str, *, text: Optional[str] = None, group: Optional[str] = None, via: str = "run",
                visitor: Optional[dict] = None) -> dict:
    """Skye's hello to one visitor she may greet now (why_not): the ready-written one, or `text` (held to the client's
    rules) with its `group`, through bot_send.lua. `visitor`: their row from the list, when the run read it already
    (the run checked why_not on it). Raises SendRefused: the script's codes (_REFUSALS), why_not's, the words' own;
    every hello sent, refused or lost is counted in BOT_HEALTH."""
    if not live_visitors.hello_enabled():
        raise SendRefused(404, "off", "Saying hello first is switched off.")
    if not live_visitors.VISITOR_ID.fullmatch(visitor_id or ""):
        raise SendRefused(400, "visitor", "Unknown visitor.")
    words = ""
    kept_group: Optional[str] = None
    if text is not None:
        try:
            words = _words(text)
        except SendRefused:
            BOT_HEALTH["refused"] += 1
            raise
        kept_group = group if group in live_visitors.HELLO_GROUPS else None
    r = get_redis()
    if r is None:
        BOT_HEALTH["send_failed"] += 1
        raise SendRefused(503, "redis", "Couldn't reach the live service. Try again.")
    hello_id = str(uuid.uuid4())
    message: dict = {}
    result = 0
    try:
        v = visitor
        if v is None:
            v = await _one(r, visitor_id)
            if v is None:
                raise SendRefused(*_REFUSALS[-1])
            now = time.time()
            note_steps([v], now, whole=False)
            reason = why_not(v, now, _steps)
            if reason:
                raise SendRefused(409, reason, _WHY_NOT[reason])
        if text is None:
            words, kept_group = ready_hello(v, int(time.time() * 1000))
        tap = live_tokens.tap_token(visitor_id, BOT_ID, hello_id)
        if not tap:
            BOT_HEALTH["send_failed"] += 1
            raise SendRefused(503, "not_configured", "The live service isn't configured.")
        sent = datetime.now(timezone.utc)
        expires = sent + timedelta(seconds=live_visitors.HELLO_TTL)
        message = {"id": hello_id, "kind": "hello", "agent": BOT_NAME, "bot": True, "text": words,
                   "sent_at": sent.isoformat(), "expires_at": expires.isoformat(), "tap": tap}
        hello: dict = {"id": hello_id, "visitor_id": visitor_id, "agent_id": BOT_ID, "text": words,
                       "sent_at": sent.isoformat(), "bot": True}
        if kept_group:
            hello["group"] = kept_group
        keys = [f"live:v:{visitor_id}", f"live:claim:{visitor_id}", f"live:dismissed:{visitor_id}",
                f"live:inbox:{visitor_id}", f"live:hello:{hello_id}", f"live:said:{visitor_id}", f"live:bot:{visitor_id}"]
        result = int(await r.eval(BOT_SEND_LUA, len(keys), *keys, BOT_ID, visitor_id,
                                  json.dumps(message, separators=(",", ":")), json.dumps(hello, separators=(",", ":")),
                                  str(live_visitors.HELLO_TTL), str(ONCE_S)))
    except SendRefused as e:
        if e.status < 500:
            BOT_HEALTH["refused"] += 1
        raise
    except Exception as e:
        redis_failed("live bot send", e)
        BOT_HEALTH["send_failed"] += 1
        logger.error(f"[live-bot] hello to {visitor_id} not sent: {type(e).__name__}: {e}")
        raise SendRefused(503, "redis", "Couldn't send the hello. Try again.") from e
    if result != 1:
        BOT_HEALTH["refused"] += 1
        raise SendRefused(*_REFUSALS.get(result, (409, "refused", "The hello wasn't sent.")))
    BOT_HEALTH["sent"] += 1
    from app.db import supabase as db
    await db.log_audit(None, None, "live_hello_sent", "live_visitors", visitor_id,
                       {"hello_id": hello_id, "text": words, "length": len(words), "group": kept_group,
                        "bot": True, "via": via})
    return {"hello_id": hello_id, "text": words, "group": kept_group, "sent_at": message["sent_at"],
            "expires_at": message["expires_at"]}


async def candidates() -> list[dict]:
    """The visitors Skye may greet now, with the words she would send them (what POST /api/live/bot/hello sends when
    it names none). Raises SendRefused when the hello is off or the list can't be read."""
    if not live_visitors.hello_enabled():
        raise SendRefused(404, "off", "Saying hello first is switched off.")
    r = get_redis()
    if r is None:
        raise SendRefused(503, "redis", "Couldn't reach the live service. Try again.")
    rows: list[dict] = []
    try:
        rows = await _listed(r)
    except Exception as e:
        redis_failed("live bot list", e)
        logger.error(f"[live-bot] the list was not read: {type(e).__name__}: {e}")
        raise SendRefused(503, "redis", "Couldn't read who is on the site. Try again.") from e
    now = time.time()
    note_steps(rows, now)
    out: list[dict] = []
    for v in rows:
        if why_not(v, now, _steps) is not None:
            continue
        words, group = ready_hello(v, int(now * 1000))
        out.append({"visitor_id": v["visitor_id"], "group": group, "text": words, "site": v["site"],
                    "device": v["device"], "page": v["page"], "page_type": v["page_type"], "trip": v["trip"],
                    "alex_card_s": v["alex_card_s"],
                    "seconds_on_site": max(int(now) - v["first_at"], 0) if v["first_at"] else None})
    return out


async def run_live_bot() -> dict:
    """The scheduler's run, every RUN_EVERY_S: Skye's hello to each visitor she may greet now, at most PER_RUN."""
    if not run_enabled():
        return {"state": "off"}
    r = get_redis()
    if r is None:
        BOT_HEALTH["run_failed"] += 1
        logger.warning("[live-bot] no Redis: nobody greeted this run")
        return {"state": "no_redis"}
    BOT_HEALTH["runs"] += 1
    rows: list[dict] = []
    try:
        rows = await _listed(r)
    except Exception as e:
        redis_failed("live bot list", e)
        BOT_HEALTH["run_failed"] += 1
        logger.error(f"[live-bot] the list was not read: {type(e).__name__}: {e}")
        return {"state": "list_failed"}
    now = time.time()
    note_steps(rows, now)
    due = [v for v in rows if why_not(v, now, _steps) is None][:PER_RUN]
    sent = refused = failed = 0
    for v in due:
        try:
            await greet(v["visitor_id"], visitor=v, via="run")
            sent += 1
        except SendRefused as e:
            # Counted in greet. A refusal here is a race the script settled (an agent claimed them first, they
            # said not now, a chat started): the visitor no longer due.
            logger.info(f"[live-bot] {v['visitor_id']} not greeted: {e.code}")
            if e.status >= 500:
                failed += 1
            else:
                refused += 1
    return {"state": "ok", "listed": len(rows), "due": len(due), "sent": sent, "refused": refused, "failed": failed}


# ── the tap ─────────────────────────────────────────────────────────


async def start_with_skye(conv_id: str, hello: dict) -> dict:
    """/chat/init with the tap of Skye's own hello (live_visitors.start_with_the_agent hands it here): the chat stays
    hers (AI mode, nobody assigned), the hello its first message, as hers. {"agent": "Skye", "text", "at"}, or {} when
    the hello could not be stored: the chat then starts as any other, counted, never a failure for the visitor."""
    from app.db import supabase as db
    from app.services.conversation_service import add_message

    BOT_HEALTH["taps"] += 1
    row = None
    try:
        row = await add_message(conv_id, "ai", hello["text"], model_used=MODEL_USED, cost=0.0)
    except Exception as e:
        logger.error(f"[live-bot] conv {conv_id}: Skye's hello was not stored: {type(e).__name__}: {e}")
    if not isinstance(row, dict):
        BOT_HEALTH["start_failed"] += 1
        return {}
    now = datetime.now(timezone.utc).isoformat()
    tagged = await db.patch_conversation_presence(conv_id, {"live_hello": {
        "id": hello["hello_id"], "bot": True, "sent_at": hello.get("sent_at"), "tapped_at": now,
        **({"group": hello["group"]} if hello.get("group") else {}),
    }})
    if not tagged:
        logger.warning(f"[live-bot] conv {conv_id}: the chat is Skye's, but its live_hello mark was not written")
    BOT_HEALTH["chats"] += 1
    return {"agent": BOT_NAME, "text": hello["text"], "at": row.get("created_at")}
