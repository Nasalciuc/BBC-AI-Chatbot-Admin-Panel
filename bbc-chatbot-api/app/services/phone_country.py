"""Where the client probably is, for a phone number typed without its country code.

Chat #3296 (17 Nov 2026): a client in Australia typed his mobile the local way (04…). The extractor
did not take it (it knew only US shapes and numbers with +), the operator dialled it as it stood, and
the call failed. format_phone_international put "+1" before every 10-digit number, so even once taken
it would have become an impossible US number.

The country comes from what the widget reports (metadata.browser_lang "en-AU", metadata.tz
"Australia/Adelaide"), then from an edge header if one ever exists (metadata.ip_country). It is only a
hint for numbers that carry no country of their own: a number with + or 00 keeps its own.
"""

from __future__ import annotations

import re
from typing import Optional

# ISO 3166-1 alpha-2 → calling code, for the countries business-class clients write from.
CALLING_CODES: dict[str, str] = {
    "US": "1", "CA": "1", "PR": "1", "GB": "44", "IE": "353", "AU": "61", "NZ": "64", "FR": "33",
    "DE": "49", "ES": "34", "IT": "39", "PT": "351", "NL": "31", "BE": "32", "CH": "41", "AT": "43",
    "SE": "46", "NO": "47", "DK": "45", "FI": "358", "PL": "48", "CZ": "420", "GR": "30", "RO": "40",
    "MD": "373", "UA": "380", "HU": "36", "TR": "90", "IL": "972", "AE": "971", "SA": "966", "QA": "974",
    "KW": "965", "EG": "20", "ZA": "27", "NG": "234", "KE": "254", "MA": "212", "IN": "91", "PK": "92",
    "BD": "880", "LK": "94", "PH": "63", "SG": "65", "MY": "60", "TH": "66", "VN": "84", "ID": "62",
    "JP": "81", "KR": "82", "CN": "86", "HK": "852", "TW": "886", "MX": "52", "BR": "55", "AR": "54",
    "CL": "56", "CO": "57", "PE": "51", "SV": "503", "GT": "502", "HN": "504", "CR": "506", "PA": "507",
    "DO": "1", "JM": "1", "EC": "593", "VE": "58", "UY": "598",
}

# IANA time zones of those countries (the widget's Intl time zone). A prefix ends with "/".
_TZ: tuple[tuple[str, str], ...] = (
    ("Australia/", "AU"), ("Pacific/Auckland", "NZ"), ("Europe/London", "GB"), ("Europe/Dublin", "IE"),
    ("Europe/Paris", "FR"), ("Europe/Berlin", "DE"), ("Europe/Madrid", "ES"), ("Atlantic/Canary", "ES"),
    ("Europe/Rome", "IT"), ("Europe/Lisbon", "PT"), ("Europe/Amsterdam", "NL"), ("Europe/Brussels", "BE"),
    ("Europe/Zurich", "CH"), ("Europe/Vienna", "AT"), ("Europe/Stockholm", "SE"), ("Europe/Oslo", "NO"),
    ("Europe/Copenhagen", "DK"), ("Europe/Helsinki", "FI"), ("Europe/Warsaw", "PL"), ("Europe/Prague", "CZ"),
    ("Europe/Athens", "GR"), ("Europe/Bucharest", "RO"), ("Europe/Chisinau", "MD"), ("Europe/Kiev", "UA"),
    ("Europe/Kyiv", "UA"), ("Europe/Budapest", "HU"), ("Europe/Istanbul", "TR"), ("Asia/Jerusalem", "IL"),
    ("Asia/Tel_Aviv", "IL"), ("Asia/Dubai", "AE"), ("Asia/Riyadh", "SA"), ("Asia/Qatar", "QA"),
    ("Asia/Kuwait", "KW"), ("Africa/Cairo", "EG"), ("Africa/Johannesburg", "ZA"), ("Africa/Lagos", "NG"),
    ("Africa/Nairobi", "KE"), ("Africa/Casablanca", "MA"), ("Asia/Kolkata", "IN"), ("Asia/Calcutta", "IN"),
    ("Asia/Karachi", "PK"), ("Asia/Dhaka", "BD"), ("Asia/Colombo", "LK"), ("Asia/Manila", "PH"),
    ("Asia/Singapore", "SG"), ("Asia/Kuala_Lumpur", "MY"), ("Asia/Bangkok", "TH"), ("Asia/Ho_Chi_Minh", "VN"),
    ("Asia/Saigon", "VN"), ("Asia/Jakarta", "ID"), ("Asia/Tokyo", "JP"), ("Asia/Seoul", "KR"),
    ("Asia/Shanghai", "CN"), ("Asia/Hong_Kong", "HK"), ("Asia/Taipei", "TW"), ("America/Mexico_City", "MX"),
    ("America/Monterrey", "MX"), ("America/Cancun", "MX"), ("America/Tijuana", "MX"), ("America/Sao_Paulo", "BR"),
    ("America/Argentina/", "AR"), ("America/Buenos_Aires", "AR"), ("America/Santiago", "CL"),
    ("America/Bogota", "CO"), ("America/Lima", "PE"), ("America/El_Salvador", "SV"), ("America/Guatemala", "GT"),
    ("America/Tegucigalpa", "HN"), ("America/Costa_Rica", "CR"), ("America/Panama", "PA"),
    ("America/Santo_Domingo", "DO"), ("America/Jamaica", "JM"), ("America/Guayaquil", "EC"),
    ("America/Caracas", "VE"), ("America/Montevideo", "UY"), ("America/Puerto_Rico", "PR"),
    ("America/Toronto", "CA"), ("America/Vancouver", "CA"), ("America/Edmonton", "CA"), ("America/Winnipeg", "CA"),
    ("America/Halifax", "CA"), ("America/St_Johns", "CA"), ("America/Regina", "CA"),
    ("America/", "US"), ("Pacific/Honolulu", "US"),
)

_LANG_REGION = re.compile(r"^[a-z]{2,3}[-_]([A-Za-z]{2})\b")


def country_hint(metadata: Optional[dict]) -> Optional[str]:
    """ISO alpha-2 of where the client probably is, or None when nothing says."""
    meta = metadata or {}
    m = _LANG_REGION.match(str(meta.get("browser_lang") or "").strip())
    if m and m.group(1).upper() in CALLING_CODES:
        return m.group(1).upper()
    tz = str(meta.get("tz") or "").strip()
    if tz:
        for prefix, iso in _TZ:
            if tz == prefix or (prefix.endswith("/") and tz.startswith(prefix)):
                return iso
    ip = str(meta.get("ip_country") or "").strip().upper()
    return ip if ip in CALLING_CODES else None


def calling_code(country: Optional[str]) -> Optional[str]:
    return CALLING_CODES.get((country or "").strip().upper()) if country else None
