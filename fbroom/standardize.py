"""Suggestions that bring one column's mixed layouts of the same kind of value to a single layout the user picks.

Each target layout is offered as its own alternative, so choosing a suggestion is choosing the layout. They are built
from ordinary steps; mixed dates use a `cast` whose `fmt` is "source|source=>target" (see `engine._multi_dates`).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Callable

from .formats import _CODE_AFFIX_RE

DATE_TARGETS = (
    ('%Y-%m-%d', 'YYYY-MM-DD'),
    ('%m/%d/%Y', 'MM/DD/YYYY'),
    ('%d/%m/%Y', 'DD/MM/YYYY'),
    ('%b %d, %Y', 'Mon DD, YYYY'),
)

PHONE_TARGETS = (
    (r'(\1) \2-\3', '(555) 123-4567'),
    (r'\1-\2-\3', '555-123-4567'),
    (r'\1.\2.\3', '555.123.4567'),
    (r'+1\1\2\3', '+15551234567'),
)

_NANP = re.compile(r'1?([2-9]\d{2})(\d{3})(\d{4})')


from functools import lru_cache as _memo

try:
    # strptime keeps only 5 compiled formats by default; this module cycles through dozens
    import _strptime as _strptime_module
    if getattr(_strptime_module, '_CACHE_MAX_SIZE', 0) < 256:
        _strptime_module._CACHE_MAX_SIZE = 256
except Exception:
    pass


@_memo(maxsize=65536)
def _parses(value: str, fmt: str, pattern: re.Pattern) -> bool:
    if not pattern.fullmatch(value):
        return False
    try:
        datetime.strptime(value, fmt)
        return True
    except ValueError:
        return False


def mixed_date_entries(col: str, vals: list[str], layouts, make_entry: Callable[..., dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per target layout when a date column mixes two or more day-level layouts."""
    usable = [(fmt, pattern) for fmt, pattern in layouts if '%d' in fmt and '%H' not in fmt and '%I' not in fmt and '%M' not in fmt]
    if len(vals) < 2:
        return []
    matches = [[fmt for fmt, pattern in usable if _parses(v, fmt, pattern)] for v in vals]
    unique: dict[str, int] = {}
    for found in matches:
        if len(found) == 1:
            unique[found[0]] = unique.get(found[0], 0) + 1
    if len(unique) < 2:
        return []
    sources = [fmt for fmt, _ in sorted(unique.items(), key=lambda item: -item[1])]
    covered = [v for v, found in zip(vals, matches) if any(f in sources for f in found)]
    if len(covered) / len(vals) < 0.9:
        return []
    skipped = len(vals) - len(covered)
    shown = []
    for fmt in sources:
        example = next(v for v, found in zip(vals, matches) if found == [fmt])
        shown.append(repr(example))
    entries = []
    for target, label in DATE_TARGETS:
        text = f"'{col}' mixes {len(sources)} date layouts (e.g. {', '.join(shown[:3])}); standardize every value to {label}"
        if skipped:
            text += f" ({skipped} value(s) that fit none of these layouts will become empty)"
        step = {'action': 'cast', 'column': col, 'params': {'to': 'datetime', 'fmt': '|'.join(sources) + '=>' + target}}
        entries.append(make_entry(text, None, 0.8 if target == '%Y-%m-%d' else 0.7, [step]))
    return entries


def mixed_phone_entries(col: str, vals: list[str], make_entry: Callable[..., dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per phone layout when North American numbers are written in two or more styles."""
    if len({re.sub(r'\d', '9', v) for v in vals}) < 2:
        return []
    if any(re.search(r'[A-Za-z#*]', v) for v in vals):
        return []
    if not all(_NANP.fullmatch(re.sub(r'\D', '', v)) for v in vals):
        return []
    entries = []
    for replacement, label in PHONE_TARGETS:
        steps = [
            {'action': 'regex_replace', 'column': col, 'params': {'pattern': r'\D', 'replace': ''}},
            {'action': 'regex_replace', 'column': col, 'params': {'pattern': r'^1?([2-9]\d{2})(\d{3})(\d{4})$', 'replace': replacement}},
        ]
        text = f"'{col}' writes phone numbers in several styles (e.g. {', '.join(repr(v) for v in vals[:3])}); standardize every number to {label}"
        entries.append(make_entry(text, None, 0.7, steps))
    return entries


_BOOL_BASE = (('Yes', 'No', 'Yes/No', {'yes', 'no', 'y', 'n'}), ('Y', 'N', 'Y/N', set()), ('1', '0', '1/0', {'1', '0'}))
_BOOL_FAMILIES = (
    ('Active', 'Inactive', {'active', 'inactive'}),
    ('On', 'Off', {'on', 'off'}),
    ('Enabled', 'Disabled', {'enabled', 'disabled'}),
    ('Present', 'Absent', {'present', 'absent'}),
    ('Pass', 'Fail', {'pass', 'fail', 'passed', 'failed'}),
    ('Approved', 'Rejected', {'approved', 'rejected'}),
    ('Paid', 'Unpaid', {'paid', 'unpaid'}),
    ('Accepted', 'Declined', {'accepted', 'declined'}),
    ('Verified', 'Unverified', {'verified', 'unverified'}),
)
_TRUE_FALSE_WORDS = {'true', 'false', 't', 'f'}


def boolean_choice_entries(col: str, mapping: dict[str, str], make_entry: Callable[..., dict[str, Any]]) -> list[dict[str, Any]]:
    """Alternatives to the true/false mapping, offered only when the column mixes spellings of yes/no style answers."""
    by_polarity: dict[str, set[str]] = {'true': set(), 'false': set()}
    for raw, polarity in mapping.items():
        by_polarity[polarity].add(raw.strip().lower())
    spellings = by_polarity['true'] | by_polarity['false']

    def family(word: str) -> str:
        for yes, _, words in _BOOL_FAMILIES:
            if word in words:
                return yes
        if word in _TRUE_FALSE_WORDS:
            return 'truefalse'
        return 'yesno'

    if max(len(by_polarity['true']), len(by_polarity['false'])) < 2 and len({family(w) for w in spellings}) < 2:
        return []
    targets = [(yes, no, label) for yes, no, label, _ in _BOOL_BASE]
    for yes, no, words in _BOOL_FAMILIES:
        if spellings & words:
            targets.append((yes, no, f'{yes}/{no}'))
    entries = []
    for yes, no, label in targets:
        chosen = {raw: (yes if polarity == 'true' else no) for raw, polarity in mapping.items()}
        entries.append(make_entry(f"'{col}' mixes spellings of yes/no style answers; standardize every value to {label}", None, 0.7,
                                  [{'action': 'map', 'column': col, 'params': {'mapping': chosen}}]))
    return entries


# ---------------------------------------------------------------- date-times and time zones

_DATE_PARTS = ('%Y-%m-%d', '%Y/%m/%d', '%m/%d/%Y', '%d/%m/%Y', '%d.%m.%Y', '%b %d, %Y', '%d %b %Y', '%B %d, %Y')
_TIME_PARTS = ('%H:%M:%S.%f', '%H:%M:%S', '%H:%M', '%I:%M:%S %p', '%I:%M %p')
DATETIME_LAYOUTS = (
    list(_DATE_PARTS)
    + [f'{d} {t}' for d in _DATE_PARTS for t in _TIME_PARTS]
    + [f'%Y-%m-%dT{t}' for t in _TIME_PARTS]
)
_ZONE_RE = re.compile(
    r'^(?P<body>.*\d:\d{2}(?::\d{2}(?:\.\d+)?)?(?:\s*[AaPp][Mm])?)\s*(?P<tz>Z|UTC|GMT|[ECMP][SD]T|[+-]\d{2}(?::?\d{2})?)$')
_ZONE_MINUTES = {'Z': 0, 'UTC': 0, 'GMT': 0, 'EST': -300, 'EDT': -240, 'CST': -360, 'CDT': -300, 'MST': -420, 'MDT': -360, 'PST': -480, 'PDT': -420}


def _split_zone(text: str):
    """(text without its zone, offset in minutes or None, whether a zone was present)."""
    match = _ZONE_RE.match(text.strip())
    if not match:
        return text.strip(), None, False
    tz = match.group('tz')
    if tz in _ZONE_MINUTES:
        return match.group('body'), _ZONE_MINUTES[tz], True
    digits = tz[1:].replace(':', '')
    minutes = int(digits[:2]) * 60 + (int(digits[2:]) if len(digits) > 2 else 0)
    return match.group('body'), -minutes if tz[0] == '-' else minutes, True


_LAYOUT_CACHE: dict[str, tuple] = {}


def _layouts_for(body: str) -> list[str]:
    hit = _LAYOUT_CACHE.get(body)
    if hit is None:
        found = []
        for layout in DATETIME_LAYOUTS:
            try:
                datetime.strptime(body, layout)
                found.append(layout)
            except ValueError:
                continue
        if len(_LAYOUT_CACHE) >= 200_000:
            _LAYOUT_CACHE.clear()
        hit = _LAYOUT_CACHE[body] = tuple(found)
    return list(hit)


def convert_dates(values, spec: str):
    """Rewrite dates or date-times: spec is 'layout|layout=>target' with an optional '@utc' or '@local' on the target."""
    sources, target = spec.split('=>', 1)
    mode = None
    if '@' in target:
        target, mode = target.rsplit('@', 1)
    layouts = [s for s in sources.split('|') if s]
    out = []
    for value in values:
        text = '' if value is None else str(value).strip()
        offset = None
        if mode:
            text, offset, _ = _split_zone(text)
        result = None
        for layout in layouts:
            try:
                parsed = datetime.strptime(text, layout)
            except ValueError:
                continue
            if mode == 'utc':
                parsed -= timedelta(minutes=offset or 0)
            result = parsed.strftime(target)
            break
        out.append(result)
    return out


def mixed_datetime_entries(col: str, vals: list[str], make_entry: Callable[..., dict[str, Any]]) -> list[dict[str, Any]]:
    """Mixed date-time layouts or time zones: one entry per target layout (and per zone treatment)."""
    if len(vals) < 2:
        return []
    zones = [_split_zone(v) for v in vals]
    matches = [_layouts_for(body) for body, _, _ in zones]
    timed = any('%H' in f or '%I' in f for found in matches for f in found[:1]) or any(z for _, _, z in zones)
    has_zone = any(z for _, _, z in zones)
    if not timed:
        return []
    unique: dict[str, int] = {}
    for found in matches:
        if len(found) == 1:
            unique[found[0]] = unique.get(found[0], 0) + 1
    sources = [layout for layout, _ in sorted(unique.items(), key=lambda item: -item[1])]
    covered = [i for i, found in enumerate(matches) if any(f in sources for f in found)]
    if not sources or len(covered) / len(vals) < 0.9:
        return []
    offsets = {off for _, off, z in zones if z}
    zone_mixed = has_zone and (len(offsets) >= 2 or not all(z for _, _, z in zones))
    if len(unique) < 2 and not zone_mixed:
        return []
    shown = [repr(next(v for v, found in zip(vals, matches) if found == [layout])) for layout in sources[:3]]
    untimed = sum(1 for i in covered if not any('%H' in f or '%I' in f for f in matches[i]))
    skipped = len(vals) - len(covered)
    reasons = []
    if len(unique) >= 2:
        reasons.append(f"{len(unique)} date-time layouts")
    if zone_mixed:
        reasons.append('different time zones' if len(offsets) >= 2 else 'values with and without a time zone')
    base = f"'{col}' mixes {' and '.join(reasons)} (e.g. {', '.join(shown)})"
    notes = []
    if untimed:
        notes.append(f"{untimed} value(s) without a time get 00:00:00")
    if has_zone and not all(z for _, _, z in zones):
        notes.append('values without a zone are treated as UTC')
    if skipped:
        notes.append(f"{skipped} value(s) that fit none of these layouts become empty")
    tail = f" ({'; '.join(notes)})" if notes else ''
    joined = '|'.join(sources)
    if has_zone:
        targets = (
            ('%Y-%m-%d %H:%M:%S@utc', 'YYYY-MM-DD HH:MM:SS converted to UTC'),
            ('%Y-%m-%dT%H:%M:%SZ@utc', 'YYYY-MM-DDTHH:MM:SSZ converted to UTC'),
            ('%m/%d/%Y %I:%M %p@utc', 'MM/DD/YYYY hh:MM AM/PM converted to UTC'),
            ('%Y-%m-%d %H:%M:%S@local', 'YYYY-MM-DD HH:MM:SS keeping each clock time as written and dropping the zone'),
            ('%Y-%m-%d@utc', 'the UTC date only (YYYY-MM-DD)'),
        )
    else:
        targets = (
            ('%Y-%m-%d %H:%M:%S', 'YYYY-MM-DD HH:MM:SS'),
            ('%Y-%m-%dT%H:%M:%S', 'YYYY-MM-DDTHH:MM:SS'),
            ('%m/%d/%Y %I:%M %p', 'MM/DD/YYYY hh:MM AM/PM'),
            ('%d/%m/%Y %H:%M', 'DD/MM/YYYY HH:MM'),
            ('%Y-%m-%d', 'the date only (YYYY-MM-DD)'),
        )
    return [make_entry(f"{base}; standardize every value to {label}{tail}", None, 0.8 if i == 0 else 0.65,
                       [{'action': 'cast', 'column': col, 'params': {'to': 'datetime', 'fmt': f'{joined}=>{target}'}}])
            for i, (target, label) in enumerate(targets)]


# ---------------------------------------------------------------- numbers

_SYMBOLS = '$€£¥₹'
_CODE_SYMBOL = {'USD': '$', 'EUR': '€', 'GBP': '£', 'JPY': '¥', 'INR': '₹'}
_PREFIXED = re.compile(r'^([A-Za-z]{1,2})\$')


@_memo(maxsize=65536)
def _split_number(text: str):
    """(currency marker or None, is negative, digits with separators) for one value, or None."""
    t = text.strip()
    marker = None
    prefixed = _PREFIXED.match(t)
    if prefixed:
        marker = prefixed.group(1).upper() + '$'
        t = t[prefixed.end():]
    code = _CODE_AFFIX_RE.search(t)
    if code:
        name = code.group(0).strip().upper()
        marker = marker or _CODE_SYMBOL.get(name, name)
        t = _CODE_AFFIX_RE.sub('', t).strip()
    for ch in t:
        if ch in _SYMBOLS:
            marker = marker or ch
    t = re.sub(rf"[{_SYMBOLS}\s\u00a0\u202f'’]", '', t)
    negative = False
    if t.startswith('(') and t.endswith(')'):
        negative, t = True, t[1:-1]
    if t.endswith('-'):
        negative, t = True, t[:-1]
    if t[:1] in ('+', '-'):
        negative = negative or t[0] == '-'
        t = t[1:]
    if not re.fullmatch(r'\d[\d.,]*', t):
        return None
    return marker, negative, t


def _classify(body: str) -> str:
    """'us' (1,234.5), 'eu' (1.234,5), 'either' (1,234 or 1.234) or 'plain'."""
    dot, comma = body.count('.'), body.count(',')
    if dot and comma:
        return 'us' if body.rfind('.') > body.rfind(',') else 'eu'
    if comma:
        head, _, tail = body.rpartition(',')
        if comma > 1:
            return 'us'
        return 'either' if len(tail) == 3 and 1 <= len(head) <= 3 and not head.startswith('0') else 'eu'
    if dot:
        head, _, tail = body.rpartition('.')
        if dot > 1:
            return 'eu'
        return 'either' if len(tail) == 3 and 1 <= len(head) <= 3 and not head.startswith('0') else 'us'
    return 'plain'


def parse_mixed_number(text: str, ambiguous: str = 'us') -> float | None:
    """One value in either notation; `ambiguous` says how to read '1,234' and '1.234'."""
    split = _split_number(text)
    if not split:
        return None
    _, negative, body = split
    style = _classify(body)
    if style == 'either':
        style = ambiguous
    try:
        if style == 'eu':
            number = float(body.replace('.', '').replace(',', '.'))
        else:
            number = float(body.replace(',', ''))
    except ValueError:
        return None
    return -number if negative else number


def mixed_number_entries(col: str, vals: list[str], make_entry: Callable[..., dict[str, Any]]) -> list[dict[str, Any]]:
    """Columns that mix 1,234.5 with 1.234,5, or several currency symbols, become plain numbers."""
    infos = [_split_number(v) for v in vals]
    ok = [i for i in infos if i]
    if len(ok) < 2 or len(ok) / len(vals) < 0.9:
        return []
    classes = [_classify(i[2]) for i in ok]
    us, eu, either = classes.count('us'), classes.count('eu'), classes.count('either')
    markers = sorted({i[0] for i in ok if i[0]})
    notation_mixed = bool(us and eu)
    if not notation_mixed and len(markers) < 2:
        return []
    default = 'us' if us >= eu else 'eu'
    reasons = []
    if notation_mixed:
        reasons.append('both 1,234.50 and 1.234,50 notations')
    if len(markers) >= 2:
        reasons.append(f"several currency markers ({', '.join(markers)})")
    styles = [default] + (['eu' if default == 'us' else 'us'] if either else [])
    entries = []
    for style in styles:
        numbers = [parse_mixed_number(v, style) for v in vals]
        usable = [x for x in numbers if x is not None]
        to = 'int' if usable and all(float(x).is_integer() for x in usable) else 'float'
        note = ''
        if either:
            note = ("; values like 1,234 are read as thousands separators" if style == 'us'
                    else "; values like 1,234 or 1.234 are read as decimal marks")
        lost = len(vals) - len(usable)
        if lost:
            note += f"; {lost} value(s) that are not numbers become empty"
        base = f"'{col}' mixes {' and '.join(reasons)}; read every value as a plain number (symbols removed, no currency conversion){note}"
        entries.append(make_entry(base, None, 0.8 if style == default else 0.6,
                                  [{'action': 'cast', 'column': col, 'params': {'to': to, 'fmt': f'num:{style}'}}]))
        entries.append(make_entry(base + ', shown with 2 decimal places (kept as text so trailing zeros stay)', None, 0.6 if style == default else 0.5,
                                  [{'action': 'cast', 'column': col, 'params': {'to': 'str', 'fmt': f'num:{style}:2'}}]))
    return entries


def mixed_decimal_entries(col: str, vals: list[str], make_entry: Callable[..., dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per decimal-place count when a numeric column writes mostly one precision but not all."""
    counts: list[int] = []
    for v in vals:
        split = _split_number(v)
        if not split or _classify(split[2]) in ('eu', 'either'):
            return []
        body = split[2].replace(',', '')
        counts.append(len(body.split('.')[1]) if '.' in body else 0)
    shares: dict[int, int] = {}
    for c in counts:
        shares[c] = shares.get(c, 0) + 1
    dominant, top = max(shares.items(), key=lambda item: item[1])
    if len(shares) < 2 or top / len(counts) < 0.6 or max(shares) > 6:
        return []
    entries = []
    for places in sorted({0, 1, 2, 3, dominant}):
        rounded = sum(1 for c in counts if c > places)
        text = f"'{col}' shows different numbers of decimal places ({', '.join(str(p) for p in sorted(shares))}); show every value with exactly {places} (e.g. 3 becomes {format(3, f'.{places}f')})"
        text += ', the most common choice in this column' if places == dominant else ''
        if rounded:
            text += f"; {rounded} value(s) with more decimals are rounded"
        entries.append(make_entry(text + '; kept as text so trailing zeros stay', None, 0.55 if places == dominant else 0.45,
                                  [{'action': 'cast', 'column': col, 'params': {'to': 'str', 'fmt': f'num:us:{places}'}}]))
    return entries


# ---------------------------------------------------------------- units

_UNIT_ROWS = (
    ('mass', 'mg', 0.001, 'mg milligram milligrams'), ('mass', 'g', 1, 'g gram grams gm'),
    ('mass', 'kg', 1000, 'kg kgs kilo kilos kilogram kilograms'), ('mass', 'lb', 453.59237, 'lb lbs pound pounds'),
    ('mass', 'oz', 28.349523125, 'oz ounce ounces'),
    ('length', 'mm', 0.001, 'mm millimeter millimeters'), ('length', 'cm', 0.01, 'cm centimeter centimeters'),
    ('length', 'm', 1, 'm meter meters metre metres'), ('length', 'km', 1000, 'km kilometer kilometers kilometre kilometres'),
    ('length', 'in', 0.0254, 'in inch inches'), ('length', 'ft', 0.3048, 'ft foot feet'),
    ('length', 'yd', 0.9144, 'yd yds yard yards'), ('length', 'mi', 1609.344, 'mi mile miles'),
    ('volume', 'ml', 0.001, 'ml milliliter milliliters millilitre millilitres'),
    ('volume', 'l', 1, 'l liter liters litre litres'), ('volume', 'gal', 3.785411784, 'gal gals gallon gallons'),
    ('volume', 'qt', 0.946352946, 'qt quart quarts'), ('volume', 'pt', 0.473176473, 'pt pint pints'),
    ('volume', 'fl oz', 0.0295735295625, 'floz'),
    ('area', 'sqm', 1, 'sqm m2 m² squaremeter squaremeters sqmeters'),
    ('area', 'sqft', 0.09290304, 'sqft ft2 ft² sqfeet squarefeet sqfoot'),
    ('area', 'acre', 4046.8564224, 'acre acres ac'), ('area', 'ha', 10000, 'ha hectare hectares'),
    ('area', 'sqkm', 1000000, 'sqkm km2 km²'), ('area', 'sqmi', 2589988.110336, 'sqmi mi2 mi²'),
    ('temperature', 'c', 1, 'c celsius centigrade'), ('temperature', 'f', 1, 'f fahrenheit'),
    ('time', 's', 1, 's sec secs second seconds'), ('time', 'min', 60, 'min mins minute minutes'),
    ('time', 'h', 3600, 'h hr hrs hour hours'), ('time', 'day', 86400, 'd day days'), ('time', 'wk', 604800, 'wk wks week weeks'),
    ('speed', 'mph', 0.44704, 'mph'), ('speed', 'km/h', 1 / 3.6, 'kph kmh km/h kmph'), ('speed', 'm/s', 1, 'm/s mps'),
)
_UNITS: dict[str, tuple[str, str, float]] = {}
for _family, _canon, _factor, _aliases in _UNIT_ROWS:
    _UNITS[re.sub(r'[\s.°]', '', _canon)] = (_family, _canon, _factor)
    for _alias in _aliases.split():
        _UNITS[_alias] = (_family, _canon, _factor)
_UNIT_VALUE = re.compile(r'^(?P<num>[-+]?\d[\d,]*(?:\.\d+)?)\s*(?P<unit>[A-Za-z°²/. ]+?)\s*$')


def _read_unit(text: str):
    match = _UNIT_VALUE.match(text.strip())
    if not match:
        return None
    key = re.sub(r'[\s.°]', '', match.group('unit').lower()).replace('²', '2')
    row = _UNITS.get(key)
    if not row:
        return None
    return float(match.group('num').replace(',', '')), row[1], row[0], row[2], key


def convert_unit(value: float, source: str, target: str) -> float | None:
    rows = {canon: (family, factor) for family, canon, factor, _ in _UNIT_ROWS}
    if source not in rows or target not in rows or rows[source][0] != rows[target][0]:
        return None
    if rows[source][0] == 'temperature':
        celsius = value if source == 'c' else (value - 32) * 5 / 9
        return celsius if target == 'c' else celsius * 9 / 5 + 32
    return value * rows[source][1] / rows[target][1]


def mixed_unit_entries(col: str, vals: list[str], make_entry: Callable[..., dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per unit already present when a column mixes units of one kind (5 kg, 5kg, 11 lb)."""
    reads = [_read_unit(v) for v in vals]
    ok = [r for r in reads if r]
    if len(ok) < 2 or len(ok) / len(vals) < 0.9:
        return []
    families = {r[2] for r in ok}
    if len(families) != 1:
        return []
    family = next(iter(families))
    canon_counts: dict[str, int] = {}
    for r in ok:
        canon_counts[r[1]] = canon_counts.get(r[1], 0) + 1
    spellings = {r[4] for r in ok}
    if len(canon_counts) < 2 and len(spellings) < 2:
        return []
    examples = []
    for unit in canon_counts:
        examples.append(repr(next(v for v, r in zip(vals, reads) if r and r[1] == unit)))
    lost = len(vals) - len(ok)
    entries = []
    for unit, count in sorted(canon_counts.items(), key=lambda item: -item[1]):
        text = f"'{col}' mixes {family} units ({', '.join(examples[:4])}); convert every value to {unit} as a plain number"
        text += f" (the unit label is dropped, so mention {unit} in the column name)"
        if lost:
            text += f"; {lost} value(s) without a recognised {family} unit become empty"
        entries.append(make_entry(text, None, 0.75, [{'action': 'cast', 'column': col, 'params': {'to': 'float', 'fmt': f'unit:{unit}'}}]))
    return entries


def convert_values(values, fmt: str, to_type: str):
    """Values rewritten for a `cast` whose fmt starts with 'num:' or 'unit:'; unreadable values become None."""
    kind, _, rest = fmt.partition(':')
    places = None
    if kind == 'num':
        style, _, dec = rest.partition(':')
        places = int(dec) if dec else (2 if to_type in ('str', 'string') else None)

        def read(text):
            return parse_mixed_number(text, style or 'us')
    else:
        target, _, _ = rest.partition(':')

        def read(text):
            parsed = _read_unit(text)
            if not parsed:
                return None
            converted = convert_unit(parsed[0], parsed[1], target)
            return None if converted is None else round(converted, 6)

    out = []
    for value in values:
        text = '' if value is None else str(value).strip()
        number = read(text) if text else None
        if number is None:
            out.append(None)
        elif to_type in ('str', 'string'):
            from decimal import Decimal, ROUND_HALF_UP
            digits = 2 if places is None else places
            out.append(f"{Decimal(repr(number)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP):f}")
        elif to_type in ('int', 'integer'):
            out.append(int(round(number)))
        else:
            out.append(float(number))
    return out


# ---------------------------------------------------------------- phones outside North America

_INTL_COUNTRIES = (('44', 'United Kingdom'), ('49', 'Germany'), ('33', 'France'), ('61', 'Australia'), ('31', 'Netherlands'),
                   ('353', 'Ireland'), ('64', 'New Zealand'), ('81', 'Japan'), ('27', 'South Africa'))


def intl_phone_entries(col: str, vals: list[str], make_entry: Callable[..., dict[str, Any]]) -> list[dict[str, Any]]:
    """International numbers written with different punctuation or with and without a country code."""
    if any(re.search(r'[A-Za-z#*]', v) for v in vals):
        return []
    cleaned = []
    for v in vals:
        t = re.sub(r'\(0\)', '', v)
        t = re.sub(r'[^\d+]', '', t)
        cleaned.append('+' + t[2:] if t.startswith('00') else t)
    if not all(re.fullmatch(r'\+?\d{7,15}', t) for t in cleaned):
        return []
    plus = [t for t in cleaned if t.startswith('+')]
    national = [t for t in cleaned if t.startswith('0')]
    if len({re.sub(r'\d', '9', v) for v in vals}) < 2 and not (plus and national):
        return []
    if not plus and not national:
        return []

    def step(pattern: str, replace: str) -> dict[str, Any]:
        return {'action': 'regex_replace', 'column': col, 'params': {'pattern': pattern, 'replace': replace}}

    base = []
    if any('(0)' in v for v in vals):
        base.append(step(r'\(0\)', ''))
    base += [step(r'[^\d+]', ''), step(r'^00', '+')]
    other = len(cleaned) - len(plus) - len(national)
    examples = ', '.join(repr(v) for v in vals[:3])
    head = f"'{col}' writes phone numbers in several styles (e.g. {examples})"
    entries = [make_entry(f"{head}; standardize every number to international format with a leading + and no spaces (e.g. +442079460958)"
                          + (f"; {len(national)} number(s) starting with 0 have no country code and are left as digits" if national else ''),
                          None, 0.65, base)]
    if national:
        for code, name in _INTL_COUNTRIES:
            entries.append(make_entry(f"{head}; standardize to international format, treating numbers that start with 0 as {name} (+{code})",
                                      None, 0.55, base + [step(r'^0(\d+)$', f'+{code}' + r'\1')]))
    entries.append(make_entry(f"{head}; standardize every number to digits only, without a + or separators", None, 0.5, base + [step(r'\+', '')]))
    return entries


# ---------------------------------------------------------------- postal codes

def _postal_choice(col, vals, make_entry, label, strict, split, joined):
    shapes = {re.sub(r'[A-Za-z]', lambda m: 'a' if m.group().islower() else 'A', re.sub(r'\d', '9', v)) for v in vals}
    if len(shapes) < 2:
        return []
    upper = {'action': 'normalize', 'column': col, 'params': {'case': 'upper'}}
    strip = {'action': 'regex_replace', 'column': col, 'params': {'pattern': r'[\s-]', 'replace': ''}}
    entries = []
    for text, steps in (
        (f"with a space ({label[0]})", [upper, strip, {'action': 'regex_replace', 'column': col, 'params': {'pattern': split, 'replace': joined}}]),
        (f"without a space ({label[1]})", [upper, strip]),
    ):
        entries.append(make_entry(f"'{col}' writes {label[2]} postal codes in different styles (e.g. {', '.join(repr(v) for v in vals[:3])}); standardize every code to uppercase {text}", None, 0.7, steps))
    return entries


_CA_POSTAL = re.compile(r'[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d')
_UK_POSTAL = re.compile(r'[A-Za-z]{1,2}\d[A-Za-z\d]?\s?\d[A-Za-z]{2}')


def mixed_zip_entries(col: str, vals: list[str], make_entry: Callable[..., dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if vals and all(_CA_POSTAL.fullmatch(v) for v in vals):
        entries += _postal_choice(col, vals, make_entry, ('K1A 0B1', 'K1A0B1', 'Canadian'), None, r'^([A-Z]\d[A-Z])(\d[A-Z]\d)$', r'\1 \2')
    elif vals and all(_UK_POSTAL.fullmatch(v) for v in vals):
        entries += _postal_choice(col, vals, make_entry, ('SW1A 1AA', 'SW1A1AA', 'UK'), None, r'^([A-Z]{1,2}\d[A-Z\d]?)(\d[A-Z]{2})$', r'\1 \2')
    nine = [v for v in vals if re.fullmatch(r'\d{9}', v)]
    if nine:
        entries.append(make_entry(
            f"Format {len(nine)} 9-digit ZIP values in '{col}' as ZIP+4 with a hyphen (e.g. {nine[0][:5]}-{nine[0][5:]})", None, 0.7,
            [{'action': 'regex_replace', 'column': col, 'params': {'pattern': r'^(\d{5})(\d{4})$', 'replace': r'\1-\2'}}]))
        entries.append(make_entry(
            f"Reduce ZIP+4 values in '{col}', with or without the hyphen, to 5-digit ZIP codes (e.g. {nine[0][:5]})", None, 0.65,
            [{'action': 'regex_replace', 'column': col, 'params': {'pattern': r'^(\d{5})-?\d{4}$', 'replace': r'\1'}}]))
    return entries


# ---------------------------------------------------------------- states and countries

def mixed_region_entries(col: str, vals: list[str], raw_values: list[str], make_entry: Callable[..., dict[str, Any]]) -> list[dict[str, Any]]:
    """State, province or country columns that mix abbreviations, full names and aliases."""
    from .regions import lookup, _key
    name = col.lower()
    if re.search(r'country|nation', name):
        kinds = ['country']
    elif re.search(r'state|province|(^|_)prov(_|$)|(^|_)st$', name):
        kinds = ['state']
    else:
        kinds = ['state', 'country']
    fits = [k for k in kinds if sum(1 for v in vals if lookup(k, v)) / len(vals) >= 0.9]
    if len(fits) != 1:
        return []
    kind = fits[0]
    forms: dict[str, str] = {}
    for v in vals:
        row = lookup(kind, v)
        if not row:
            continue
        key = _key(v)
        form = 'abbr' if key == _key(row[0]) else 'iso3' if row[2] and key == _key(row[2]) else 'name' if key == _key(row[1]) else 'alias'
        forms.setdefault(form, v)
    if len(forms) < 2:
        return []
    unknown = sum(1 for v in vals if not lookup(kind, v))
    if kind == 'state':
        targets = (('2-letter abbreviation', 0), ('full name', 1))
    else:
        targets = (('2-letter ISO code', 0), ('3-letter ISO code', 2), ('full name', 1))
    entries = []
    for label, position in targets:
        mapping = {}
        for raw in dict.fromkeys(raw_values):
            row = lookup(kind, raw)
            if row and row[position] != raw:
                mapping[raw] = row[position]
        if not mapping:
            continue
        sample = next(lookup(kind, v) for v in vals if lookup(kind, v))[position]
        text = f"'{col}' mixes {kind} spellings (e.g. {', '.join(repr(v) for v in list(forms.values())[:3])}); standardize every recognized value to its {label} (e.g. {sample})"
        if unknown:
            text += f"; {unknown} unrecognized value(s) are left unchanged"
        entries.append(make_entry(text, None, 0.7, [{'action': 'map', 'column': col, 'params': {'mapping': mapping}}]))
    return entries
