"""Number and date formats that the plain cast cannot read, expressed as ordinary regex_replace/cast steps.

Every suggestion built here uses existing step actions, so it previews, applies and undoes like any other step.
"""
import re
from typing import Any

_CURRENCY_SYMBOLS = "$€£¥₹"
_CURRENCY_CODES = "usd eur gbp cad aud nzd jpy cny inr mxn chf brl zar sek nok dkk pln krw sgd hkd".split()
_CODE_ALT = "|".join(_CURRENCY_CODES)
_CODE_AFFIX_RE = re.compile(rf"(?i)^(?:(?:{_CODE_ALT})\s*)|(?:\s*(?:{_CODE_ALT}))$")
_SWISS_RE = re.compile(r"^[-+]?\d{1,3}(?:['’]\d{3})+(?:\.\d+)?$")
_TRAILING_MINUS_RE = re.compile(rf"^[{_CURRENCY_SYMBOLS}]?\s?\d[\d.,]*\s?-$")
_MAGNITUDES = (("thousand", 3), ("million", 6), ("billion", 9), ("bn", 9), ("mm", 6), ("mn", 6), ("k", 3), ("m", 6), ("b", 9))
_MAGNITUDE_RE = re.compile(
    rf"(?i)^(?P<sign>[-+]?)(?P<cur>[{_CURRENCY_SYMBOLS}]?)\s?(?P<int>\d+)(?:\.(?P<dec>\d{{1,3}}))?\s?"
    r"(?P<suffix>thousand|million|billion|bn|mm|mn|k|m|b)$"
)
_EURO_RE = re.compile(r"^[-+]?(?:\d{1,3}(?:\.\d{3})+|\d+),\d+$")
_US_DECIMAL_RE = re.compile(r"^[-+]?(?:\d{1,3}(?:,\d{3})*|\d+)\.\d+$")
_PLAIN_RE = re.compile(rf"^[{_CURRENCY_SYMBOLS}]?\s?[-+]?\(?[{_CURRENCY_SYMBOLS}]?\s?\d[\d,\s]*(?:\.\d+)?\)?$")
_ZEROS = dict(_MAGNITUDES)
# A zero cannot follow a backreference ('\1000' reads as group 1000), so zeros are written after a marker that is removed next.
_MARK = "~"


def _magnitude(text: str):
    # A lowercase 'm' or 'b' is far more often metres or bytes than millions or billions.
    match = _MAGNITUDE_RE.fullmatch(text)
    return None if match and match.group('suffix') in ('m', 'b') else match


def _magnitude_value(match: re.Match) -> float:
    digits = match.group('dec') or ''
    zeros = _ZEROS[match.group('suffix').lower()]
    return float(f"{match.group('sign')}{match.group('int')}.{digits or '0'}") * (10 ** zeros)


def parse_number(value: str) -> float | None:
    """Best-effort reading of one value, used only to describe and size a suggestion."""
    text = value.strip()
    if not text:
        return None
    text = _CODE_AFFIX_RE.sub('', text).strip()
    magnitude = _magnitude(text)
    if magnitude:
        return _magnitude_value(magnitude)
    if _SWISS_RE.fullmatch(text):
        text = re.sub(r"['’]", '', text)
    if _TRAILING_MINUS_RE.fullmatch(text):
        text = '-' + text[:-1].strip()
    cleaned = re.sub(rf"[{_CURRENCY_SYMBOLS}\s]", '', text)
    if cleaned.startswith('(') and cleaned.endswith(')'):
        cleaned = '-' + cleaned[1:-1]
    if _EURO_RE.fullmatch(cleaned):
        cleaned = cleaned.replace('.', '').replace(',', '.')
    else:
        cleaned = cleaned.replace(',', '')
    try:
        return float(cleaned)
    except ValueError:
        return None


def numeric_format_entry(col: str, vals: list[str], plain_ok) -> dict[str, Any] | None:
    """A suggestion for columns whose numbers use a notation the plain cast would turn into blanks.

    `plain_ok(value)` says whether the ordinary cast already reads a value; only the others are considered here.
    """
    if not vals:
        return None
    features: dict[str, list[str]] = {'code': [], 'swiss': [], 'minus': [], 'magnitude': [], 'euro': []}
    unreadable = []
    parsed = 0
    for value in vals:
        text = value.strip()
        if plain_ok(text):
            parsed += 1
            continue
        stripped = _CODE_AFFIX_RE.sub('', text).strip()
        feature = None
        if stripped != text and parse_number(stripped) is not None:
            features['code'].append(value)
            feature = 'code'
        if _magnitude(stripped):
            features['magnitude'].append(value)
            feature = 'magnitude'
        elif _SWISS_RE.fullmatch(stripped):
            features['swiss'].append(value)
            feature = 'swiss'
        elif _TRAILING_MINUS_RE.fullmatch(stripped):
            features['minus'].append(value)
            feature = 'minus'
        elif _EURO_RE.fullmatch(re.sub(rf"[{_CURRENCY_SYMBOLS}\s]", '', stripped)):
            features['euro'].append(value)
            feature = 'euro'
        if feature:
            parsed += 1
        else:
            unreadable.append(value)
    if not any(features.values()) or parsed / len(vals) < 0.75:
        return None
    # Comma-decimal notation conflicts with US decimals ('1.234' is 1234 or 1.234), so refuse mixed columns.
    if features['euro'] and any(_US_DECIMAL_RE.fullmatch(v.strip()) for v in vals):
        return None

    steps: list[dict[str, Any]] = []

    def replace(pattern: str, replacement: str) -> None:
        steps.append({'action': 'regex_replace', 'column': col, 'params': {'pattern': pattern, 'replace': replacement}})

    notes = []
    if features['code']:
        replace(rf"(?i)^(?:(?:{_CODE_ALT})\s*)|(?:\s*(?:{_CODE_ALT}))$", '')
        notes.append('currency codes')
    if features['swiss']:
        replace(r"['’]", '')
        notes.append("apostrophe grouping (1'234.50)")
    if features['minus']:
        replace(rf"^([{_CURRENCY_SYMBOLS}]?\s?\d[\d.,]*)\s?-$", r'-\1')
        notes.append('trailing minus (123-)')
    if features['magnitude']:
        for word, zeros in _MAGNITUDES:
            for decimals in range(0, 4):
                if decimals > zeros:
                    continue
                flag = '' if word in ('m', 'b') else '(?i)'
                body = rf"\.(\d{{{decimals}}})" if decimals else ''
                groups = r'\1\2' if decimals else r'\1'
                pattern = (rf"{flag}^([{_CURRENCY_SYMBOLS}]?\s?[-+]?\d+){body}\s?{word.upper() if flag == '' else word}$")
                replace(pattern, groups + _MARK + '0' * (zeros - decimals))
        replace(re.escape(_MARK), '')
        notes.append('K/M/B magnitudes (1.2M, 50k)')
    if features['euro']:
        replace(r'\.', '')
        replace(',', '.')
        notes.append('comma decimals (1.234,56)')
    values = [parse_number(v) for v in vals]
    numbers = [v for v in values if v is not None]
    whole = all(float(n).is_integer() for n in numbers)
    to = 'int' if whole and not features['euro'] else 'float'
    steps.append({'action': 'cast', 'column': col, 'params': {'to': to}})
    examples = []
    for group in features.values():
        for value in group:
            if value not in examples:
                examples.append(value)
            if len(examples) >= 3:
                break
    text = f"Read {', '.join(notes)} in '{col}' as numbers (e.g. {', '.join(repr(e) for e in examples[:3])}) and convert it to {to}"
    if features['euro']:
        text += "; assumes '.' groups thousands and ',' is the decimal mark, so review a few values first"
    if unreadable:
        text += f". {len(unreadable)} other value(s) (e.g. {', '.join(repr(u) for u in unreadable[:3])}) are not numbers and would become empty"
    return {'text': text, 'steps': steps, 'confidence': 0.75}


# More date layouts than the built-in list. (strptime format, matching pattern, note when the day is missing)
EXTRA_DATE_FORMATS: tuple[tuple[str, re.Pattern, str], ...] = tuple(
    (fmt, re.compile(pattern, re.I), note) for fmt, pattern, note in (
        ("%d.%m.%Y", r"^\d{1,2}\.\d{1,2}\.\d{4}$", ""),
        ("%d.%m.%y", r"^\d{1,2}\.\d{1,2}\.\d{2}$", ""),
        ("%Y.%m.%d", r"^\d{4}\.\d{1,2}\.\d{1,2}$", ""),
        ("%Y%m%d", r"^\d{8}$", ""),
        ("%m/%d/%y", r"^\d{1,2}/\d{1,2}/\d{2}$", ""),
        ("%d/%m/%y", r"^\d{1,2}/\d{1,2}/\d{2}$", ""),
        ("%m-%d-%y", r"^\d{1,2}-\d{1,2}-\d{2}$", ""),
        ("%d-%b-%y", r"^\d{1,2}-[A-Za-z]{3}-\d{2}$", ""),
        ("%d %b %Y", r"^\d{1,2} [A-Za-z]{3} \d{4}$", ""),
        ("%d %B %Y", r"^\d{1,2} [A-Za-z]{4,9} \d{4}$", ""),
        ("%B %d %Y", r"^[A-Za-z]{3,9} \d{1,2} \d{4}$", ""),
        ("%b %Y", r"^[A-Za-z]{3} \d{4}$", "the day is set to the 1st"),
        ("%B %Y", r"^[A-Za-z]{4,9} \d{4}$", "the day is set to the 1st"),
        ("%Y-%m", r"^\d{4}-\d{2}$", "the day is set to the 1st"),
        ("%Y-%m-%d %H:%M", r"^\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}$", ""),
        ("%Y-%m-%d %H:%M:%S.%f", r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+$", ""),
        ("%Y-%m-%dT%H:%M:%S.%f", r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+$", ""),
        ("%Y-%m-%dT%H:%M", r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", ""),
        ("%m/%d/%Y %H:%M", r"^\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}$", ""),
        ("%m/%d/%Y %H:%M:%S", r"^\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2}$", ""),
        ("%m/%d/%Y %I:%M %p", r"^\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2} ?[ap]m$", ""),
        ("%m/%d/%Y %I:%M:%S %p", r"^\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2} ?[ap]m$", ""),
        ("%d/%m/%Y %H:%M", r"^\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}$", ""),
        ("%d.%m.%Y %H:%M", r"^\d{1,2}\.\d{1,2}\.\d{4} \d{1,2}:\d{2}$", ""),
    )
)
