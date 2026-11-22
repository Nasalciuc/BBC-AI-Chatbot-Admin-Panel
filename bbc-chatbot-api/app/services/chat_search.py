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


# ── a reader whose rows are masked (a supervisor, app.security.pii): what is hidden is found only whole ──
#
# Their rows show •••ce, gr•••@example.com and •••0185. A search that matched part of what is hidden would
# give it back a letter at a time (type "gr", "gra", "grac" and watch which chats stay). For them the
# e-mail matches only whole, the number only whole (with or without a country code) and the name by whole
# words: a complaint's e-mail, number or name still finds its chat (the owner's choice, 22 Nov 2026).
# Regexes, never LIKE: PostgREST turns a typed * into a wildcard, and one wildcard per position would read
# the address back letter by letter all the same. Every regex here reads the same in Postgres (ARE) and
# Python's re, so tests/postgrest_eval.py holds them to rows.

MIN_WHOLE_PHONE_CORE = 10   # a country code is told apart only from a number this long
_CC_MAX = 3                 # a country code's digits
_WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*")
_SPACES = r"\s*"
_WORD_START, _WORD_END = r"(^|\W)", r"(\W|$)"


def rx_literal(text: str) -> str:
    """`text` as a regex that matches only itself: every character but a letter or a digit escaped."""
    return "".join(ch if ch.isalnum() else "\\" + ch for ch in text)


def email_whole(value: str) -> str:
    """The whole address, in any case, spaces around it allowed."""
    return f"visitor_email.imatch.{quoted('^' + _SPACES + rx_literal(value) + _SPACES + '$')}"


def phone_whole_condition(digits: str, *, digits_column: bool) -> str:
    """The number by all its digits: as kept, or one of the two with a country code (1 to 3 digits) the other
    lacks, the rest being 10 digits or more. Around a shorter number "a code" would be any three digits: a
    7-digit guess and the four digits the row shows would find a 10-digit number three digits at a time."""
    forms = [digits] + [digits[k:] for k in range(1, _CC_MAX + 1) if len(digits) - k >= MIN_WHOLE_PHONE_CORE]
    with_code = len(digits) >= MIN_WHOLE_PHONE_CORE  # kept with a code the typed number lacks
    if digits_column:
        parts = [f"visitor_phone_digits.eq.{quoted(f)}" for f in forms]
        if with_code:
            parts.append(f"visitor_phone_digits.match.{quoted('^[0-9]{1,3}' + digits + '$')}")
        return ",".join(parts)
    # Without 054: the kept text, the separators between and around its digits skipped.
    def whole(d: str, code: bool) -> str:
        lead = "([0-9][^0-9]*){1,3}" if code else ""
        return f"visitor_phone.match.{quoted('^[^0-9]*' + lead + phone_regex(d) + '[^0-9]*$')}"

    parts = [whole(f, False) for f in forms]
    if with_code:
        parts.append(whole(digits, True))
    return ",".join(parts)


def name_words_condition(text: str) -> Optional[str]:
    """Every word typed is a whole word of the name, in any case: "grace" finds Grace Example, "gra" does not.
    None when nothing typed is a word."""
    words = _WORD.findall(text)
    if not words:
        return None
    parts = [f"visitor_name.imatch.{quoted(_WORD_START + rx_literal(w) + _WORD_END)}" for w in words]
    return parts[0] if len(parts) == 1 else f"and({','.join(parts)})"


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
