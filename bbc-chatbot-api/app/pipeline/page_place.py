"""The place a page is about is not the client's trip until the client says it.

#3823 (27 Nov 2026): the quote card opened on a destination page. The page's city went onto the lead as the
destination, the prompt called it the client's own search ("treat it as known"), and the bot told the client
their trip was to that city; they wanted another one.

A page names a place two ways: the trip the proactive card read from it when it is the page's own (a route
page, its title: TripContext.from_the_page; a search the visitor ran, on Kayak or the site, is theirs), and the
city its address is about (the landing hint the prompt reads, entity_extractor.destination_from_path). The seed
no longer writes the first (proactive._entities_from_trip). The AI's tool reads the whole conversation, the
prompt's page lines with it, and fills either as the client's: its origin or destination is merged only once
the client has written that place (its name or its code) or said yes to a question that named it.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# The start of a client's yes. Not "ok": it acknowledges more often than it agrees ("ok, from Chicago"). "Right"
# only alone ("Right.", "right, from Chicago"), never "right now…".
_YES_RE = re.compile(
    r"^\W*(?:yes|yeah|yea|yep|yup|ya|sure|correct|right(?=\s*(?:[.!,]|$))|exactly|indeed|absolutely|definitely"
    r"|of\s+course|that'?s\s+(?:right|correct|it)|sounds\s+(?:right|good)|da|s[ií]|sim|oui)\b",
    re.I,
)


def _path(url) -> Optional[str]:
    """The address's path: a query string's words (an ad's search, a click id) name no page."""
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        return urlsplit(url.strip()).path or None
    except ValueError:  # noqa: silent — an address urlsplit cannot read names no place; nothing is held back
        return None


def page_places(meta: Optional[dict]) -> dict[str, set[str]]:
    """The places the page names, as {IATA code: the words that name it, lower case}: the route page's trip
    (TripContext.from_the_page) and the city of the page's address, without the codes of a search the
    visitor ran. {} when the page names none."""
    from app.models.trip_context import TripContext
    from app.pipeline.entity_extractor import AIRPORTS, CITY_TO_CODE, destination_from_path

    meta = meta if isinstance(meta, dict) else {}
    places: dict[str, set[str]] = {}
    searched: set[str] = set()

    def _add(code: Optional[str], name: Optional[str]) -> None:
        name = (name or "").strip().lower()
        for c in {code, CITY_TO_CODE.get(name)} - {None}:
            places.setdefault(c, set()).update(
                {n for n, mapped in CITY_TO_CODE.items() if mapped == c and len(n) >= 4})
            if name:
                places[c].add(name)

    raw = meta.get("proactive_trip")
    if isinstance(raw, dict):
        try:
            trip = TripContext(**raw)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"page_place: stored trip unreadable ({type(e).__name__}) — its places not held back")
            trip = None
        if trip is not None and trip.from_the_page:
            _add(trip.origin_code, trip.origin_name)
            _add(trip.destination_code, trip.destination_name)
        elif trip is not None:
            searched = {c for c in (trip.origin_code, trip.destination_code) if c}

    city = destination_from_path(_path(meta.get("page_url") or meta.get("landing_page")))
    if city:
        _add(city if city in AIRPORTS else None, None if city in AIRPORTS else city)
    for code in searched:
        places.pop(code, None)
    return places


def _names(text: Optional[str], code: str, words: set[str]) -> bool:
    """`text` names the place: its code as the client types it (upper case, alone), or one of its words."""
    if not text:
        return False
    if re.search(r"(?<![A-Za-z])" + re.escape(code) + r"(?![A-Za-z])", text):
        return True
    return any(re.search(r"\b" + re.escape(w) + r"\b", text, re.I) for w in words)


def _questions(text: Optional[str]) -> list[str]:
    """The questions of one of our messages: a yes answers a question, never a statement beside it
    ("Barcelona fares are available. Shall I send you a quote?", or with a semicolon, a colon or a dash, asks
    nothing about Barcelona: CodeRabbit #291)."""
    return re.findall(r"[^.!?;:\n—–]*\?", text or "")


def _client_words(history: Optional[list], message: str) -> tuple[list[str], list[str]]:
    """(the client's messages, the questions of ours the client answered yes to), over the history and this
    message. A yes answers our message just before it: "yes" to "Is Barcelona where you'd like to go?" still
    counts on the turn after, when the client answers where they fly from (CodeRabbit #291)."""
    from app.pipeline.contact_capture import last_question

    said: list[str] = []
    agreed: list[str] = []
    ours = ""
    for m in history or []:
        if not isinstance(m, dict):
            continue
        content = m.get("content") or ""
        if m.get("role") in ("ai", "agent"):
            ours = content
        elif m.get("role") == "user":
            said.append(content)
            if ours and _YES_RE.match(content):
                agreed.extend(_questions(ours))
    # This message: the history holds it when it was saved first; read again either way.
    said.append(message or "")
    if _YES_RE.match(message or ""):
        agreed.extend(_questions(last_question(history)))
    return said, agreed


def held_back(tool_entities: Optional[dict], meta: Optional[dict], history: Optional[list], message: str,
              lead: Optional[dict]) -> set[str]:
    """The keys of the tool's reading ("origin", "destination") that are a place the page names and the
    client has not said: not in their messages (history and this one), and no yes to a question of ours that
    named it. A place the lead already holds is not held back: nothing changes."""
    places = page_places(meta)
    if not places or not isinstance(tool_entities, dict):
        return set()
    said, agreed = _client_words(history, message)
    lead = lead if isinstance(lead, dict) else {}
    held: set[str] = set()
    for key, column in (("origin", "origin_code"), ("destination", "destination_code")):
        code = tool_entities.get(key)
        code = code.strip().upper() if isinstance(code, str) else None
        if not code or code not in places or str(lead.get(column) or "").upper() == code:
            continue
        words = places[code]
        if any(_names(text, code, words) for text in said + agreed):
            continue
        held.add(key)
    return held
