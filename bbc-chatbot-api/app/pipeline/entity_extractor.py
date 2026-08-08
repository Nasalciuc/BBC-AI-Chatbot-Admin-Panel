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


# ── Patterns ──────────────────────────────────────────────────

EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')

PHONE_RE = re.compile(
    r'(?:\+?1[-.\s]?)?\(?[2-9]\d{2}\)?[-.\s]?\d{3}[-.\s]?\d{4}'
    r'|(?:\+\d{1,3}[-.\s]?)(?:\d[-.\s]?){7,14}\d'
)

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

# "March 15", "Mar 15th", "15 March", "15th of March", "March 15, 2026"
_DATE_MONTH_DAY = re.compile(
    r'\b(?:'
    r'(?P<m1>' + _MONTH_PAT + r')\s+(?P<d1>\d{1,2})(?:st|nd|rd|th)?'
    r'|(?P<d2>\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<m2>' + _MONTH_PAT + r')'
    r')(?:\s*,?\s*(?P<y>\d{4}))?',
    re.I
)

# "3/15", "3/15/2026", "03-15-2026"
_DATE_NUMERIC = re.compile(r'\b(?P<m>\d{1,2})[/\-](?P<d>\d{1,2})(?:[/\-](?P<y>\d{4}))?\b')

# "March 15-22" (same month range with dash)
_DATE_RANGE_DASH = re.compile(
    r'\b(?P<m>' + _MONTH_PAT + r')\s+(?P<d1>\d{1,2})(?:st|nd|rd|th)?'
    r'\s*[-–]\s*(?P<d2>\d{1,2})(?:st|nd|rd|th)?',
    re.I
)


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

    # 1. Same-month range: "March 15-22"
    m = _DATE_RANGE_DASH.search(text)
    if m:
        month = MONTH_NAMES.get(m.group("m").lower())
        if month:
            dep = _make_date(month, int(m.group("d1")))
            ret = _make_date(month, int(m.group("d2")))
            if dep:
                return dep, ret

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


def extract_entities(message: str) -> ExtractedEntities:
    """Extract structured entities from a visitor chat message."""
    entities = ExtractedEntities()
    text = message.strip()

    # 1. Email
    m = EMAIL_RE.search(text)
    if m:
        entities.email = m.group(0).lower()

    # 2. Phone
    m = PHONE_RE.search(text)
    if m:
        entities.phone = m.group(0).strip()

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
    m = PAX_RE.search(text)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 9:
            entities.passengers = n
        elif n > 9:
            entities.passengers = 9
    elif re.search(r'\bjust\s+me\b', text, re.I):
        entities.passengers = 1
    elif re.search(r'\btwo of us\b|\bme and my\b', text, re.I):
        entities.passengers = 2

    # Word numbers: "one" as a short answer, "two travelers" anywhere.
    # Bare word-numbers only count in short answers, and never "one way".
    if not entities.passengers:
        wm = PAX_WORD_RE.search(text)
        if wm:
            _has_unit = bool(re.search(
                r'travell?er|passenger|person|people|pax', wm.group(0), re.I
            ))
            _short_answer = len(text.split()) <= 3
            _trip_phrase = re.search(r'\bone[\s-]?way\b', text, re.I)
            if _has_unit or (_short_answer and not _trip_phrase):
                entities.passengers = WORD_NUMBERS[wm.group(1).lower()]

    # Children/infants — add to adult count if both present
    child_m = CHILD_RE.search(text)
    infant_m = INFANT_RE.search(text)
    children_count = int(child_m.group(1)) if child_m else 0
    infant_count = int(infant_m.group(1)) if infant_m else 0

    if children_count or infant_count:
        adult_count = entities.passengers or 0
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
    if not entities.passengers:
        gm = GROUP_RE.search(text)
        if gm:
            entities.passengers = min(9, max(1, int(gm.group(1))))
    if not entities.passengers:
        if COUPLE_RE.search(text):
            entities.passengers = 2
    if not entities.passengers:
        if SOLO_RE.search(text):
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

    found = [k for k in ["email", "phone", "name", "origin_code", "destination_code",
                          "passengers", "cabin_class", "departure_date", "return_date", "trip_type",
                          "occasion", "booking_for", "nonstop_only", "airline_preference",
                          "airline_avoid", "budget_hint", "price_seen", "best_call_time"]
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
