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
# Leading digit followed by punctuation (catches "2. Both over 65", "3, all adults")
PAX_LEADING_RE = re.compile(r'^\s*(\d{1,2})\s*[.,;:\-!)\]]', re.I)
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


def _resolve_year(month: int) -> int:
    """If month is in the past relative to today, assume next year."""
    today = date.today()
    return today.year + 1 if month < today.month else today.year


def _make_date(month: int, day: int, year: int | None = None) -> str | None:
    """Create YYYY-MM-DD string. Returns None if date is invalid (e.g. Feb 30)."""
    try:
        return date(year or _resolve_year(month), month, day).isoformat()
    except (ValueError, OverflowError):
        return None


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
            m_val, d_val = int(match.group("m")), int(match.group("d"))
            y_val = int(match.group("y")) if match.group("y") else None
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
    if len(codes) >= 2:
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

    # 7b. Trip type (one-way vs round-trip)
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

    # Infer trip_type from return_date if not explicitly stated
    if entities.return_date and not entities.trip_type:
        entities.trip_type = "round_trip"

    found = [k for k in ["email", "phone", "name", "origin_code", "destination_code",
                          "passengers", "cabin_class", "departure_date", "return_date", "trip_type"]
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
