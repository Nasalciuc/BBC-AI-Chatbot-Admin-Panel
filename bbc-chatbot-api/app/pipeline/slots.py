"""Slot policy (PR-7): what to ask next, in the measured order, one per turn.

The order comes from I.5: route → departure date → trip type → return (if round
trip) → passengers. trip_type is special: the leads column is NOT NULL DEFAULT
'round_trip', so it always looks filled; the slot counts as answered only when
the client actually said it (trip_notes.trip_type_source == 'client'), tracked
here. One question per turn: a reply with no '?' while details are missing gets
the next slot's question appended; a second question is trimmed.
All of this is behind SLOT_POLICY_ENABLED — off = today's behaviour.
"""

from __future__ import annotations

import re
from typing import Optional

SLOTS_HEALTH = {"question_appended": 0, "second_question_trimmed": 0}

# slot key → default question (the measured I.5 phrasings; PR-8 A/Bs variants).
DEFAULT_QUESTIONS = {
    "route": "Where are you flying from and to?",
    "departure_date": "What date would you like to depart?",
    "trip_type": "Is this a round trip, or one way?",
    "return_date": "When would you like to fly back?",
    "passengers": "How many travelers will be on this trip?",
}
_ORDER = ["route", "departure_date", "trip_type", "return_date", "passengers"]


def _trip_type_from_client(lead: dict) -> bool:
    notes = lead.get("trip_notes") or {}
    if isinstance(notes, dict) and notes.get("trip_type_source") == "client":
        return True
    # A real return_date is itself proof the client described a round trip.
    return bool(lead.get("return_date"))


def ordered_missing(lead: dict) -> list[str]:
    """The slot keys still missing, in the ask order."""
    lead = lead or {}
    missing: list[str] = []
    has_route = bool(lead.get("origin_code") and lead.get("destination_code"))
    if not has_route:
        missing.append("route")
    if not lead.get("departure_date"):
        missing.append("departure_date")
    if not _trip_type_from_client(lead):
        missing.append("trip_type")
    elif (lead.get("trip_type") == "round_trip") and not lead.get("return_date"):
        missing.append("return_date")
    if not lead.get("passengers"):
        missing.append("passengers")
    return [s for s in _ORDER if s in missing]


def next_slot(lead: dict) -> Optional[str]:
    m = ordered_missing(lead)
    return m[0] if m else None


def question_for(slot: Optional[str], variants: Optional[dict] = None) -> Optional[str]:
    if not slot:
        return None
    if variants and variants.get(slot):
        return variants[slot]
    return DEFAULT_QUESTIONS.get(slot)


_QMARK = re.compile(r"[^?]*\?")


def ensure_one_question(text: str, slot_question: Optional[str]) -> str:
    """Exactly one question while details are missing.

    No '?' and a slot to ask → append the slot's question (counted). Two or more
    '?' (beyond the combined 'from and to') → trim to the first (counted)."""
    body = (text or "").strip()
    qcount = body.count("?")
    if qcount == 0:
        if slot_question:
            SLOTS_HEALTH["question_appended"] += 1
            joiner = " " if body and not body.endswith((".", "!", "?")) else " "
            return (body + (joiner if body else "") + slot_question).strip()
        return body
    if qcount >= 2:
        m = _QMARK.search(body)
        if m:
            SLOTS_HEALTH["second_question_trimmed"] += 1
            return body[: m.end()].strip()
    return body


def _reset_for_tests() -> None:
    SLOTS_HEALTH.update(question_appended=0, second_question_trimmed=0)
