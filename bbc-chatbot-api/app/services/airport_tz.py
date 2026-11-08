"""The city and time zone of a departure airport, for "13:40 in Chicago".

A client's local time is a guess: we don't know where they are, only where
the trip starts. The panel says which city it used, never "his time", so the
guess shows. Codes cover the departure airports in leads over 120 days (8 Nov
2026: LAX, JFK, SFO, DFW, ATL… down to 3 leads) plus the gateways the
extractor knows. An unknown code gives nothing, and the panel shows only its
own time.
"""

from __future__ import annotations

from typing import Optional

# IATA code → (city as the panel writes it, IANA time zone).
AIRPORTS: dict[str, tuple[str, str]] = {
    # United States, Eastern
    "JFK": ("New York", "America/New_York"), "LGA": ("New York", "America/New_York"),
    "EWR": ("Newark", "America/New_York"), "BOS": ("Boston", "America/New_York"),
    "ATL": ("Atlanta", "America/New_York"), "MIA": ("Miami", "America/New_York"),
    "FLL": ("Fort Lauderdale", "America/New_York"), "MCO": ("Orlando", "America/New_York"),
    "TPA": ("Tampa", "America/New_York"), "JAX": ("Jacksonville", "America/New_York"),
    "RSW": ("Fort Myers", "America/New_York"), "PBI": ("West Palm Beach", "America/New_York"),
    "IAD": ("Washington", "America/New_York"), "DCA": ("Washington", "America/New_York"),
    "BWI": ("Baltimore", "America/New_York"), "PHL": ("Philadelphia", "America/New_York"),
    "PIT": ("Pittsburgh", "America/New_York"), "CLT": ("Charlotte", "America/New_York"),
    "RDU": ("Raleigh", "America/New_York"), "DTW": ("Detroit", "America/Detroit"),
    "CLE": ("Cleveland", "America/New_York"), "CMH": ("Columbus", "America/New_York"),
    "CVG": ("Cincinnati", "America/New_York"), "IND": ("Indianapolis", "America/Indiana/Indianapolis"),
    "TYS": ("Knoxville", "America/New_York"), "MYR": ("Myrtle Beach", "America/New_York"),
    "ORF": ("Norfolk", "America/New_York"), "RIC": ("Richmond", "America/New_York"),
    "CAE": ("Columbia", "America/New_York"), "SAV": ("Savannah", "America/New_York"),
    "CHS": ("Charleston", "America/New_York"), "BDL": ("Hartford", "America/New_York"),
    # United States, Central
    "ORD": ("Chicago", "America/Chicago"), "MDW": ("Chicago", "America/Chicago"),
    "DFW": ("Dallas", "America/Chicago"), "DAL": ("Dallas", "America/Chicago"),
    "IAH": ("Houston", "America/Chicago"), "HOU": ("Houston", "America/Chicago"),
    "AUS": ("Austin", "America/Chicago"), "SAT": ("San Antonio", "America/Chicago"),
    "MSY": ("New Orleans", "America/Chicago"), "STL": ("St. Louis", "America/Chicago"),
    "MSP": ("Minneapolis", "America/Chicago"), "MCI": ("Kansas City", "America/Chicago"),
    "BNA": ("Nashville", "America/Chicago"), "MEM": ("Memphis", "America/Chicago"),
    "OMA": ("Omaha", "America/Chicago"), "OKC": ("Oklahoma City", "America/Chicago"),
    "SGF": ("Springfield", "America/Chicago"), "AMA": ("Amarillo", "America/Chicago"),
    "PNS": ("Pensacola", "America/Chicago"), "MKE": ("Milwaukee", "America/Chicago"),
    # United States, Mountain
    "DEN": ("Denver", "America/Denver"), "ABQ": ("Albuquerque", "America/Denver"),
    "SLC": ("Salt Lake City", "America/Denver"), "BOI": ("Boise", "America/Boise"),
    "PHX": ("Phoenix", "America/Phoenix"), "TUS": ("Tucson", "America/Phoenix"),
    # United States, Pacific and beyond
    "LAX": ("Los Angeles", "America/Los_Angeles"), "SFO": ("San Francisco", "America/Los_Angeles"),
    "SJC": ("San Jose", "America/Los_Angeles"), "OAK": ("Oakland", "America/Los_Angeles"),
    "SAN": ("San Diego", "America/Los_Angeles"), "SMF": ("Sacramento", "America/Los_Angeles"),
    "ONT": ("Ontario, California", "America/Los_Angeles"), "SNA": ("Santa Ana", "America/Los_Angeles"),
    "LAS": ("Las Vegas", "America/Los_Angeles"), "RNO": ("Reno", "America/Los_Angeles"),
    "SEA": ("Seattle", "America/Los_Angeles"), "PDX": ("Portland", "America/Los_Angeles"),
    "GEG": ("Spokane", "America/Los_Angeles"), "ANC": ("Anchorage", "America/Anchorage"),
    "HNL": ("Honolulu", "Pacific/Honolulu"), "ITO": ("Hilo", "Pacific/Honolulu"),
    "OGG": ("Maui", "Pacific/Honolulu"),
    # Canada
    "YYZ": ("Toronto", "America/Toronto"), "YOW": ("Ottawa", "America/Toronto"),
    "YUL": ("Montreal", "America/Toronto"), "YVR": ("Vancouver", "America/Vancouver"),
    "YYC": ("Calgary", "America/Edmonton"), "YEG": ("Edmonton", "America/Edmonton"),
    # Europe
    "LHR": ("London", "Europe/London"), "LGW": ("London", "Europe/London"),
    "MAN": ("Manchester", "Europe/London"), "EDI": ("Edinburgh", "Europe/London"),
    "DUB": ("Dublin", "Europe/Dublin"), "CDG": ("Paris", "Europe/Paris"),
    "NCE": ("Nice", "Europe/Paris"), "AMS": ("Amsterdam", "Europe/Amsterdam"),
    "FRA": ("Frankfurt", "Europe/Berlin"), "MUC": ("Munich", "Europe/Berlin"),
    "ZRH": ("Zurich", "Europe/Zurich"), "GVA": ("Geneva", "Europe/Zurich"),
    "FCO": ("Rome", "Europe/Rome"), "MXP": ("Milan", "Europe/Rome"),
    "MAD": ("Madrid", "Europe/Madrid"), "BCN": ("Barcelona", "Europe/Madrid"),
    "LIS": ("Lisbon", "Europe/Lisbon"), "ATH": ("Athens", "Europe/Athens"),
    "IST": ("Istanbul", "Europe/Istanbul"), "VIE": ("Vienna", "Europe/Vienna"),
    "CPH": ("Copenhagen", "Europe/Copenhagen"), "ARN": ("Stockholm", "Europe/Stockholm"),
    "OSL": ("Oslo", "Europe/Oslo"), "HEL": ("Helsinki", "Europe/Helsinki"),
    "WAW": ("Warsaw", "Europe/Warsaw"), "PRG": ("Prague", "Europe/Prague"),
    "BRU": ("Brussels", "Europe/Brussels"),
    # Middle East, Africa
    "DXB": ("Dubai", "Asia/Dubai"), "AUH": ("Abu Dhabi", "Asia/Dubai"),
    "DOH": ("Doha", "Asia/Qatar"), "BEY": ("Beirut", "Asia/Beirut"),
    "TLV": ("Tel Aviv", "Asia/Jerusalem"), "CAI": ("Cairo", "Africa/Cairo"),
    "CMN": ("Casablanca", "Africa/Casablanca"), "JNB": ("Johannesburg", "Africa/Johannesburg"),
    "LOS": ("Lagos", "Africa/Lagos"), "ADD": ("Addis Ababa", "Africa/Addis_Ababa"),
    # Asia, Pacific
    "DEL": ("Delhi", "Asia/Kolkata"), "BOM": ("Mumbai", "Asia/Kolkata"),
    "HYD": ("Hyderabad", "Asia/Kolkata"), "BLR": ("Bangalore", "Asia/Kolkata"),
    "MAA": ("Chennai", "Asia/Kolkata"), "SIN": ("Singapore", "Asia/Singapore"),
    "HKG": ("Hong Kong", "Asia/Hong_Kong"), "MNL": ("Manila", "Asia/Manila"),
    "BKK": ("Bangkok", "Asia/Bangkok"), "NRT": ("Tokyo", "Asia/Tokyo"),
    "HND": ("Tokyo", "Asia/Tokyo"), "ICN": ("Seoul", "Asia/Seoul"),
    "PEK": ("Beijing", "Asia/Shanghai"), "PVG": ("Shanghai", "Asia/Shanghai"),
    "TPE": ("Taipei", "Asia/Taipei"), "SYD": ("Sydney", "Australia/Sydney"),
    "MEL": ("Melbourne", "Australia/Melbourne"), "BNE": ("Brisbane", "Australia/Brisbane"),
    "AKL": ("Auckland", "Pacific/Auckland"),
    # Latin America, Caribbean
    "MEX": ("Mexico City", "America/Mexico_City"), "CUN": ("Cancún", "America/Cancun"),
    "GRU": ("São Paulo", "America/Sao_Paulo"), "EZE": ("Buenos Aires", "America/Argentina/Buenos_Aires"),
    "BOG": ("Bogotá", "America/Bogota"), "LIM": ("Lima", "America/Lima"),
    "SCL": ("Santiago", "America/Santiago"), "SJU": ("San Juan", "America/Puerto_Rico"),
}


def city_and_zone(code: Optional[str]) -> Optional[tuple[str, str]]:
    """(city, IANA zone) for a departure code, or None when we don't know it."""
    if not code or not isinstance(code, str):
        return None
    return AIRPORTS.get(code.strip().upper())
