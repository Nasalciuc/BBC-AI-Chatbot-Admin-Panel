"""The trip a visitor was looking at, read by the widget from the page.

Kayak sends the whole search in the landing link (from, to, depart_at,
return_at, adults, cabin_class…), /search carries the same in its query, and
route pages name the city in their title. The widget reads it so the
proactive chat can open with the visitor's own search instead of asking for
it again.

Everything here comes from a URL or a page title, so every field is checked
on its own: a bad field is dropped (None), never the whole request — a
visitor with a mangled return date still gets a card with the route. The
values only ever reach a message through `app.services.proactive`, which
composes the words itself; nothing typed in a URL is copied into a prompt,
a message or the panel as free text.
"""

from __future__ import annotations

from datetime import date, timedelta
import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

# How far ahead a trip can be: airlines sell ~330 days out; a little slack.
_MAX_DAYS_AHEAD = 400
_NAME_EXTRA = set(" .'-(),")


def _code(v) -> Optional[str]:
    """An IATA airport or city code: exactly three letters."""
    if not isinstance(v, str):
        return None
    s = v.strip().upper()
    return s if len(s) == 3 and s.isascii() and s.isalpha() else None


def _name(v) -> Optional[str]:
    """A city as a page writes it: letters (any script), spaces and . ' - ( ) ,
    2–40 characters, starting with a letter. Digits, symbols, markup → None."""
    if not isinstance(v, str):
        return None
    s = " ".join(v.split())
    if not 2 <= len(s) <= 40 or not s[0].isalpha():
        return None
    if not all(ch.isalpha() or ch in _NAME_EXTRA for ch in s):
        return None
    return s


_KEYWORD_KEEP = re.compile(r"[^a-z0-9 '&-]+")


def _keyword(v) -> Optional[str]:
    """What the visitor typed in Google or Bing before the ad (utm_term), as the card may read it: lower case,
    letters, digits, spaces and ' & - only, 3–80 characters with at least three letters. An ad id, markup or
    anything longer → None. Never said back as text: app.services.proactive reads a place and a cabin from it."""
    if not isinstance(v, str):
        return None
    s = " ".join(_KEYWORD_KEEP.sub(" ", v.lower()).split())
    if not 3 <= len(s) <= 80 or sum(ch.isalpha() for ch in s) < 3:
        return None
    return s


def _today() -> date:
    return date.today()


class TripContext(BaseModel):
    """What the page says the visitor searched for. Every field is optional.

    `keyword` (20 Nov 2026): the search an ad visitor typed before the click. Not a field of the trip: the
    ads card reads the destination and the cabin from it (proactive.with_keyword), and it never reaches a
    lead (_entities_from_trip, is_empty leave it out)."""

    model_config = ConfigDict(extra="ignore")

    source: Literal["kayak", "search", "route_page", "title"] = "title"
    origin_code: Optional[str] = None
    destination_code: Optional[str] = None
    origin_name: Optional[str] = None
    destination_name: Optional[str] = None
    depart_date: Optional[date] = None
    return_date: Optional[date] = None
    trip_type: Optional[Literal["round_trip", "one_way", "multi_city"]] = None
    cabin: Optional[Literal["economy", "premium_economy", "business", "first"]] = None
    adults: Optional[int] = None
    children: Optional[int] = None
    infants: Optional[int] = None
    keyword: Optional[str] = None

    @field_validator("keyword", mode="before")
    @classmethod
    def _keyword(cls, v):
        return _keyword(v)

    @field_validator("source", mode="before")
    @classmethod
    def _source(cls, v):
        return v if v in ("kayak", "search", "route_page", "title") else "title"

    @field_validator("origin_code", "destination_code", mode="before")
    @classmethod
    def _codes(cls, v):
        return _code(v)

    @field_validator("origin_name", "destination_name", mode="before")
    @classmethod
    def _names(cls, v):
        return _name(v)

    @field_validator("depart_date", "return_date", mode="before")
    @classmethod
    def _dates(cls, v):
        if isinstance(v, date):
            d = v
        elif isinstance(v, str):
            try:
                d = date.fromisoformat(v.strip()[:10])
            except ValueError:  # noqa: silent — a date the link cannot spell is no date, not an error
                return None
        else:
            return None
        today = _today()
        if d < today or d > today + timedelta(days=_MAX_DAYS_AHEAD):
            return None
        return d

    @field_validator("trip_type", mode="before")
    @classmethod
    def _trip_type(cls, v):
        if not isinstance(v, str):
            return None
        s = v.strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "round_trip": "round_trip", "roundtrip": "round_trip", "return": "round_trip",
            "one_way": "one_way", "oneway": "one_way",
            "multi_city": "multi_city", "multicity": "multi_city",
        }
        return aliases.get(s)

    @field_validator("cabin", mode="before")
    @classmethod
    def _cabin(cls, v):
        if not isinstance(v, str):
            return None
        s = v.strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "economy": "economy", "coach": "economy",
            "premium_economy": "premium_economy", "premium": "premium_economy",
            "business": "business", "business_class": "business",
            "first": "first", "first_class": "first",
        }
        return aliases.get(s)

    @field_validator("adults", mode="before")
    @classmethod
    def _adults(cls, v):
        n = _int(v)
        return n if n is not None and 1 <= n <= 9 else None

    @field_validator("children", "infants", mode="before")
    @classmethod
    def _kids(cls, v):
        n = _int(v)
        return n if n is not None and 0 <= n <= 8 else None

    @model_validator(mode="after")
    def _consistent(self):
        # A return before the departure is a mangled link: keep the departure.
        if self.depart_date and self.return_date and self.return_date < self.depart_date:
            self.return_date = None
        # A lap infant needs an adult each.
        if self.infants and (self.adults or 0) < self.infants:
            self.infants = None
        # Nine seats is the most one booking takes.
        if (self.adults or 0) + (self.children or 0) + (self.infants or 0) > 9:
            self.children = None
            self.infants = None
        if self.origin_code and self.origin_code == self.destination_code:
            self.destination_code = None
        return self

    @property
    def has_route(self) -> bool:
        return bool((self.origin_code or self.origin_name) and (self.destination_code or self.destination_name))

    @property
    def passengers(self) -> Optional[int]:
        if not self.adults:
            return None
        return self.adults + (self.children or 0) + (self.infants or 0)

    def is_empty(self) -> bool:
        return not any((
            self.origin_code, self.destination_code, self.origin_name, self.destination_name,
            self.depart_date, self.return_date, self.cabin, self.adults,
        ))


def _int(v) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    # ASCII digits only: "²" passes str.isdigit() and int() refuses it, which
    # would fail the whole request instead of dropping this one field.
    if isinstance(v, str) and v.strip().isascii() and v.strip().isdigit():
        return int(v.strip())
    return None
