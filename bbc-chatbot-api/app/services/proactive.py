"""The chat that opens by itself, with the visitor's own search (no form).

Two doors use this module:

- ``POST /chat/proactive/offer`` — the widget saw a visitor hesitate (Kayak
  landing after a few seconds, the quote form refused, a long read, a move to
  leave) and asks whether to show the card and what it says. Nothing is
  written: no conversation, no lead, no row. A visitor who never answers
  leaves no trace in the database (Dan's rule: no anonymous chats).
- ``POST /chat/init`` with ``proactive`` — the visitor answered the card. Only
  now is the conversation created; the card's words become the bot's first
  message and the lead starts with the trip from the page, marked as not
  confirmed by the client.

The words are composed here, from validated fields only (``TripContext``),
never taken from the widget: both doors write the same sentence, and nothing
typed into a URL reaches a prompt, a message or the panel as free text.

Switches (``app_settings.flags``, read every 30 s, no deploy):
- ``PROACTIVE_ENABLED`` (on: the owner's decision of 16 Nov) — the whole
  feature; ``false`` in ``app_settings.flags`` turns it off in 30 s;
- ``PROACTIVE_PERCENT`` (100: every visitor, the owner's decision of 16 Nov,
  over an A/B half) — the share of visitors in the proactive arm, bucketed by
  visitor id, so a visitor stays in one arm across visits; a lower value
  brings the control arm back for a comparison;
- ``PROACTIVE_TRIGGERS`` ("kayak,quote,reader,exit") — which moments may open it.

One more door is not a moment and the switches above do not hold it:
``note``, the visitor TAPPED the 10 s note beside the button ("Tell me where
and when, and an agent will check fares"). The owner's decision of 16 Nov: that tap
opens the chat with the same words and no form, for every visitor — from
13 Nov, 0 of the 35 visitors who opened the chat got past the form the note
led to. Its own switch, ``PROACTIVE_NOTE_ENABLED`` (on at merge), sends the
note back to the form in 30 s, without a deploy, if it ever must.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Literal, Optional

from app.models.trip_context import TripContext

logger = logging.getLogger(__name__)

Trigger = Literal["kayak", "quote", "reader", "exit"]
TRIGGERS: tuple[str, ...] = ("kayak", "quote", "reader", "exit")
# The tapped note (module docstring): asked for by the visitor, so in no arm.
NOTE = "note"
EXPERIMENT = "proactive_chat"
MODEL_USED = "proactive_template"

# What each card offers under its message. "Not now" never reaches the
# server: the widget closes the card and stays quiet for 24 hours. The first
# button is a yes that asks for nothing personal (ADR 14, 16 Nov debate): the
# bot asks for the email in the chat, after it, with the reason.
# It names what a person will do — send the fares, make a quote — never that
# Alex checks them: only an agent can (owner, 17 Nov). "Check fares" promised
# fares on the spot; of the 13 who tapped it with no trip known on 16-17 Nov,
# 10 left at the first question. "Get a fare quote" is the note's own button.
_REPLIES: dict[str, list[str]] = {
    "kayak": ["Yes, send me fares", "Change details", "Not now"],
    "quote": ["Yes, send me fares", "I have a question", "Not now"],
    "reader": ["Get a fare quote", "I have a question", "Not now"],
    # "Before you go: want an agent to check fares…?" — a yes answers it.
    "exit": ["Yes, send me fares", "I have a question", "Not now"],
    # The visitor asked for the chat: no "Not now" to offer them.
    "note": ["Get a fare quote", "I have a question"],
}

# Who speaks (ADR 12; owner, 16 Nov): Alex, the brand's AI Travel Agent. The
# widget's header shows the same name over "Travel Agent · <brand>", with an
# "AI" badge on his photo: that is where the visitor sees it is an AI, so the
# opening says the name and the brand only (bbc-widget/src/config, `persona`;
# keep the name equal on both sides). The brands as the widget's header writes
# them, by the validated `site` (bbc | bct).
PERSONA = "Alex"
_BRANDS: dict[str, str] = {"bbc": "BuyBusinessClass", "bct": "Business Class Tickets"}

# The cabins the leads table accepts (leads_cabin_class_check).
_LEAD_CABINS = ("business", "first", "economy")

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_CABINS = {"economy": "Economy", "premium_economy": "Premium Economy", "business": "Business", "first": "First"}

# Health of the proactive door, shown next to the other counters.
# open_failures: the opening message was not saved (the chat goes on as a
# plain one); seed_errors: the lead could not start with the page's search.
PROACTIVE_HEALTH: dict = {
    "offers": 0, "shown": 0, "opened": 0, "open_failures": 0, "seed_errors": 0, "last_seed_error_at": None,
}


# --- switches -------------------------------------------------------------

def enabled() -> bool:
    """On unless app_settings.flags (or the env) says ``false`` (owner, 16 Nov)."""
    from app.flags import flag
    return bool(flag("PROACTIVE_ENABLED", True))


def note_enabled() -> bool:
    """The tapped note opens the chat (on at merge, the owner's decision);
    off, the widget falls back to the form."""
    from app.flags import flag
    return bool(flag("PROACTIVE_NOTE_ENABLED", True))


def active_triggers() -> set[str]:
    from app.flags import flag
    raw = str(flag("PROACTIVE_TRIGGERS", ",".join(TRIGGERS)) or "")
    return {t.strip().lower() for t in raw.split(",") if t.strip().lower() in TRIGGERS}


def arm_for(visitor_id: Optional[str]) -> str:
    """"proactive" or "control", stable per visitor (sha256 bucket, like every
    other experiment). No visitor id → control: nothing to keep stable."""
    from app.experiments import in_arm
    from app.flags import flag
    if not visitor_id:
        return "control"
    percent = int(flag("PROACTIVE_PERCENT", 100) or 0)
    return "proactive" if in_arm(EXPERIMENT, visitor_id, percent) else "control"


# --- the words ------------------------------------------------------------

def _city(code: Optional[str]) -> Optional[str]:
    """A city for an airport code when we know one, else None."""
    if not code:
        return None
    from app.services.airport_tz import city_and_zone
    found = city_and_zone(code)
    if found:
        return found[0]
    return _code_to_city().get(code)


_code_city_cache: dict[str, str] = {}


def _code_to_city() -> dict[str, str]:
    if not _code_city_cache:
        from app.pipeline.entity_extractor import CITY_TO_CODE
        for city, code in CITY_TO_CODE.items():
            # The first spelling is the full name ("new york" before "nyc").
            if len(city) > 3 and code not in _code_city_cache:
                _code_city_cache[code] = city.title()
    return _code_city_cache


def _place(name: Optional[str], code: Optional[str]) -> Optional[str]:
    return name or _city(code) or code


def _day(d: date) -> str:
    return f"{_MONTHS[d.month - 1]} {d.day}"


def _dates(trip: TripContext) -> Optional[str]:
    if not trip.depart_date:
        return None
    if trip.return_date:
        return f"{_day(trip.depart_date)} – {_day(trip.return_date)}"
    return _day(trip.depart_date)


def _people(trip: TripContext) -> Optional[str]:
    if not trip.adults:
        return None
    parts = [f"{trip.adults} adult{'s' if trip.adults != 1 else ''}"]
    if trip.children:
        parts.append(f"{trip.children} child{'ren' if trip.children != 1 else ''}")
    if trip.infants:
        parts.append(f"{trip.infants} infant{'s' if trip.infants != 1 else ''}")
    return ", ".join(parts)


def _trip_line(trip: Optional[TripContext]) -> Optional[str]:
    """"Atlanta → Seoul, May 25 – Jun 28, 4 adults in First" — or None
    when the page told us no route."""
    if not trip:
        return None
    origin = _place(trip.origin_name, trip.origin_code)
    dest = _place(trip.destination_name, trip.destination_code)
    if not dest:
        return None
    line = f"{origin} → {dest}" if origin else dest
    dates = _dates(trip)
    if dates:
        line += f", {dates}"
    people = _people(trip)
    cabin = _CABINS.get(trip.cabin or "")
    if people and cabin:
        line += f", {people} in {cabin}"
    elif people:
        line += f", {people}"
    elif cabin:
        line += f", {cabin}"
    return line


def brand_for(site: Optional[str]) -> str:
    """The brand the visitor is on; BBC when the site is unknown or missing."""
    return _BRANDS.get(site or "bbc", _BRANDS["bbc"])


def compose(trigger: str, trip: Optional[TripContext], site: Optional[str] = None) -> tuple[str, list[str]]:
    """The card's message and its quick replies. Pure: the same trigger, trip
    and site always give the same words, so /offer and /init agree.

    The 16 Nov debate (ADR 11-15): Alex introduces himself as the brand's
    assistant, says what the page already told us and never asks for it again
    (a third of the /flight chats stopped at "Where are you flying from?"),
    names a difficulty only where there was one (the quote form that did not
    send), and promises no time and no price."""
    hello = f"Hi, I'm {PERSONA} from {brand_for(site)}."
    line = _trip_line(trip)
    dest = _place(trip.destination_name, trip.destination_code) if trip else None
    full = bool(line and trip and (trip.origin_code or trip.origin_name) and trip.depart_date)
    replies = list(_REPLIES.get(trigger, _REPLIES["reader"]))

    if trigger == "quote":
        if line:
            text = f"{hello} Looks like the quote form is giving you trouble. Can I help? An agent can check fares for {line}."
        else:
            text = (f"{hello} Looks like the quote form is giving you trouble. Can I help? "
                    "Tell me where and roughly when, and an agent will check fares for you.")
    elif trigger == "exit":
        if line:
            text = f"{hello} Before you go: want an agent to check fares for {line}?"
        else:
            text = f"{hello} Before you go: want an agent to check business class fares for you?"
    elif full:
        # A full search (Kayak, /search): say it back, offer the one next step.
        text = f"{hello} I see you're looking at {line}. Want an agent to check private fares for exactly this trip?"
    elif dest and trip and trip.depart_date:
        # Where to and when, not where from: said back, nothing it gave asked
        # again. The origin is asked in the chat, after the visitor answers —
        # never "Where are you flying from?" in the opening (ADR 11).
        text = f"{hello} I see you're looking at {line}. Want an agent to check business class fares for these dates?"
    elif dest:
        text = f"{hello} Planning a trip to {dest}? Tell me roughly when, and an agent will check business class fares for you."
    else:
        text = (f"{hello} Planning a business class trip? "
                "Tell me where and roughly when, and an agent will check fares for you.")
    return text, replies


# --- the doors ------------------------------------------------------------

def offer(visitor_id: Optional[str], trigger: str, trip: Optional[TripContext], site: Optional[str] = None) -> dict:
    """Should this visitor see the card now, and what does it say.

    Always answers (never raises): {"show", "arm", "trigger", "message",
    "quick_replies"}. The control arm gets show=False so the widget can count
    the moment without showing anything — the comparison the test rests on.
    """
    PROACTIVE_HEALTH["offers"] += 1
    if trigger == NOTE:
        # The visitor tapped the note: the words, whatever the experiment's
        # switches say; its own switch off → "no", and the widget shows the form.
        if not note_enabled():
            return {"show": False, "arm": "off", "trigger": trigger, "message": None, "quick_replies": []}
        text, replies = compose(trigger, trip, site)
        PROACTIVE_HEALTH["shown"] += 1
        return {"show": True, "arm": NOTE, "trigger": trigger, "message": text, "quick_replies": replies}
    if not enabled() or trigger not in active_triggers():
        return {"show": False, "arm": "off", "trigger": trigger, "message": None, "quick_replies": []}
    arm = arm_for(visitor_id)
    if arm != "proactive":
        return {"show": False, "arm": arm, "trigger": trigger, "message": None, "quick_replies": []}
    text, replies = compose(trigger, trip, site)
    PROACTIVE_HEALTH["shown"] += 1
    return {"show": True, "arm": arm, "trigger": trigger, "message": text, "quick_replies": replies}


def init_metadata(trigger: str, trip: Optional[TripContext]) -> dict:
    """Conversation metadata written at creation: which moment opened the chat,
    and the search as the page gave it (for the panel). Server keys only."""
    meta: dict = {"proactive_trigger": trigger, "proactive_at": datetime.now(timezone.utc).isoformat()}
    if trip and not trip.is_empty():
        meta["proactive_trip"] = trip.model_dump(mode="json", exclude_none=True)
    return meta


# Conversation metadata key: set once, by the one /chat/init that writes the opening.
OPENING_CLAIM_KEY = "proactive_opening_at"


async def _claim_opening(conversation_id: str) -> Optional[bool]:
    """Win the right to write this conversation's opening, once.

    The message count is check-then-act: two /chat/init for the same visitor
    (a request the browser sent again, two tabs) both read 0 and both wrote an
    opening. The decision moves into one UPDATE whose WHERE requires the flag
    absent — Postgres gives the row to exactly one caller (the pattern of
    handoff._claim_fallback_notice). True: write it. False: another request
    has. None: the claim could not be made (logged) — the opening is written as
    before: a rare duplicate is better than a chat without the words the
    visitor already sees."""
    from app.db import supabase as db

    try:
        client = db.get_client()
        res = await db._run_sync(
            lambda: client.table("conversations").select("metadata").eq("id", conversation_id).single().execute()
        )
        existing = (res.data or {}).get("metadata") or {}
        if not isinstance(existing, dict):
            existing = {}
        if existing.get(OPENING_CLAIM_KEY):
            return False
        payload = {**existing, OPENING_CLAIM_KEY: datetime.now(timezone.utc).isoformat()}
        res = await db._run_sync(
            lambda: client.table("conversations")
            .update({"metadata": payload})
            .eq("id", conversation_id)
            .is_(f"metadata->>{OPENING_CLAIM_KEY}", "null")
            .execute(),
            idempotent=False,
        )
        return bool(res.data)
    except Exception as e:
        logger.warning(
            f"[{conversation_id}] proactive opening claim failed ({type(e).__name__}): written without it"
        )
        return None


async def _stored_opening(conversation_id: str, tries: int = 3) -> Optional[dict]:
    """The opening the request that won the claim stored: {"content",
    "created_at"}, waiting a moment for its write; None when it is not there."""
    from app.db import supabase as db

    for attempt in range(tries):
        try:
            client = db.get_client()
            res = await db._run_sync(
                lambda: client.table("messages")
                .select("content, created_at, model_used")
                .eq("conversation_id", conversation_id)
                .eq("role", "ai")
                .order("created_at")
                .limit(1)
                .execute()
            )
        except Exception as e:
            logger.warning(f"[{conversation_id}] stored proactive opening not read ({type(e).__name__})")
            return None
        row = (res.data or [None])[0]
        if isinstance(row, dict) and row.get("model_used") == MODEL_USED and isinstance(row.get("content"), str):
            created_at = row.get("created_at")
            return {"content": row["content"], "created_at": created_at if isinstance(created_at, str) else None}
        if attempt + 1 < tries:
            await asyncio.sleep(0.25)
    return None


async def open_conversation(
    conversation_id: str, trigger: str, trip: Optional[TripContext], site: Optional[str] = None,
) -> Optional[dict]:
    """The visitor answered the card: write the card's words as the bot's
    first message and start the lead with the trip from the page.

    Idempotent: a conversation that already has messages (the visitor's
    active chat was reused, or a retried /init) is left as it is — no
    opening, no seed and no proactive label on a chat the card did not open.
    Two at once write one opening (_claim_opening); the other hands back the
    stored one. Returns {"content", "created_at"} of the stored opening (after
    the output guard), or None when nothing was written. The widget already
    shows the card's words: it takes the stored text and moves its poll cursor
    past `created_at`, so the opening is not shown twice."""
    from app.db import supabase as db
    from app.services import conversation_service

    if await db.count_messages(conversation_id) > 0:
        return None
    if await _claim_opening(conversation_id) is False:
        # A concurrent /chat/init for this conversation writes the opening,
        # the label and the seed. Its words and time go back to this widget too.
        logger.info(f"[{conversation_id}] proactive opening written by a concurrent /chat/init")
        return await _stored_opening(conversation_id)
    text, _ = compose(trigger, trip, site)
    row = await conversation_service.add_message(conversation_id, "ai", text, model_used=MODEL_USED, cost=0.0)
    if not isinstance(row, dict):
        # Not saved (db.add_message logged why): the conversation does not
        # have the opening, so nothing may say it does — no label, no page
        # trip that "was shown in your opening message". A plain chat.
        PROACTIVE_HEALTH["open_failures"] += 1
        logger.error(f"[{conversation_id}] proactive opening not saved: the chat goes on as a plain one")
        return None
    PROACTIVE_HEALTH["opened"] += 1
    # The label for the panel and the prompt (email first). Written here and
    # not at creation: /chat/init may hand back the visitor's open chat, and
    # the first-touch merge would label a chat the card never opened.
    await db._merge_conv_metadata(conversation_id, init_metadata(trigger, trip))
    if trip and not trip.is_empty():
        await seed_lead(conversation_id, trip)
    content = row.get("content")
    created_at = row.get("created_at")
    return {
        "content": content if isinstance(content, str) else text,
        "created_at": created_at if isinstance(created_at, str) else None,
    }


def _entities_from_trip(trip: TripContext) -> dict:
    """The page's search in the shape update_lead_from_entities takes. Trip
    type is left out on purpose: that writer marks it as said by the client."""
    ent: dict = {}
    if trip.origin_code:
        ent["origin"] = trip.origin_code
    if trip.destination_code:
        ent["destination"] = trip.destination_code
    if trip.depart_date:
        ent["departure_date"] = trip.depart_date.isoformat()
    if trip.return_date:
        ent["return_date"] = trip.return_date.isoformat()
    if trip.passengers:
        ent["passengers"] = trip.passengers
        ent["_children_count"] = trip.children or 0
        ent["_infant_count"] = trip.infants or 0
    # leads_cabin_class_check takes business, first, economy (and mixed):
    # Premium Economy would refuse the whole update, route and dates with it.
    # It stays in trip_notes.page_trip and in the opening message.
    if trip.cabin in _LEAD_CABINS:
        ent["cabin_class"] = trip.cabin
    return ent


async def seed_lead(conversation_id: str, trip: TripContext) -> None:
    """Write the page's search onto the lead and record where it came from.

    `trip_notes.page_trip` says the client has not confirmed it yet: the
    prompt shows it as "from the page" and the summary the client confirms
    before the CRM still carries it, so a wrong link is corrected in chat.
    A failure is counted and logged; the chat goes on without the seed."""
    from app.services import lead_service

    entities = _entities_from_trip(trip)
    try:
        lead = await lead_service.ensure_lead(conversation_id)
        if not isinstance(lead, dict):
            raise RuntimeError("no lead row for the conversation")
        if "trip_notes" not in lead:
            # Migration 040 not applied: nothing could say these came from the
            # page, so the prompt would take them as the client's and the CRM
            # would get them unmarked. The opening still shows the search.
            logger.warning(f"[{conversation_id}] proactive lead seed skipped: no trip_notes column to mark the page's search")
            return
        if entities:
            await lead_service.update_lead_from_entities(conversation_id, entities)
        await _mark_page_trip(conversation_id, trip)
    except Exception as e:
        PROACTIVE_HEALTH["seed_errors"] += 1
        PROACTIVE_HEALTH["last_seed_error_at"] = datetime.now(timezone.utc).isoformat()
        logger.error(f"[{conversation_id}] proactive lead seed failed: {type(e).__name__}")


async def _mark_page_trip(conversation_id: str, trip: TripContext) -> None:
    from app.db.supabase import get_client, _run_sync

    client = get_client()
    res = await _run_sync(
        lambda: client.table("leads").select("*").eq("conversation_id", conversation_id).limit(1).execute()
    )
    if not res.data:
        return
    lead = res.data[0]
    if "trip_notes" not in lead:
        return  # migration 040 not applied: nowhere to say where it came from
    notes = dict(lead.get("trip_notes") or {}) if isinstance(lead.get("trip_notes"), dict) else {}
    notes["page_trip"] = {
        **trip.model_dump(mode="json", exclude_none=True),
        "confirmed": False,
        # What the seed wrote, read back now: later the lead cannot tell a
        # field the seed never wrote from one the client set back to the
        # column's default (cabin_class "business").
        "landed": _landed_now(lead, trip),
    }
    lead_id = lead["id"]
    await _run_sync(lambda: client.table("leads").update({"trip_notes": notes}).eq("id", lead_id).execute())


def _norm(value) -> Optional[str]:
    """A lead or page value as comparable text: "ATL", "2027-05-25", "4", "first"."""
    if value is None or value == "":
        return None
    return str(value).strip()[:10].lower()


# leads.cabin_class's column default: every new lead has it, said or not.
_CABIN_DEFAULT = "business"

# The lead columns the seed writes from the page (_entities_from_trip), and
# the ones the CRM note is about.
_SEED_FIELDS = ("origin_code", "destination_code", "departure_date", "return_date", "passengers", "cabin_class")
_ROUTE_AND_DATES = ("origin_code", "destination_code", "departure_date", "return_date")


def _pairs(lead: dict, trip: TripContext) -> dict:
    """Each seeded field as (on the lead, from the page), comparable; only the
    fields the page gave. Premium Economy is never written on the lead
    (_LEAD_CABINS): nothing to compare."""
    page = {
        "origin_code": trip.origin_code,
        "destination_code": trip.destination_code,
        "departure_date": trip.depart_date.isoformat() if trip.depart_date else None,
        "return_date": trip.return_date.isoformat() if trip.return_date else None,
        "passengers": trip.passengers,
        "cabin_class": trip.cabin if trip.cabin in _LEAD_CABINS else None,
    }
    return {f: (_norm(lead.get(f)), _norm(page[f])) for f in _SEED_FIELDS if page[f] is not None}


def _landed_now(lead: dict, trip: TripContext) -> list[str]:
    """The page's fields the lead holds right after the seed: what it wrote.
    A cabin equal to the column's default counts: the lead says the same as
    the page, written or not."""
    return [f for f, (have, page) in _pairs(lead, trip).items() if have == page]


def _recorded_landed(lead: dict) -> Optional[list[str]]:
    """What the seed wrote, as recorded with the page trip; None when not recorded."""
    notes = lead.get("trip_notes")
    page = notes.get("page_trip") if isinstance(notes, dict) else None
    landed = page.get("landed") if isinstance(page, dict) else None
    if not isinstance(landed, list):
        return None
    return [f for f in landed if f in _SEED_FIELDS]


def _page_state(lead: dict, trip: TripContext, fields: tuple = _SEED_FIELDS) -> str:
    """How the lead stands against what the page seeded, over `fields` (all
    the seeded ones for the prompt, route and dates for the CRM note):

    - "changed": the client changed one of them (another route, other dates,
      other travellers or another cabin, or dropped the return) — the lead's
      own fields are the truth now;
    - "held": the lead still holds what the seed wrote of them;
    - "absent": the seed wrote none of them — the page is all the bot knows.

    The seed writes only the page's values, so another value on the lead is
    the client's. Two cases need to know what the seed wrote: a field now
    empty (the client cleared it, or the seed never wrote it), and the cabin
    "business", the column's default on every new lead (the client chose it,
    or nobody did). The seed records what it wrote ("landed"); a page trip
    without the record counts the seed as written when the lead holds any of
    the page's fields, the cabin "business" aside (one update writes them
    all or none)."""
    # A route the page named only in words (a route page's title) is never
    # written on the lead: a code there is one the client gave.
    if (trip.origin_name and not trip.origin_code and lead.get("origin_code")) or (
        trip.destination_name and not trip.destination_code and lead.get("destination_code")
    ):
        return "changed"
    every = _pairs(lead, trip)
    recorded = _recorded_landed(lead)
    if recorded is None:
        seeded = any(have is not None and not (f == "cabin_class" and have == _CABIN_DEFAULT)
                     for f, (have, _) in every.items())
        wrote = set(every) if seeded else set()
    else:
        wrote = set(recorded)
    pairs = {f: p for f, p in every.items() if f in fields}
    for f, (have, page) in pairs.items():
        if have == page:
            continue
        untouched = have is None or (f == "cabin_class" and have == _CABIN_DEFAULT)
        if not untouched or f in wrote:
            return "changed"
    return "held" if any(have == page and f in wrote for f, (have, page) in pairs.items()) else "absent"


def _unconfirmed_page_trip(lead: Optional[dict]) -> Optional[TripContext]:
    """The page's search kept on the lead, while the client has not confirmed it."""
    if not isinstance(lead, dict):
        return None
    notes = lead.get("trip_notes")
    if not isinstance(notes, dict):
        return None
    page = notes.get("page_trip")
    if not isinstance(page, dict) or page.get("confirmed"):
        return None
    try:
        return TripContext(**{k: v for k, v in page.items() if k not in ("confirmed", "landed")})
    except Exception as e:
        logger.warning(f"page_trip unreadable ({type(e).__name__}) — no page line")
        return None


def page_trip_line(lead: Optional[dict]) -> Optional[str]:
    """The VISITOR CONTEXT line for a search the page gave us, or None."""
    trip = _unconfirmed_page_trip(lead)
    if trip is None or _page_state(lead, trip) == "changed":
        return None
    line = _trip_line(trip)
    if not line:
        return None
    return (
        f"From the page (the client's own search, already shown in your opening message): {line}. "
        "Treat it as known and do not ask for it again; if the client changes anything, "
        "the client's words win."
    )


def page_trip_note(lead: Optional[dict]) -> Optional[str]:
    """The CRM line for a lead the abandoned-lead job sends while its route
    and dates are still the page's: the client never confirmed them in chat,
    so the consultant checks them first. Only the route and the dates count
    here: a client who changed the travellers or the cabin still never
    confirmed the route and the dates."""
    trip = _unconfirmed_page_trip(lead)
    if trip is None or _page_state(lead, trip, _ROUTE_AND_DATES) != "held":
        return None
    where = "the Kayak link" if trip.source == "kayak" else "the page"
    return f"Route and dates from {where} the client came from, not confirmed in chat"
