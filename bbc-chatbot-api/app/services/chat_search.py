"""The panel's search box: what was typed decides where to look, and nothing typed can change the filter.

Hole A (found 21 Nov 2026): the text went into PostgREST's or=(...) as it was. A comma in it started a
new condition outside the blind-take guard, so an agent could find a waiting chat by an e-mail they are
not allowed to see ("a%,visitor_email.ilike.%gmail" matched every Gmail address). Now every value inside
an or=()/and=() goes in double-quoted, with PostgREST's own escapes (a backslash before \\ and "), and
LIKE's wildcards in it match themselves: a comma, a parenthesis or a dot is just a character of the search.

PostgREST's grammar, read in its source (v12, ApiRequest/QueryParams.hs and Query/SqlFragment.hs):
  - inside or=()/and=() a value may be double-quoted; inside the quotes a backslash makes the next
    character plain. A top-level filter (column=ilike.value) takes the rest of the parameter as it is,
    quotes included: `quoted` is for logic trees only, never for .eq()/.ilike() on the query itself.
  - like/ilike turn every * into %, quoted or not. A * the client typed cannot match only itself, so
    `like_literal` makes it _ (any one character): it still finds the *, and adds no condition.
  - match/imatch (~, ~*) get the value as it is: the phone fallback and the source chips use them.

What was typed (parse):
  "3403", "#3403"            the chat number, for everyone
  anything with "@"          an e-mail
  7 digits or more, with any + - . ( ) / or spaces between them
                             a phone, compared on its digits: "+1 (214) 208-5185" finds 2142085185
  three capital letters      an airport, departure or arrival ("IST")
  anything else              a name, or a city in the route ("Istanbul")
An e-mail, a phone, an airport or a city is the trip or the contact: for an agent they match only chats
the agent may read (the blind take, conversations._taken_blind and supabase._readable_to_agent).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.pipeline.entity_extractor import COMPARISON_CLICK_IDS, COMPARISON_SOURCES

_SEPARATORS = re.compile(r"[\s+\-.()/]")
_AIRPORT = re.compile(r"[A-Z]{3}")
_ASCII_DIGITS = re.compile(r"[0-9]+")
MAX_CHAT_NUMBER_DIGITS = 6
MIN_PHONE_DIGITS = 7
MIN_ROUTE_TEXT = 3   # a city is looked for in the routes from three letters on

# Per instance, in /health (`search`). Nothing here is on the money path; a failure still says so.
SEARCH_HEALTH: dict = {"route_read_failed": 0, "messages_failed": 0, "rate_limited": 0, "recent_failed": 0}


@dataclass(frozen=True)
class Query:
    kind: str     # number | email | phone | airport | text
    value: str    # what to look for: the number, the e-mail, the digits, the code, the text


def parse(text: Optional[str]) -> Optional[Query]:
    raw = (text or "").strip()
    if not raw:
        return None
    number = raw[1:] if raw.startswith("#") else raw
    if _ASCII_DIGITS.fullmatch(number) and len(number) <= MAX_CHAT_NUMBER_DIGITS:
        return Query("number", str(int(number)))
    if "@" in raw:
        return Query("email", raw.lower())
    squeezed = _SEPARATORS.sub("", raw)
    if _ASCII_DIGITS.fullmatch(squeezed) and len(squeezed) >= MIN_PHONE_DIGITS:
        return Query("phone", squeezed)
    if _AIRPORT.fullmatch(raw):
        return Query("airport", raw)
    return Query("text", raw)


def quoted(value: str) -> str:
    """A value PostgREST reads as one literal inside or()/and(): double quotes, \\ and \" escaped."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def like_literal(text: str) -> str:
    """The client's text inside a LIKE pattern: its % _ and \\ match themselves, its * any one character."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").replace("*", "_")


def contains(column: str, text: str, op: str = "ilike") -> str:
    """`column` contains `text`, as one quoted PostgREST condition (for a logic tree)."""
    return f"{column}.{op}.{quoted('%' + like_literal(text) + '%')}"


def contains_pattern(text: str) -> str:
    """The same `contains` for a top-level .ilike()/.like(), which takes its value unquoted."""
    return "%" + like_literal(text) + "%"


def phone_regex(digits: str) -> str:
    """Without the digits column (054): these digits in a row, with only separators between them."""
    return "[^0-9]*".join(digits)


def phone_condition(digits: str, *, digits_column: bool) -> str:
    if digits_column:
        return f"visitor_phone_digits.like.{quoted('%' + digits + '%')}"
    return f"visitor_phone.match.{quoted(phone_regex(digits))}"


def airport_condition(code: str) -> str:
    """For the leads table (reference_table="leads"): departure or arrival."""
    return f"origin_code.eq.{quoted(code)},destination_code.eq.{quoted(code)}"


def guarded(condition: str, readable: Optional[str]) -> str:
    """The condition, and for an agent only on chats they may read."""
    return f"and(or({condition}),{readable})" if readable else condition


def ids_condition(ids: list[str]) -> Optional[str]:
    """Conversation ids (UUIDs from our own read) as one in() condition; None when there are none."""
    clean = [i for i in ids if re.fullmatch(r"[0-9a-fA-F-]{36}", i or "")]
    return f"id.in.({','.join(clean)})" if clean else None


# ── where the client came from: the three chips of the filter row ──────────
#
# The same reading as the pipeline's (entity_extractor.is_comparison_origin, infer_paid_source), on the
# chat's metadata. Production, 30 days to 21 Nov: Kayak arrives as utm_source=kayak with utm_medium=cpc,
# so an ad is a paid signal on a chat that is NOT a comparison site; "Google" comes capitalised, and
# Facebook as paid_social. Every term is false (never NULL) on a chat without the key, so "site", the
# chats that are neither, is exactly their complement.

PAID_CLICK_IDS = ("gclid", "msclkid", "fbclid")
PAID_MEDIUMS = ("cpc", "ppc", "paid", "paidsearch", "paid_search", "paidsocial", "paid_social", "cpm")
SOURCES = ("kayak", "ads", "site")


def _present(key: str) -> str:
    return f"and(metadata->>{key}.not.is.null,metadata->>{key}.neq.\"\")"


def _one_of(key: str, words) -> str:
    """The key, trimmed and in any case, is one of `words` (no backslash inside the f-string: Python 3.11)."""
    pattern = quoted(r"^\s*(" + "|".join(re.escape(w) for w in sorted(words)) + r")\s*$")
    return f"and(metadata->>{key}.not.is.null,metadata->>{key}.imatch.{pattern})"


def _comparison_terms() -> list[str]:
    return [_one_of("utm_source", COMPARISON_SOURCES)] + [_present(k) for k in COMPARISON_CLICK_IDS]


def _paid_terms() -> list[str]:
    return [_present(k) for k in PAID_CLICK_IDS] + [_one_of("utm_medium", PAID_MEDIUMS)]


def source_conditions(source: str) -> list[str]:
    """The or=() groups (each ANDed with the rest) that keep the chats from this source."""
    kayak, paid = ",".join(_comparison_terms()), ",".join(_paid_terms())
    if source == "kayak":
        return [kayak]
    if source == "ads":
        return [f"not.or({kayak})", paid]
    if source == "site":
        return [f"not.or({kayak},{paid})"]
    raise ValueError(f"unknown source {source!r}")
