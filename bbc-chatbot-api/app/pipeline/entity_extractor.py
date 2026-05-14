"""Entity extraction V1 — regex patterns for structured data from chat messages.

Extracts: email (99%), phone (95%), airport codes (100%), 
city names top-20 (100%), passengers, cabin class, name (~70%).

Does NOT extract: dates from text, verbal phone numbers, airport aliases.
Those are V2 (Claude-based extraction).
"""

import re
import logging
from dataclasses import dataclass
from datetime import date
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

PAX_RE = re.compile(r'(\d+)\s*(?:passengers?|people|persons?|travelers?|pax|of us)', re.I)

CABIN_KEYWORDS = {"business", "business class", "first", "first class", "economy", "coach"}
CABIN_RE = re.compile(r'\b(business\s*class|first\s*class|business|first|economy|coach)\b', re.I)
CABIN_MAP = {
    "business": "business", "business class": "business",
    "first": "first", "first class": "first",
    "economy": "economy", "coach": "economy",
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
    Only handles ABSOLUTE dates. Does NOT handle relative dates like 'next Monday'.
    """
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
        if 1 <= n <= 20:
            entities.passengers = n
    elif re.search(r'\bjust\s+me\b', text, re.I):
        entities.passengers = 1
    elif re.search(r'\btwo of us\b|\bme and my\b', text, re.I):
        entities.passengers = 2

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
