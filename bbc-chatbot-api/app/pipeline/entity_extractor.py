"""Entity extraction V1 — regex patterns for structured data from chat messages.

Extracts: email (99%), phone (95%), airport codes (100%), 
city names top-20 (100%), passengers, cabin class, name (~70%).

Does NOT extract: dates from text, verbal phone numbers, airport aliases.
Those are V2 (Claude-based extraction).
"""

import re
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from app.flags import flag

logger = logging.getLogger(__name__)


@dataclass
class ExtractedEntities:
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    origin_code: Optional[str] = None
    destination_code: Optional[str] = None
    passengers: Optional[int] = None
    cabin_class: Optional[str] = None
    departure_date: Optional[str] = None   # YYYY-MM-DD
    return_date: Optional[str] = None      # YYYY-MM-DD
    trip_type: Optional[str] = None        # one_way, round_trip
    children_count: Optional[int] = None
    infant_count: Optional[int] = None
    # Sales-methodology signals. The chat never asks for the volunteered ones —
    # it keeps them when the client offers them, for the consultant's call.
    occasion: Optional[str] = None         # business, celebration, family, medical, raw phrase
    booking_for: Optional[str] = None      # boss, parents, wife… when booking for someone else
    itinerary: Optional[str] = None        # full leg chain when >2 cities (multi-city)
    date_flexible: Optional[bool] = None
    airline_preference: Optional[str] = None
    airline_avoid: Optional[str] = None
    nonstop_only: Optional[bool] = None
    budget_hint: Optional[str] = None
    price_seen: Optional[str] = None
    best_call_time: Optional[str] = None
    # How the client wants to be reached, when they say it: "text", "email",
    # "no calls" (comma-joined). The consultant reads it before dialling.
    contact_channel: Optional[str] = None
    # What the client needs on the trip, as labels ("aisle seat; travel insurance"): needs_of().
    needs: Optional[str] = None
    # Set ONLY on the correction path (context given): "17th am" — the
    # morning/evening preference rides into the lead notes.
    time_preference: Optional[str] = None
    # Two dates the client offered as alternatives ("Oct 1 or Oct 8") —
    # the pipeline must ASK which, never choose.
    date_alternatives: Optional[list] = None


# ── Patterns ──────────────────────────────────────────────────

EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')

PHONE_RE = re.compile(
    r'(?:\+?1[-.\s]?)?\(?[2-9]\d{2}\)?[-.\s]?\d{3}[-.\s]?\d{4}'
    # A number never ends on a lone digit after a space or a dash: that digit is the next field
    # ("+61 412 345 678 2 adults", "0412 345 678 2 passengers" — CodeRabbit #270).
    r'|(?:\+\d{1,3}[-.\s]?)(?:\d[-.\s]?){7,14}\d(?<![-.\s]\d)'
    # A number typed the local way, with its trunk 0 and no country code
    # ("0412 345 678", "07911 123456"): #3296 was never taken. The country
    # comes from the visit (app.services.phone_country) when it is formatted.
    r'|(?<![\d+])0[1-9](?:[-.\s]?\d){7,11}(?!\d)(?<![-.\s]\d)'
)
# "01.12.2026", "05-10-2026 to 05-20-2026": a date has the shape of a local number.
_DATE_LIKE = re.compile(r"^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}$")

AIRPORTS = {
    "JFK", "LHR", "CDG", "FCO", "LAX", "SFO", "ORD", "MIA", "BOS", "ATL",
    "DXB", "SIN", "HKG", "NRT", "HND", "ICN", "FRA", "AMS", "MAD", "BCN",
    "IST", "DOH", "AUH", "EWR", "IAD", "DFW", "SEA", "DEN", "PHX", "LGA",
    "YYZ", "YVR", "MEX", "GRU", "SCL", "LIM", "BOG", "PTY", "SYD", "MEL",
    "ZRH", "VIE", "MUC", "CPH", "OSL", "ARN", "HEL", "LIS", "DUB", "EDI",
    # Extended coverage — popular BBC destinations
    "MXP", "LIN", "BKK", "DEL", "BOM", "CAI", "JNB", "EZE", "GIG",
    "KUL", "CGK", "MNL", "TPE", "KIX", "PEK", "PVG", "CAN",
    "NBO", "LOS", "ACC", "CMN", "ATH", "WAW", "PRG", "BUD", "OTP",
    "BRU", "NCE", "LYO", "MAN", "YUL", "YYC", "HNL",
    "LAS", "MCO", "IAH", "PHL", "CLT", "PDX", "SAN", "TPA", "MSP",
    "DTW", "SLC", "MSY", "BNA", "AUS", "RDU", "PIT", "CLE", "CMH",
    "IND", "STL", "MKE", "MCI", "CVG", "RUH",
    "KIV", "TAS", "BEG", "KBP", "TBS", "GYD", "TLL", "RIX", "VNO",
    "SOF", "ZAG", "AKL", "CUN", "MAA", "CCU", "BLR", "HYD",
    "GOI", "JED", "MCT", "KWI", "AMM", "ADD", "DAR", "KGL",
}
AIRPORT_RE = re.compile(r'\b([A-Z]{3})\b')

CITY_TO_CODE = {
    # North America
    "new york": "JFK", "nyc": "JFK", "manhattan": "JFK",
    "los angeles": "LAX", "la": "LAX",
    "san francisco": "SFO", "sf": "SFO",
    "chicago": "ORD", "miami": "MIA", "boston": "BOS",
    "atlanta": "ATL", "seattle": "SEA", "denver": "DEN",
    "dallas": "DFW", "houston": "IAH",
    "washington": "IAD", "dc": "IAD",
    "philadelphia": "PHL", "charlotte": "CLT", "phoenix": "PHX",
    "portland": "PDX", "san diego": "SAN", "tampa": "TPA",
    "minneapolis": "MSP", "detroit": "DTW", "salt lake city": "SLC",
    "new orleans": "MSY", "nashville": "BNA", "austin": "AUS",
    "raleigh": "RDU", "pittsburgh": "PIT", "cleveland": "CLE",
    "columbus": "CMH", "indianapolis": "IND",
    "st louis": "STL", "saint louis": "STL",
    "milwaukee": "MKE", "kansas city": "MCI", "cincinnati": "CVG",
    "las vegas": "LAS", "orlando": "MCO", "honolulu": "HNL",
    "toronto": "YYZ", "vancouver": "YVR", "montreal": "YUL", "calgary": "YYC",
    "mexico city": "MEX",
    # Europe
    "london": "LHR", "paris": "CDG", "rome": "FCO", "roma": "FCO",
    "milan": "MXP", "milano": "MXP",
    "frankfurt": "FRA", "amsterdam": "AMS",
    "madrid": "MAD", "barcelona": "BCN", "istanbul": "IST",
    "zurich": "ZRH", "vienna": "VIE", "munich": "MUC",
    "dublin": "DUB", "lisbon": "LIS", "copenhagen": "CPH",
    "athens": "ATH", "warsaw": "WAW", "prague": "PRG",
    "budapest": "BUD", "bucharest": "OTP",
    "helsinki": "HEL", "oslo": "OSL", "stockholm": "ARN",
    "brussels": "BRU", "nice": "NCE", "lyon": "LYO",
    "edinburgh": "EDI", "manchester": "MAN",
    # Middle East
    "dubai": "DXB", "doha": "DOH", "abu dhabi": "AUH", "riyadh": "RUH",
    # Asia Pacific
    "singapore": "SIN", "hong kong": "HKG",
    "tokyo": "NRT", "osaka": "KIX",
    "seoul": "ICN", "beijing": "PEK", "shanghai": "PVG",
    "guangzhou": "CAN", "taipei": "TPE",
    "bangkok": "BKK", "kuala lumpur": "KUL", "jakarta": "CGK",
    "manila": "MNL", "delhi": "DEL", "new delhi": "DEL", "mumbai": "BOM",
    "sydney": "SYD", "melbourne": "MEL",
    # South America
    "sao paulo": "GRU", "rio de janeiro": "GIG", "rio": "GIG",
    "buenos aires": "EZE", "lima": "LIM", "bogota": "BOG",
    # Africa
    "cairo": "CAI", "johannesburg": "JNB", "nairobi": "NBO",
    "lagos": "LOS", "accra": "ACC", "casablanca": "CMN",
    # Abbreviations, typos, and missing cities
    "mil": "MXP", "malpensa": "MXP",
    "chisinau": "KIV", "kishinev": "KIV",
    "istambul": "IST",
    "tashkent": "TAS",
    "belgrade": "BEG", "beograd": "BEG",
    "kyiv": "KBP", "kiev": "KBP",
    "tbilisi": "TBS",
    "baku": "GYD",
    "tallinn": "TLL",
    "riga": "RIX",
    "vilnius": "VNO",
    "sofia": "SOF",
    "zagreb": "ZAG",
    "bucuresti": "OTP",
    "auckland": "AKL",
    "cancun": "CUN",
    "chennai": "MAA", "madras": "MAA",
    "kolkata": "CCU", "calcutta": "CCU",
    "bangalore": "BLR", "bengaluru": "BLR",
    "hyderabad": "HYD",
    "goa": "GOI",
    "jeddah": "JED",
    "muscat": "MCT",
    "kuwait": "KWI",
    "amman": "AMM",
    "addis ababa": "ADD", "addis": "ADD",
    "dar es salaam": "DAR",
    "kigali": "KGL",
}

NAME_PATTERNS = [
    re.compile(r"(?:my name is|I'm|I am|this is|call me)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", re.I),
]

# "Going by myself. Ticket for me only." — 18 Aug 2026, said AFTER a summary
# showed 7 passengers, and matched nothing: `just me` was the only solo phrase
# we knew. The client repeated himself three times and the lead still went to a
# consultant saying seven people were flying.
#
# Intent without a number is still a number: every one of these means one.
# (Bare "solo" already has SOLO_RE further down — left exactly as it was.)
SOLO_PAX_RE = re.compile(
    r"\b(?:"
    r"by\s+myself|just\s+me|only\s+me|me\s+only|myself\s+only|just\s+for\s+me|"
    r"ticket\s+for\s+me\s+only|(?:travell?ing|flying|going)\s+alone"
    r")\b",
    re.I,
)
# …unless the same breath adds someone. "just me and 3 friends" is four people,
# and reading that as one would be the same bug pointing the other way.
SOLO_VETO_RE = re.compile(
    r"\b(?:and|plus|with)\s+"
    r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|a|an|my|his|her|their|the)\b"
    r"|\b\d+\s*(?:friends?|others?|colleagues?|family|kids?|children|people|persons?)\b",
    re.I,
)

PAX_RE = re.compile(r'(\d+)\s*(?:passengers?|people|persons?|travelers?|pax|of us|adults?)', re.I)
# Word-number passengers: "one" as a short answer, "two travelers", …
# Conv #1244: the client confirmed "one" and passengers stayed NULL.
WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9,
}
PAX_WORD_RE = re.compile(
    r'\b(one|two|three|four|five|six|seven|eight|nine)\s*'
    r'(?:travell?ers?|passengers?|persons?|people|pax)?\b', re.I
)
# PR-0 (PAX_REGEX_V2): the phrasings the 3 Nov reading found lost. "2 seniors"
# and "3 tickets" carry the count with a unit the old list did not know; "my
# husband and I" carries it with no number at all. Old patterns stay as they
# are; the v2 ones are chosen per call. On by default since 23 Nov 2026: of the
# month's chats whose client said how many travel and the lead kept none, most
# were these phrasings or "passanger", "one adult and a child", "only one (me)".
# PAX_REGEX_V2=false brings back exactly the old reading.
_PAX_UNITS_V2 = r'pass[ae]ngers?|people|persons?|travell?ers?|pax|of us|adults?|seniors?|elderly|tickets?|seats?'
PAX_RE_V2 = re.compile(r'(\d+)\s*(?:' + _PAX_UNITS_V2 + r')\b', re.I)
PAX_WORD_RE_V2 = re.compile(
    r'\b(one|two|three|four|five|six|seven|eight|nine)\s*'
    r'(?:travell?ers?|pass[ae]ngers?|persons?|people|pax|adults?|seniors?|elderly|tickets?|seats?)?\b', re.I
)
_PAX_WORD_UNIT_V2 = re.compile(
    r'travell?er|pass[ae]nger|person|people|pax|adult|senior|elderly|ticket|seat', re.I
)
# "Only one (me)", "just 1 - me", "one (myself)": one traveller, said with the count and "me".
# Never "only one stop", "just one more question": "me" or "myself" must follow the count.
SOLO_ONE_ME_RE = re.compile(
    r"\b(?:(?:only|just)\s+(?:one|1)\b\s*[(\[,:\-–]?\s*|(?:one|1)\s*[(\[]\s*)(?:me|myself)\b",
    re.I,
)
# A minor counted in words after the adults: "one adult and a child", "2 adults with one baby".
# Only after "and"/"with"/"plus", and never a child seat, fare or discount.
_WORD_COUNT = {"a": 1, "an": 1, **WORD_NUMBERS}
_NOT_A_MINOR = r"(?!\s*(?:seats?|fares?|discounts?|prices?|tickets?|polic(?:y|ies)|meals?|menus?|rates?)\b)"
CHILD_WORD_RE = re.compile(
    r"(?:\b(?:and|with|plus)\s+|\+\s*)(a|an|one|two|three|four|five|six)\s+(?:child(?:ren)?|kids?|minors?)\b"
    + _NOT_A_MINOR,
    re.I,
)
INFANT_WORD_RE = re.compile(
    r"(?:\b(?:and|with|plus)\s+|\+\s*)(a|an|one|two|three)\s+(?:lap\s+)?(?:infants?|bab(?:y|ies))\b"
    + _NOT_A_MINOR,
    re.I,
)
# "my wife and I", "our son and me": two people, no digit anywhere. Not vetoed
# by SOLO_VETO_RE — children are added by CHILD_RE below, as for "me and my".
MY_X_AND_I_RE = re.compile(
    r"\b(?:my|our)\s+(?:wife|husband|partner|spouse|mom|mother|dad|father|son|daughter|"
    r"friend|colleague|boss|sister|brother|fianc[eé]e?|girlfriend|boyfriend)\s+and\s+(?:I|me)\b",
    re.I,
)
# "me and my 9-year-old", "me and my son": the one companion "me and my"
# counts may be the minor the age names, not an extra traveller.
ME_AND_MY_MINOR_RE = re.compile(
    r"\bme and my\s+(?:"
    r"\d{1,2}[\s-]?(?:years?|yrs?)[\s-]?old"
    r"|(?:little\s+|baby\s+)?(?:kid|child|baby|toddler|infant)"
    # a son or daughter is a minor only when the age follows: "my son, 9"
    r"|(?:son|daughter|boy|girl|grandson|granddaughter|nephew|niece)[\s,(]+"
    r"(?:(?:who\s+is|who's|he's|she's|he\s+is|she\s+is|aged?)\s+)?\d{1,2}\b(?!\s*(?:pm|am|h|:))"
    r")\b",
    re.I,
)
# "a 9-year-old", "my 15 year old": the age, not the count, names a minor.
AGE_RE = re.compile(r'\b(\d{1,2})[\s-]?(?:years?|yrs?)[\s-]?old\b', re.I)
# The same with the word that makes it a person: "and a 1-year-old", "and her 5 year old" is one more minor
# beside a kind already counted ("2 kids and a 1-year-old"), where "he is 1 year old" or "2 kids (5 and 1 years
# old)" only describes one already counted.
_PERSON_AGE = r'\b(?:a|an|one|my|our|his|her|their)\s+(\d{1,2})[\s-]?(?:years?|yrs?)[\s-]?old\b'
ADDED_AGE_RE = re.compile(_PERSON_AGE, re.I)
# Ages set off right after the kids counted name those kids, a baby among them: "2 kids, a 5-year-old and a
# 1-year-old" is two kids, not two kids and a baby. A baby's age joined by "and"/"plus" is one more.
KIDS_NAMED_RE = re.compile(r'\s*(?P<sep>[,:(\[]|--?|[–—])\s*(?P<age>' + _PERSON_AGE + ')', re.I)
# The run of such ages after a comma, colon or dash: "a 5-year-old and a 1-year-old". Inside brackets the
# closing bracket ends them (_kids_named_span).
_PERSON_AGE_ANY = r'\b(?:a|an|one|my|our|his|her|their)\s+\d{1,2}[\s-]?(?:years?|yrs?)[\s-]?old\b'
KIDS_NAMED_RUN_RE = re.compile(_PERSON_AGE_ANY + r'(?:\s*(?:,|&|\band\b)\s*' + _PERSON_AGE_ANY + r')*', re.I)


def _kids_named_span(text: str, named: re.Match) -> tuple[int, int]:
    """Where the ages that name the kids counted stand (KIDS_NAMED_RE matched right after the count): up to the
    closing bracket, or the run of ages after a comma, colon or dash. An age after that span is one more minor:
    "2 kids (a 5-year-old and a 7-year-old) plus a 1-year-old" is two kids and an infant (CodeRabbit #279, round 4)."""
    closer = {"(": ")", "[": "]"}.get(named.group("sep"))
    if closer:
        end = text.find(closer, named.end())
        return named.start(), (end + 1 if end >= 0 else len(text))
    run = KIDS_NAMED_RUN_RE.match(text, named.start("age"))
    return named.start(), (run.end() if run else named.end())
# "we are a family of four" — FAMILY_RE only knew digits.
FAMILY_WORD_RE = re.compile(
    r'\b(?:family|group|party)\s+of\s+(one|two|three|four|five|six|seven|eight|nine)\b', re.I
)
# A whole answer that is just "myself" / "just me" — never "I'll book it myself".
SOLO_SHORT_RE = re.compile(r'\s*(?:just\s+|only\s+)?(?:myself|me)\s*[.!]*\s*', re.I)
# Leading digit followed by punctuation (catches "2. Both over 65", "3, all adults")
# Hyphen excluded when followed by digit (avoids "2-3 options" false positive).
PAX_LEADING_RE = re.compile(r'^\s*(\d{1,2})\s*(?:[.,;:!)\]]|\-(?!\d))', re.I)
CHILD_RE = re.compile(r'(\d+)\s*(?:child(?:ren)?|kids?|minors?)', re.I)
INFANT_RE = re.compile(r'(\d+)\s*(?:infants?|babies|baby)', re.I)
FAMILY_RE = re.compile(r'\bfamily\s+of\s+(\d+)\b', re.I)
GROUP_RE = re.compile(r'\b(?:group|party)\s+of\s+(\d+)\b', re.I)
COUPLE_RE = re.compile(r'\b(?:a\s+)?couple\b', re.I)
SOLO_RE = re.compile(r'\bsolo(?:\s+traveler)?\b', re.I)

CABIN_KEYWORDS = {
    "business", "business class", "first", "first class", "economy", "coach",
    "biz", "biz class", "j class", "premium economy", "premium",
}
CABIN_RE = re.compile(
    r'\b(business\s*class|first\s*class|biz\s*class|j\s*class|premium\s*economy|'
    r'business|first|economy|coach|biz|premium)\b',
    re.I,
)
CABIN_MAP = {
    "business": "business", "business class": "business",
    "biz": "business", "biz class": "business",
    "j class": "business",
    "first": "first", "first class": "first",
    "economy": "economy", "coach": "economy",
    "premium economy": "premium_economy", "premium": "premium_economy",
}

ROUTE_RE = re.compile(
    r'(?:(?:from|departing|leaving|flying)\s+)?([\w\s]{2,25}?)\s+(?:to|→|->|–)\s+([\w\s]{2,25}?)(?:\s|$|[,.])',
    re.I,
)
# "flights from Dallas to Dubai" — ROUTE_RE alone captures origin wrong; this disambiguates.
ROUTE_FROM_TO_RE = re.compile(
    r'\bfrom\s+([\w\s]{2,25}?)\s+(?:to|→|->|–)\s+([\w\s]{2,25}?)(?:\s|$|[,.])',
    re.I,
)

# Trip type detection
ONE_WAY_RE = re.compile(
    r'\b(?:one[\s\-]?way|ow|single|only\s+going)\b', re.I
)
ROUND_TRIP_RE = re.compile(
    r'\b(?:round[\s\-]?trip|rt|return\s+(?:trip|flight)|back\s+and\s+forth)\b', re.I
)

# ── Date extraction ───────────────────────────────────────────

MONTH_NAMES = {
    "january": 1, "jan": 1, "february": 2, "feb": 2,
    "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

_MONTH_PAT = '|'.join(MONTH_NAMES.keys())

# "March 15", "Mar 15th", "Nov. 8", "15 March", "15th of March", "March 15, 2026".
# The day is never the start of a year: "Nov 2026" and "January 2027" made the 20th of the month, a
# day no client wrote (23 Nov 2026), so a day must not run on into another digit.
_DATE_MONTH_DAY = re.compile(
    r'\b(?:'
    r'(?P<m1>' + _MONTH_PAT + r')(?:\.\s*|\s+)(?P<d1>\d{1,2})(?!\d)(?:st|nd|rd|th)?'
    r'|(?P<d2>\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<m2>' + _MONTH_PAT + r')'
    r')(?:\s*,?\s*(?P<y>\d{4}))?',
    re.I
)

# "3/15", "3/15/2026", "03-15-2026"
_DATE_NUMERIC = re.compile(r'\b(?P<m>\d{1,2})[/\-](?P<d>\d{1,2})(?:[/\-](?P<y>\d{4}))?\b')

# "Dec23rd -1/3/27" — one client, one line, two date formats and no spaces.
# The text parser demanded a space after the month, so "Dec23rd" vanished; the
# numeric one only accepted four-digit years, so "1/3/27" was read loosely.
# What came back was the RETURN date sitting in the departure field and no
# return at all, from a client who had given a perfectly clear range.
#
# Either side may be written either way, the separator may be a dash or a word,
# and the year may be two digits. First date is the departure, second is the
# return — in that order, always.
def _range_atom(p: str) -> str:
    """One date, written as text ("Dec 23", "Dec23rd") or as slashes ("1/3/27")."""
    return (
        "(?:"
        "(?:(?P<" + p + "_tm>" + _MONTH_PAT + r")\.?\s*(?P<" + p + r"_td>\d{1,2})(?!\d)(?:st|nd|rd|th)?"
        r"(?:\s*,?\s*(?P<" + p + r"_ty>\d{2,4}))?)"
        "|"
        r"(?:(?P<" + p + r"_nm>\d{1,2})/(?P<" + p + r"_nd>\d{1,2})(?:/(?P<" + p + r"_ny>\d{2,4}))?)"
        ")"
    )


_DATE_RANGE_MIXED = re.compile(
    r"\b" + _range_atom("a")
    + r"\s*(?:-|–|—|to|until|through|thru)\s*"
    + _range_atom("b"),
    re.I,
)


def _range_side(m: "re.Match", p: str) -> "tuple[int, int, int | None] | None":
    """(month, day, year) for one side of a matched range, or None."""
    if m.group(p + "_tm"):
        month = MONTH_NAMES.get(m.group(p + "_tm").lower())
        day = int(m.group(p + "_td"))
        year = m.group(p + "_ty")
    else:
        month = int(m.group(p + "_nm"))
        day = int(m.group(p + "_nd"))
        year = m.group(p + "_ny")
    if not month or not day or month > 12:
        return None
    if year is not None:
        year = int(year)
        if year < 100:          # "27" is 2027, not year 27
            year += 2000
    return month, day, year


# A number followed by one of these counts or measures something, it is not a day: a length ("10 days"),
# a count ("2 people", "1-2 stops"), a time ("7 pm"), a share ("15%").
_UNIT_AFTER = (
    r"\s*(?:(?:days?|nights?|weeks?|months?|years?|yrs?|hours?|hrs?|people|persons?|pax|pass[ae]ngers?"
    r"|adults?|travell?ers?|kids?|child(?:ren)?|tickets?|seats?|stops?|bags?|options?|layovers?|connections?"
    r"|of\s+us|am|pm)\b|%)"
)
_UNIT_AFTER_RE = re.compile(_UNIT_AFTER, re.I)

# What may follow the end of a range that names only the day, and makes it something else: a length
# ("march 5 - 10 days"), a count ("april 7 to 2 people"), a time ("june 9 to 12:30", "may 6 to 7 pm"), or
# its own month ("May 6 to 7 June": the named dates read both, CodeRabbit #279).
_NOT_A_DAY_END = (
    r"(?!" + _UNIT_AFTER + r"|\s*[:/.]\d"
    r"|\s*(?:of\s+)?(?:" + _MONTH_PAT + r")\b)"
)
# "March 15-22" (same month range with dash)
_DATE_RANGE_DASH = re.compile(
    r'\b(?P<m>' + _MONTH_PAT + r')(?:\.\s*|\s+)(?P<d1>\d{1,2})(?!\d)(?:st|nd|rd|th)?'
    r'\s*[-–]\s*(?P<d2>\d{1,2})(?!\d)(?:st|nd|rd|th)?\b' + _NOT_A_DAY_END,
    re.I
)
# "april 5 to 26", "december 15 to 27", "january 9 t0 23": the same range written with a word, the end
# only a day. Chats #3385, #3106 and #3178 (Nov 2026) kept the departure and lost the return.
_DATE_RANGE_DAY_END = re.compile(
    r'\b(?P<m>' + _MONTH_PAT + r')(?:\.\s*|\s+)(?P<d1>\d{1,2})(?!\d)(?:st|nd|rd|th)?'
    r'\s*,?\s+(?:to|t0|till?|until|through|thru)\s+(?:the\s+)?(?P<d2>\d{1,2})(?!\d)(?:st|nd|rd|th)?\b'
    + _NOT_A_DAY_END,
    re.I
)


def _day_end(dep_iso: str, day: int) -> str | None:
    """The return of a range whose end names only the day: the departure's month when the day comes
    after the departure's ("April 5 to 26"), the next month when it comes before ("March 28-5"), none
    when it is the same day. Counted from the departure, never from today, so "November 20-28" read on
    23 Nov is 2027-11-20 → 2027-11-28, not a return a year before the departure."""
    dep = date.fromisoformat(dep_iso)
    if day == dep.day:
        return None
    year, month = dep.year, dep.month
    if day < dep.day:
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    try:
        return date(year, month, day).isoformat()
    except ValueError:  # noqa: silent — a day the month lacks ("Feb 27 to 30") is no return; the departure stands
        return None


def _resolve_year(month: int, day: int) -> int:
    """Year for the NEXT occurrence of (month, day) relative to today.
    Compares (month, day) — not month alone — so e.g. today's month but an
    earlier day still resolves to next year."""
    today = date.today()
    return today.year + 1 if (month, day) < (today.month, today.day) else today.year


def _coerce_future_iso(month: int, day: int, explicit_year: int | None) -> str | None:
    """YYYY-MM-DD that is today or later, or None if unsalvageable.

    - inferred year (explicit_year None): next occurrence of (month, day)
    - explicit year >= today.year but already past: bump to next occurrence
    - explicit year < today.year (2+ yrs old / clearly stale): None (don't guess)
    - invalid calendar date (e.g. Feb 30): None
    """
    today = date.today()
    try:
        if explicit_year is not None:
            if explicit_year < today.year:
                return None
            d = date(explicit_year, month, day)
            if d < today:
                d = date(_resolve_year(month, day), month, day)
        else:
            d = date(_resolve_year(month, day), month, day)
        return d.isoformat() if d >= today else None
    except (ValueError, OverflowError):
        return None


def _make_date(month: int, day: int, year: int | None = None) -> str | None:
    """Create a future-safe YYYY-MM-DD string, or None if invalid/unsalvageable.
    Delegates to _coerce_future_iso so past dates never reach a lead."""
    return _coerce_future_iso(month, day, year)


# A client message that speaks of a date: a month with a day ("Feb 10", "10th of March", "Nov. 8"), a
# month by its full name, a date in digits ("2/10", "12.01", "2027-02-10"), a weekday, a day of the month ("the
# 17th", "on the 20"), a year, a length ("2 weeks", "a couple of weeks"), a holiday or season ("for
# Christmas", "next summer"), "asap", or a move ("a week later", "push it back", "change the dates").
# It errs on the side of a date: a message it takes for one only lets the AI's reading through for that
# turn, as before; a date it missed would be one the client changed and the lead did not take.
# Chats #3579 and #3403 (23 and 20 Nov 2026): the AI's extraction on each turn rewrote dates the client
# had written clearly (a January → March trip, a week in February) on a message with no date in it
# ("I will", "sounds good"), and the CRM would have had the new ones.
_DATE_MENTION_RE = re.compile(
    r"\b(?:" + _MONTH_PAT + r")\.?\s*\d{1,2}(?:st|nd|rd|th)?\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:" + _MONTH_PAT + r")\b"
    r"|\b(?:january|february|march|april|june|july|august|september|october|november|december)\b"
    # "in May", "early Feb", "mid-March": "may" alone is a verb ("we may go business"), after these a month.
    r"|\b(?:in|for|by|until|till|from|since|around|early|mid|late|end\s+of|beginning\s+of|start\s+of"
    r"|middle\s+of)[\s-]+(?:" + _MONTH_PAT + r")\b"
    r"|\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b|\b20\d\d-\d{1,2}-\d{1,2}\b"
    r"|\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)\b|\bon\s+the\s+\d{1,2}\b|\b20[2-3]\d\b"
    r"|\b(?:today|tomorrow|tonight|weekend|asap|as\s+soon\s+as\s+possible"
    r"|next\s+(?:week|month|year)|this\s+(?:week|month))\b"
    r"|\b(?:\d+|an?|one|two|three|four|five|six|seven|eight|nine|ten|couple(?:\s+of)?|few)"
    r"\s+(?:days?|weeks?|months?|nights?)\b"
    r"|\b(?:christmas|xmas|new\s+year|thanksgiving|easter|spring\s+break|summer|winter|autumn"
    r"|(?:this|next|the|in)\s+(?:spring|fall))\b"
    r"|\b(?:postpone|reschedule|push(?:ed)?\s+(?:it\s+|the\s+\w+\s+)?back"
    r"|move(?:d)?\s+(?:it|the\s+(?:trip|flight|dates?))"
    r"|(?:change|different|other|new|return|departure|travel)\s+(?:the\s+)?dates?"
    r"|(?:earlier|later)\s+(?:date|flight))\b",
    re.I,
)


def mentions_date(text: Optional[str]) -> bool:
    """True when the client's message speaks of a date (_DATE_MENTION_RE): only then may a later reading
    replace the dates the lead already holds."""
    return bool(text and _DATE_MENTION_RE.search(text))


def _extract_dates(text: str) -> tuple[str | None, str | None]:
    """Extract departure and optional return date from message text.
    Returns (departure_date, return_date) as YYYY-MM-DD strings or None.
    Handles absolute dates, relative dates, and 'returning/coming back' signals.
    """
    # 0. Return signal: "returning/coming back/back on [date]" → return_date
    _return_signal = re.search(
        r'\b(?:returning|coming\s+back|back\s+on|return(?:ing)?\s+on)\b', text, re.I
    )
    if _return_signal:
        _after = text[_return_signal.end():]
        _before = text[:_return_signal.start()]
        _ret_matches = list(_DATE_MONTH_DAY.finditer(_after))
        if _ret_matches:
            _rm = _ret_matches[0]
            _rm_name = (_rm.group("m1") or _rm.group("m2") or "").lower()
            _rm_day = int(_rm.group("d1") or _rm.group("d2") or "0")
            _rm_year = int(_rm.group("y")) if _rm.group("y") else None
            _rm_month = MONTH_NAMES.get(_rm_name)
            if _rm_month and _rm_day:
                ret_date = _make_date(_rm_month, _rm_day, _rm_year)
                dep_date = None
                _dep_matches = list(_DATE_MONTH_DAY.finditer(_before))
                if _dep_matches:
                    _dm = _dep_matches[0]
                    _dm_name = (_dm.group("m1") or _dm.group("m2") or "").lower()
                    _dm_day = int(_dm.group("d1") or _dm.group("d2") or "0")
                    _dm_year = int(_dm.group("y")) if _dm.group("y") else None
                    _dm_month = MONTH_NAMES.get(_dm_name)
                    if _dm_month and _dm_day:
                        dep_date = _make_date(_dm_month, _dm_day, _dm_year)
                return dep_date, ret_date

    # 0. Mixed-format range: "Dec23rd -1/3/27", "12/23/26-1/3/27".
    # Runs first: both sides are explicit, so there is nothing to infer.
    _mixed = _DATE_RANGE_MIXED.search(text)
    if _mixed:
        _a = _range_side(_mixed, "a")
        _b = _range_side(_mixed, "b")
        if _a and _b:
            _dep = _make_date(*_a)
            _ret = _make_date(*_b)
            if _dep and _ret:
                return _dep, _ret

    # 1. A range whose end is only a day: "March 15-22", "april 5 to 26" (the first one written wins)
    _day_ranges = [r for r in (_DATE_RANGE_DASH.search(text), _DATE_RANGE_DAY_END.search(text)) if r]
    m = min(_day_ranges, key=lambda r: r.start()) if _day_ranges else None
    if m:
        month = MONTH_NAMES.get(m.group("m").lower())
        if month:
            dep = _make_date(month, int(m.group("d1")))
            if dep:
                return dep, _day_end(dep, int(m.group("d2")))

    # 2. Named dates: "March 15", "15th March", "June 10, 2026"
    matches = list(_DATE_MONTH_DAY.finditer(text))
    if matches:
        dates = []
        for match in matches[:2]:
            m_name = (match.group("m1") or match.group("m2") or "").lower()
            day_val = int(match.group("d1") or match.group("d2") or "0")
            year_val = int(match.group("y")) if match.group("y") else None
            month_val = MONTH_NAMES.get(m_name)
            if month_val and day_val:
                d = _make_date(month_val, day_val, year_val)
                if d:
                    dates.append(d)
        if len(dates) >= 2:
            return dates[0], dates[1]
        if len(dates) == 1:
            return dates[0], None

    # 3. Numeric: "3/15", "3/15/2026"
    matches = list(_DATE_NUMERIC.finditer(text))
    if matches:
        dates = []
        for match in matches[:2]:
            # "available 24/7" is an idiom, not a date — the European swap
            # below would coerce it into July 24 (conv "Costa").
            if re.fullmatch(r"24\s*/\s*7", match.group(0)):
                continue
            # "for 2-3 weeks", "10-14 days", "we are 3-4 people", "1-2 stops": a length or a count, not the
            # 3rd of February, and it became the lead's departure. The agent's part of a chat is read by
            # default now (#279, 23 Nov 2026: 7 such messages in 60 days, 6 in chats that had an agent).
            # Only with a dash: "3/15 pm flight" is a date.
            if (match.group("y") is None and "-" in match.group(0)
                    and _UNIT_AFTER_RE.match(text, match.end())):
                continue
            m_val, d_val = int(match.group("m")), int(match.group("d"))
            y_val = int(match.group("y")) if match.group("y") else None
            # DD/MM (European): first number can't be a month → swap. Ambiguous
            # both-<=12 case stays MM/DD (unchanged).
            if m_val > 12 and d_val <= 12:
                m_val, d_val = d_val, m_val
            if 1 <= m_val <= 12 and 1 <= d_val <= 31:
                d = _make_date(m_val, d_val, y_val)
                if d:
                    dates.append(d)
        if len(dates) >= 2:
            return dates[0], dates[1]
        if len(dates) == 1:
            return dates[0], None

    # 4. Relative dates: "next month", "tomorrow", "next week"
    _today = datetime.now()
    _lower = text.lower()

    if "tomorrow" in _lower:
        dep = (_today + timedelta(days=1)).strftime("%Y-%m-%d")
        return dep, None

    if re.search(r'\bnext\s+month\b', _lower):
        if _today.month == 12:
            dep_date = _today.replace(year=_today.year + 1, month=1, day=15)
        else:
            dep_date = _today.replace(month=_today.month + 1, day=15)
        return dep_date.strftime("%Y-%m-%d"), None

    if re.search(r'\bnext\s+week\b', _lower):
        days_until_monday = (7 - _today.weekday()) % 7 or 7
        dep_date = _today + timedelta(days=days_until_monday)
        return dep_date.strftime("%Y-%m-%d"), None

    if re.search(r'\bthis\s+month\b', _lower):
        dep_date = _today.replace(day=15)
        if dep_date < _today:
            dep_date = _today + timedelta(days=3)
        return dep_date.strftime("%Y-%m-%d"), None

    _in_weeks = re.search(r'\bin\s+(\d+)\s+weeks?\b', _lower)
    if _in_weeks:
        weeks = int(_in_weeks.group(1))
        dep_date = _today + timedelta(weeks=weeks)
        return dep_date.strftime("%Y-%m-%d"), None

    return None, None


# ── Stopwords for KB search ───────────────────────────────────
STOPWORDS = frozenset({
    "the", "and", "for", "from", "with", "that", "this", "have", "been",
    "will", "what", "when", "where", "which", "about", "into", "your",
    "just", "want", "need", "like", "some", "also", "very", "much",
    "many", "than", "then", "them", "could", "would", "should", "their",
    "there", "these", "those", "other", "each", "only", "more", "most",
    "look", "looking", "help", "please", "thanks", "hello", "class",
    "flights", "flight", "flying", "travel", "trip",
})


# ── Sales-methodology signals ─────────────────────────────────
# Keyword-first, no extra LLM call: these ride the same regex pass as the
# travel fields.

OCCASION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("business", re.compile(
        r"\b(business trip|business travel|work trip|for work|on business|"
        r"meeting|conference|convention|summit|client visit)\b", re.I)),
    ("celebration", re.compile(
        r"\b(anniversary|honeymoon|wedding|birthday|celebration|celebrating|"
        r"proposal|graduation|bucket list|dream trip|special getaway)\b", re.I)),
    ("medical", re.compile(
        r"\b(medical|surgery|treatment|hospital|clinic|doctor's appointment)\b", re.I)),
    ("family", re.compile(
        r"\b(visiting (?:my )?(?:parents|family|relatives|mother|father|mom|dad)|"
        r"see (?:my )?family|family visit|my parents|my grandparents|relatives)\b", re.I)),
]

# "for my anniversary" / "it's our honeymoon" — a raw occasion we did not name.
OCCASION_PHRASE_RE = re.compile(
    r"\b(?:it'?s|for)\s+(?:our|my)\s+([a-z][\w' -]{2,38})", re.I
)

BOOKING_FOR_RE = re.compile(
    r"\bfor\s+(?:my|our)\s+(boss|wife|husband|partner|parents|mother|father|mom|dad|"
    r"son|daughter|brother|sister|friend|colleague|client|team|in-laws|"
    r"grandmother|grandfather|grandparents)\b", re.I
)

# Frequent or price-first family travel reads value_driven, not needs_based.
FREQUENT_TRAVEL_RE = re.compile(
    r"\b(every few months|every month|every year|a few times a year|as usual|"
    r"again|routine|regularly|cheapest|cheapest option|best price)\b", re.I
)

COMPARISON_SOURCES = {"kayak", "skyscanner", "momondo", "kiwi", "cheapflights", "expedia"}
COMPARISON_CLICK_IDS = ("kayak_click_id", "kclid", "skyscanner_click_id")

DATE_FLEXIBLE_RE = re.compile(
    r"\b(flexible|some flexibility|a day or two|couple of days|few days either|"
    r"open on dates|dates are open)\b", re.I
)
DATE_FIXED_RE = re.compile(
    r"\b(fixed dates?|dates? are fixed|date is fixed|locked|exact dates?|"
    r"must be|no flexibility|can'?t move)\b", re.I
)

NONSTOP_RE = re.compile(
    r"\b(non[- ]?stop only|nonstop only|direct only|only direct|no layovers|"
    r"no connections|direct flights? only|non[- ]?stop flights? only)\b", re.I
)

AIRLINES = [
    "emirates", "qatar airways", "qatar", "etihad", "turkish airlines", "turkish",
    "lufthansa", "swiss", "british airways", "air france", "klm", "iberia",
    "virgin atlantic", "singapore airlines", "cathay pacific", "ana", "japan airlines",
    "delta", "united", "american airlines", "jetblue", "alaska airlines",
    "air canada", "qantas", "spirit", "frontier", "ryanair", "easyjet",
    "ita airways", "aer lingus", "sas", "finnair", "austrian", "tap",
]
AIRLINE_AVOID_RE = re.compile(
    r"\b(never|avoid|hate|not|no more|anything but|refuse|worst)\b", re.I
)
AIRLINE_LOVE_RE = re.compile(
    r"\b(prefer|love|like|always fly|fly with|loyal|miles with|favou?rite)\b", re.I
)

BUDGET_RE = re.compile(
    r"\b(?:budget|max|maximum|up to|no more than|around|about)\s*(?:is\s*)?"
    r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(k\b)?", re.I
)
PRICE_SEEN_RE = re.compile(
    r"\b(?:saw|seen|found|quoted|quote of|priced at|listed at)\b[^.\n]{0,25}?"
    r"\$\s*(\d[\d,]*(?:\.\d+)?)\s*(k\b)?", re.I
)
CALL_TIME_RE = re.compile(
    r"\b(?:call|reach|phone|ring)\s+me\s+((?:after|before|around|at|in the|during)"
    r"\s+[\w:\. ]{1,20})", re.I
)
# How the client wants to be reached. In 3,255 chats the clients who said no
# were refusing calls, not contact: "text only", "email me the quotes",
# "I'll chat, don't want to talk". A consultant who calls anyway loses them.
# "I don't want" / "don't send me" in Spanish and Portuguese, before a channel: a withdrawal, never the wish.
_NO_WANT = (r"(?:no\s+(?:quiero|me\s+(?:manden|env[ií]en|mandes|escriban))|"
            r"n[aã]o\s+(?:quero|me\s+(?:mande|mandem|envie|enviem)))")

CONTACT_CHANNEL_RES: tuple[tuple[str, re.Pattern], ...] = (
    ("text", re.compile(
        r"\b(?:text(?:ing)?\s+only|only\s+(?:by\s+)?text|text\s+me|txt\s+me|sms\s+only"
        r"|only\s+sms|prefer\s+(?:a\s+)?text(?:ing|s)?|text\s+message\s+chats?"
        # Spanish, French, Portuguese (#3325: "mensaje de texto")
        r"|mensajes?\s+de\s+texto|(?:por|solo|s[oó]lo\s+por)\s+mensajes?|prefiero\s+(?:un\s+)?mensajes?"
        r"|escr[ií]b(?:eme|ame|anme)|m[aá]nd(?:ame|eme)\s+un\s+mensaje"
        r"|par\s+(?:message|sms|texto)|[ée]crivez[- ]moi|envoyez[- ]moi\s+un\s+(?:message|sms)"
        r"|mensage(?:m|ns)\s+de\s+texto|(?:por|s[oó]\s+por)\s+mensage(?:m|ns)|me\s+mande\s+(?:uma\s+)?mensagem)\b",
        re.I)),
    # WhatsApp, said in words ("on WhatsApp please", "por WhatsApp"); the button after the card says it
    # too (app.pipeline.card_buttons). A message app, not a call: turns_down_calls reads it so.
    ("whatsapp", re.compile(r"\bwhats\s*app\b", re.I)),
    ("email", re.compile(
        r"\b(?:e-?mail\s+only|only\s+(?:by\s+)?e-?mail|e-?mail\s+me|just\s+e-?mail"
        r"|prefer\s+(?:an\s+)?e-?mail|(?:quotes?|options|fares|prices?)\s+(?:on|to|via|by)\s+(?:my\s+)?e-?mail"
        r"|(?:por|al)\s+correo(?:\s+electr[oó]nico)?|par\s+(?:e-?mail|courriel)|(?:por|pelo)\s+e-?mail)\b",
        re.I)),
    ("no calls", re.compile(
        r"\b(?:no\s+(?:phone\s+)?calls?|don'?t\s+call|do\s+not\s+call|please\s+don'?t\s+call"
        r"|can'?t\s+talk|cannot\s+talk|don'?t\s+want\s+to\s+talk|no\s+talking|stop\s+calling"
        r"|(?:don'?t|do\s+not|never)\s+(?:text|txt|sms)\s+(?:or|and|nor)\s+call"
        r"|no\s+me\s+llam(?:en|e|es)|(?:sin|no)\s+llamadas|no\s+puedo\s+hablar"
        r"|pas\s+d'appels?|ne\s+m'appelez\s+pas|n[aã]o\s+(?:me\s+)?ligue|sem\s+liga[cç](?:[aã]o|[oõ]es)"
        r"|n[aã]o\s+posso\s+falar)\b", re.I)),
    # A channel withdrawn: "Please don't text me", "stop texting me". It is a
    # wish of its own, not silence: the consultant must not text a client who
    # said so, and an earlier "text only" gives way to it (merge_contact_channel).
    # "don't text me", "don't text." — not "I don't text much". The bare "no
    # texts" / "no email" too, like "no calls" ("No calls, no texts, no email."),
    # but not "no texts from you yet": that is news, not a wish (CodeRabbit #255).
    ("no texts", re.compile(
        r"\b(?:(?:don'?t|do\s+not|never)\s+(?:(?:call|phone|ring)\s+(?:or|and|nor)\s+)?(?:text|txt|sms)"
        r"(?:\s+me\b|(?=\s*(?:[.,!?;]|$))|(?=\s+(?:or|and|nor)\s+(?:call|phone|ring)))"
        r"|stop\s+(?:texting|txting|sending\s+(?:me\s+)?(?:texts?|sms))|no\s+more\s+(?:texts?|sms)"
        r"|no\s+(?:texts?|txts?|sms|text\s+messages?)(?!\s+(?:from|yet)\b)"
        # Spanish, French, Portuguese: "No quiero mensajes de texto", "sin SMS", "pas de SMS",
        # "ne m'envoyez pas de message", "não quero mensagens", "sem SMS" (CodeRabbit #270).
        rf"|{_NO_WANT}\s+(?:mensajes?(?:\s+de\s+texto)?|sms|mensage(?:m|ns)(?:\s+de\s+texto)?)"
        r"|(?:sin|sem)\s+(?:mensajes?|sms|mensage(?:m|ns))|pas\s+de\s+(?:sms|messages?|textos?)"
        r"|ne\s+m'envoyez\s+pas\s+de\s+(?:sms|messages?))\b", re.I)),
    ("no email", re.compile(
        r"\b(?:(?:don'?t|do\s+not|never)\s+e-?mail(?:\s+me\b|(?=\s*(?:[.,!?;]|$)))"
        r"|stop\s+e-?mailing|no\s+more\s+e-?mails?|no\s+e-?mails?(?!\s+(?:from|yet)\b)"
        rf"|{_NO_WANT}\s+(?:correos?|e-?mails?)|(?:sin|sem)\s+(?:correos?|e-?mails?)"
        r"|pas\s+(?:d'e-?mails?|de\s+(?:courriels?|mails?|e-?mails?)))\b", re.I)),
)

# A wish and its withdrawal: the later one wins (merge_contact_channel).
_CHANNEL_OPPOSITES = {"text": "no texts", "no texts": "text", "email": "no email", "no email": "email"}

# "don't text me", "never email me", "don't call or text me", "not only text":
# what follows is no wish for that channel.
_NEGATED_BEFORE = re.compile(
    r"\b(?:don'?t|do\s+not|never|not|no"
    # "No quiero mensajes de texto", "sin correo", "não quero mensagens", "sem e-mail" (CodeRabbit #270)
    rf"|{_NO_WANT}|sin|sem)\s*(?:(?:call|phone|ring)\s+(?:or|and|nor)\s+)?$", re.I
)


def contact_channel_of(text: str) -> Optional[str]:
    """'text', 'email', 'no calls', 'no texts', 'no email' or a comma-joined
    mix — None when the message says nothing about it.

    "Please don't text me" is no wish for texts: a text or email phrase right
    after a negation does not count as the wish; the withdrawal is read by its
    own phrases ("no texts"), so it can replace an earlier "text only". "Not
    only text" withdraws nothing and says nothing.

    A wish and its withdrawal in the same message ("Email me the options.
    Actually, don't email me") keep only the later one: the client's last word.

    A phone types "don’t" (U+2019) by default: "don’t call me" is the same
    refusal as "don't call me" (17 Nov: "I can’t always take a call").
    """
    text = (text or "").replace("’", "'").replace("‘", "'")
    last_at: dict[str, int] = {}
    for label, rx in CONTACT_CHANNEL_RES:
        for m in rx.finditer(text):
            if label in ("text", "email", "whatsapp") and _NEGATED_BEFORE.search(text[max(0, m.start() - 24):m.start()]):
                continue
            last_at[label] = max(m.start(), last_at.get(label, -1))
    for label, opposite in _CHANNEL_OPPOSITES.items():
        if label in last_at and opposite in last_at:
            last_at.pop(label if last_at[label] < last_at[opposite] else opposite)
    found = list(dict.fromkeys(label for label, _ in CONTACT_CHANNEL_RES if label in last_at))
    return ", ".join(found) or None


# A message to the phone is one of the two: SMS chosen after WhatsApp is SMS, and the other way round
# (CodeRabbit #272: "whatsapp, text" kept the older WhatsApp first for the next bare number).
_ONE_PHONE_MESSAGE = {"text": "whatsapp", "whatsapp": "text"}


def merge_contact_channel(stored: Optional[str], new: str) -> str:
    """The channel wishes so far, with this message's: a later withdrawal
    replaces the wish ("text only", then "please don't text me" → "no texts"),
    a later wish replaces the withdrawal, a later SMS replaces an earlier
    WhatsApp (and the other way round), and unrelated wishes stay ("no calls",
    then "no texts" → "no calls, no texts"). Both said in one message stay both."""
    new_parts = [p.strip() for p in (new or "").split(",") if p.strip()]
    old_parts = [p.strip() for p in stored.split(",") if p.strip()] if isinstance(stored, str) else []
    kept = [p for p in old_parts if p not in new_parts and _CHANNEL_OPPOSITES.get(p) not in new_parts
            and _ONE_PHONE_MESSAGE.get(p) not in new_parts]
    return ", ".join(kept + new_parts)


# What the client needs on the trip, said at any turn: a seat, help at the airport, a lie-flat bed, the
# TSA PreCheck / Known Traveler number, a loyalty account, travel insurance. Until 23 Nov 2026 a need
# reached the lead only in the one turn after the summary (#3579 asked for trip insurance and named a
# loyalty account; the lead kept neither). Kept as LABELS only: never the number a client types (a KTN
# or a loyalty number is not stored), never a reason or a condition ("needs a wheelchair", not why).
_NEEDS: tuple[tuple[str, re.Pattern], ...] = (
    ("aisle seat", re.compile(
        r"\baisle\s+seats?\b|\b(?:prefers?|wants?|needs?|likes?|loves?|get|book)\s+(?:an?\s+|the\s+)?aisle\b"
        r"|\baisle\s+(?:please|pls|for\s+me)\b|\bon\s+the\s+aisle\b", re.I)),
    ("window seat", re.compile(
        r"\bwindow\s+seats?\b|\b(?:prefers?|wants?|needs?|likes?|loves?|get|book)\s+(?:an?\s+|the\s+)?window\b"
        r"|\bwindow\s+(?:please|pls|for\s+me)\b|\bby\s+the\s+window\b", re.I)),
    ("lie-flat seat", re.compile(r"\b(?:lie|lay)[\s-]?flat\b|\bflat[\s-]?beds?\b|\bfully\s+flat\b", re.I)),
    ("wheelchair / airport assistance", re.compile(
        r"\bwheel\s?chairs?\b|\b(?:special|airport|mobility|wheelchair)\s+assistance\b"
        r"|\bassistance\s+(?:at|through|in)\s+the\s+airports?\b|\bmeet\s+(?:and|&)\s+assist\b", re.I)),
    ("TSA PreCheck / KTN", re.compile(
        r"\bpre[\s-]?check\b|\bknown\s+travell?er\b|\bktn\b|\bglobal\s+entry\b|\bredress\s+number\b",
        re.I)),
    ("loyalty number / miles", re.compile(
        r"\bfrequent\s+fl[yi]er\b|\bloyalty\s+(?:number|program(?:me)?|account|card|status)\b"
        r"|\b(?:skymiles|aadvantage|mileageplus|avios|flying\s+blue|miles\s*(?:&|and)\s*more)\b"
        r"|\b(?:miles|points|mileage|rewards?)\s+(?:number|account|#)"
        r"|number\s+for\s+(?:points|miles)\b"
        r"|\b(?:use|redeem|book\s+with|pay\s+with)\s+(?:my\s+|our\s+)?(?:points|miles)\b", re.I)),
    ("travel insurance", re.compile(
        r"\b(?:travel|trip|cancell?ation)\s+insurance\b|\binsurance\s+for\s+(?:the|our|my)\s+trip\b"
        r"|\binsure\s+(?:the|our|my)\s+trip\b", re.I)),
)
# "no travel insurance", "I don't need a wheelchair", "without TSA": the need said away. Only within its
# clause: "No window seat but aisle seat please" says the aisle seat (CodeRabbit #279).
_NEED_SAID_AWAY = re.compile(
    r"\b(?:no|not|don'?t|do\s+not|doesn'?t|does\s+not|without|never|nor|skip)\b"
    r"(?:(?!\b(?:but|however|though|although|instead|rather|except|yet|whereas|while)\b)[^.,;!?]){0,24}$",
    re.I,
)


def needs_of(text: Optional[str]) -> Optional[str]:
    """The trip needs this message names, as labels joined by "; " ("aisle seat; travel insurance").
    None when it names none, or only says them away ("no travel insurance")."""
    text = (text or "").replace("’", "'")
    found = []
    for label, rx in _NEEDS:
        for m in rx.finditer(text):
            if not _NEED_SAID_AWAY.search(text[max(0, m.start() - 30):m.start()]):
                found.append(label)
                break
    return "; ".join(found) or None


def merge_must_haves(stored: Optional[str], new: Optional[str], limit: int = 200) -> Optional[str]:
    """Every need said so far: the lead's, then this turn's that it does not already name (a need
    said twice is kept once, a label the client's own words already hold is not added). Never more
    than `limit` characters: a part that would not fit is left out, never cut."""
    parts = [p.strip() for p in stored.split(";") if p.strip()] if isinstance(stored, str) else []
    held = "; ".join(parts).lower()
    for p in (x.strip() for x in (new or "").split(";")):
        if p and p.lower() not in held and len("; ".join([*parts, p])) <= limit:
            parts.append(p)
            held = "; ".join(parts).lower()
    return "; ".join(parts) or None


def turns_down_calls(channel: Optional[str]) -> bool:
    """These channel words turn a call down: the client chose texts or WhatsApp,
    or said no calls. A call they asked for earlier is no longer owed — the newest wish
    wins, as a later "call me" wins over an older "text only" (CodeRabbit #262)."""
    parts = {p.strip() for p in (channel or "").split(",")}
    return "text" in parts or "whatsapp" in parts or "no calls" in parts


def current_contact_channel(lead: Optional[dict], entities: Optional[dict] = None) -> Optional[str]:
    """Every channel wish so far, this message's included. The lead holds the
    merged wishes; this turn's go on top, so a refusal said now counts even when
    the lead refresh failed and the lead is a turn behind (merging again is a
    no-op when it is current)."""
    signals = (lead or {}).get("intent_signals")
    stored = signals.get("contact_channel") if isinstance(signals, dict) else None
    stored = stored if isinstance(stored, str) and stored else None
    now = (entities or {}).get("contact_channel")
    if isinstance(now, str) and now:
        return merge_contact_channel(stored, now)
    return stored


def refused_contact_fields(channel: Optional[str]) -> set[str]:
    """The missing-field labels (models.lead.get_missing_fields) the client's own
    words rule out: the phone once calls AND texts are refused, the email once
    email is. Nobody is asked again for a channel they turned down — not in the
    prompt's "Still needed", not in the queue reply (CodeRabbit #255)."""
    parts = {p.strip() for p in (channel or "").split(",")}
    refused: set[str] = set()
    if {"no calls", "no texts"} <= parts:
        refused.add("phone number")
    if "no email" in parts:
        refused.add("email")
    return refused

# Reverse of CITY_TO_CODE for the landing-page hint. Short keys ("la", "sf")
# match too much inside a slug, so only real city names are used.
_SLUG_CITIES: dict[str, str] = {
    name: name.title() for name in CITY_TO_CODE if len(name) >= 4
}

# Country landing pages (/flight/country/india/410). Slug → display name.
_SLUG_COUNTRIES: dict[str, str] = {
    "india": "India",
    "japan": "Japan",
    "thailand": "Thailand",
    "italy": "Italy",
    "france": "France",
    "spain": "Spain",
    "greece": "Greece",
    "uk": "UK",
    "united-kingdom": "UK",
    "germany": "Germany",
    "australia": "Australia",
    "new-zealand": "New Zealand",
    "uae": "UAE",
    "united-arab-emirates": "UAE",
    "singapore": "Singapore",
    "china": "China",
    "vietnam": "Vietnam",
    "philippines": "Philippines",
    "brazil": "Brazil",
    "argentina": "Argentina",
    "south-africa": "South Africa",
    "turkey": "Turkey",
    "portugal": "Portugal",
    "indonesia": "Indonesia",
    "malaysia": "Malaysia",
    "south-korea": "South Korea",
    "korea": "South Korea",
    "egypt": "Egypt",
    "mexico": "Mexico",
    "pakistan": "Pakistan",
    "bangladesh": "Bangladesh",
}

# Country → gateway cities offered in the refinement opener.
_COUNTRY_GATEWAYS: dict[str, list[str]] = {
    "India": ["Delhi", "Mumbai", "Bangalore"],
    "Japan": ["Tokyo", "Osaka"],
    "Thailand": ["Bangkok", "Phuket"],
    "Italy": ["Rome", "Milan"],
    "France": ["Paris", "Nice"],
    "Spain": ["Madrid", "Barcelona"],
    "Greece": ["Athens", "Santorini"],
    "UK": ["London", "Manchester"],
    "Germany": ["Frankfurt", "Munich", "Berlin"],
    "Australia": ["Sydney", "Melbourne"],
    "New Zealand": ["Auckland", "Queenstown"],
    "UAE": ["Dubai", "Abu Dhabi"],
    "Singapore": ["Singapore"],
    "China": ["Beijing", "Shanghai"],
    "Vietnam": ["Ho Chi Minh City", "Hanoi"],
    "Philippines": ["Manila", "Cebu"],
    "Brazil": ["Sao Paulo", "Rio de Janeiro"],
    "Argentina": ["Buenos Aires"],
    "South Africa": ["Johannesburg", "Cape Town"],
    "Turkey": ["Istanbul"],
    "Portugal": ["Lisbon"],
    "Indonesia": ["Jakarta", "Bali"],
    "Malaysia": ["Kuala Lumpur"],
    "South Korea": ["Seoul"],
    "Egypt": ["Cairo"],
    "Mexico": ["Mexico City", "Cancun"],
    "Pakistan": ["Karachi", "Islamabad"],
    "Bangladesh": ["Dhaka"],
}

# Region landing pages (/flight/region/oceania/693) → countries to offer first.
_REGION_GATEWAYS: dict[str, list[str]] = {
    "oceania": ["Australia", "New Zealand"],
    "europe": ["UK", "France", "Italy"],
    "asia": ["Japan", "Thailand", "India"],
    "south-america": ["Brazil", "Argentina"],
    "middle-east": ["UAE", "Turkey"],
}

# Diaspora corridors where phone-country == destination + IP differs → needs_based prior.
_DIASPORA_CORRIDORS = {
    "india", "philippines", "vietnam", "pakistan", "bangladesh", "mexico",
}

_FREE_MAIL = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
    "aol.com", "mail.com", "protonmail.com", "live.com", "msn.com",
}

_VALUE_KW_RE = re.compile(
    r"\b(cheap|cheapest|deal|deals|discount|discounts|bargain|affordable|"
    r"budget|save|savings|price|prices|low[- ]?cost)\b", re.I
)
_FIRST_CLASS_KW_RE = re.compile(r"\bfirst\s+class\b", re.I)
_FAMILY_KW_RE = re.compile(
    r"\b(for\s+parents|for\s+my\s+parents|family\s+visit|visiting\s+family)\b", re.I
)
_USA_CORRIDOR_RE = re.compile(r"^usa[-_]", re.I)
_NUMERIC_ID_RE = re.compile(r"^\d+$")
_MOBILE_UA_RE = re.compile(
    r"Mobile|Android|iPhone|iPod|webOS|BlackBerry|IEMobile|Opera Mini", re.I
)
_OWN_DOMAINS = (
    "buybusinessclass.com",
    "buybusinesstravel.com",
    "businessclass.com",
)


def _normalize_amount(raw: str, k_suffix: Optional[str]) -> str:
    """'3,200' → '$3,200'; '5' + 'k' → '$5,000'."""
    value = raw.replace(",", "")
    try:
        number = float(value)
    except ValueError:
        return f"${raw}"
    if k_suffix:
        number *= 1000
    if number.is_integer():
        return f"${int(number):,}"
    return f"${number:,.2f}"


def _extract_occasion(text: str) -> Optional[str]:
    for label, pattern in OCCASION_PATTERNS:
        if pattern.search(text):
            return label
    m = OCCASION_PHRASE_RE.search(text)
    if m:
        phrase = m.group(1).strip().rstrip(".!,")
        # "for my boss" is who travels, not why — BOOKING_FOR_RE owns that.
        if phrase and not BOOKING_FOR_RE.search(m.group(0)) and len(phrase) <= 40:
            return phrase
    return None


def _extract_airline_preferences(text: str) -> tuple[Optional[str], Optional[str]]:
    """(preferred, avoided) airline names mentioned with a love/avoid verb."""
    preferred: Optional[str] = None
    avoided: Optional[str] = None
    lowered = text.lower()
    for airline in AIRLINES:
        idx = lowered.find(airline)
        if idx == -1:
            continue
        window = lowered[max(0, idx - 40):idx]
        name = airline.title()
        if AIRLINE_AVOID_RE.search(window):
            avoided = avoided or name
        elif AIRLINE_LOVE_RE.search(window):
            preferred = preferred or name
    return preferred, avoided


def is_comparison_origin(
    utm_source: Optional[str] = None, click_ids: Optional[dict] = None
) -> bool:
    """Did this visitor arrive from a fare-comparison site / kayak network?"""
    source = (utm_source or "").strip().lower()
    if source in COMPARISON_SOURCES:
        return True
    if click_ids:
        if any(click_ids.get(key) for key in COMPARISON_CLICK_IDS):
            return True
        # R2: kayak click id with empty utm_source still counts.
        if not source and (click_ids.get("kayak_click_id") or click_ids.get("kclid")):
            return True
    return False


def infer_paid_source(metadata: Optional[dict]) -> Optional[str]:
    """Click-id fallback when utm_source is empty: gclid→google, fbclid→fb, msclkid→bing."""
    if not metadata:
        return None
    if metadata.get("gclid"):
        return "google"
    if metadata.get("fbclid"):
        return "fb"
    if metadata.get("msclkid"):
        return "bing"
    if metadata.get("kayak_click_id") or metadata.get("kclid"):
        return "kayak"
    return None


def is_numeric_utm(value: Optional[str]) -> bool:
    """fb-style numeric campaign/term ids must never enter the prompt."""
    return bool(value and _NUMERIC_ID_RE.match(str(value).strip()))


def is_own_domain_referrer(referrer: Optional[str]) -> bool:
    if not referrer:
        return False
    lowered = referrer.lower()
    return any(domain in lowered for domain in _OWN_DOMAINS)


def is_mobile_ua(user_agent: Optional[str]) -> bool:
    return bool(user_agent and _MOBILE_UA_RE.search(user_agent))


def is_paid_social(utm_source: Optional[str], metadata: Optional[dict] = None) -> bool:
    source = (utm_source or "").strip().lower()
    if source in ("fb", "facebook", "instagram", "ig"):
        return True
    if metadata and metadata.get("fbclid") and not source:
        return True
    medium = ((metadata or {}).get("utm_medium") or "").strip().lower()
    return source in ("fb", "facebook") and medium in ("paid", "cpc", "cpm", "paidsocial")


def priors_from_keyword(utm_term: Optional[str]) -> dict:
    """Deterministic scan of the search keyword. Hint-only — never a lead field."""
    if not utm_term or is_numeric_utm(utm_term):
        return {}
    text = utm_term.strip().lower()
    if not text:
        return {}
    out: dict = {}

    # Country / city / airport destination hint (longest country slug first).
    padded = f" {re.sub(r'[^a-z0-9]+', ' ', text)} "
    for slug in sorted(_SLUG_COUNTRIES, key=len, reverse=True):
        key = slug.replace("-", " ")
        if f" {key} " in padded:
            out["destination_hint"] = _SLUG_COUNTRIES[slug]
            break
    if "destination_hint" not in out:
        for name in sorted(_SLUG_CITIES, key=len, reverse=True):
            if f" {name} " in padded:
                out["destination_hint"] = _SLUG_CITIES[name]
                break

    if _FIRST_CLASS_KW_RE.search(text):
        out["cabin_interest"] = "first"
        out["persona_prior"] = "experience_seeker"
        out["persona_source"] = "keyword_first_class"
    elif _VALUE_KW_RE.search(text):
        out["persona_prior"] = "value_driven"
        out["persona_source"] = "keyword_value"
    elif _FAMILY_KW_RE.search(text):
        out["persona_prior"] = "needs_based"
        out["persona_source"] = "keyword_family"
        out["confidence"] = "tint"

    return out


def parse_landing_hints(page_path: Optional[str]) -> dict:
    """Name-first landing parse. City beats country beats region (R1).

    The URL type segment lies (amsterdam under /country/) — resolve the slug
    NAME against maps regardless of path type. Hint-only.
    """
    if not page_path:
        return {}
    # Strip trailing numeric ids (/410, /8).
    cleaned = re.sub(r"/\d+/?$", "", page_path.strip())
    slug = re.sub(r"[^a-z0-9]+", " ", cleaned.lower()).strip()
    if not slug:
        return {}

    # Prefer the name after city|country|region when present.
    m = re.search(r"\b(?:city|country|region)\s+(.+)$", slug)
    name_part = (m.group(1) if m else slug).strip()
    padded = f" {name_part} "

    # City first (more specific) — amsterdam under /country/ resolves as city.
    city_matches = [(padded.rfind(f" {name} "), name) for name in _SLUG_CITIES]
    city_matches = [(pos, name) for pos, name in city_matches if pos != -1]
    if city_matches:
        _pos, name = max(city_matches, key=lambda item: (item[0], len(item[1])))
        return {"destination_city": _SLUG_CITIES[name], "landing_kind": "city"}

    # Airport codes in the slug.
    codes = [t.upper() for t in name_part.split() if len(t) == 3 and t.upper() in AIRPORTS]
    if codes:
        code = codes[-1]
        for name, mapped in CITY_TO_CODE.items():
            if mapped == code and len(name) >= 4:
                return {"destination_city": name.title(), "landing_kind": "city"}
        return {"destination_city": code, "landing_kind": "city"}

    # Country.
    for slug_key in sorted(_SLUG_COUNTRIES, key=len, reverse=True):
        key = slug_key.replace("-", " ")
        if f" {key} " in padded or name_part == key:
            country = _SLUG_COUNTRIES[slug_key]
            return {
                "destination_country": country,
                "gateways": _COUNTRY_GATEWAYS.get(country, []),
                "landing_kind": "country",
            }

    # Region.
    for region, countries in _REGION_GATEWAYS.items():
        key = region.replace("-", " ")
        if f" {key} " in padded or name_part == key:
            return {
                "destination_region": region.replace("-", " ").title(),
                "region_countries": countries,
                "landing_kind": "region",
            }

    return {}


def destination_from_path(page_path: Optional[str]) -> Optional[str]:
    """City the landing page was about, or None. A hint only — never a lead field."""
    hints = parse_landing_hints(page_path)
    return hints.get("destination_city")


def destination_country_from_path(page_path: Optional[str]) -> Optional[str]:
    """Country the landing page was about, or None. Separate from the city hint."""
    hints = parse_landing_hints(page_path)
    return hints.get("destination_country")


def campaign_origin_hint(utm_campaign: Optional[str]) -> Optional[str]:
    """R6: campaign 'USA-{X}' → origin hint US."""
    if not utm_campaign or is_numeric_utm(utm_campaign):
        return None
    if _USA_CORRIDOR_RE.search(utm_campaign.strip()):
        return "US"
    return None


def diaspora_triangulation(
    *,
    ip_country: Optional[str],
    phone_country: Optional[str],
    destination_country: Optional[str],
) -> bool:
    """J2: phone ≠ IP AND destination == phone AND corridor in the known set."""
    if not (ip_country and phone_country and destination_country):
        return False
    ip_c = ip_country.strip().upper()
    phone_c = phone_country.strip().upper()
    dest = destination_country.strip().lower()
    if ip_c == phone_c:
        return False
    # Map common ISO / names loosely for the corridor check.
    dest_slug = dest.replace(" ", "-")
    phone_as_dest = {
        "IN": "india", "PH": "philippines", "VN": "vietnam",
        "PK": "pakistan", "BD": "bangladesh", "MX": "mexico",
    }.get(phone_c)
    if not phone_as_dest:
        return False
    if dest_slug != phone_as_dest and dest != phone_as_dest:
        return False
    return phone_as_dest in _DIASPORA_CORRIDORS


def concordance_tier(metadata: Optional[dict], landing: Optional[dict] = None) -> str:
    """R3: HIGH when campaign + keyword + landing agree; MEDIUM one signal; else NONE."""
    meta = metadata or {}
    landing = landing or parse_landing_hints(
        meta.get("page_url") or meta.get("landing_page")
    )
    keyword = priors_from_keyword(meta.get("utm_term"))
    campaign = (meta.get("utm_campaign") or "").strip().lower()

    signals = 0
    if landing.get("destination_country") or landing.get("destination_city") or landing.get("destination_region"):
        signals += 1
    if keyword.get("destination_hint"):
        signals += 1
    if campaign and not is_numeric_utm(campaign):
        signals += 1

    if signals >= 3:
        # Check agreement when we have keyword + landing.
        kw_dest = (keyword.get("destination_hint") or "").lower()
        land_dest = (
            landing.get("destination_country")
            or landing.get("destination_city")
            or landing.get("destination_region")
            or ""
        ).lower()
        if kw_dest and land_dest and (kw_dest in land_dest or land_dest in kw_dest):
            return "high"
        if "oceania" in campaign and landing.get("landing_kind") == "region":
            return "high"
        return "medium"
    if signals == 1:
        return "medium"
    if signals == 2:
        return "medium"
    return "none"


def derive_t0_persona(
    metadata: Optional[dict] = None,
    *,
    occasion: Optional[str] = None,
    message_text: Optional[str] = None,
    visitor_email: Optional[str] = None,
    history_persona: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], str]:
    """t0→type map. Returns (persona, persona_source, confidence).

    confidence is 'frame' (full playbook) or 'tint' (one colored word) or 'none'.
    Occasion ALWAYS overrides. Forbidden: destination alone, product keywords,
    paid-social, device, time of day.
    """
    # LIVE occasion override — silent, always wins.
    if occasion:
        key = occasion.strip().lower()
        if key == "business":
            return "time_is_money", "occasion", "frame"
        if key == "celebration":
            return "experience_seeker", "occasion", "frame"
        if key == "medical":
            return "needs_based", "occasion", "frame"
        if key == "family":
            if message_text and FREQUENT_TRAVEL_RE.search(message_text):
                return "value_driven", "occasion_family_frequent", "frame"
            return "needs_based", "occasion_family", "frame"

    # 1. Returning visitor with confirmed persona.
    if history_persona in (
        "time_is_money", "experience_seeker", "needs_based", "value_driven"
    ):
        return history_persona, "history", "frame"

    meta = metadata or {}
    source = (meta.get("utm_source") or "").strip().lower() or infer_paid_source(meta) or ""
    keyword = priors_from_keyword(meta.get("utm_term"))
    landing = parse_landing_hints(meta.get("page_url") or meta.get("landing_page"))

    # 2. first class in keyword.
    if keyword.get("persona_source") == "keyword_first_class":
        return "experience_seeker", "keyword_first_class", "frame"

    # 3. cheap|deal|price in keyword.
    if keyword.get("persona_source") == "keyword_value":
        return "value_driven", "keyword_value", "frame"

    # 4. comparison network.
    if is_comparison_origin(source or meta.get("utm_source"), meta):
        return "value_driven", "utm_prior", "frame"

    # 5. Full diaspora triangulation only (all three conditions).
    dest_country = landing.get("destination_country") or keyword.get("destination_hint")
    if diaspora_triangulation(
        ip_country=meta.get("ip_country") or meta.get("origin_country_hint"),
        phone_country=meta.get("phone_country") or meta.get("country_code"),
        destination_country=dest_country,
    ):
        return "needs_based", "diaspora_prior", "frame"

    # 6. Corporate email → tint only.
    if visitor_email and "@" in visitor_email:
        domain = visitor_email.rsplit("@", 1)[-1].lower()
        if domain and domain not in _FREE_MAIL:
            return "time_is_money", "corporate_email", "tint"

    # 7. Family words in keyword → tint.
    if keyword.get("persona_source") == "keyword_family":
        return "needs_based", "keyword_family", "tint"

    # 8. Everything else → neutral (including paid-social, device, destination alone).
    return None, None, "none"


def derive_persona(
    occasion: Optional[str] = None,
    message_text: Optional[str] = None,
    utm_source: Optional[str] = None,
    click_ids: Optional[dict] = None,
    metadata: Optional[dict] = None,
    visitor_email: Optional[str] = None,
    history_persona: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """(persona, persona_source). Occasion wins; else the t0 map.

    Back-compat: callers that only pass utm_source/click_ids still work — we
    assemble a minimal metadata dict for derive_t0_persona.
    """
    meta = dict(metadata or {})
    if utm_source and "utm_source" not in meta:
        meta["utm_source"] = utm_source
    if click_ids:
        for key, value in click_ids.items():
            meta.setdefault(key, value)

    persona, source, _confidence = derive_t0_persona(
        meta,
        occasion=occasion,
        message_text=message_text,
        visitor_email=visitor_email,
        history_persona=history_persona,
    )
    return persona, source


DREAM_OUTCOMES = {
    "time_is_money": "rested",
    "experience_seeker": "experience",
    "needs_based": "comfort",
    "value_driven": "deal",
}


# ── Correction-path day-only dates (Deborah, TPA→SJU) ────────────────────
# "No returning on the 17th am if possible" burned a client turn: no parser
# handled a day without a month. With the summary's departure as context the
# month/year is resolvable deterministically.
_DAY_ONLY_RETURN_RE = re.compile(
    r"returning\s+(?:on\s+)?(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\b", re.IGNORECASE
)
# Bare form REQUIRES the ordinal suffix — "the 3 of us" must never read as a date.
_DAY_ONLY_BARE_RE = re.compile(r"\bthe\s+(\d{1,2})(?:st|nd|rd|th)\b", re.IGNORECASE)
# Collision guard (Ravi): a number that belongs to a passenger phrase is not a day.
_PAX_PHRASE_RE = re.compile(
    r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine)\s*"
    r"(?:of\s+us|people|passengers|travelers|travellers|pax)\b",
    re.IGNORECASE,
)
_TIME_PREF_MORNING_RE = re.compile(r"\b(?:am|morning)\b", re.IGNORECASE)
_TIME_PREF_EVENING_RE = re.compile(r"\b(?:pm|evening|night)\b", re.IGNORECASE)

# POST-SUMMARY ONLY. "Return", "Returning", "Return glight" (Alistair's
# typo) mean round trip once a summary is on the table — but ONLY in a
# short, return-centric message. Inside a sentence the word means many
# other things ("what is your return policy", "a returning customer",
# "I'll return to you later"), and reading those as a round trip rewrites
# a lead the client never touched.
_CORRECTION_RETURN_RE = re.compile(r"\breturn(?:ing)?\b", re.IGNORECASE)
_RETURN_MAX_WORDS = 4
# The same word with a NEGATION is the opposite instruction: the client is
# cancelling the return, which is a ONE-WAY correction (KAZUO: "Drop the
# return", "no return needed").
_RETURN_CANCEL_RE = re.compile(
    r"\b(?:no|not|dont|don't|do\s+not|without|cancel|drop|remove|skip|forget)\b"
    r"[^.?!]{0,24}\breturn(?:ing)?\b"
    r"|\breturn(?:ing)?\b[^.?!]{0,16}\b(?:not\s+needed|isn'?t\s+needed|cancelled|canceled)\b",
    re.IGNORECASE,
)
# Figures of speech that must never delete a booking field.
_RETURN_IDIOM_RE = re.compile(r"point\s+of\s+no\s+return", re.IGNORECASE)
# Nouns that are not a flight leg.
_RETURN_NOUN_RE = re.compile(
    r"\breturn(?:ing)?\s+(?:the\s+|my\s+|your\s+|a\s+)?"
    r"(?:call|policy|ticket|customer|item|order|favou?r|email|message)\b",
    re.IGNORECASE,
)
# "Oct 1 or Oct 8" / "Oct 1 or the 8th" — alternatives, never a range.
# The word half MUST be a real month: an unconstrained \w+ turned
# "Terminal 5 or Terminal 2" and "gate 5 or gate 6" into date questions.
_ALT_SIDE = (
    rf"(?:(?:{_MONTH_PAT})\s+\d{{1,2}}(?:st|nd|rd|th)?"
    rf"|\d{{1,2}}(?:st|nd|rd|th)\s+(?:of\s+)?(?:{_MONTH_PAT})"
    rf"|the\s+\d{{1,2}}(?:st|nd|rd|th)"
    rf"|\d{{1,2}}(?:st|nd|rd|th)"
    rf"|\d{{4}}-\d{{2}}-\d{{2}})"
)
_ALT_JOIN_RE = re.compile(
    rf"({_ALT_SIDE})\s+or\s+({_ALT_SIDE})", re.IGNORECASE
)


def _date_alternatives(text: str) -> Optional[list[str]]:
    """The two options a client offered, verbatim — the pipeline asks
    which one instead of silently picking the first."""
    m = _ALT_JOIN_RE.search(text)
    if not m:
        return None
    a, b = m.group(1).strip(), m.group(2).strip()
    return [a, b] if a.lower() != b.lower() else None


def _resolve_day_only_return(entities: ExtractedEntities, text: str, departure_iso: str) -> None:
    """Resolve "returning on the 17th" against the summary's departure.

    Guards: a full date with a month already parsed from THIS message wins
    (skip); numbers inside passenger phrases are never days; day must be
    1-31 and produce a real calendar date — nonsense stays unset (the
    re-ask is then correct)."""
    # Time preference survives regardless of whether a date resolves.
    if _TIME_PREF_MORNING_RE.search(text):
        entities.time_preference = "morning"
    elif _TIME_PREF_EVENING_RE.search(text):
        entities.time_preference = "evening"

    # The full-date parsers already spoke — their answer wins.
    if entities.return_date or entities.departure_date:
        return

    pax_spans = [m.span(1) for m in _PAX_PHRASE_RE.finditer(text)]

    def _in_pax_phrase(start: int, end: int) -> bool:
        return any(ps <= start and end <= pe for ps, pe in pax_spans)

    m = _DAY_ONLY_RETURN_RE.search(text)
    if not m:
        m = _DAY_ONLY_BARE_RE.search(text)
    if not m or _in_pax_phrase(*m.span(1)):
        return
    day = int(m.group(1))
    if not 1 <= day <= 31:
        return

    from datetime import date as _date

    try:
        dep = _date.fromisoformat(departure_iso[:10])
    except ValueError:
        return
    # Same month as departure when the day falls after it; else next month.
    year, month = dep.year, dep.month
    if day <= dep.day:
        month += 1
        if month == 13:
            month, year = 1, year + 1
    try:
        resolved = _date(year, month, day)
    except ValueError:
        return  # e.g. Feb 31 — no entity; the re-ask is the right answer
    entities.return_date = resolved.isoformat()
    if entities.trip_type != "multi_city":
        entities.trip_type = "round_trip"


def extract_entities(
    message: str, *, context: dict | None = None, country: str | None = None
) -> ExtractedEntities:
    """Extract structured entities from a visitor chat message.

    context (correction path ONLY — the post-summary probe passes it):
    {"departure_date": "YYYY-MM-DD"} lets day-only return mentions
    ("returning on the 17th") resolve their month/year from the summary's
    departure. Default None — every other call site is unchanged.

    country (the visit's, app.services.phone_country.country_hint): a number
    typed the national way with no trunk 0 ("912345678" from Portugal) is
    taken when its length is that country's (CodeRabbit #270)."""
    entities = ExtractedEntities()
    text = message.strip()

    # 1. Email
    m = EMAIL_RE.search(text)
    if m:
        entities.email = m.group(0).lower()

    # 2. Phone (never a date written in digits, nor a booking reference: "ref 2105550100")
    from app.pipeline.callback_intent import follows_reference

    for m in PHONE_RE.finditer(text):
        if not _DATE_LIKE.match(m.group(0).strip()) and not follows_reference(text, m.start()):
            entities.phone = m.group(0).strip()
            break
    if not entities.phone and country:
        # As typed, like every number above: it is placed with the same country where it is saved.
        from app.pipeline.callback_intent import national_candidate

        entities.phone = national_candidate(text, country)

    # 3. Name
    for pattern in NAME_PATTERNS:
        m = pattern.search(text)
        if m:
            entities.name = m.group(1).strip()
            break

    # 4. Airport codes (direct in message)
    codes = [c.group(1) for c in AIRPORT_RE.finditer(text) if c.group(1) in AIRPORTS]
    _unique_codes = list(dict.fromkeys(codes))
    if len(_unique_codes) > 2:
        # Multi-city (conv #1244 "Ali": JFK→CMN→CAI→DEL→JFK became "JFK↔CMN
        # round trip", middle legs lost). Route fields carry the FIRST leg;
        # the full chain rides `itinerary` into the lead notes; return_date
        # is never guessed from a middle leg.
        entities.origin_code = codes[0]
        entities.destination_code = codes[1]
        entities.trip_type = "multi_city"
        # Collapse consecutive repeats: "JFK CMN, CMN CAI, CAI DEL, DEL JFK"
        # reads as the chain JFK→CMN→CAI→DEL→JFK.
        _chain = [codes[0]]
        for _c in codes[1:]:
            if _c != _chain[-1]:
                _chain.append(_c)
        entities.itinerary = "→".join(_chain)
    elif len(codes) >= 2:
        _from_iata = None
        _to_iata = None
        for code in codes:
            if re.search(r'\bfrom\s+' + re.escape(code) + r'\b', text, re.I):
                _from_iata = code
            if re.search(r'\bto\s+' + re.escape(code) + r'\b', text, re.I):
                _to_iata = code
        if _from_iata and _to_iata and _from_iata != _to_iata:
            entities.origin_code = _from_iata
            entities.destination_code = _to_iata
        else:
            entities.origin_code = codes[0]
            entities.destination_code = codes[1]
    elif len(codes) == 1:
        # Context-aware: determine if code is origin or destination
        code = codes[0]
        code_lower = code.lower()
        lower_text = text.lower()
        from_match = re.search(
            r'(?:from|departing|leaving)\s+' + re.escape(code_lower),
            lower_text,
        )
        to_match = re.search(
            r'(?:to|arriving|going\s+to|bound\s+for|heading\s+to)\s+' + re.escape(code_lower),
            lower_text,
        )
        if from_match and not to_match:
            entities.origin_code = code
        elif to_match and not from_match:
            entities.destination_code = code
        # Both or neither matched? Don't assign — let ROUTE_RE / Claude handle it

    # 5. City names → airport codes (fallback if either endpoint still missing)
    if not entities.origin_code or not entities.destination_code:
        m = ROUTE_RE.search(text)
        if m:
            origin = m.group(1).strip().lower()
            dest = m.group(2).strip().lower()
            origin_iata = CITY_TO_CODE.get(origin) or (
                origin.upper() if origin.upper() in AIRPORTS else None
            )
            dest_iata = CITY_TO_CODE.get(dest) or (
                dest.upper() if dest.upper() in AIRPORTS else None
            )
            if origin_iata and not entities.origin_code:
                entities.origin_code = origin_iata
            if dest_iata and not entities.destination_code:
                entities.destination_code = dest_iata
        if not entities.origin_code or not entities.destination_code:
            m2 = ROUTE_FROM_TO_RE.search(text)
            if m2:
                origin = m2.group(1).strip().lower()
                dest = m2.group(2).strip().lower()
                origin_iata = CITY_TO_CODE.get(origin) or (
                    origin.upper() if origin.upper() in AIRPORTS else None
                )
                dest_iata = CITY_TO_CODE.get(dest) or (
                    dest.upper() if dest.upper() in AIRPORTS else None
                )
                if origin_iata and not entities.origin_code:
                    entities.origin_code = origin_iata
                if dest_iata and not entities.destination_code:
                    entities.destination_code = dest_iata

    # 6. Passengers
    _pax_v2 = flag("PAX_REGEX_V2", True)
    # True when the count names adults only ("2 adults", "my wife and I"), so a
    # child named by age is added to it rather than already inside it.
    _pax_adults_only = False
    # True when "me and my" names the minor as the companion: the count is
    # then one adult plus the minors the ages name, not two adults plus them.
    _pax_minor_companion = False
    m = (PAX_RE_V2 if _pax_v2 else PAX_RE).search(text)
    if m:
        _pax_adults_only = bool(re.search(r'adults?', m.group(0), re.I))
        n = int(m.group(1))
        if 1 <= n <= 9:
            entities.passengers = n
        elif n > 9:
            entities.passengers = 9
    elif SOLO_PAX_RE.search(text) and not SOLO_VETO_RE.search(text):
        entities.passengers = 1
    elif _pax_v2 and SOLO_ONE_ME_RE.search(text) and not SOLO_VETO_RE.search(text):
        entities.passengers = 1
    elif re.search(r'\btwo of us\b|\bme and my\b', text, re.I):
        entities.passengers = 2
        _pax_adults_only = bool(re.search(r'\bme and my\b', text, re.I))
        _pax_minor_companion = _pax_v2 and bool(ME_AND_MY_MINOR_RE.search(text))
    elif _pax_v2 and MY_X_AND_I_RE.search(text):
        entities.passengers = 2
        _pax_adults_only = True

    # Word numbers: "one" as a short answer, "two travelers" anywhere.
    # Bare word-numbers only count in short answers, and never "one way".
    if not entities.passengers:
        wm = (PAX_WORD_RE_V2 if _pax_v2 else PAX_WORD_RE).search(text)
        if wm:
            _has_unit = bool(
                _PAX_WORD_UNIT_V2.search(wm.group(0)) if _pax_v2 else re.search(
                    r'travell?er|passenger|person|people|pax', wm.group(0), re.I
                )
            )
            _short_answer = len(text.split()) <= 3
            _trip_phrase = re.search(r'\bone[\s-]?way\b', text, re.I)
            if _has_unit or (_short_answer and not _trip_phrase):
                entities.passengers = WORD_NUMBERS[wm.group(1).lower()]
                # "one adult and a child": the count names the adults, the child is added to it.
                _pax_adults_only = _pax_v2 and bool(re.search(r'adults?', wm.group(0), re.I))

    # Children/infants — add to adult count if both present
    child_m = CHILD_RE.search(text)
    infant_m = INFANT_RE.search(text)
    children_count = int(child_m.group(1)) if child_m else 0
    infant_count = int(infant_m.group(1)) if infant_m else 0
    # v2: a minor counted in words after the adults ("and a child", "with one baby"). Only on top of a
    # count that names the adults, or of none: "3 passengers with a child" is three, not four.
    child_words = None
    infant_words = None
    if _pax_v2 and (not entities.passengers or _pax_adults_only):
        child_words = None if child_m else CHILD_WORD_RE.search(text)
        infant_words = None if infant_m else INFANT_WORD_RE.search(text)
        if child_words:
            children_count = _WORD_COUNT[child_words.group(1).lower()]
        if infant_words:
            infant_count = _WORD_COUNT[infant_words.group(1).lower()]
    if _pax_v2 and (not child_m or not infant_m) and (not entities.passengers or _pax_adults_only):
        # "2 adults + a 9-year-old": the child is named by an age, not a count.
        # 2–11 is a child fare, under 2 an infant; 12+ is already an adult. A kind counted in words or in digits
        # keeps its count (the age then names that minor); the other kind still comes from the ages: "2 adults
        # and a child plus a 1-year-old" is a child and an infant (CodeRabbit #279). Beside a kind counted, only
        # an age that names a person is one more minor: "2 kids and a 1-year-old" is two children and an infant,
        # "2 kids (5 and 1 years old)" two children (CodeRabbit #279, round 2), "a baby, she'll be 2 years old"
        # one infant. Kids may be babies, so a baby's age set off right after them names one of them.
        _kids = child_m or child_words
        _counted = _kids or infant_m or infant_words
        _ages = [(int(m.group(1)), m.start()) for m in (ADDED_AGE_RE if _counted else AGE_RE).finditer(text)]
        _named = KIDS_NAMED_RE.match(text, _kids.end()) if _kids else None
        # Only the ages inside that span name the kids counted; an age outside it is one more minor.
        _named_from, _named_to = _kids_named_span(text, _named) if _named else (0, 0)
        if not child_m and not child_words:
            children_count = sum(1 for a, _ in _ages if 2 <= a <= 11)
        if not infant_m and not infant_words:
            infant_count = sum(1 for a, at in _ages if a < 2 and not _named_from <= at < _named_to)

    if children_count or infant_count:
        adult_count = entities.passengers or 0
        if _pax_minor_companion:
            adult_count = 1  # "me": the companion is one of the minors
        if not adult_count and (children_count or infant_count):
            adult_count = 1
        entities.passengers = adult_count + children_count + infant_count
        entities.children_count = children_count
        entities.infant_count = infant_count

    # Extended patterns: family, group, couple, solo
    if not entities.passengers:
        fm = FAMILY_RE.search(text)
        if fm:
            entities.passengers = min(9, max(1, int(fm.group(1))))
    if not entities.passengers and _pax_v2:
        fwm = FAMILY_WORD_RE.search(text)
        if fwm:
            entities.passengers = WORD_NUMBERS[fwm.group(1).lower()]
    if not entities.passengers and _pax_v2 and SOLO_SHORT_RE.fullmatch(text):
        entities.passengers = 1
    if not entities.passengers:
        gm = GROUP_RE.search(text)
        if gm:
            entities.passengers = min(9, max(1, int(gm.group(1))))
    if not entities.passengers:
        if COUPLE_RE.search(text):
            entities.passengers = 2
    if not entities.passengers:
        if SOLO_RE.search(text) and not SOLO_VETO_RE.search(text):
            entities.passengers = 1

    # Cap passengers at 9 (CRM API max)
    if entities.passengers and entities.passengers > 9:
        entities.passengers = 9

    # Leading digit + punctuation: "2. Both over 65", "3, all adults"
    if not entities.passengers:
        _lead_match = PAX_LEADING_RE.match(text)
        if _lead_match:
            _val = int(_lead_match.group(1))
            if 1 <= _val <= 9:
                entities.passengers = _val

    # Standalone single digit (1-9) — likely answering "how many passengers?"
    if not entities.passengers:
        standalone_m = re.match(r'^(\d)$', text.strip())
        if standalone_m:
            n = int(standalone_m.group(1))
            if 1 <= n <= 9:
                entities.passengers = n

    # 7. Cabin class
    m = CABIN_RE.search(text)
    if m:
        entities.cabin_class = CABIN_MAP.get(m.group(1).lower().strip(), "business")

    # 7b. Trip type (one-way vs round-trip) — never demote a detected multi-city
    if entities.trip_type != "multi_city":
        if ONE_WAY_RE.search(text):
            entities.trip_type = "one_way"
        elif ROUND_TRIP_RE.search(text):
            entities.trip_type = "round_trip"

    # 8. Dates
    dep, ret = _extract_dates(text)
    if dep:
        entities.departure_date = dep
    if ret:
        entities.return_date = ret

    # Multi-city: the second date in the message is a MIDDLE-LEG date, not a
    # return (conv #1244 mislabeled CMN→CAI's date as return_date). Leave NULL —
    # the consultant confirms the real final leg on the call.
    if entities.trip_type == "multi_city":
        entities.return_date = None

    # Infer trip_type from return_date if not explicitly stated
    if entities.return_date and not entities.trip_type:
        entities.trip_type = "round_trip"

    # 9. Sales-methodology signals — the occasion we ask for once, and the
    # signals we never ask for but keep when volunteered.
    entities.occasion = _extract_occasion(text)

    bf = BOOKING_FOR_RE.search(text)
    if bf:
        entities.booking_for = bf.group(1).lower()[:20]

    if DATE_FLEXIBLE_RE.search(text):
        entities.date_flexible = True
    elif DATE_FIXED_RE.search(text):
        entities.date_flexible = False

    if NONSTOP_RE.search(text):
        entities.nonstop_only = True

    entities.airline_preference, entities.airline_avoid = _extract_airline_preferences(text)

    seen = PRICE_SEEN_RE.search(text)
    if seen:
        entities.price_seen = _normalize_amount(seen.group(1), seen.group(2))

    budget = BUDGET_RE.search(text)
    if budget and not (seen and seen.start() <= budget.start() <= seen.end()):
        entities.budget_hint = _normalize_amount(budget.group(1), budget.group(2))

    call_time = CALL_TIME_RE.search(text)
    if call_time:
        entities.best_call_time = call_time.group(1).strip().rstrip(".")[:40]

    entities.contact_channel = contact_channel_of(text)
    entities.needs = needs_of(text)

    # Correction-path only: day-only return mentions resolve against the
    # summary's departure. Runs AFTER the normal parsers so a full date
    # with a month always wins.
    if context and context.get("departure_date"):
        _resolve_day_only_return(entities, text, context["departure_date"])

    # We ASKED "when would you fly back?" — the answer is a return date,
    # whatever shape it takes. Reading it as a departure moved the
    # outbound and re-triggered the same question every turn.
    if context and context.get("expect") == "return":
        if entities.departure_date and not entities.return_date:
            entities.return_date = entities.departure_date
            entities.departure_date = None
        if entities.return_date and entities.trip_type != "multi_city":
            entities.trip_type = "round_trip"

    # Correction-path only (context given): after a summary, a bare
    # "Return" / "Return glight" IS a round-trip correction — Alistair
    # said it twice and the pipeline heard nothing. Mid-collection the
    # same word is ambiguous ("I'll return the call"), so this never
    # runs there.
    if context is not None:
        if not entities.trip_type:
            _words = len(text.split())
            if _RETURN_IDIOM_RE.search(text):
                pass  # "point of no return" is not a booking instruction
            elif _RETURN_CANCEL_RE.search(text):
                # "No return needed" is a ONE-WAY correction, not a round
                # trip. Reading it as round trip made the pipeline argue
                # with the client and re-ask forever.
                entities.trip_type = "one_way"
            elif (
                _CORRECTION_RETURN_RE.search(text)
                and not _RETURN_NOUN_RE.search(text)
                and "?" not in text
                and _words <= _RETURN_MAX_WORDS
            ):
                entities.trip_type = "round_trip"
        # "Oct 1 or Oct 8" — two dates joined by OR are ALTERNATIVES, not
        # a range. Picking one silently is how a client ends up booked on
        # a day they never chose.
        _alts = _date_alternatives(text)
        if _alts:
            entities.date_alternatives = _alts

    found = [k for k in ["email", "phone", "name", "origin_code", "destination_code",
                          "passengers", "cabin_class", "departure_date", "return_date", "trip_type",
                          "occasion", "booking_for", "nonstop_only", "airline_preference",
                          "airline_avoid", "budget_hint", "price_seen", "best_call_time",
                          "contact_channel", "time_preference", "needs"]
             if getattr(entities, k) is not None]
    if found:
        logger.info(f"Entities extracted: {', '.join(found)}")

    return entities


def extract_kb_keywords(message: str) -> list[str]:
    """Extract meaningful keywords for KB search (stopwords removed)."""
    words = re.findall(r'[a-zA-Z]{3,}', message.lower())
    keywords = [w for w in words if w not in STOPWORDS]

    # Boost airport codes
    codes = [c.group(1).lower() for c in AIRPORT_RE.finditer(message) if c.group(1) in AIRPORTS]
    keywords = codes + [k for k in keywords if k not in codes]

    return keywords[:5]
