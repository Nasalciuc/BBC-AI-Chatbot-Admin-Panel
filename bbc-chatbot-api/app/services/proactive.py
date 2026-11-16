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
- ``PROACTIVE_ENABLED`` (off at merge) — the whole feature;
- ``PROACTIVE_PERCENT`` (0) — the share of visitors in the proactive arm,
  bucketed by visitor id, so a visitor stays in one arm across visits;
- ``PROACTIVE_TRIGGERS`` ("kayak,quote,reader,exit") — which moments may open it.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Literal, Optional

from app.models.trip_context import TripContext

logger = logging.getLogger(__name__)

Trigger = Literal["kayak", "quote", "reader", "exit"]
TRIGGERS: tuple[str, ...] = ("kayak", "quote", "reader", "exit")
EXPERIMENT = "proactive_chat"
MODEL_USED = "proactive_template"

# What each card offers under its message. "Not now" never reaches the
# server: the widget closes the card and stays quiet for 24 hours.
_REPLIES: dict[str, list[str]] = {
    "kayak": ["Yes, check fares", "Change details", "Not now"],
    "quote": ["Send me the fares", "I have a question", "Not now"],
    "reader": ["Check fares", "I have a question", "Not now"],
    "exit": ["Check fares", "I have a question", "Not now"],
}

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
    from app.flags import flag
    return bool(flag("PROACTIVE_ENABLED", False))


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
    percent = int(flag("PROACTIVE_PERCENT", 0) or 0)
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


def compose(trigger: str, trip: Optional[TripContext]) -> tuple[str, list[str]]:
    """The card's message and its quick replies. Pure: the same trigger and
    trip always give the same words, so /offer and /init agree."""
    line = _trip_line(trip)
    dest = _place(trip.destination_name, trip.destination_code) if trip else None
    replies = list(_REPLIES.get(trigger, _REPLIES["reader"]))

    if trigger == "quote":
        if line:
            text = f"Prefer to skip the form? Tell me your email and I'll send fares for {line}."
        else:
            text = "Prefer to skip the form? Tell me your email and I'll send you the fares."
    elif trigger == "exit":
        if line:
            text = f"Before you go: want one of our agents to check fares for {line}? It takes a minute."
        else:
            text = "Before you go: want one of our agents to check business class fares for you? It takes a minute."
    elif line and (trip and (trip.origin_code or trip.origin_name) and trip.depart_date):
        # A full search (Kayak, /search): repeat it, offer the one next step.
        text = f"Flying {line}? I can have an agent check fares for exactly this trip."
    elif dest:
        text = f"Planning a trip to {dest}? Tell me your dates and I'll have an agent check business class fares."
    else:
        text = "Planning a business class trip? Tell me where and when, and I'll check fares for you."
    return text, replies


# --- the doors ------------------------------------------------------------

def offer(visitor_id: Optional[str], trigger: str, trip: Optional[TripContext]) -> dict:
    """Should this visitor see the card now, and what does it say.

    Always answers (never raises): {"show", "arm", "trigger", "message",
    "quick_replies"}. The control arm gets show=False so the widget can count
    the moment without showing anything — the comparison the test rests on.
    """
    PROACTIVE_HEALTH["offers"] += 1
    if not enabled() or trigger not in active_triggers():
        return {"show": False, "arm": "off", "trigger": trigger, "message": None, "quick_replies": []}
    arm = arm_for(visitor_id)
    if arm != "proactive":
        return {"show": False, "arm": arm, "trigger": trigger, "message": None, "quick_replies": []}
    text, replies = compose(trigger, trip)
    PROACTIVE_HEALTH["shown"] += 1
    return {"show": True, "arm": arm, "trigger": trigger, "message": text, "quick_replies": replies}


def init_metadata(trigger: str, trip: Optional[TripContext]) -> dict:
    """Conversation metadata written at creation: which moment opened the chat,
    and the search as the page gave it (for the panel). Server keys only."""
    meta: dict = {"proactive_trigger": trigger, "proactive_at": datetime.now(timezone.utc).isoformat()}
    if trip and not trip.is_empty():
        meta["proactive_trip"] = trip.model_dump(mode="json", exclude_none=True)
    return meta


async def open_conversation(conversation_id: str, trigger: str, trip: Optional[TripContext]) -> Optional[dict]:
    """The visitor answered the card: write the card's words as the bot's
    first message and start the lead with the trip from the page.

    Idempotent: a conversation that already has messages (the visitor's
    active chat was reused, or a retried /init) is left as it is — no
    opening, no seed and no proactive label on a chat the card did not open.
    Returns {"content", "created_at"} of the stored opening (after the output
    guard), or None when nothing was written. The widget already shows the
    card's words: it takes the stored text and moves its poll cursor past
    `created_at`, so the opening is not shown twice."""
    from app.db import supabase as db
    from app.services import conversation_service

    if await db.count_messages(conversation_id) > 0:
        return None
    text, _ = compose(trigger, trip)
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
