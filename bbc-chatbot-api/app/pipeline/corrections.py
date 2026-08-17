"""What a post-summary correction actually MEANS — pure decisions.

Four live transcripts, one blind spot each:

MARKY    "And return from Paris to Sydney on nov 9" — a third city that is
         neither origin nor destination. The pipeline overwrote the route
         with it, so the trip he described silently disappeared.
KAZUO    corrections that must UNSET or REJECT: an explicit one-way clears
         the return date; a return BEFORE departure is impossible; a
         return ON the departure day is almost certainly a mistake; "X or
         Y" between two dates is a choice, not a value.
ALISTAIR named a field with no value ("round trip dates", "change the
         dates") and got the same summary back, unchanged, three times.
LOOP     the same summary rendered again and again is not a conversation.

The orchestrator ACTS on these; the decisions live here so they can be
read and tested without a pipeline around them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

# A field named without any value attached. Ordered: the first match wins,
# and "round trip dates" must read as DATES, not as a trip-type change.
# Bare "when" and "seats" are NOT field names: "I need to know when you
# will call" is not a request to change the dates. Every token here has
# to be unambiguous on its own.
_FIELD_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("dates", re.compile(r"\b(?:dates?|departure\s+date|travel\s+days?)\b", re.I)),
    ("route", re.compile(r"\b(?:route|cities|destination|origin|airport)\b", re.I)),
    ("passengers", re.compile(r"\b(?:passengers?|travell?ers?|pax)\b", re.I)),
    ("cabin", re.compile(r"\b(?:cabin|cabin\s+class|travel\s+class)\b", re.I)),
)
# "change the dates", "the dates are wrong", "round trip dates" — a request
# ABOUT a field. "need"/"want" are deliberately absent: they turn ordinary
# questions ("I need to know when you will call") into field asks.
_CHANGE_CUE_RE = re.compile(
    r"\b(?:change|fix|correct|update|wrong|incorrect|different|not\s+right|"
    r"round\s*trip|one\s*way)\b",
    re.I,
)


# "And return from Paris to Sydney on nov 9" — the client is ADDING a leg.
# LEADING only. A coordinating "and" in the middle of a sentence
# ("fix the route, Miami to Boston and 2 passengers") is not an added
# leg — reading it as one filed the correction as decoration and left
# the client staring at the old route.
_ADD_CUE_RE = re.compile(
    r"^\s*(?:and|then|also|plus|additionally|after\s+that)\b", re.I
)
# The trip type must be stated about the TRIP, not inferred from a word
# like "single" that usually describes a traveler.
_EXPLICIT_ONE_WAY_RE = re.compile(
    r"\b(?:one[\s\-]?way|only\s+going|no\s+return|without\s+a?\s*return|"
    r"drop\s+the\s+return|cancel\s+the\s+return)\b",
    re.I,
)
# "Actually from Miami…", "no, make it…" — the client is REPLACING a value.
_REPLACE_CUE_RE = re.compile(
    r"\b(?:actually|instead|rather|no[,\s]+make\s+it|change\s+it\s+to|"
    r"not\s+\w+\s+but|correction)\b",
    re.I,
)


@dataclass
class CorrectionOutcome:
    """What the pipeline must do with this correction turn."""

    # Ask instead of re-rendering. One of: return_before_departure,
    # return_equals_departure, date_choice, field:<name>, return_missing.
    ask: Optional[str] = None
    ask_options: list = field(default_factory=list)
    # MARKY: an additional client-stated leg (never an endpoint edit).
    extra_leg: Optional[dict] = None
    multi_city: bool = False
    # KAZUO: an explicit one-way wipes a stale return date.
    clear_return: bool = False
    # Values the probe produced that must NOT be written (rejected pairs).
    drop_return: bool = False


def detect_field_only_correction(message: str, probe) -> Optional[str]:
    """The client named a FIELD but gave no value — ask for that field.

    Returns 'dates' | 'route' | 'passengers' | 'cabin', or None."""
    if not _CHANGE_CUE_RE.search(message or ""):
        return None
    # Any concrete value in the probe means this is a real correction, not
    # a request to be asked.
    for attr in (
        "origin_code", "destination_code", "departure_date",
        "return_date", "passengers", "cabin_class",
    ):
        if getattr(probe, attr, None):
            return None
    for name, pattern in _FIELD_PATTERNS:
        if pattern.search(message):
            return name
    return None


def _as_date(value) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def detect_extra_leg(probe, lead: dict, message: str) -> Optional[dict]:
    """MARKY: a leg between two cities the trip doesn't currently touch.

    Requires BOTH an additive cue ("And return from Paris to Sydney…")
    and two endpoints the trip doesn't have. Replacement language
    ("actually from Miami…") is always an endpoint correction — and the
    cue is what makes this safe when the IATA resolver mis-reads a city
    ("San Juan" → SAN), where "both endpoints look new" alone would
    silently invent a leg."""
    if _REPLACE_CUE_RE.search(message or ""):
        return None
    if not _ADD_CUE_RE.search(message or ""):
        return None
    new_from = (probe.origin_code or "").upper()
    new_to = (probe.destination_code or "").upper()
    if not (new_from and new_to):
        return None
    known = {
        (lead.get("origin_code") or "").upper(),
        (lead.get("destination_code") or "").upper(),
    }
    known.discard("")
    if not known:
        return None  # nothing to be "third" to yet
    if new_from in known or new_to in known:
        return None
    return {
        "from": new_from,
        "to": new_to,
        "date": probe.return_date or probe.departure_date,
        "source": "client-stated",
    }


def classify_correction(probe, lead: dict, message: str) -> CorrectionOutcome:
    """Decide what this correction turn does. Asks beat writes."""
    out = CorrectionOutcome()
    lead = lead or {}

    # 1. Alternatives are a question, never a value (KAZUO).
    alternatives = getattr(probe, "date_alternatives", None)
    if alternatives:
        out.ask = "date_choice"
        out.ask_options = list(alternatives)
        out.drop_return = True
        return out

    # 2. An EXPLICIT one-way wipes any stale return date (KAZUO). The
    # extractor's one-way regex also matches "single" and "ow", so
    # "just a single traveler" would otherwise DELETE a confirmed return
    # date the client never mentioned — a destructive NULL with no way
    # back. The words must be about the trip, not about a passenger.
    if probe.trip_type == "one_way" and _EXPLICIT_ONE_WAY_RE.search(message or ""):
        out.clear_return = True

    # 3. Impossible / suspicious return pairs — reject and ASK (KAZUO).
    # BOTH sides fall back to the lead: "actually depart Nov 20" against a
    # stored return of Nov 10 is exactly the inverted trip this guards.
    dep = _as_date(probe.departure_date or lead.get("departure_date"))
    ret = _as_date(probe.return_date or lead.get("return_date"))
    if dep and ret:
        if ret < dep:
            out.ask = "return_before_departure"
            out.drop_return = True
            return out
        if ret == dep:
            out.ask = "return_equals_departure"
            out.drop_return = True
            return out

    # 4. A third city is an added leg, not an endpoint edit (MARKY).
    leg = detect_extra_leg(probe, lead, message)
    if leg:
        out.extra_leg = leg
        out.multi_city = True

    if probe.trip_type == "multi_city":
        out.multi_city = True

    return out


def needs_return_date(lead: dict) -> bool:
    """ALISTAIR: a round trip with no return date must be ASKED about,
    never rendered — an unlabelled date sent him round the loop three
    times."""
    return bool(
        (lead or {}).get("trip_type") == "round_trip"
        and not (lead or {}).get("return_date")
    )


# How many identical summaries a client may receive before the pipeline
# admits the wall isn't working.
LOOP_ESCALATION_AT = 3
LOOP_CONSULTANT_AT = 4


def summary_fingerprint(text: str) -> str:
    """Stable across processes — Python's hash() is salted per process, so
    with more than one worker the stored fingerprint would never match and
    the loop breaker would silently never fire."""
    import hashlib

    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:12]


def loop_action(render_count: int) -> Optional[str]:
    """None → render normally. 'escalate' → ask for the trip in one line.
    'consultant' → stop asking and offer a human."""
    if render_count >= LOOP_CONSULTANT_AT:
        return "consultant"
    if render_count >= LOOP_ESCALATION_AT:
        return "escalate"
    return None
