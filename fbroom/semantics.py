"""What common data values look like (dates, money, phones, places, ...), used to veto false column-swap suggestions.

A value can have several kinds ('12345' is a zip, an integer and a number). Detection is deliberately generous so that
messy but correct columns are never reported as swapped; the data tables stay small (places live in data/places.bin).
"""
from __future__ import annotations

import re
import unicodedata
import zlib
from collections import Counter
from functools import lru_cache
from pathlib import Path

_PLACES_FILE = Path(__file__).resolve().parent / "data" / "places.bin"

_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE_RES = (
    re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?"),
    re.compile(r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}"),
    re.compile(rf"(?i){_MONTHS}[ -]\d{{1,2}}(?:(?:st|nd|rd|th)?,?[ -]\d{{2,4}})?"),
    re.compile(rf"(?i)\d{{1,2}}(?:st|nd|rd|th)?[ -]{_MONTHS}(?:,?[ -]\d{{2,4}})?"),
    re.compile(rf"(?i){_MONTHS},?[ -]\d{{4}}"),
    re.compile(r"\d{4}[-/]\d{1,2}"),
    re.compile(r"(?i)q[1-4][ -]?\d{2,4}"),
)
_TIME_RE = re.compile(r"(?i)\d{1,2}:\d{2}(?::\d{2})?\s?(?:am|pm)?")
_CURRENCY_RE = re.compile(r"(?i)(?:[$€£¥₹]|\b(?:usd|eur|gbp|cad|aud|jpy|inr|mxn|chf|cny|brl)\b)")
_GROUPED_NUMBER_RE = re.compile(r"[-+(]?\d{1,3}(?:[.,' ]\d{3})+(?:[.,]\d+)?\)?")
_PERCENT_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?\s?%")
_INTEGER_RE = re.compile(r"[-+]?\d+")
_DECIMAL_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?")
_YEAR_RE = re.compile(r"(?:1[5-9]|20)\d{2}")
_TEMPERATURE_RE = re.compile(r"(?i)[-+−]?\d+(?:\.\d+)?\s?(?:°\s?[fck]?|º\s?[fck]?|deg(?:rees?)?\.?\s?[fck]?|[fc])")
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"(?i)(?:https?://|www\.)\S+")
_UUID_RE = re.compile(r"(?i)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_PHONE_RES = (
    re.compile(r"(?:\+?\d{1,3}[ .-]?)?(?:\(\d{2,4}\)|\d{2,4})[ .-]?\d{3,4}[ .-]?\d{3,4}(?:\s?(?:x|ext\.?)\s?\d{1,6})?"),
    re.compile(r"\d{3}[ .-]\d{4}"),
    re.compile(r"\+\d{7,15}"),
)
_ZIP_RES = (
    re.compile(r"\d{5}(?:-\d{4})?"),
    re.compile(r"(?i)[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z][ -]?\d[ABCEGHJ-NPRSTV-Z]\d"),
    re.compile(r"(?i)[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}"),
)

_BINARY_WORDS = frozenset("yes no y n true false t f on off x ok active inactive enabled disabled".split())
_WEATHER_WORDS = frozenset(
    "sunny clear cloudy overcast rain rainy raining drizzle shower showers storm stormy thunderstorm thunderstorms "
    "snow snowy snowing sleet hail fog foggy mist misty haze hazy windy breezy humid hot cold cool warm freezing "
    "frost frosty blizzard tornado hurricane flurries fair dry wet".split()
)
_WEATHER_PHRASES = re.compile(r"(?i)(?:partly|mostly|mainly|light|heavy|scattered|isolated|patchy)\s+(?:sunny|cloudy|clear|rain|snow|showers|storms?|fog)")
_COMPANY_WORDS = frozenset(
    "inc incorporated llc llp lp pllc ltd limited corp corporation co company plc gmbh ag sa sas srl spa bv nv ab oy "
    "pty pte kk homes construction builders builder building contractors contracting developers development "
    "group holdings holding partners associates enterprises industries services solutions technologies technology "
    "systems ventures properties realty capital trust bros brothers sons labs studio studios consulting logistics "
    "manufacturing supply supplies".split()
)
_ORG_WORDS = frozenset(
    "university college school institute foundation association society council department ministry hospital church "
    "bank club union agency authority commission committee bureau board federation alliance league academy "
    "museum library cooperative coop charity fund network".split()
)
_KNOWN_ACRONYMS = frozenset(
    "nasa fbi cia nsa irs epa fda cdc nih un who nato eu imf oecd unesco unicef opec wto usda dod doj hud fema "
    "ibm hp ge gm bmw ups fedex dhl usps amazon aws sap".split()
)
_LEGAL_SUFFIXES = frozenset("inc llc llp pllc ltd corp gmbh plc".split())
_REGION_WORDS = ("city", "county", "township", "town", "village", "borough", "municipality", "parish", "district")
_PLACE_PREFIXES = ("city of ", "town of ", "county of ", "village of ", "township of ")
_PLACE_ABBREVIATIONS = {"st": "saint", "ste": "sainte", "mt": "mount", "ft": "fort", "pt": "port"}

# North American subdivisions kept in code: names are matched case-insensitively, 2-letter codes only in UPPER case.
_SUBDIVISION_NAMES = frozenset(
    name.replace("-", " ") for name in (
        "alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii idaho illinois "
        "indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota mississippi missouri "
        "montana nebraska nevada new-hampshire new-jersey new-mexico new-york north-carolina north-dakota ohio oklahoma "
        "oregon pennsylvania rhode-island south-carolina south-dakota tennessee texas utah vermont virginia washington "
        "west-virginia wisconsin wyoming district-of-columbia puerto-rico guam alberta british-columbia manitoba "
        "new-brunswick newfoundland-and-labrador nova-scotia ontario prince-edward-island quebec saskatchewan yukon "
        "northwest-territories nunavut aguascalientes baja-california baja-california-sur campeche chiapas chihuahua "
        "coahuila colima durango guanajuato guerrero hidalgo jalisco mexico-city michoacan morelos nayarit nuevo-leon "
        "oaxaca puebla queretaro quintana-roo san-luis-potosi sinaloa sonora tabasco tamaulipas tlaxcala veracruz "
        "yucatan zacatecas new-south-wales victoria queensland tasmania england scotland wales northern-ireland"
    ).split()
)
_SUBDIVISION_CODES = frozenset(
    ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA "
     "RI SC SD TN TX UT VT VA WA WV WI WY DC PR GU VI AB BC MB NB NL NS NT NU ON PE QC SK YT").split()
)

# Kinds that say something about meaning; shared generic kinds (integer, number, ...) never veto a pair on their own.
SEMANTIC_KINDS = frozenset({
    'date', 'time', 'phone', 'zip', 'email', 'url', 'uuid', 'temperature', 'percent', 'money', 'place', 'binary',
    'weather', 'company', 'org',
})
_PRIORITY = (
    'email', 'url', 'uuid', 'date', 'time', 'temperature', 'percent', 'money', 'zip', 'phone', 'year', 'decimal',
    'integer', 'binary', 'weather', 'place', 'company', 'org',
)


def _shape(value: str) -> str:
    out: list[str] = []
    for ch in re.sub(r"[A-Za-z]", "a", value.strip()):
        token = "9" if ch.isdigit() else ch
        if not (out and out[-1] == token and token in "9a"):
            out.append(token)
    return "".join(out)


def normalize_place(value: str) -> str:
    """Accent-free lowercase words ('Saint-Étienne' / 'St. Etienne' -> 'saint etienne')."""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold().replace("&", " and ")
    text = re.sub(r"[.'’`]", "", text)
    words = re.sub(r"[^a-z0-9]+", " ", text).split()
    if words and words[0] in _PLACE_ABBREVIATIONS:
        words[0] = _PLACE_ABBREVIATIONS[words[0]]
    return " ".join(words)


@lru_cache(maxsize=1)
def _places() -> tuple[frozenset, frozenset]:
    """(normalized place names, exact-case codes), loaded once from data/places.bin when it is present."""
    names: set[str] = set()
    codes: set[str] = set(_SUBDIVISION_CODES)
    try:
        lines = zlib.decompress(_PLACES_FILE.read_bytes()).decode("utf-8").split("\n")
    except (OSError, zlib.error, UnicodeDecodeError):
        lines = []
    for line in lines:
        if line.startswith("#"):
            codes.add(line[1:])
        elif line:
            names.add(line)
    names.update(_SUBDIVISION_NAMES)
    return frozenset(names), frozenset(codes)


def is_place(value: str) -> bool:
    names, codes = _places()
    text = value.strip()
    if not text or len(text) > 60 or any(ch.isdigit() for ch in text):
        return False
    if text in codes:
        return True
    head = text.split(",")[0]
    key = normalize_place(head)
    if not key:
        return False
    if key in names:
        return True
    for prefix in _PLACE_PREFIXES:
        if key.startswith(prefix) and key[len(prefix):] in names:
            return True
    words = key.split()
    return len(words) > 1 and words[-1] in _REGION_WORDS and " ".join(words[:-1]) in names


def _words(value: str) -> list[str]:
    return re.findall(r"[a-z]+", value.casefold())


def value_kinds(value: str) -> frozenset:
    """Every kind a single value could be (empty for blanks)."""
    text = value.strip()
    if not text:
        return frozenset()
    if _EMAIL_RE.fullmatch(text):
        return frozenset({'email'})
    if _URL_RE.match(text):
        return frozenset({'url'})
    if _UUID_RE.fullmatch(text):
        return frozenset({'uuid'})
    if any(pattern.fullmatch(text) for pattern in _DATE_RES):
        return frozenset({'date'})
    if _TIME_RE.fullmatch(text):
        return frozenset({'time'})
    if _TEMPERATURE_RE.fullmatch(text):
        return frozenset({'temperature', 'number'})
    if _PERCENT_RE.fullmatch(text):
        return frozenset({'percent'})
    if _CURRENCY_RE.search(text) and re.search(r"\d", text):
        return frozenset({'money', 'number'})
    kinds: set[str] = set()
    if _GROUPED_NUMBER_RE.fullmatch(text):
        return frozenset({'money', 'number'})
    if _INTEGER_RE.fullmatch(text):
        kinds.update({'integer', 'number'})
        if _YEAR_RE.fullmatch(text):
            kinds.add('year')
        if _ZIP_RES[0].fullmatch(text):
            kinds.add('zip')
        if any(pattern.fullmatch(text) for pattern in _PHONE_RES) and len(re.sub(r"\D", "", text)) >= 7:
            kinds.add('phone')
        return frozenset(kinds)
    if _DECIMAL_RE.fullmatch(text):
        return frozenset({'decimal', 'number'})
    if re.search(r"\d", text):
        if any(pattern.fullmatch(text) for pattern in _PHONE_RES) and len(re.sub(r"\D", "", text)) >= 7:
            kinds.add('phone')
        if any(pattern.fullmatch(text) for pattern in _ZIP_RES):
            kinds.add('zip')
    else:
        words = _words(text)
        lowered = text.casefold()
        if lowered in _BINARY_WORDS:
            kinds.add('binary')
        if lowered in _WEATHER_WORDS or _WEATHER_PHRASES.fullmatch(text) or (words and all(w in _WEATHER_WORDS or w in ('and', 'with', 'partly', 'mostly') for w in words)):
            kinds.add('weather')
        if is_place(text):
            kinds.add('place')
        if (len(words) > 1 and words[-1] in _COMPANY_WORDS) or lowered.rstrip('.') in _KNOWN_ACRONYMS or _LEGAL_SUFFIXES.intersection(words):
            kinds.add('company')
        if len(words) > 1 and (words[0] in _ORG_WORDS or words[-1] in _ORG_WORDS):
            kinds.add('org')
    kinds.add('other:' + _shape(text))
    return frozenset(kinds)


def primary_kind(kinds: frozenset) -> str:
    for kind in _PRIORITY:
        if kind in kinds:
            return kind
    return min(kinds)


def column_kinds(raw_values: list) -> tuple[frozenset, frozenset]:
    """(main kinds covering 75% of the values, those widened by what each kind legitimately sits beside)."""
    counts: Counter = Counter()
    total = 0
    for value in raw_values:
        if not value:
            continue
        kinds = value_kinds(value)
        if kinds:
            total += 1
            counts[primary_kind(kinds)] += 1
    main: set = set()
    covered = 0
    for kind, count in counts.most_common():
        main.add(kind)
        covered += count
        if covered / max(total, 1) >= 0.75:
            break
    return frozenset(main), widen(main)


def widen(main) -> frozenset:
    """What each kind legitimately sits beside (a date column also holds bare years, money holds plain numbers, ...)."""
    main = set(main)
    widened = set(main)
    if 'date' in main:
        widened.add('year')
    if 'year' in main:
        widened.update({'date', 'integer', 'number', 'zip'})
    if main & {'money', 'decimal', 'integer', 'percent', 'zip', 'phone', 'temperature'}:
        widened.add('number')
    if 'money' in main:
        widened.update({'integer', 'decimal'})
    if 'decimal' in main:
        widened.add('integer')
    if 'zip' in main:
        widened.update({'integer', 'phone'})
    if 'phone' in main:
        widened.update({'integer', 'zip'})
    if 'temperature' in main:
        widened.update({'integer', 'decimal'})
    if 'company' in main:
        widened.add('org')
    if 'org' in main:
        widened.add('company')
    if 'binary' in main:
        widened.update({'integer'})
    return frozenset(widened)


def fits(value: str, widened: frozenset) -> bool:
    return bool(value_kinds(value) & widened)


def same_meaning(left_main: frozenset, right_main: frozenset) -> bool:
    """Two columns whose main kinds share a semantic kind (two dates, two phones, two places...) are siblings, not swaps."""
    return bool(left_main & right_main & SEMANTIC_KINDS)


_HEADER_KINDS = (
    (re.compile(r"(?i)e-?mail"), {'email'}),
    (re.compile(r"(?i)\b(?:url|website|web_?site|link|homepage)\b"), {'url'}),
    (re.compile(r"(?i)phone|mobile|\bfax\b|\btel\b|telephone|\bcell\b"), {'phone'}),
    (re.compile(r"(?i)\bzip|postal|post_?code"), {'zip'}),
    (re.compile(r"(?i)\byear\b|\byr\b"), {'year'}),
    (re.compile(r"(?i)(?<!up)(?<!candi)date|timestamp|\bdob\b|birth ?(?:date|day)|\b(?:created|updated|modified)\b"), {'date', 'time'}),
    (re.compile(r"(?i)\btime\b"), {'time'}),
    (re.compile(r"(?i)cost|price|amount|salary|revenue|budget|\bfee\b|\busd\b|\bwage\b|\bpay\b|\bbalance\b"), {'money'}),
    (re.compile(r"(?i)percent|\bpct\b"), {'percent'}),
    (re.compile(r"(?i)\btemp(?:erature)?\b|\bcelsius\b|\bfahrenheit\b"), {'temperature'}),
    (re.compile(r"(?i)\bweather\b"), {'weather'}),
    (re.compile(r"(?i)\b(?:city|town|municipality|county|state|province|country|region|nation)\b"), {'place'}),
)


def _header_words(name: str) -> str:
    return re.sub(r"[_\-\s]+", " ", re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(name))).lower()


def header_kinds(name: str) -> frozenset | None:
    """What a column's name promises ('city' -> places, 'phone' -> phones); None when the name says nothing."""
    words = _header_words(name)
    for pattern, kinds in _HEADER_KINDS:
        if pattern.search(words):
            return widen(kinds)
    return None


def _unknown(main: frozenset) -> bool:
    return not main or all(kind.startswith('other:') for kind in main)


def _coarse(value: str) -> str:
    kind = primary_kind(value_kinds(value))
    if kind.startswith('other:'):
        return 'other:digit' if not re.search(r"[A-Za-z]", value) else 'other:text'
    return kind


def _dominant_share(values: list) -> float:
    if not values:
        return 0.0
    return Counter(_coarse(v) for v in values).most_common(1)[0][1] / len(values)


def swap_is_safe(
    left: str, right: str, left_values: list, right_values: list, crossed: list, home_rows: int,
    left_meaning: tuple, right_meaning: tuple,
) -> bool:
    """Final checks on a candidate swap; any doubt rejects it (a missed swap costs far less than a false one).

    1. Header: values moved must not already suit the column they sit in, and must suit the target's header.
    2. Purity: nearly every moved value must fit the kinds the other column usually holds.
    3. Unknown kinds: columns we can't classify need many more rows and a clearer majority.
    4. Consistency: moved values must be one kind of thing, not scattered noise.
    """
    rows = [i for i in crossed if i < len(left_values) and i < len(right_values)]
    if not rows:
        return False
    left_main, left_wide = left_meaning
    right_main, right_wide = right_meaning
    left_header, right_header = header_kinds(left), header_kinds(right)

    violations = 0
    for i in rows:
        a, b = left_values[i] or '', right_values[i] or ''
        bad = False
        for value, own, other in ((a, left_header, right_header), (b, right_header, left_header)):
            if own and fits(value, own):
                bad = True
            if other and other != widen({'place'}) and not fits(value, other):
                bad = True
        violations += bad
    if violations > 0.3 * len(rows):
        return False

    left_unknown, right_unknown = _unknown(left_main), _unknown(right_main)
    impure = 0
    for i in rows:
        a, b = left_values[i] or '', right_values[i] or ''
        if (not right_unknown and not fits(a, right_wide)) or (not left_unknown and not fits(b, left_wide)):
            impure += 1
    if impure > 0.3 * len(rows):
        return False

    if left_unknown or right_unknown:
        if len(rows) < 10 or home_rows < 4 * len(rows):
            return False

    return (
        _dominant_share([left_values[i] for i in rows if left_values[i]]) >= 0.5
        and _dominant_share([right_values[i] for i in rows if right_values[i]]) >= 0.5
    )
