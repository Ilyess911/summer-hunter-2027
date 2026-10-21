"""Deterministic location → country resolution (no external geocoding)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from summer_hunter.utils.text import normalize_for_match

# ISO2 -> (aliases, major cities). Aliases/cities are matched on accent-free lowercase text.
COUNTRIES: dict[str, tuple[list[str], list[str]]] = {
    "FR": (["france", "french republic"], ["paris", "lyon", "marseille", "toulouse", "nice", "nantes", "bordeaux", "lille", "rennes", "grenoble", "sophia antipolis", "montpellier", "strasbourg", "la defense", "rueil-malmaison", "issy-les-moulineaux", "boulogne-billancourt", "velizy", "marignane", "blagnac"]),
    "IL": (["israel"], ["tel aviv", "tel-aviv", "jerusalem", "haifa", "herzliya", "petah tikva", "ra'anana", "raanana", "netanya", "beersheba", "yokneam", "rehovot"]),
    "GB": (["united kingdom", "uk", "u.k.", "great britain", "england", "scotland", "wales", "northern ireland"], ["london", "manchester", "edinburgh", "cambridge", "oxford", "bristol", "birmingham", "glasgow", "leeds", "reading", "belfast", "cardiff"]),
    "IE": (["ireland", "republic of ireland"], ["dublin", "cork", "galway", "limerick"]),
    "CH": (["switzerland", "suisse", "schweiz"], ["zurich", "geneva", "geneve", "basel", "lausanne", "bern", "zug", "schaan", "lugano"]),
    "DE": (["germany", "deutschland"], ["berlin", "munich", "munchen", "hamburg", "frankfurt", "cologne", "koln", "stuttgart", "dusseldorf", "erlangen", "walldorf", "nuremberg", "nurnberg", "karlsruhe", "dresden", "leipzig", "hanover", "aachen", "bonn"]),
    "NL": (["netherlands", "the netherlands", "holland"], ["amsterdam", "rotterdam", "eindhoven", "the hague", "utrecht", "delft"]),
    "SE": (["sweden", "sverige"], ["stockholm", "gothenburg", "goteborg", "malmo", "lund", "vasteras"]),
    "DK": (["denmark", "danmark"], ["copenhagen", "aarhus", "odense"]),
    "NO": (["norway", "norge"], ["oslo", "bergen", "trondheim", "stavanger"]),
    "FI": (["finland", "suomi"], ["helsinki", "espoo", "tampere", "oulu"]),
    "BE": (["belgium", "belgique", "belgie"], ["brussels", "bruxelles", "antwerp", "ghent", "leuven", "liege"]),
    "LU": (["luxembourg"], ["luxembourg city"]),
    "ES": (["spain", "espana"], ["madrid", "barcelona", "valencia", "seville", "malaga", "bilbao"]),
    "IT": (["italy", "italia"], ["milan", "milano", "rome", "roma", "turin", "torino", "bologna", "florence", "naples"]),
    "PT": (["portugal"], ["lisbon", "lisboa", "porto"]),
    "AT": (["austria", "osterreich"], ["vienna", "wien", "graz", "linz", "salzburg"]),
    "PL": (["poland", "polska"], ["warsaw", "krakow", "wroclaw", "gdansk", "poznan"]),
    "CZ": (["czech republic", "czechia"], ["prague", "brno"]),
    "HU": (["hungary"], ["budapest"]),
    "RO": (["romania"], ["bucharest", "cluj-napoca", "cluj"]),
    "GR": (["greece"], ["athens", "thessaloniki"]),
    "EE": (["estonia"], ["tallinn"]),
    "LT": (["lithuania"], ["vilnius"]),
    "LV": (["latvia"], ["riga"]),
    "BG": (["bulgaria"], ["sofia"]),
    "HR": (["croatia"], ["zagreb"]),
    "SK": (["slovakia"], ["bratislava"]),
    "SI": (["slovenia"], ["ljubljana"]),
    "MT": (["malta"], ["valletta"]),
    "CY": (["cyprus"], ["nicosia", "limassol"]),
    "IS": (["iceland"], ["reykjavik"]),
    "MC": (["monaco"], []),
    "TR": (["turkey", "turkiye"], ["istanbul", "ankara", "izmir"]),
    "UA": (["ukraine"], ["kyiv", "kiev", "lviv"]),
    "RU": (["russia", "russian federation"], ["moscow", "saint petersburg"]),
    "US": (["united states", "united states of america", "usa", "u.s.", "u.s.a."], ["new york", "nyc", "san francisco", "seattle", "boston", "chicago", "austin", "los angeles", "palo alto", "mountain view", "menlo park", "sunnyvale", "san jose", "santa clara", "redmond", "atlanta", "denver", "dallas", "houston", "miami", "washington, d.c.", "washington dc", "pittsburgh", "philadelphia", "san diego", "raleigh", "salt lake city", "minneapolis", "detroit", "phoenix", "portland", "nashville", "boulder", "cupertino", "irvine", "kirkland", "bellevue", "cambridge, ma", "pleasanton", "foster city", "armonk", "durham", "charlotte", "columbus", "st. louis", "kalamazoo", "mahwah"]),
    "CA": (["canada"], ["toronto", "vancouver", "montreal", "ottawa", "calgary", "waterloo", "edmonton", "quebec city", "mississauga"]),
    "MX": (["mexico"], ["mexico city", "guadalajara", "monterrey"]),
    "BR": (["brazil", "brasil"], ["sao paulo", "rio de janeiro", "belo horizonte", "campinas"]),
    "AR": (["argentina"], ["buenos aires"]),
    "CL": (["chile"], ["santiago"]),
    "CO": (["colombia"], ["bogota", "medellin"]),
    "PE": (["peru"], ["lima"]),
    "SG": (["singapore"], []),
    "HK": (["hong kong", "hong kong sar"], ["kowloon"]),
    "TW": (["taiwan"], ["taipei", "hsinchu", "taichung", "kaohsiung"]),
    "JP": (["japan"], ["tokyo", "osaka", "kyoto", "yokohama", "nagoya", "fukuoka"]),
    "KR": (["south korea", "korea", "republic of korea"], ["seoul", "busan", "pangyo", "seongnam", "incheon"]),
    "CN": (["china", "people's republic of china", "prc", "mainland china"], ["beijing", "shanghai", "shenzhen", "guangzhou", "hangzhou", "chengdu", "suzhou", "nanjing", "wuhan", "xi'an"]),
    "AU": (["australia"], ["sydney", "melbourne", "brisbane", "perth", "adelaide", "canberra"]),
    "NZ": (["new zealand"], ["auckland", "wellington", "christchurch"]),
    "VN": (["vietnam", "viet nam"], ["ho chi minh city", "hanoi", "da nang"]),
    "ID": (["indonesia"], ["jakarta", "bandung", "surabaya", "bali"]),
    "TH": (["thailand"], ["bangkok", "chiang mai"]),
    "MY": (["malaysia"], ["kuala lumpur", "penang", "cyberjaya"]),
    "PH": (["philippines"], ["manila", "makati", "taguig", "cebu"]),
    "IN": (["india"], ["bangalore", "bengaluru", "mumbai", "new delhi", "delhi", "hyderabad", "pune", "chennai", "gurgaon", "gurugram", "noida", "kolkata"]),
    "PK": (["pakistan"], ["karachi", "lahore", "islamabad"]),
    "BD": (["bangladesh"], ["dhaka"]),
    "LK": (["sri lanka"], ["colombo"]),
    "AE": (["united arab emirates", "uae", "u.a.e."], ["dubai", "abu dhabi", "sharjah"]),
    "QA": (["qatar"], ["doha"]),
    "SA": (["saudi arabia", "ksa"], ["riyadh", "jeddah", "dammam", "neom"]),
    "KW": (["kuwait"], ["kuwait city"]),
    "BH": (["bahrain"], ["manama"]),
    "OM": (["oman"], ["muscat"]),
    "JO": (["jordan"], ["amman"]),
    "LB": (["lebanon"], ["beirut"]),
    "EG": (["egypt"], ["cairo", "alexandria"]),
    "MA": (["morocco", "maroc"], ["casablanca", "rabat", "marrakech", "tangier"]),
    "TN": (["tunisia", "tunisie"], ["tunis"]),
    "DZ": (["algeria", "algerie"], ["algiers"]),
    "ZA": (["south africa"], ["johannesburg", "cape town", "pretoria", "durban"]),
    "NG": (["nigeria"], ["lagos", "abuja"]),
    "KE": (["kenya"], ["nairobi"]),
    "GH": (["ghana"], ["accra"]),
    "SN": (["senegal"], ["dakar"]),
    "CI": (["cote d'ivoire", "ivory coast"], ["abidjan"]),
    "RW": (["rwanda"], ["kigali"]),
    "ET": (["ethiopia"], ["addis ababa"]),
    "TZ": (["tanzania"], ["dar es salaam"]),
    "UG": (["uganda"], ["kampala"]),
    "CM": (["cameroon", "cameroun"], ["douala", "yaounde"]),
    "AO": (["angola"], ["luanda"]),
}

SOUTHERN_HEMISPHERE = {"AU", "NZ", "ZA", "AR", "CL", "BR", "PE", "ID"}
EU_EEA_CH = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT",
             "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "IS", "LI", "NO", "CH"}

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington state": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
US_STATE_CODES = set(US_STATES.values()) | {"DC"}
CA_PROVINCES = {"ontario": "ON", "british columbia": "BC", "quebec": "QC", "alberta": "AB", "manitoba": "MB", "nova scotia": "NS"}
CA_PROVINCE_CODES = set(CA_PROVINCES.values())

COUNTRY_NAMES = {code: aliases[0].title() for code, (aliases, _) in COUNTRIES.items()}
COUNTRY_NAMES.update({"US": "United States", "GB": "United Kingdom", "AE": "United Arab Emirates", "HK": "Hong Kong", "KR": "South Korea"})

_REMOTE = re.compile(r"\b(remote|anywhere|work from home|wfh|distributed|teletravail|télétravail)\b", re.I)
_SPLIT = re.compile(r"\s*(?:;|\||/|\bor\b|\bou\b|·|\n| & | \+ )\s*", re.I)


def _compile(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])")


_ALIAS_PATTERNS: list[tuple[re.Pattern[str], str, str]] = []
for _code, (_aliases, _cities) in COUNTRIES.items():
    for _alias in _aliases:
        _ALIAS_PATTERNS.append((_compile(_alias), _code, "country"))
    for _city in _cities:
        _ALIAS_PATTERNS.append((_compile(_city), _code, "city"))
for _state in US_STATES:
    _ALIAS_PATTERNS.append((_compile(_state), "US", "state"))
for _prov in CA_PROVINCES:
    _ALIAS_PATTERNS.append((_compile(_prov), "CA", "province"))
# Longest terms first so "new south wales" style overlaps resolve to the specific match.
_ALIAS_PATTERNS.sort(key=lambda item: -len(item[0].pattern))

# Ambiguous city names that also exist elsewhere: only trusted with a country/state context.
_AMBIGUOUS = {"cambridge", "georgia", "jordan", "washington", "santiago", "perth", "london", "nice", "reading",
              "sofia", "charlotte", "phoenix", "bali", "durham", "columbus", "lima", "victoria", "bath", "lund"}


@dataclass
class LocationResolution:
    countries: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    remote: bool = False
    evidence: list[str] = field(default_factory=list)

    @property
    def primary_country(self) -> str | None:
        return self.countries[0] if self.countries else None


def _resolve_part(part: str) -> tuple[str | None, str | None, str | None]:
    raw = part.strip()
    norm = normalize_for_match(raw)
    if not norm:
        return None, None, None
    # US "City, ST" / Canada "City, ON" suffix.
    suffix = re.search(r",\s*([A-Z]{2})\b(?:\s*,?\s*((?i:usa|us|united states|canada)))?\s*$", raw)
    if suffix:
        code = suffix.group(1)
        country_word = (suffix.group(2) or "").lower()
        if code in CA_PROVINCE_CODES and (country_word == "canada" or code not in US_STATE_CODES):
            return "CA", raw.split(",")[0].strip(), f"province code {code}"
        if code in US_STATE_CODES and country_word != "canada":
            return "US", raw.split(",")[0].strip(), f"US state code {code}"
    for pattern, code, kind in _ALIAS_PATTERNS:
        match = pattern.search(norm)
        if match:
            term = match.group(0)
            if kind == "city":
                return code, term.title(), f"city '{term}'"
            return code, None, f"{kind} '{term}'"
    return None, None, None


def resolve_location(location_text: str | None, country_hint: str | None = None) -> LocationResolution:
    result = LocationResolution()
    text = location_text or ""
    result.remote = bool(_REMOTE.search(text))
    if country_hint:
        hint = country_hint.strip().upper()
        if len(hint) == 2 and hint.isalpha():
            result.countries.append("GB" if hint == "UK" else hint)
            result.evidence.append(f"source country field '{country_hint}'")
        else:
            code, _, ev = _resolve_part(country_hint)
            if code:
                result.countries.append(code)
                result.evidence.append(f"source country field: {ev}")
    for part in _SPLIT.split(text):
        if not part or not part.strip():
            continue
        code, city, ev = _resolve_part(part)
        if code and code not in result.countries:
            result.countries.append(code)
            result.evidence.append(ev or "")
        if city and city not in result.cities:
            result.cities.append(city)
    return result


def country_name(code: str | None) -> str:
    if not code:
        return "Unknown"
    return COUNTRY_NAMES.get(code, code)


def find_countries_in_text(text: str) -> list[str]:
    """Countries mentioned anywhere in free text (used for LinkedIn posts)."""
    found: list[str] = []
    norm = normalize_for_match(text)
    for pattern, code, kind in _ALIAS_PATTERNS:
        if kind == "state":
            continue
        match = pattern.search(norm)
        if match and match.group(0) not in _AMBIGUOUS and code not in found:
            found.append(code)
    return found
