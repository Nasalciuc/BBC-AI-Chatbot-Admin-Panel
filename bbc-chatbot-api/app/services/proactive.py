"""The chat that opens by itself, with the visitor's own search (no form).

Two doors use this module:

- ``POST /chat/proactive/offer`` — the widget saw a visitor hesitate (Kayak
  landing after a few seconds, a Google or Bing ad landing on a destination's
  page after a few seconds, the quote form refused, a long read, a move to
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
- ``PROACTIVE_TRIGGERS`` ("kayak,ads,quote,reader") — which moments may open it. ``exit`` is off by
  default (owner, 17 Nov: of the 35 panels it opened on 16-17 Nov none got an answer and 21 visitors
  were gone within 10 s — it opened as the tab closed); naming it here brings it back for a widget
  that still asks (the widget no longer picks it). ``ads`` (owner, 18 Nov) is on with the default:
  ``kayak,quote,reader`` here turns it off in 30 s.

One more door is not a moment and the switches above do not hold it:
``note``, the visitor TAPPED the 10 s note beside the button ("Tell me where
and when, and an agent will check fares"). The owner's decision of 16 Nov: that tap
opens the chat with the same words and no form, for every visitor — from
13 Nov, 0 of the 35 visitors who opened the chat got past the form the note
led to. Its own switch, ``PROACTIVE_NOTE_ENABLED`` (on at merge), sends the
note back to the form in 30 s, without a deploy, if it ever must. When the page named no
destination, the note asks its first question one of two ways, chosen per chat by a bandit that
learns from every chat (app/services/note_bandit.py).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timezone
from typing import Literal, Optional

from app.models.trip_context import TripContext, _name
from app.services import note_bandit, proactive_i18n

logger = logging.getLogger(__name__)

Trigger = Literal["kayak", "ads", "quote", "reader", "exit"]
# Every moment a widget may name; the ones that open the card unless PROACTIVE_TRIGGERS says otherwise.
# "ads": the visit came from a Google or Bing ad and the page names a destination (bbc-widget triggers.ts):
# on 17-18 Nov about 350 such visitors a day stayed at least 8 s and got no moment at all.
TRIGGERS: tuple[str, ...] = ("kayak", "ads", "quote", "reader", "exit")
DEFAULT_TRIGGERS: tuple[str, ...] = ("kayak", "ads", "quote", "reader")
# The tapped note (module docstring): asked for by the visitor, so in no arm.
NOTE = "note"
# The answer for a moment switched off on its own (PROACTIVE_TRIGGERS) while the feature is on: the widget keeps
# the visit's one moment for the others and does not ask with this one again (bbc-widget triggers.ts,
# TRIGGER_OFF_ARM). "off" stays the answer for the feature switched off: that one ends the visit's moments.
TRIGGER_OFF = "trigger_off"
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
    # The quote form refused a send for its contact fields (CONTACT below): the yes says how the fares come.
    "quote_contact": ["Email me the fares", "I have a question", "Not now"],
    "reader": ["Get a fare quote", "I have a question", "Not now"],
    # The ad's page named a destination: "Planning a trip to Paris? Tell me roughly when…" — the reader's
    # words for the same page, and its buttons.
    "ads": ["Get a fare quote", "I have a question", "Not now"],
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
    raw = str(flag("PROACTIVE_TRIGGERS", ",".join(DEFAULT_TRIGGERS)) or "")
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


def _day(d: date, lang: Optional[str] = None) -> str:
    """"May 25"; "25 may" in Spanish, "25 mai" in French."""
    months = proactive_i18n.MONTHS.get(lang or "")
    if months:
        return f"{d.day} {months[d.month - 1]}"
    return f"{_MONTHS[d.month - 1]} {d.day}"


def _dates(trip: TripContext, lang: Optional[str] = None) -> Optional[str]:
    if not trip.depart_date:
        return None
    if trip.return_date:
        return f"{_day(trip.depart_date, lang)} – {_day(trip.return_date, lang)}"
    return _day(trip.depart_date, lang)


def _count(n: int, kind: str, lang: Optional[str]) -> str:
    words = proactive_i18n.PEOPLE.get(lang or "")
    if words:
        one, many = words[kind]
        return f"{n} {one if n == 1 else many}"
    english = {"adult": ("adult", "adults"), "child": ("child", "children"), "infant": ("infant", "infants")}[kind]
    return f"{n} {english[0] if n == 1 else english[1]}"


def _people(trip: TripContext, lang: Optional[str] = None) -> Optional[str]:
    if not trip.adults:
        return None
    parts = [_count(trip.adults, "adult", lang)]
    if trip.children:
        parts.append(_count(trip.children, "child", lang))
    if trip.infants:
        parts.append(_count(trip.infants, "infant", lang))
    return ", ".join(parts)


def _trip_line(trip: Optional[TripContext], lang: Optional[str] = None) -> Optional[str]:
    """"Atlanta → Seoul, May 25 – Jun 28, 4 adults in First" — or None
    when the page told us no route. `lang` ("es", "fr"): the months, the
    travellers and the cabin in that language; the cities stay as named."""
    if not trip:
        return None
    origin = _place(trip.origin_name, trip.origin_code)
    dest = _place(trip.destination_name, trip.destination_code)
    if not dest:
        return None
    line = f"{origin} → {dest}" if origin else dest
    dates = _dates(trip, lang)
    if dates:
        line += f", {dates}"
    if trip.trip_type == "one_way":
        # ADR A4 (20 Nov): said, so a one-way search is never taken for a round trip.
        line += f", {proactive_i18n.ONE_WAY[lang if lang in proactive_i18n.ONE_WAY else 'en']}"
    people = _people(trip, lang)
    cabin = (proactive_i18n.CABINS.get(lang or "") or _CABINS).get(trip.cabin or "")
    if people and cabin:
        line += f", {people} {proactive_i18n.IN_CABIN.get(lang or 'en', 'in')} {cabin}"
    elif people:
        line += f", {people}"
    elif cabin:
        line += f", {cabin}"
    return line


def _route_line(trip: Optional[TripContext]) -> Optional[str]:
    """"New York → Paris", or the destination alone; None without one."""
    if not trip:
        return None
    origin = _place(trip.origin_name, trip.origin_code)
    dest = _place(trip.destination_name, trip.destination_code)
    if not dest:
        return None
    return f"{origin} → {dest}" if origin else dest


def full_trip(trip: Optional[TripContext]) -> bool:
    """The page gave the whole trip — where from, where to and when: the card says it back and asks
    nothing about it, and the yes can be answered at once (A2)."""
    line = _trip_line(trip)
    return bool(line and trip and (trip.origin_code or trip.origin_name) and trip.depart_date)


def trip_line(trip: Optional[TripContext], lang: Optional[str] = None) -> Optional[str]:
    """The trip as the card says it back (for the words after the card: app.services.card_words)."""
    return _trip_line(trip, lang)


# --- the ad's search (ADR A5, 20 Nov) -------------------------------------------
# What a Google or Bing visitor typed before the click (trip.keyword) names where they want to go and in
# which cabin: "first class flights to rome". Bing, 13-20 Nov: 371 of the 402 visits that landed on the home
# page (no destination on it) had the place after "to"; they got no card until the reader's 25 s. Read here,
# deterministically: only the words after "to" (or "in") are a place, so an airline ("air france business
# class") never becomes one; the search words around it are dropped, a country after a city goes ("naples
# italy" → Naples), at most three words stay, and the result passes the trip's own name rule. Nothing else of
# the keyword is ever said back, and none of it reaches a lead.
_KW_TO = re.compile(r"\bto\s+(.+)$")
_KW_PLACE = re.compile(r"\b(?:to|in)\s+(.+)$")
_KW_END = re.compile(
    r"\b(?:in|from|for|on|with|via|during|next|this|and|or|jan|january|feb|february|mar|march|apr|april|may|"
    r"jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\b|\d")
_KW_NOISE = frozenset({
    "business", "biz", "class", "first", "1st", "flight", "flights", "ticket", "tickets", "airfare", "airfares",
    "fare", "fares", "deal", "deals", "cheap", "cheapest", "cheaper", "discount", "discounted", "best", "low",
    "lowest", "cost", "book", "booking", "buy", "find", "fly", "flying", "plane", "airline", "airlines", "trip",
    "trips", "travel", "international", "seat", "seats", "sale", "a", "the", "of", "offer", "offers", "economy",
    "premium", "round", "one", "way", "return", "private", "luxury", "upgrade", "price", "prices",
    # The visitors' own country is no destination of theirs (the ads run in the US).
    "usa", "us", "america",
})
_KW_COUNTRIES = frozenset({
    "italy", "france", "spain", "germany", "scotland", "england", "uk", "ireland", "portugal", "greece",
    "netherlands", "holland", "switzerland", "austria", "hungary", "croatia", "turkey", "morocco", "japan",
    "china", "india", "thailand", "vietnam", "philippines", "australia", "egypt", "israel", "denmark", "sweden",
    "norway", "finland", "poland", "czech", "belgium", "canada", "mexico", "brazil", "argentina", "colombia",
})
_KW_FIRST = re.compile(r"\b(?:first|1st)\b")
_KW_BUSINESS = re.compile(r"\b(?:business|biz)\b")
_KW_PREMIUM_ECONOMY = re.compile(r"\bpremium\s+economy\b")
_KW_ECONOMY = re.compile(r"\beconomy\b")


def keyword_place(keyword: Optional[str]) -> Optional[str]:
    """The place an ad's search names after "to"/"in", as a name ("Rome", "New York"), or None."""
    if not keyword:
        return None
    # "to" first: in "flights in business class to rome" the place follows "to", not "in" (CodeRabbit #272).
    found = _KW_TO.search(keyword) or _KW_PLACE.search(keyword)
    if not found:
        return None
    seg = _KW_END.split(found.group(1), maxsplit=1)[0]
    words = [w for w in seg.split() if w not in _KW_NOISE]
    while len(words) > 1 and words[-1] in _KW_COUNTRIES:
        words.pop()
    if not words or len(words) > 3:
        return None
    return _name(" ".join(w.capitalize() for w in words))


def keyword_cabin(keyword: Optional[str]) -> Optional[str]:
    """The cabin the ad's search names — "first", "business", "premium_economy" or "economy" — else None. A
    search for economy on a business class page is not answered with business fares (CodeRabbit #272)."""
    if not keyword:
        return None
    if _KW_FIRST.search(keyword):
        return "first"
    if _KW_BUSINESS.search(keyword):
        return "business"
    if _KW_PREMIUM_ECONOMY.search(keyword):
        return "premium_economy"
    return "economy" if _KW_ECONOMY.search(keyword) else None


def with_keyword(trip: Optional[TripContext]) -> Optional[TripContext]:
    """The trip as the ads card speaks of it: the destination the search named when the page named none, and
    the cabin the search named over the page's (a business class page visited for "first class"). A trip
    without a keyword comes back as it was."""
    if trip is None or not trip.keyword:
        return trip
    update: dict = {}
    if not _names_a_destination(trip):
        place = keyword_place(trip.keyword)
        if place:
            update["destination_name"] = place
    cabin = keyword_cabin(trip.keyword)
    if cabin and cabin != trip.cabin:
        update["cabin"] = cabin
    return trip.model_copy(update=update) if update else trip


# The longest title the widget takes (bbc-widget api.ts, TITLE_MAX): two 40-character places, the dates, nine
# travellers, the cabin and "aller simple" come to ~175, so a title never reaches it; the cap only guards the
# promise that a title the server sends is one the widget shows (CodeRabbit #272).
TITLE_MAX = 240


def _title(trip: Optional[TripContext], lang: Optional[str] = None) -> Optional[str]:
    """The phone card's title (ADR A3): "Springfield → Lakeside · Nov 17 – Nov 24 · Business class", with the
    travellers when more than one and "one-way" when it is; "Lakeside · First class" for a destination alone.
    None without a destination, or when it would not fit TITLE_MAX (the card then shows the whole message)."""
    route = _route_line(trip)
    if not route or trip is None:
        return None
    parts = [route]
    dates = _dates(trip, lang)
    if dates:
        parts.append(dates)
    if trip.adults and (trip.adults > 1 or trip.children or trip.infants):
        parts.append(_people(trip, lang) or "")
    cabins = proactive_i18n.TITLE_CABINS.get(lang or "en", proactive_i18n.TITLE_CABINS["en"])
    if trip.cabin in cabins:
        parts.append(cabins[trip.cabin])
    if trip.trip_type == "one_way":
        parts.append(proactive_i18n.ONE_WAY[lang if lang in proactive_i18n.ONE_WAY else "en"])
    title = " · ".join(p for p in parts if p)
    return title if len(title) <= TITLE_MAX else None


def brand_for(site: Optional[str]) -> str:
    """The brand the visitor is on; BBC when the site is unknown or missing."""
    return _BRANDS.get(site or "bbc", _BRANDS["bbc"])


def _names_a_destination(trip: Optional[TripContext]) -> bool:
    return bool(trip and _place(trip.destination_name, trip.destination_code))


# The quote card when the form refused a send for its contact fields (owner, 18 Nov). On 16-17 Nov,
# 138 of the 436 visitors who opened the quote form pressed its button and were refused, almost always
# for the name, the email and the phone (the trip was filled), and 101 of them never sent it. The card
# then offers the chat instead of the rest of the form: an agent emails the fares.
CONTACT = "contact"
# The kinds a widget may name for a refused field: bbc-widget/src/siteSignals.ts FIELD_KINDS, the same list
# (tests/test_site_signals.py holds them equal).
REFUSED_KINDS: tuple[str, ...] = (
    "name", "email", "phone", "origin", "destination", "dates", "passengers", "cabin", "trip_type", "message",
    "consent", "captcha", "other",
)
_CONTACT_KINDS = frozenset({"name", "email", "phone"})


def quote_variant(refused: Optional[list[str]]) -> Optional[str]:
    """CONTACT when the refused send flagged a contact field (name, email or phone), else None."""
    return CONTACT if refused and _CONTACT_KINDS.intersection(refused) else None


# D1 (ADR-4a, 18 Nov): after the quote form refused a send, a row beside the form offers the fares on
# WhatsApp or by email, instead of the chat card ("Looks like the quote form is giving you trouble"
# caught 0 of the 23 it was shown to on 17-18 Nov). The widget asks for it when it can place the row
# beside the form it saw (`place="form"`); the row's sentence is the chat's opening if the client answers.
ROW = "row"
# C5 (ADR-2b, 18 Nov): the Kayak card's second sentence, tested against the first — "Already seen a
# price? An agent checks if they can beat it". The client may say the price; Alex never does.
BEAT = "beat"
BEAT_EXPERIMENT = "kayak_card_beat"
# The two tests on the Kayak card (C4: the moment, in the widget; C5: the words, here) start after
# #269's 72 hours of measuring, so that measure is of one card only (ADR-1a, ADR-1b).
TESTS_START = datetime(2026, 11, 21, 13, 49, tzinfo=timezone.utc)


def tests_started(now: Optional[datetime] = None) -> bool:
    return (now or datetime.now(timezone.utc)) >= TESTS_START


def known_variant(variant: object) -> Optional[str]:
    """A variant any door may carry back (the note's bandit, the quote's CONTACT or ROW, the Kayak card's
    BEAT); anything else None."""
    if variant in (CONTACT, ROW, BEAT):
        return variant  # type: ignore[return-value]
    return note_bandit.known(variant)


def note_variant(trip: Optional[TripContext]) -> Optional[str]:
    """How the tapped note asks its first question (note_bandit): only when the page named no
    destination — otherwise the opening says the page's trip back and asks nothing about where."""
    return None if _names_a_destination(trip) else note_bandit.choose()


def beat_variant(visitor_id: Optional[str], trip: Optional[TripContext], now: Optional[datetime] = None) -> Optional[str]:
    """C5: BEAT for half the Kayak visitors (stable per visitor) once the tests started, on a card that says
    the whole trip back; None (the usual words) otherwise. KAYAK_CARD_BEAT (app.flags, on) stops it."""
    from app.experiments import in_arm
    from app.flags import flag

    if not visitor_id or not full_trip(trip) or not tests_started(now):
        return None
    if not flag("KAYAK_CARD_BEAT", True):
        return None
    return BEAT if in_arm(BEAT_EXPERIMENT, visitor_id, 50) else None


def applied_variant(trigger: str, trip: Optional[TripContext], variant: Optional[str]) -> Optional[str]:
    """The variant the words were composed with: CONTACT or ROW on the quote moment; BEAT on a Kayak card
    with the whole trip; on the note, a known one with no destination named; otherwise None (the words are
    the usual ones, and the chat counts for no variant)."""
    if trigger == "quote":
        return variant if variant in (CONTACT, ROW) else None
    if trigger == "kayak":
        return BEAT if variant == BEAT and full_trip(trip) else None
    if trigger != NOTE or _names_a_destination(trip):
        return None
    return note_bandit.known(variant)


def compose(
    trigger: str, trip: Optional[TripContext], site: Optional[str] = None, variant: Optional[str] = None,
    lang: Optional[str] = None,
) -> tuple[str, list[str]]:
    """The card's message and its quick replies. Pure: the same trigger, trip,
    site, variant and language always give the same words, so /offer and /init agree.

    The 16 Nov debate (ADR 11-15): Alex introduces himself as the brand's
    assistant, says what the page already told us and never asks for it again
    (a third of the /flight chats stopped at "Where are you flying from?"),
    names a difficulty only where there was one (the quote form that did not
    send), and promises no time and no price. `variant`: how the tapped note
    asks where to when the page named no destination (note_bandit); CONTACT
    on the quote card when the form refused its contact fields, ROW for the row
    beside the refused form (D1); BEAT, the Kayak card's tested sentence (C5);
    ignored anywhere else. `lang`: "es" or "fr" (proactive_i18n.card_lang), else
    English."""
    lang = proactive_i18n.card_lang(lang)
    if trigger == "ads":
        # The ad's search names the place and the cabin (with_keyword): /offer and /init read it alike.
        trip = with_keyword(trip)
    say = lambda key, **kw: proactive_i18n.sentence(lang, key).format(**kw)  # noqa: E731
    hello = say("hello", persona=PERSONA, brand=brand_for(site))
    fares = proactive_i18n.cabin_fares(lang, trip.cabin if trip else None)
    line = _trip_line(trip, lang)
    dest = _place(trip.destination_name, trip.destination_code) if trip else None
    full = full_trip(trip)
    reply_key = trigger if trigger in _REPLIES else "reader"
    replies = proactive_i18n.replies(lang, reply_key) or list(_REPLIES[reply_key])

    if trigger == "quote" and variant == ROW:
        # The row beside the refused form: short (the route only, the form shows the rest), and its own
        # two buttons (card_words.row_buttons), no chips.
        route = _route_line(trip)
        return (say("row_line", line=route) if route else say("row")), []
    if trigger == "quote" and variant == CONTACT:
        # The trip was filled and the name, email and phone were not: the chat instead of the rest of
        # the form. The trip the page gave is said back, nothing it gave is asked again.
        replies = proactive_i18n.replies(lang, "quote_contact") or list(_REPLIES["quote_contact"])
        text = say("quote_contact_line", hello=hello, line=line) if line else say("quote_contact", hello=hello)
    elif trigger == "quote":
        text = say("quote_line", hello=hello, line=line) if line else say("quote", hello=hello)
    elif trigger == "exit":
        text = say("exit_line", hello=hello, line=line) if line else say("exit", hello=hello)
    elif full:
        # A full search (Kayak, /search): say it back, offer the one next step.
        beat = applied_variant(trigger, trip, variant) == BEAT
        text = say("full_beat" if beat else "full", hello=hello, line=line)
    elif dest and trip and trip.depart_date:
        # Where to and when, not where from: said back, nothing it gave asked
        # again. The origin is asked in the chat, after the visitor answers —
        # never "Where are you flying from?" in the opening (ADR 11).
        text = say("dest_date", hello=hello, line=line, cabin_fares=fares)
    elif dest:
        text = say("dest", hello=hello, dest=dest, cabin_fares=fares)
    elif applied_variant(trigger, trip, variant) == "places":
        # Where to as buttons (note_bandit): a tap instead of typing; anything else is typed.
        text = say("places", hello=hello)
        replies = proactive_i18n.replies(lang, "places") or list(note_bandit.PLACES_REPLIES)
    else:
        text = say("open", hello=hello)
    return text, replies


def card_face(
    trigger: str, trip: Optional[TripContext], variant: Optional[str] = None, lang: Optional[str] = None,
) -> Optional[dict]:
    """The phone card's own face (ADR A3, 20 Nov): {"headline": the trip, "card_text": only the question of
    the opening} for the Kayak and ads cards that name a destination; None otherwise (the card shows the
    whole message, as before). A visitor who stays 3 s reads one line: the trip, not a sentence with the trip
    inside it. The chat still opens with compose's whole sentence, whose question this is."""
    if trigger not in ("kayak", "ads"):
        return None
    lang = proactive_i18n.card_lang(lang)
    if trigger == "ads":
        trip = with_keyword(trip)
    headline = _title(trip, lang)
    if not headline or trip is None:
        return None
    say = lambda key, **kw: proactive_i18n.sentence(lang, key).format(**kw)  # noqa: E731
    fares = proactive_i18n.cabin_fares(lang, trip.cabin)
    if full_trip(trip):
        ask = say("ask_full_beat" if applied_variant(trigger, trip, variant) == BEAT else "ask_full")
    elif trip.depart_date:
        ask = say("ask_dest_date", cabin_fares=fares)
    else:
        ask = say("ask_dest", cabin_fares=fares)
    return {"headline": headline, "card_text": ask}


# --- the doors ------------------------------------------------------------

# C4 (ADR-1b, 18 Nov): the Kayak card at 4 s in view for half the visitors, against the moment of today
# (5 s), from TESTS_START, stopped at C4_CAP cards shown on the early side. The widget picks the side
# (stable per visitor) and asks earlier on its side; while the test runs the answer is the usual one,
# and once it is over (or KAYAK_EARLY_TEST is off) the early side is told C4_HOLD and asks again at the
# usual moment. The cards shown on each side are counted where every process sees them (Redis, when
# REDIS_URL is set; this process's memory otherwise).
C4_ARMS: tuple[str, ...] = ("early", "now")
C4_CAP = 300
C4_HOLD = "c4_hold"
C4_SHOWN: dict[str, int] = {"early": 0, "now": 0}
_C4_KEY = "proactive:c4:shown:{arm}"
_c4_redis = None
_c4_redis_failed = False


def _c4_client():
    """An asyncio Redis client when REDIS_URL is set, else None (counted in memory)."""
    global _c4_redis, _c4_redis_failed
    if _c4_redis is not None or _c4_redis_failed:
        return _c4_redis
    try:
        from config.settings import settings

        if settings.redis_url:
            import redis.asyncio as aioredis

            _c4_redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    except Exception as e:  # noqa: silent — logged; the count falls back to this process's memory
        _c4_redis_failed = True
        logger.warning(f"[proactive] C4 counts in memory only: Redis unavailable ({type(e).__name__})")
    return _c4_redis


async def c4_shown(arm: str) -> int:
    """How many cards the C4 side `arm` has shown (Redis when it answers, else this process's count)."""
    client = _c4_client()
    if client is not None:
        try:
            raw = await client.get(_C4_KEY.format(arm=arm))
            return max(int(raw or 0), C4_SHOWN.get(arm, 0))
        except Exception as e:  # noqa: silent — logged; this process's count answers instead
            logger.warning(f"[proactive] C4 count not read ({type(e).__name__}): this process's count used")
    return C4_SHOWN.get(arm, 0)


async def c4_count(arm: str) -> None:
    """One more card shown on side `arm`."""
    C4_SHOWN[arm] = C4_SHOWN.get(arm, 0) + 1
    client = _c4_client()
    if client is None:
        return
    try:
        await client.incr(_C4_KEY.format(arm=arm))
    except Exception as e:  # noqa: silent — logged; the process's own count still went up
        logger.warning(f"[proactive] C4 count not written ({type(e).__name__})")


async def c4_running(now: Optional[datetime] = None) -> bool:
    """The early side may still show cards: the test started, its switch is on, its cap not reached."""
    from app.flags import flag

    if not tests_started(now) or not flag("KAYAK_EARLY_TEST", True):
        return False
    return await c4_shown("early") < C4_CAP


def offer(
    visitor_id: Optional[str], trigger: str, trip: Optional[TripContext], site: Optional[str] = None,
    refused: Optional[list[str]] = None, lang: Optional[str] = None, device: Optional[str] = None,
    place: Optional[str] = None, c4: Optional[str] = None, c4_open: bool = True, team_online: bool = False,
) -> dict:
    """Should this visitor see the card now, and what does it say.

    Always answers (never raises): {"show", "arm", "trigger", "message",
    "quick_replies"}, and "variant" when the words were one of the variants
    (the widget sends it back with the answer, so /chat/init writes the same
    words). The control arm gets show=False so the widget can count the moment
    without showing anything — the comparison the test rests on. `refused`:
    the kinds of the fields the quote form refused (quote_variant).

    The widget may also say the browser's language (`lang`, ADR-9b), whether it
    is a phone (`device`: the card's own channel, ADR-3a), that it can put a
    row beside the refused quote form (`place="form"`, ADR-4a), and which side
    of the C4 test it is on (`c4`; `c4_open`: the early side still runs).
    On a card that says the whole trip back the answer carries `confirm` (the
    instant answer to its yes, A2: card_words.confirm) and `channel` (the
    card's own channel button, C1/C2); the row carries `buttons`.
    """
    from app.services import card_words

    PROACTIVE_HEALTH["offers"] += 1
    lang = proactive_i18n.card_lang(lang)
    if trigger == NOTE:
        # The visitor tapped the note: the words, whatever the experiment's
        # switches say; its own switch off → "no", and the widget shows the form.
        if not note_enabled():
            return {"show": False, "arm": "off", "trigger": trigger, "message": None, "quick_replies": []}
        # With no destination named, the bandit picks how the first question is asked; the widget
        # sends the variant back with the answer, so /chat/init writes the same words and counts them.
        variant = note_variant(trip)
        text, replies = compose(trigger, trip, site, variant, lang)
        PROACTIVE_HEALTH["shown"] += 1
        out = {"show": True, "arm": NOTE, "trigger": trigger, "message": text, "quick_replies": replies}
        if variant:
            out["variant"] = variant
        return _with_after_words(out, trigger, trip, lang, device, variant, team_online)
    if not enabled():
        return {"show": False, "arm": "off", "trigger": trigger, "message": None, "quick_replies": []}
    if trigger not in active_triggers():
        # This moment alone is off: switching the ads card off must not take the reader from its visitors too.
        return {"show": False, "arm": TRIGGER_OFF, "trigger": trigger, "message": None, "quick_replies": []}
    arm = arm_for(visitor_id)
    if arm != "proactive":
        return {"show": False, "arm": arm, "trigger": trigger, "message": None, "quick_replies": []}
    if trigger == "kayak" and c4 == "early" and not c4_open:
        # The early side of C4 is over: this visitor's card comes at the usual moment instead.
        return {"show": False, "arm": C4_HOLD, "trigger": trigger, "message": None, "quick_replies": []}
    if trigger == "ads":
        trip = with_keyword(trip)
        if not _names_a_destination(trip):
            # ADR A5: the widget asked on the ad's search alone and it names no place this card can say: this
            # visit's ads moment is off (the reader may still come), as when the moment is switched off.
            return {"show": False, "arm": TRIGGER_OFF, "trigger": trigger, "message": None, "quick_replies": [],
                    "reason": "no_place"}
    if trigger == "quote":
        variant = ROW if place == "form" else quote_variant(refused)
    elif trigger == "kayak":
        variant = beat_variant(visitor_id, trip)
    else:
        variant = None
    text, replies = compose(trigger, trip, site, variant, lang)
    PROACTIVE_HEALTH["shown"] += 1
    out = {"show": True, "arm": arm, "trigger": trigger, "message": text, "quick_replies": replies}
    if variant:
        out["variant"] = variant
    face = card_face(trigger, trip, variant, lang)
    if face:
        out.update(face)
    if variant == ROW:
        out["buttons"] = card_words.row_buttons(lang)
        # Its language too, as every other card says it: the widget writes the opening in it (CodeRabbit #270).
        if lang:
            out["lang"] = lang
        return out
    return _with_after_words(out, trigger, trip, lang, device, variant, team_online)


def _with_after_words(
    out: dict, trigger: str, trip: Optional[TripContext], lang: Optional[str], device: Optional[str],
    variant: Optional[str], team_online: bool,
) -> dict:
    """The card's answer with what comes after its yes (A2) and its own channel button (C1), when it says
    the whole trip back. The quote card that offered the chat instead of the form keeps its own way."""
    from app.services import card_words

    if lang:
        out["lang"] = lang
    if trigger == "ads" and not full_trip(trip) and _names_a_destination(with_keyword(trip)):
        # ADR A7 (20 Nov): the ads card names a place but no dates, so no instant answer to its yes; its own
        # channel button still lets the visitor leave one contact in one tap (and stays as the pill).
        out["channel"] = card_words.card_channel(card_words.device_channel(device), lang)
        return out
    if not full_trip(trip) or variant == CONTACT:
        return out
    line = _trip_line(trip, lang) or ""
    out["confirm"] = {
        "message": card_words.confirm(line, lang, beat=applied_variant(trigger, trip, variant) == BEAT),
        "buttons": card_words.confirm_buttons(lang, team_online),
    }
    if trigger != NOTE:
        out["channel"] = card_words.card_channel(card_words.device_channel(device), lang)
    return out


def init_metadata(
    trigger: str, trip: Optional[TripContext], variant: Optional[str] = None, lang: Optional[str] = None,
) -> dict:
    """Conversation metadata written at creation: which moment opened the chat,
    the search as the page gave it (for the panel), and the variant of the words
    (note_bandit counts the note's chats by it; CONTACT marks the quote card that
    offered the chat instead of the form). A card shown in Spanish or French
    keeps that language for the chat (`client_lang`: the fixed replies speak it,
    app.pipeline.language). Server keys only."""
    meta: dict = {"proactive_trigger": trigger, "proactive_at": datetime.now(timezone.utc).isoformat()}
    if trip and not trip.is_empty():
        meta["proactive_trip"] = trip.model_dump(mode="json", exclude_none=True)
    if known_variant(variant):
        meta["proactive_variant"] = variant
    lang = proactive_i18n.card_lang(lang)
    if lang:
        meta["client_lang"] = lang
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
    variant: Optional[str] = None, lang: Optional[str] = None,
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
    # The variant the widget sent back, kept only where it shaped the words (the note with no destination
    # named, the quote card's CONTACT or ROW, the Kayak card's BEAT).
    variant = applied_variant(trigger, trip, variant)
    lang = proactive_i18n.card_lang(lang)
    text, _ = compose(trigger, trip, site, variant, lang)
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
    await db._merge_conv_metadata(conversation_id, init_metadata(trigger, trip, variant, lang))
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
