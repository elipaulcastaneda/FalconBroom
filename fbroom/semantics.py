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

_NAMES_FILE = Path(__file__).resolve().parent / "data" / "names.bin"
_MISSING = frozenset(
    "n/a na n.a. n.a nan null none nil unknown unk missing undefined tbd tba tbc blank empty not-available "
    "not-applicable #n/a #na ? ?? - -- --- . .. ...".split()
)
_SSN_RE = re.compile(r"\d{3}-\d{2}-\d{4}")
_PHONE_STRICT_RE = re.compile(r"(?:\+?\d{1,3}[ .-])?(?:\(\d{3}\)\s?|\d{3}[ .-])\d{3}[ .-]\d{4}(?:\s?(?:x|ext\.?)\s?\d{1,6})?")
_IP_RE = re.compile(r"(?:\d{1,3}\.){3}\d{1,3}")
_MAC_RE = re.compile(r"(?i)(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}")
_LATLONG_RE = re.compile(r"[-+]?\d{1,3}\.\d+\s*,\s*[-+]?\d{1,3}\.\d+")
_IBAN_RE = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}")
_ISIN_RE = re.compile(r"[A-Z]{2}[A-Z0-9]{9}\d")
_CURRENCY_CODES = frozenset(
    "USD EUR GBP JPY CNY CAD AUD NZD CHF SEK NOK DKK PLN CZK HUF RON BGN TRY RUB UAH ILS AED SAR QAR KWD EGP ZAR NGN "
    "KES GHS MAD INR PKR BDT LKR NPR THB VND IDR MYR SGD PHP HKD TWD KRW MXN BRL ARS CLP COP PEN UYU BOB CRC DOP "
    "JMD TTD ISK HRK RSD GEL KZT UZS BTC ETH".split()
)
_BPS_RE = re.compile(r"(?i)[-+]?\d+(?:\.\d+)?\s?(?:bps|bp|basis points?)")
_PAREN_NUMBER_RE = re.compile(r"\(\s*[$€£¥₹]?\s*\d[\d,]*(?:\.\d+)?\s*\)")
_MAGNITUDE_RE = re.compile(r"(?i)[$€£¥₹]?\s?[-+]?\d+(?:\.\d+)?\s?(?:k|m|mm|b|bn|mn|million|billion|thousand)")
_BLOOD_PRESSURE_RE = re.compile(r"\d{2,3}\s?/\s?\d{2,3}(?:\s?mm\s?hg)?")
_UNITS = (
    "mg mcg µg ug g gm kg lb lbs oz ml l dl cl mmol/l mg/dl mg/l mmol iu u bpm mmhg cm mm m km in inch inches ft foot "
    "feet yd yds mi mile miles sqft sq ft sqm sq m ft² m² km² acres acre ac ha hectares mph kph km/h m/s kwh kw w watt "
    "watts v volts a amps psi bar pa kpa hz khz mhz ghz mb gb tb kb kib mib gib db s sec secs seconds min mins minutes "
    "h hr hrs hours day days wk weeks mo months yr yrs years cc cu bd ba br bed beds bath baths floors units"
).split()
_QUANTITY_RE = re.compile(
    r"(?i)[-+]?\d[\d,]*(?:\.\d+)?\s?(?:" + "|".join(sorted((re.escape(u) for u in _UNITS), key=len, reverse=True)) + r")\.?"
)
_ICD_RE = re.compile(r"[A-TV-Z]\d{2}(?:\.[0-9A-TV-Z]{1,4})?")
_SCI_RE = re.compile(r"[-+]?\d+(?:\.\d+)?[eE][-+]?\d+")
_HEX_RE = re.compile(r"(?:0[xX])?[0-9a-fA-F]{6,}")
_ID_RE = re.compile(r"(?=[^\s]*\d)(?=[^\s]*[A-Za-z])[A-Za-z0-9]+(?:[-_/.#:][A-Za-z0-9]+)*")
_STREET_SUFFIXES = (
    "st street ave avenue rd road dr drive ln lane blvd boulevard ct court way pl place ter terrace pkwy parkway hwy "
    "highway cir circle trl trail sq square loop run row path pike alley"
).split()
_ADDRESS_RE = re.compile(
    r"(?i)(?:\d+[A-Za-z]?(?:-\d+)?\s+(?:[NSEW]{1,2}\.?\s+)?[A-Za-z0-9 .'’-]{1,40}?\s(?:" + "|".join(_STREET_SUFFIXES)
    + r")\b\.?(?:[ ,#].*)?|p\.?\s?o\.?\s?box\s+\d+.*)"
)
_GRADE_RE = re.compile(r"[A-F][+-]?")
_TITLES = frozenset(
    "mr mrs ms miss mx dr prof professor sir madam madame mme mlle rev fr hon capt col gen sgt lt jr sr ii iii iv md "
    "phd esq dds rn dvm".split()
)
_GENDER_WORDS = frozenset("male female man woman boy girl nonbinary non-binary m f other".split())
_COLOR_WORDS = frozenset(
    "red blue green yellow orange purple pink black white gray grey brown beige tan gold silver navy teal maroon "
    "cyan magenta ivory cream turquoise violet indigo olive lime coral khaki charcoal bronze copper".split()
)
_SIZE_WORDS = frozenset("xs s m l xl xxl xxxl small medium large petite regular tall short".split())
_STATUS_WORDS = frozenset(
    "pending active inactive completed complete incomplete in progress on hold approved rejected denied cancelled "
    "canceled open closed new draft failed passed pass fail permitted planned delayed started not paused scheduled "
    "submitted review reviewed resolved unresolved overdue paid unpaid refunded shipped delivered processing "
    "ready done waiting expired suspended terminated queued running stopped finished ongoing underway of".split()
)
_JOB_WORDS = frozenset(
    "manager engineer director analyst assistant developer nurse teacher clerk supervisor coordinator specialist "
    "technician officer president vice ceo cfo cto coo accountant consultant administrator associate representative "
    "driver operator mechanic electrician plumber carpenter architect foreman laborer inspector estimator "
    "superintendent designer executive intern lead senior junior principal chief head physician surgeon pharmacist "
    "dentist lawyer attorney paralegal programmer scientist researcher professor instructor trainer recruiter "
    "salesperson cashier receptionist secretary janitor custodian guard".split()
)
_DEPARTMENT_WORDS = frozenset(
    "sales marketing finance hr human resources engineering operations it legal support accounting procurement "
    "logistics production quality research development customer service services admin administration facilities "
    "security purchasing maintenance training compliance communications design product management and".split()
)
_STRIP_TITLE_PUNCT = re.compile(r"[.,]+$")

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
    'weather', 'company', 'org', 'ssn', 'ip', 'mac', 'latlong', 'financial_id', 'card', 'bloodpressure', 'quantity',
    'address', 'icd', 'routing', 'barcode', 'epoch', 'id', 'hex', 'currency_code', 'gender', 'title', 'status',
    'color', 'size', 'person', 'name', 'job_title', 'department', 'grade',
})
_PRIORITY = (
    'email', 'url', 'uuid', 'ssn', 'ip', 'mac', 'latlong', 'financial_id', 'card', 'date', 'time', 'temperature',
    'percent', 'money', 'bloodpressure', 'quantity', 'address', 'icd', 'routing', 'barcode', 'zip', 'phone', 'epoch',
    'id', 'hex', 'year', 'decimal', 'integer', 'currency_code', 'binary', 'gender', 'title', 'weather', 'status',
    'color', 'size', 'grade', 'person', 'place', 'company', 'org', 'job_title', 'department', 'name',
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


@lru_cache(maxsize=1)
def _person_names() -> tuple[frozenset, frozenset]:
    """(first names, surnames), loaded once from data/names.bin when it is present."""
    first: set[str] = set()
    last: set[str] = set()
    try:
        lines = zlib.decompress(_NAMES_FILE.read_bytes()).decode("utf-8").split("\n")
    except (OSError, zlib.error, UnicodeDecodeError):
        lines = []
    for line in lines:
        if line.startswith("f"):
            first.add(line[1:])
        elif line.startswith("l"):
            last.add(line[1:])
    return frozenset(first), frozenset(last)


def _luhn(digits: str) -> bool:
    total = 0
    for index, ch in enumerate(reversed(digits)):
        n = int(ch)
        if index % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _aba(digits: str) -> bool:
    d = [int(ch) for ch in digits]
    return len(d) == 9 and (3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])) % 10 == 0


def _gtin(digits: str) -> bool:
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(ch) * (3 if index % 2 == 0 else 1) for index, ch in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def _name_kind(text: str) -> str | None:
    """'person' for a full name, 'name' for a lone first name or surname, None otherwise."""
    if len(text) > 50 or re.search(r"[\d@_/\\]", text):
        return None
    first, last = _person_names()
    if not first:
        return None
    cleaned = text.strip()
    comma = [part.strip() for part in cleaned.split(",")]
    tokens: list[str]
    if len(comma) == 2 and comma[0] and comma[1]:
        tokens = comma[1].split() + comma[0].split()
    else:
        tokens = cleaned.split()
    tokens = [_STRIP_TITLE_PUNCT.sub("", t) for t in tokens]
    titled = bool(tokens) and tokens[0].casefold() in _TITLES
    while tokens and tokens[0].casefold() in _TITLES:
        tokens = tokens[1:]
    while tokens and tokens[-1].casefold() in _TITLES:
        tokens = tokens[:-1]
    keys = [unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().casefold().replace("'", "").replace("’", "") for t in tokens]
    keys = [k for k in keys if k]
    if not keys or any(not re.fullmatch(r"[a-z]+(?:-[a-z]+)*", k) for k in keys):
        return None
    if len(keys) == 1:
        key = keys[0]
        if len(key) < 2:
            return None
        return 'name' if (key in first or key in last) else None
    if len(keys) > 4:
        return None
    pieces = [piece for key in (keys[0], keys[-1]) for piece in key.split("-")]
    middle_ok = all(len(k) == 1 or k in first or k in last for k in keys[1:-1])
    head_ok = keys[0].split("-")[0] in first or len(keys[0]) == 1 or (titled and keys[0] in last)
    tail_ok = keys[-1].split("-")[-1] in last or keys[-1].split("-")[-1] in first
    if head_ok and tail_ok and middle_ok and pieces:
        return 'person'
    if titled and (keys[-1] in last or keys[-1] in first):
        return 'person'
    return None


@lru_cache(maxsize=131072)
def value_kinds(value: str) -> frozenset:
    """Every kind a single value could be (empty for blanks)."""
    text = value.strip()
    if not text:
        return frozenset()
    if text.casefold() in _MISSING:
        return frozenset({'missing'})
    if _EMAIL_RE.fullmatch(text):
        return frozenset({'email'})
    if _URL_RE.match(text):
        return frozenset({'url'})
    if _UUID_RE.fullmatch(text):
        return frozenset({'uuid'})
    if _SSN_RE.fullmatch(text):
        return frozenset({'ssn', 'id'})
    if _IP_RE.fullmatch(text):
        return frozenset({'ip'})
    if _MAC_RE.fullmatch(text):
        return frozenset({'mac'})
    if _PHONE_STRICT_RE.fullmatch(text):
        return frozenset({'phone'})
    if _LATLONG_RE.fullmatch(text):
        return frozenset({'latlong'})
    if any(pattern.fullmatch(text) for pattern in _DATE_RES):
        return frozenset({'date'})
    if _TIME_RE.fullmatch(text):
        return frozenset({'time'})
    if _TEMPERATURE_RE.fullmatch(text):
        return frozenset({'temperature', 'number'})
    if _PERCENT_RE.fullmatch(text) or _BPS_RE.fullmatch(text):
        return frozenset({'percent'})
    if _BLOOD_PRESSURE_RE.fullmatch(text):
        return frozenset({'bloodpressure'})
    if _CURRENCY_RE.search(text) and re.search(r"\d", text):
        return frozenset({'money', 'number'})
    if _PAREN_NUMBER_RE.fullmatch(text):
        return frozenset({'money', 'number'})
    if _MAGNITUDE_RE.fullmatch(text):
        return frozenset({'quantity', 'money', 'number'})
    if _QUANTITY_RE.fullmatch(text):
        return frozenset({'quantity', 'number'})
    kinds: set[str] = set()
    compact = re.sub(r"[ -]", "", text)
    if compact.isdigit() and 13 <= len(compact) <= 19 and _luhn(compact) and re.fullmatch(r"\d{4}(?:[ -]?\d{4}){2,4}[ -]?\d{1,4}", text):
        return frozenset({'card', 'number'})
    if _GROUPED_NUMBER_RE.fullmatch(text):
        return frozenset({'money', 'number'})
    if _SCI_RE.fullmatch(text):
        return frozenset({'decimal', 'number'})
    if _INTEGER_RE.fullmatch(text):
        kinds.update({'integer', 'number'})
        digits = text.lstrip("+-")
        if _YEAR_RE.fullmatch(text):
            kinds.add('year')
        if _ZIP_RES[0].fullmatch(text):
            kinds.add('zip')
        if len(digits) == 9 and _aba(digits):
            kinds.add('routing')
        if len(digits) in (8, 12, 13, 14) and _gtin(digits):
            kinds.add('barcode')
        if re.fullmatch(r"1\d{9}(?:\d{3})?", digits):
            kinds.add('epoch')
        if any(pattern.fullmatch(text) for pattern in _PHONE_RES) and len(digits) >= 7:
            kinds.add('phone')
        return frozenset(kinds)
    if _DECIMAL_RE.fullmatch(text):
        return frozenset({'decimal', 'number'})
    if re.search(r"\d", text):
        if any(pattern.fullmatch(text) for pattern in _PHONE_RES) and len(re.sub(r"\D", "", text)) >= 7:
            kinds.add('phone')
        if any(pattern.fullmatch(text) for pattern in _ZIP_RES):
            kinds.add('zip')
        upper = compact.upper()
        if upper == compact and (_IBAN_RE.fullmatch(upper) and len(upper) <= 34 or _ISIN_RE.fullmatch(upper)):
            kinds.add('financial_id')
        if _ICD_RE.fullmatch(text):
            kinds.add('icd')
        if _ADDRESS_RE.fullmatch(text):
            kinds.add('address')
        if _ID_RE.fullmatch(text):
            kinds.add('id')
        if _HEX_RE.fullmatch(text) and re.search(r"[a-fA-F]", text):
            kinds.add('hex')
    else:
        words = _words(text)
        lowered = text.casefold()
        if text in _CURRENCY_CODES:
            kinds.add('currency_code')
        if lowered in _BINARY_WORDS:
            kinds.add('binary')
        if lowered.rstrip('.') in _TITLES:
            kinds.add('title')
        if lowered in _GENDER_WORDS:
            kinds.add('gender')
        if words and (lowered in _COLOR_WORDS or all(w in _COLOR_WORDS or w in ('light', 'dark', 'bright', 'pale', 'deep') for w in words)):
            kinds.add('color')
        if lowered in _SIZE_WORDS or lowered in ('extra large', 'extra small', 'x-large', 'xx-large'):
            kinds.add('size')
        if _GRADE_RE.fullmatch(text):
            kinds.add('grade')
        if words and len(words) <= 4 and all(w in _STATUS_WORDS for w in words):
            kinds.add('status')
        if lowered in _WEATHER_WORDS or _WEATHER_PHRASES.fullmatch(text) or (words and all(w in _WEATHER_WORDS or w in ('and', 'with', 'partly', 'mostly') for w in words)):
            kinds.add('weather')
        person = _name_kind(text)
        if person:
            kinds.add(person)
        if is_place(text):
            kinds.add('place')
        if (len(words) > 1 and words[-1] in _COMPANY_WORDS) or lowered.rstrip('.') in _KNOWN_ACRONYMS or _LEGAL_SUFFIXES.intersection(words):
            kinds.add('company')
        if len(words) > 1 and (words[0] in _ORG_WORDS or words[-1] in _ORG_WORDS):
            kinds.add('org')
        if words and len(words) <= 5 and _JOB_WORDS.intersection(words) and len(words) == len(text.split()):
            kinds.add('job_title')
        if words and len(words) <= 4 and all(w in _DEPARTMENT_WORDS for w in words):
            kinds.add('department')
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
        if kinds and 'missing' not in kinds:
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
        widened.update({'integer', 'status'})
    if 'status' in main:
        widened.add('binary')
    if 'gender' in main:
        widened.add('binary')
    if main & {'quantity', 'integer', 'decimal', 'money'}:
        widened.update({'quantity', 'number'})
    if 'quantity' in main:
        widened.update({'integer', 'decimal', 'number'})
    if 'id' in main:
        widened.update({'hex', 'uuid'})
    if 'epoch' in main:
        widened.add('date')
    if 'person' in main:
        widened.update({'name', 'title'})
    if 'name' in main:
        widened.update({'person', 'place', 'title'})
    if 'job_title' in main:
        widened.add('department')
    if 'department' in main:
        widened.add('job_title')
    if 'icd' in main:
        widened.add('id')
    if main & {'card', 'routing', 'barcode', 'financial_id', 'ssn'}:
        widened.update({'integer', 'id'})
    return frozenset(widened)


def fits(value: str, widened: frozenset) -> bool:
    kinds = value_kinds(value)
    return 'missing' in kinds or bool(kinds & widened)


def same_meaning(left_main: frozenset, right_main: frozenset) -> bool:
    """Two columns whose main kinds share a semantic kind (two dates, two phones, two places...) are siblings, not swaps."""
    return bool(left_main & right_main & SEMANTIC_KINDS)


_NUMERIC = {'integer', 'decimal', 'number'}
# (keywords, kinds the column name promises). Matching is per word, so 'estate' never reads as 'state'.
_HEADER_RULES: tuple[tuple[frozenset, frozenset], ...] = tuple(
    (frozenset(words.split()), frozenset(kinds)) for words, kinds in (
        ("email emails mail", {'email'}),
        ("url urls website web homepage uri link", {'url'}),
        ("phone phones mobile cell cellphone tel telephone fax whatsapp phonenumber", {'phone'}),
        ("zip zipcode postal postcode postalcode zip4", {'zip'}),
        ("year yr yob", {'year'}),
        ("date dates dob birthday birthdate timestamp datetime created updated modified expires expiry deadline startdate enddate duedate", {'date', 'time', 'epoch'}),
        ("time", {'time', 'date', 'epoch'}),
        ("cost costs price prices amount amt salary wage wages pay revenue budget fee fees balance total income expense "
         "spend charge payment usd eur gbp cad value worth", {'money'} | _NUMERIC),
        ("percent pct percentage ratio share", {'percent'} | _NUMERIC),
        ("temp temperature celsius fahrenheit", {'temperature'} | _NUMERIC),
        ("weather sky forecast", {'weather'}),
        ("city town municipality county state province country region nation location locality borough "
         "neighborhood neighbourhood place birthplace", {'place'}),
        ("address addr street", {'address', 'place'}),
        ("ip ipaddress", {'ip'}),
        ("gender sex", {'gender'}),
        ("salutation honorific prefix suffix", {'title'}),
        ("job position role occupation designation profession", {'job_title'}),
        ("department dept division team", {'department'}),
        ("currency ccy", {'currency_code'}),
        ("iban swift bic isin cusip sedol ticker symbol", {'financial_id', 'currency_code'}),
        ("routing aba", {'routing', 'integer'}),
        ("card cc creditcard", {'card', 'integer'}),
        ("ssn sin", {'ssn', 'integer'}),
        ("icd diagnosis dx cpt procedure", {'icd', 'id', 'integer'}),
        ("bp pressure bloodpressure", {'bloodpressure', 'integer'}),
        ("lat latitude lon lng long longitude coord coordinates", {'latlong'} | _NUMERIC),
        ("barcode upc ean gtin isbn", {'barcode', 'id', 'integer'}),
        ("size area sqft sqm sq square feet foot acre acres length width height depth weight mass volume distance "
         "dose dosage qty quantity speed capacity lot footage ft kg lb mg ml cm mm km", {'quantity', 'size'} | _NUMERIC),
        ("bedroom bedrooms bed beds bath baths bathroom bathrooms story stories floor floors room rooms count age "
         "units", _NUMERIC | {'quantity'}),
        ("rating score gpa grade stars rank", _NUMERIC | {'grade'}),
        ("passed failed active enabled approved verified paid flag boolean bool subscribed consent is has "
         "accepted", {'binary', 'status', 'integer'}),
        ("status stage phase condition", {'status', 'binary'}),
        ("color colour", {'color'}),
        ("contractor builder vendor supplier company organization organisation org employer business firm agency "
         "dealer brand manufacturer client customer", {'company', 'org', 'person'}),
        ("firstname lastname surname fullname givenname familyname forename maiden", {'person', 'name'}),
        ("id ids key guid uuid sku order invoice tracking shipment permit parcel apn serial vin license plate "
         "reference ref ticket case mrn npi", {'id', 'integer', 'uuid', 'hex', 'barcode', 'card', 'routing'}),
    )
)
_NAME_PARTS = frozenset("first last middle given family maiden".split())
_NAME_WORDS = frozenset("name names".split())
# A qualifier as the last word says the column holds a label about the thing, not the thing ('city_id', 'zip_type').
_GENERIC_HEADS = frozenset(
    "id ids key type kind flag category class group source format method reason note notes description desc label "
    "level index center centre bucket tier band".split()
)
_UNIT_TAILS = frozenset("usd eur gbp cad aud sqft sqm ft kg lb lbs mg ml cm mm km mi f c k m hrs days yrs".split())
_NEVER_KEYWORD = frozenset("update updates candidate estate mandate validate".split())
_FALLBACK_CODE = frozenset({'id', 'integer', 'icd', 'currency_code', 'financial_id', 'hex', 'barcode'})
_WEAK_WORDS = frozenset("code codes number numbers num no nbr name names of".split())
_CODE_WORDS = frozenset("code codes number numbers num no nbr".split())


def _header_words(name: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(name))
    return [w for w in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if w]


def header_kinds(name: str) -> frozenset | None:
    """What a column's name alone promises ('city' -> places, 'phone' -> phones); None when it says nothing."""
    words = [w for w in _header_words(name) if w not in _NEVER_KEYWORD]
    if not words:
        return None
    base = list(words)
    while len(base) > 1 and base[-1] in _UNIT_TAILS:
        base.pop()
    head = base[-1]
    if head in _GENERIC_HEADS and len(base) > 1 and head not in ('id', 'ids', 'key'):
        return None
    kinds: set[str] = set()
    if _NAME_PARTS.intersection(words) and _NAME_WORDS.intersection(words):
        kinds.update({'person', 'name'})
    elif head in ('id', 'ids', 'key') and len(base) > 1:
        # 'city_id', 'zip_key': an identifier for the thing, not the thing itself.
        kinds = set(_HEADER_RULES[-1][1])
    else:
        # The last meaningful word is the head noun ('contractor_phone' is a phone, not a contractor).
        for word in reversed(base):
            if word in _WEAK_WORDS:
                continue
            for keywords, promised in _HEADER_RULES:
                if word in keywords or (word.endswith('s') and word[:-1] in keywords):
                    kinds.update(promised)
            if kinds:
                break
    if not kinds and any(w in _CODE_WORDS for w in base):
        kinds = set(_FALLBACK_CODE) | _NUMERIC
    if not kinds:
        return None
    promised = widen(kinds)
    # Single names overlap with places ('Austin'), so a name header never promises places.
    return promised if 'place' in kinds else promised - {'place'}


def header_promise(name: str, values: list) -> frozenset | None:
    """The header's promise, trusted only when the column's own values back it up (>= 40% of the non-missing ones)."""
    promised = header_kinds(name)
    if not promised:
        return None
    seen = [v for v in values if v and 'missing' not in value_kinds(v)]
    if len(seen) < 5:
        return None
    hits = sum(bool(value_kinds(v) & promised) for v in seen)
    return promised if hits / len(seen) >= 0.4 else None


def _unknown(main: frozenset) -> bool:
    return not main or all(kind.startswith('other:') for kind in main)


def _coarse(value: str) -> str:
    kind = primary_kind(value_kinds(value))
    if kind.startswith('other:'):
        return 'other:digit' if not re.search(r"[A-Za-z]", value) else 'other:text'
    return kind


def _dominant_share(values: list) -> float:
    values = [v for v in values if v and 'missing' not in value_kinds(v)]
    if not values:
        return 0.0
    return Counter(_coarse(v) for v in values).most_common(1)[0][1] / len(values)


def _lives_here(value: str, here: Counter, there: Counter) -> bool:
    """A value that recurs in its own column and dominates its count across the pair is native there, not misplaced."""
    key = value.strip().casefold()
    return bool(key) and here[key] >= 5 and here[key] >= 3 * there[key]


def swap_is_safe(
    left: str, right: str, left_values: list, right_values: list, crossed: list, home_rows: int,
    left_meaning: tuple, right_meaning: tuple,
) -> bool:
    """Final checks on a candidate swap; any doubt rejects it (a missed swap costs far less than a false one).

    1. Header: only when the column's values back its name up. Values moved must not already suit the column they sit
       in, and must suit the target's name.
    2. Native values: a value that recurs in its own column (and rarely appears in the other) is not misplaced.
    3. Purity: nearly every moved value must fit the kinds the other column usually holds.
    4. Unknown kinds: columns we can't classify need many more rows and a clearer majority.
    5. Consistency: moved values must be one kind of thing, not scattered noise.
    Blank and null-like values ('N/A', 'unknown', '-') fit every column, so they never count as misplaced.
    """
    rows = [i for i in crossed if i < len(left_values) and i < len(right_values)]
    if not rows:
        return False
    left_main, left_wide = left_meaning
    right_main, right_wide = right_meaning
    left_promise = header_promise(left, left_values)
    right_promise = header_promise(right, right_values)
    left_seen = Counter((v or '').strip().casefold() for v in left_values)
    right_seen = Counter((v or '').strip().casefold() for v in right_values)

    violations = 0
    for i in rows:
        a, b = left_values[i] or '', right_values[i] or ''
        bad = False
        for value, own_promise, other_promise, here, there in (
            (a, left_promise, right_promise, left_seen, right_seen),
            (b, right_promise, left_promise, right_seen, left_seen),
        ):
            if own_promise and fits(value, own_promise):
                # 'Austin' or 'Orlando' fit a name header, but they are just as clearly places.
                ambiguous = 'place' in value_kinds(value) and 'place' not in own_promise
                if not ambiguous:
                    bad = True
            if other_promise and 'place' not in other_promise and not fits(value, other_promise):
                bad = True
            if _lives_here(value, here, there):
                bad = True
        violations += bad
    if violations > 0.3 * len(rows):
        return False

    left_unknown = _unknown(left_main) and not left_promise
    right_unknown = _unknown(right_main) and not right_promise
    impure = 0
    for i in rows:
        a, b = left_values[i] or '', right_values[i] or ''
        if (not _unknown(right_main) and not fits(a, right_wide)) or (not _unknown(left_main) and not fits(b, left_wide)):
            impure += 1
    if impure > 0.3 * len(rows):
        return False

    if left_unknown or right_unknown:
        if len(rows) < 10 or home_rows < 4 * len(rows):
            return False

    return (
        _dominant_share([left_values[i] for i in rows]) >= 0.5
        and _dominant_share([right_values[i] for i in rows]) >= 0.5
    )
