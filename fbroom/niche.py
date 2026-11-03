"""Niche standardizers: Excel/epoch dates, quarters, fiscal years, durations, feet-inches, fractions, CR/DR,
sentinels, names, addresses, industry check digits, privacy masking, unit-aware renames, fuzzy duplicates and
cross-column checks.

Everything is expressed with the existing step actions (`cast`, `regex_replace`, `map`, `rename`, `normalize`,
`drop_column`), so preview, apply and Undo work unchanged. Value conversions ride on `cast` with
`fmt="conv:<name>:<arg>"`, evaluated by `convert_special`. Checks that cannot be fixed safely are review-only.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Callable

from .standardize import _UNITS, _read_unit, convert_unit, parse_mixed_number

MakeEntry = Callable[..., dict[str, Any]]


def _tokens(col: str) -> list[str]:
    spaced = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', str(col))
    return [t for t in re.split(r'[^a-z0-9]+', spaced.lower()) if t]


def _ex(items, k: int = 3) -> str:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
        if len(seen) >= k:
            break
    return ', '.join(repr(s) for s in seen)


def _num_text(x: float) -> str:
    return f"{x:.6f}".rstrip('0').rstrip('.') or '0'


_NOT_DATE_WORDS = {'update', 'candidate', 'validate', 'mandate', 'consolidate', 'outdate', 'backdate', 'accommodate', 'invalidate', 'predate', 'postdate'}
_TIME_WORDS = {'date', 'datetime', 'time', 'timestamp', 'ts', 'dt', 'created', 'updated', 'modified', 'dob', 'birth', 'born', 'expiry',
               'expires', 'expiration', 'deadline', 'dated', 'epoch', 'logged', 'recorded', 'posted'}


def _time_like(col: str) -> bool:
    for t in _tokens(col):
        if t in _TIME_WORDS:
            return True
        if t not in _NOT_DATE_WORDS and len(t) > 5 and t.endswith(('date', 'stamp', 'datetime', 'timestamp')):
            return True
    return False


# ---------------------------------------------------------------- converters

_QPATS = (
    ('qy', re.compile(r"^Q([1-4])\s*[-/,'\u2019.]?\s*(\d{4}|\d{2})$", re.I)),
    ('nq', re.compile(r"^([1-4])Q\s*[-/,'\u2019.]?\s*(\d{4}|\d{2})$", re.I)),
    ('ord', re.compile(r"^([1-4])(?:st|nd|rd|th)\s+quarter,?\s*(\d{4})$", re.I)),
    ('yq', re.compile(r"^(\d{4}|\d{2})\s*[-/,'\u2019.]?\s*Q([1-4])$", re.I)),
)


def _year(text: str) -> int:
    y = int(text)
    return y if y >= 100 else (2000 + y if y < 70 else 1900 + y)


def parse_quarter(text: str):
    s = text.strip()
    for pid, rx in _QPATS:
        m = rx.match(s)
        if m:
            a, b = m.groups()
            q, y = (b, a) if pid == 'yq' else (a, b)
            layout = (pid, re.sub(r'[\dA-Za-z]', '', s), len(y), s.upper() == s)
            return _year(y), int(q), layout
    return None


_FYPATS = (
    ('fy4', re.compile(r"^F\.?Y\.?\s*'?(\d{4})$", re.I)),
    ('fy2', re.compile(r"^F\.?Y\.?\s*'?(\d{2})$", re.I)),
    ('fyr', re.compile(r"^(F\.?Y\.?\s*)?(\d{4})\s*[-/\u2013]\s*(\d{4}|\d{2})$", re.I)),
    ('yr4', re.compile(r"^(\d{4})$")),
)


def parse_fiscal_year(text: str, allow_bare: bool = False):
    s = text.strip()
    for pid, rx in _FYPATS:
        m = rx.match(s)
        if not m:
            continue
        if pid == 'yr4':
            return (int(m.group(1)), ('yr4',)) if allow_bare else None
        if pid == 'fyr':
            start = int(m.group(2))
            end_text = m.group(3)
            end = int(end_text) if len(end_text) == 4 else (start // 100) * 100 + int(end_text)
            if end != start + 1 and not (len(end_text) == 2 and int(end_text) == (start + 1) % 100):
                return None
            return end, ('fyr', bool(m.group(1)), len(end_text))
        return (_year(m.group(1)), (pid,))
    return None


_ISO_DUR = re.compile(r'^P(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?$')
_CLOCK = re.compile(r'^(\d+):([0-5]\d)(?::([0-5]\d(?:\.\d+)?))?$')
_DUR_TOKEN = re.compile(r'(?<![\d.])(\d+(?:\.\d+)?)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)(?![a-z])')
_DUR_FACTOR = {'d': 86400, 'h': 3600, 'm': 60, 's': 1}


def parse_duration(text: str):
    s = text.strip()
    iso = _ISO_DUR.match(s.upper())
    if iso and s.upper() not in ('P', 'PT') and any(iso.groups()):
        d, h, m, sec = (float(g) if g else 0.0 for g in iso.groups())
        return d * 86400 + h * 3600 + m * 60 + sec, 'iso'
    clock = _CLOCK.match(s)
    if clock:
        h, m, sec = int(clock.group(1)), int(clock.group(2)), float(clock.group(3) or 0)
        return h * 3600 + m * 60 + sec, 'clock3' if clock.group(3) is not None else 'clock2'
    low = s.lower()
    tokens = _DUR_TOKEN.findall(low)
    if not tokens:
        return None
    leftover = _DUR_TOKEN.sub('', low)
    if re.sub(r'\b(?:and)\b|[\s,&]', '', leftover):
        return None
    total = sum(float(n) * _DUR_FACTOR[u[0]] for n, u in tokens)
    return total, 'compound' if len(tokens) > 1 else 'single'


def _format_duration(sec: float, style: str):
    if style == 'min':
        return round(sec / 60, 4)
    if style == 'hours':
        return round(sec / 3600, 4)
    if style == 'sec':
        return round(sec, 3)
    total = int(round(sec))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    if style == 'hms':
        return f"{h}:{m:02d}:{s:02d}"
    if style == 'words':
        parts = [f"{v}{u}" for v, u in ((h, 'h'), (m, 'm'), (s, 's')) if v]
        return ' '.join(parts) or '0m'
    if style == 'iso':
        body = ''.join(f"{v}{u}" for v, u in ((h, 'H'), (m, 'M'), (s, 'S')) if v)
        return 'PT' + (body or '0S')
    return None


_FT = re.compile(r"^(\d+(?:\.\d+)?)\s*(?:'|\u2032|ft\.?|feet|foot)\s*(?:(\d+(?:\.\d+)?)\s*(?:\"|\u2033|''|in\.?|inch|inches)?)?$", re.I)
_IN = re.compile(r"^(\d+(?:\.\d+)?)\s*(?:\"|\u2033|in\.?|inch|inches)$", re.I)


def parse_feet_inches(text: str):
    s = text.strip()
    m = _FT.match(s)
    if m:
        return float(m.group(1)) * 12 + float(m.group(2) or 0), 'ftin' if m.group(2) else 'ft'
    m = _IN.match(s)
    if m:
        return float(m.group(1)), 'in'
    unit = _read_unit(s)
    if unit and unit[2] == 'length':
        meters = convert_unit(unit[0], unit[1], 'm')
        return (meters / 0.0254 if meters is not None else None), 'metric'
    return None


_UF = {'\u00bd': (1, 2), '\u2153': (1, 3), '\u2154': (2, 3), '\u00bc': (1, 4), '\u00be': (3, 4), '\u2155': (1, 5), '\u2156': (2, 5),
       '\u2157': (3, 5), '\u2158': (4, 5), '\u2159': (1, 6), '\u215a': (5, 6), '\u215b': (1, 8), '\u215c': (3, 8), '\u215d': (5, 8), '\u215e': (7, 8)}
_DENOMINATORS = {2, 3, 4, 5, 6, 8, 10, 12, 16, 32, 64}
_UF_CLASS = ''.join(_UF)


def parse_fraction(text: str):
    s = text.strip().rstrip('"\u2033').strip()
    s = re.sub(r'\s*(?:inch(?:es)?|in\.?)$', '', s, flags=re.I)
    sign = -1 if s.startswith('-') else 1
    s = s.lstrip('+-').strip()
    m = re.fullmatch(rf'(\d+)?\s*([{_UF_CLASS}])', s)
    if m:
        n, d = _UF[m.group(2)]
        return sign * (int(m.group(1) or 0) + n / d), 'frac'
    m = re.fullmatch(r'(?:(\d+)[\s-]+)?(\d+)/(\d+)', s)
    if m:
        d = int(m.group(3))
        if d not in _DENOMINATORS:
            return None
        return sign * (int(m.group(1) or 0) + int(m.group(2)) / d), 'frac'
    if re.fullmatch(r'\d+(?:\.\d+)?', s):
        return sign * float(s), 'plain'
    return None


_CRDR = re.compile(r'^(?:(?P<p>cr|dr)\.?\s*)?(?P<n>[-+(]?[\d.,\s]*\d\)?)\s*(?:(?P<s>cr|dr)\.?)?$', re.I)


def parse_crdr(text: str):
    s = re.sub(r'[$\u20ac\u00a3\u00a5\u20b9]', '', text.strip())
    m = _CRDR.match(s)
    if not m or (m.group('p') and m.group('s')):
        return None
    number = parse_mixed_number(m.group('n').strip('() '), 'us')
    if number is None:
        return None
    side = (m.group('p') or m.group('s') or '').lower() or None
    return abs(number) if side else number, side


_TITLES = {'mr', 'mrs', 'ms', 'miss', 'dr', 'prof', 'sir', 'mx'}
_SUFFIX_FIX = {'jr': 'Jr', 'sr': 'Sr', 'ii': 'II', 'iii': 'III', 'iv': 'IV', 'md': 'MD', 'phd': 'PhD', 'dds': 'DDS', 'rn': 'RN', 'esq': 'Esq', 'dvm': 'DVM'}
_PARTICLES = {'van', 'von', 'der', 'den', 'de', 'del', 'della', 'di', 'da', 'dos', 'das', 'du', 'la', 'le', 'bin', 'ibn', 'al', 'el', 'ter', 'ten', 'zu', 'y'}


def _cap_token(tok: str, first: bool) -> str:
    bare = tok.strip('.,').lower()
    trail = tok[len(tok.rstrip('.,')):]
    if bare in _SUFFIX_FIX:
        return _SUFFIX_FIX[bare] + trail
    if bare in _PARTICLES and not first:
        return bare + trail
    out = []
    for part in tok.split('-'):
        low = part.lower()
        if "'" in low or '\u2019' in low:
            sep = "'" if "'" in low else '\u2019'
            head, _, tail = low.partition(sep)
            low = head[:1].upper() + head[1:] + sep + tail[:1].upper() + tail[1:] if len(head) <= 2 else low[:1].upper() + low[1:]
            out.append(low)
        elif low.startswith('mc') and len(low) > 2:
            out.append('Mc' + low[2:3].upper() + low[3:])
        else:
            out.append(low[:1].upper() + low[1:])
    return '-'.join(out)


def _shouty(s: str) -> bool:
    letters = [c for c in s if c.isalpha()]
    return len(letters) >= 2 and (s == s.upper() or s == s.lower())


def name_case(text: str) -> str:
    if not _shouty(text):
        return text
    return ' '.join(_cap_token(t, i == 0) for i, t in enumerate(text.split()))


def _split_suffix(tokens: list[str]) -> tuple[list[str], list[str]]:
    tail: list[str] = []
    while len(tokens) > 1 and tokens[-1].strip('.,').lower() in _SUFFIX_FIX:
        tail.insert(0, tokens.pop())
    return tokens, tail


def _comma_name(text: str):
    if text.count(',') != 1:
        return None
    last, first = (p.strip() for p in text.split(','))
    if not last or not first or first.strip('.').lower() in _SUFFIX_FIX:
        return None
    return last, first


def name_first_last(text: str) -> str:
    parts = _comma_name(text)
    if not parts:
        return text
    last, first = parts
    first_tokens, suffix = _split_suffix(first.split())
    return ' '.join(first_tokens + [last] + suffix)


def name_last_first(text: str) -> str:
    if ',' in text or len(text.split()) < 2:
        return text
    tokens, suffix = _split_suffix(text.split())
    if len(tokens) < 2:
        return text
    last = [tokens.pop()]
    while len(tokens) > 1 and tokens[-1].lower() in _PARTICLES:
        last.insert(0, tokens.pop())
    return f"{' '.join(last)}, {' '.join(tokens + suffix)}"


_STREET = {'street': 'St', 'avenue': 'Ave', 'boulevard': 'Blvd', 'road': 'Rd', 'drive': 'Dr', 'lane': 'Ln', 'court': 'Ct', 'place': 'Pl',
           'circle': 'Cir', 'highway': 'Hwy', 'parkway': 'Pkwy', 'terrace': 'Ter', 'square': 'Sq', 'trail': 'Trl', 'alley': 'Aly', 'expressway': 'Expy'}
_STREET_ALIASES = {'str': 'street', 'st': 'street', 'av': 'avenue', 'ave': 'avenue', 'aven': 'avenue', 'blvd': 'boulevard', 'bvd': 'boulevard',
                   'rd': 'road', 'dr': 'drive', 'drv': 'drive', 'ln': 'lane', 'ct': 'court', 'pl': 'place', 'cir': 'circle', 'hwy': 'highway',
                   'pkwy': 'parkway', 'pky': 'parkway', 'ter': 'terrace', 'terr': 'terrace', 'sq': 'square', 'trl': 'trail', 'aly': 'alley', 'expy': 'expressway'}
_DIRECTIONS = {'north': 'N', 'south': 'S', 'east': 'E', 'west': 'W', 'northeast': 'NE', 'northwest': 'NW', 'southeast': 'SE', 'southwest': 'SW'}
_UNIT_WORDS = {'apartment': 'Apt', 'suite': 'Ste', 'floor': 'Fl', 'room': 'Rm', 'building': 'Bldg'}
_UNIT_ALIASES = {'apt': 'apartment', 'ste': 'suite', 'fl': 'floor', 'rm': 'room', 'bldg': 'building'}


def _addr_key(token: str) -> str:
    return token.strip('.,').lower()


def _street_family(key: str):
    key = _STREET_ALIASES.get(key, key)
    return key if key in _STREET else None


def _direction_family(key: str):
    if key in _DIRECTIONS:
        return key
    for full, abbr in _DIRECTIONS.items():
        if abbr.lower() == key:
            return full
    return None


def _unit_family(key: str):
    key = _UNIT_ALIASES.get(key, key)
    return key if key in _UNIT_WORDS else None


def _address_slots(text: str):
    """(index, token, family-kind, family) for each replaceable token of one address."""
    tokens = text.split()
    slots = []
    first_end = next((i for i, t in enumerate(tokens) if t.endswith(',')), len(tokens) - 1)
    street_at = None
    for i in range(first_end, 0, -1):
        if _street_family(_addr_key(tokens[i])):
            street_at = i
            break
    if street_at is not None:
        slots.append((street_at, tokens[street_at], 'street', _street_family(_addr_key(tokens[street_at]))))
        if street_at + 1 <= first_end and _direction_family(_addr_key(tokens[street_at + 1])):
            slots.append((street_at + 1, tokens[street_at + 1], 'dir', _direction_family(_addr_key(tokens[street_at + 1]))))
    if len(tokens) >= 3 and tokens[0][:1].isdigit() and _direction_family(_addr_key(tokens[1])) and street_at not in (None, 1):
        slots.append((1, tokens[1], 'dir', _direction_family(_addr_key(tokens[1]))))
    for i, tok in enumerate(tokens[1:], 1):
        fam = _unit_family(_addr_key(tok))
        if fam and i + 1 < len(tokens):
            slots.append((i, tok, 'unit', fam))
    return tokens, slots


def _restyle(original: str, new: str) -> str:
    bare = original.rstrip('.,')
    trail = original[len(bare):].replace('.', '')
    if len(bare) > 1 and bare.isupper() and len(new) > 1:
        new = new.upper()
    elif bare.islower():
        new = new.lower()
    return new + trail


def normalize_address(text: str, style: str) -> str:
    tokens, slots = _address_slots(text)
    for index, token, kind, family in slots:
        table = {'street': _STREET, 'dir': _DIRECTIONS, 'unit': _UNIT_WORDS}[kind]
        new = table[family] if style == 'abbr' else family.capitalize()
        tokens[index] = _restyle(token, new)
    return ' '.join(tokens)


# ---- check digits

def _digits(text: str) -> str:
    return re.sub(r'[\s-]', '', text)


def gtin_ok(d: str) -> bool:
    if not d.isdigit() or len(d) not in (8, 12, 13, 14):
        return False
    total = sum(int(x) * (3 if i % 2 == 0 else 1) for i, x in enumerate(reversed(d[:-1])))
    return (10 - total % 10) % 10 == int(d[-1])


def isbn10_ok(d: str) -> bool:
    if not re.fullmatch(r'\d{9}[\dX]', d):
        return False
    return sum((10 - i) * (10 if c == 'X' else int(c)) for i, c in enumerate(d)) % 11 == 0


def isbn_ok(d: str) -> bool:
    return isbn10_ok(d) if len(d) == 10 else len(d) == 13 and gtin_ok(d)


def isbn10_to_13(d: str) -> str:
    body = '978' + d[:9]
    total = sum(int(x) * (3 if i % 2 else 1) for i, x in enumerate(body))
    return body + str((10 - total % 10) % 10)


def luhn_ok(d: str) -> bool:
    if not d.isdigit() or len(d) < 8:
        return False
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i % 2:
            n *= 2
            n -= 9 if n > 9 else 0
        total += n
    return total % 10 == 0


def npi_ok(d: str) -> bool:
    return bool(re.fullmatch(r'\d{10}', d)) and luhn_ok('80840' + d)


def iban_ok(s: str) -> bool:
    s = re.sub(r'\s', '', s).upper()
    if not re.fullmatch(r'[A-Z]{2}\d{2}[A-Z0-9]{11,30}', s):
        return False
    moved = s[4:] + s[:4]
    return int(''.join(str(int(c, 36)) for c in moved)) % 97 == 1


def aba_ok(d: str) -> bool:
    return bool(re.fullmatch(r'\d{9}', d)) and sum(int(c) * w for c, w in zip(d, (3, 7, 1) * 3)) % 10 == 0


_VIN_MAP = {**{str(i): i for i in range(10)}, **dict(zip('ABCDEFGH', range(1, 9))), **dict(zip('JKLMN', range(1, 6))), 'P': 7, 'R': 9,
            **dict(zip('STUVWXYZ', (2, 3, 4, 5, 6, 7, 8, 9)))}


def vin_ok(v: str) -> bool:
    v = v.upper()
    if not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', v):
        return False
    total = sum(_VIN_MAP[c] * w for c, w in zip(v, (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)))
    check = total % 11
    return v[8] == ('X' if check == 10 else str(check))


def ndc_to_11(text: str):
    s = text.strip()
    m = re.fullmatch(r'(\d{4,5})-(\d{3,4})-(\d{1,2})', s)
    if m:
        a, b, c = m.groups()
        if (len(a), len(b), len(c)) in ((4, 4, 2), (5, 3, 2), (5, 4, 1), (5, 4, 2)):
            return a.zfill(5) + b.zfill(4) + c.zfill(2)
        return None
    return s if re.fullmatch(r'\d{11}', s) else None


def hs_digits(text: str):
    d = re.sub(r'[\s.\-]', '', text)
    if not re.fullmatch(r'\d{3,10}', d):
        return None
    return '0' + d if len(d) % 2 else d


def hs_chapter_ok(d: str) -> bool:
    return 1 <= int(d[:2]) <= 97 and int(d[:2]) != 77


def _hs_dotted(d: str) -> str:
    return '.'.join(p for p in (d[:4], d[4:6], d[6:]) if p)


def _gtin_convert(text: str, arg: str) -> str:
    d = _digits(text)
    if not d.isdigit():
        return text
    n = int(arg)
    if len(d) < n and gtin_ok(d.zfill(n)):
        return d.zfill(n)
    return d


def _ndc_convert(text: str, arg: str) -> str:
    d = ndc_to_11(text)
    if d is None:
        return text
    return d if arg == 'digits' else f"{d[:5]}-{d[5:9]}-{d[9:]}"


def _hs_convert(text: str, arg: str) -> str:
    d = hs_digits(text)
    if d is None:
        return text
    return d if arg == 'digits' else _hs_dotted(d)


def _isbn_convert(text: str, arg: str) -> str:
    d = _digits(text).upper()
    if isbn10_ok(d):
        return isbn10_to_13(d)
    return d if re.fullmatch(r'\d{13}', d) else text


def _iban_convert(text: str, arg: str) -> str:
    return re.sub(r'\s', '', text).upper()


# ---- dispatcher

def _excel(text: str, arg: str):
    x = float(text.replace(',', ''))
    if not 1 <= x < 2958466:
        return None
    base = datetime(1899, 12, 30)
    if arg == 'date':
        return (base + timedelta(days=int(x))).strftime('%Y-%m-%d')
    return (base + timedelta(seconds=round(x * 86400))).strftime('%Y-%m-%d %H:%M:%S')


_EPOCH_DIV = {'s': 1, 'ms': 1000, 'us': 1_000_000}


def _epoch(text: str, arg: str):
    unit, _, shape = arg.partition(':')
    moment = datetime(1970, 1, 1) + timedelta(seconds=float(text) / _EPOCH_DIV[unit])
    return moment.strftime('%Y-%m-%d') if shape == 'date' else moment.strftime('%Y-%m-%d %H:%M:%S')


def _quarter(text: str, arg: str):
    parsed = parse_quarter(text)
    if not parsed:
        return text
    y, q, _ = parsed
    return {'iso': f"{y}-Q{q}", 'us': f"Q{q} {y}", 'compact': f"{y}Q{q}", 'start': f"{y}-{(q - 1) * 3 + 1:02d}-01"}[arg]


def _fiscal(text: str, arg: str):
    parsed = parse_fiscal_year(text, allow_bare=True)
    if not parsed:
        return text
    y = parsed[0]
    return {'full': f"FY{y}", 'short': f"FY{y % 100:02d}", 'range': f"{y - 1}-{y % 100:02d}", 'year': str(y)}[arg]


def _duration(text: str, arg: str):
    parsed = parse_duration(text)
    return None if parsed is None else _format_duration(parsed[0], arg)


def _feet_inches(text: str, arg: str):
    parsed = parse_feet_inches(text)
    if parsed is None or parsed[0] is None:
        return None
    inches = parsed[0]
    if arg == 'in':
        return round(inches, 2)
    if arg == 'cm':
        return round(inches * 2.54, 1)
    if arg == 'm':
        return round(inches * 0.0254, 3)
    if arg == 'ft':
        return round(inches / 12, 3)
    whole = int(round(inches))
    return f"{whole // 12}'{whole % 12}\""


def _fraction(text: str, arg: str):
    parsed = parse_fraction(text)
    return None if parsed is None else round(parsed[0], 6)


def _crdr(text: str, arg: str):
    parsed = parse_crdr(text)
    if parsed is None:
        return None
    amount, side = parsed
    if side is None:
        return amount
    positive = 'dr' if arg == 'dr_pos' else 'cr'
    return amount if side == positive else -amount


def _name(text: str, arg: str):
    return {'case': name_case, 'first_last': name_first_last, 'last_first': name_last_first}[arg](text)


def _address(text: str, arg: str):
    return normalize_address(text, arg)


_CONVERTERS = {'xl': _excel, 'epoch': _epoch, 'quarter': _quarter, 'fy': _fiscal, 'dur': _duration, 'ftin': _feet_inches, 'frac': _fraction,
               'crdr': _crdr, 'name': _name, 'addr': _address, 'gtin': _gtin_convert, 'ndc': _ndc_convert, 'hs': _hs_convert,
               'isbn': _isbn_convert, 'iban': _iban_convert}


def convert_special(values, fmt: str, to_type: str):
    """Values rewritten for a `cast` whose fmt is 'conv:<name>:<arg>'; unreadable values become None."""
    name, _, arg = fmt.partition(':')[2].partition(':')
    fn = _CONVERTERS[name]
    out = []
    for value in values:
        text = '' if value is None else str(value).strip()
        try:
            result = fn(text, arg) if text else None
        except (ValueError, OverflowError, KeyError, ArithmeticError):
            result = None
        if result is None:
            out.append(None)
        elif to_type in ('str', 'string'):
            out.append(_num_text(result) if isinstance(result, float) else str(result))
        else:
            out.append(float(result))
    return out


def _cast(col: str, spec: str, to: str) -> dict[str, Any]:
    return {'action': 'cast', 'column': col, 'params': {'to': to, 'fmt': f'conv:{spec}'}}


# ---------------------------------------------------------------- exclusive (replace the generic analysis)

_NUM_RE = re.compile(r'-?\d+(?:\.\d+)?')


def _serial_entries(col, vals, make_entry):
    if not _time_like(col) or sum(1 for v in vals if _NUM_RE.fullmatch(v)) / len(vals) < 0.9:
        return []
    nums = [float(v) for v in vals if _NUM_RE.fullmatch(v)]
    entries = []
    if sum(1 for x in nums if 20000 <= x <= 60000) / len(nums) >= 0.9:
        fractional = any(x != int(x) for x in nums)
        ex = _ex(v for v in vals if _NUM_RE.fullmatch(v))
        entries.append(make_entry(f"'{col}' holds Excel serial day numbers ({ex}); convert them to ISO dates (Excel 1900 date system)", None, 0.8,
                                  [_cast(col, 'xl:date', 'str')]))
        if fractional:
            entries.append(make_entry(f"'{col}' holds Excel serial numbers with a time-of-day fraction ({ex}); convert them to ISO date-times", None, 0.75,
                                      [_cast(col, 'xl:datetime', 'str')]))
        return entries
    ints = [v for v in vals if re.fullmatch(r'\d+', v)]
    if len(ints) / len(vals) >= 0.9:
        lengths = Counter(len(v) for v in ints)
        unit = None
        for names, ls in (('s', (9, 10)), ('ms', (12, 13)), ('us', (15, 16))):
            if sum(lengths[l] for l in ls) / len(ints) >= 0.9:
                unit = names
        if unit:
            label = {'s': 'seconds', 'ms': 'milliseconds', 'us': 'microseconds'}[unit]
            ex = _ex(ints)
            for shape, text in (('date', 'ISO dates'), ('datetime', 'ISO date-times (UTC)')):
                entries.append(make_entry(f"'{col}' holds Unix epoch timestamps in {label} ({ex}); convert them to {text}", None, 0.8,
                                          [_cast(col, f'epoch:{unit}:{shape}', 'str')]))
    return entries


def _quarter_entries(col, vals, make_entry):
    parsed = [parse_quarter(v) for v in vals]
    ok = [p for p in parsed if p]
    if len(ok) < 2 or len(ok) / len(vals) < 0.9 or len({p[2] for p in ok}) < 2:
        return []
    ex = _ex(v for v, p in zip(vals, parsed) if p)
    note = '' if len(ok) == len(vals) else f"; {len(vals) - len(ok)} value(s) that are not quarters stay unchanged"
    labels = (('iso', '2024-Q1'), ('us', 'Q1 2024'), ('compact', '2024Q1'), ('start', '2024-01-01 (the first day of the calendar quarter)'))
    return [make_entry(f"'{col}' writes quarters in different layouts ({ex}); standardize them all as {label}{note}", None, 0.75,
                       [_cast(col, f'quarter:{key}', 'str')]) for key, label in labels]


def _fiscal_entries(col, vals, make_entry):
    hint = bool(set(_tokens(col)) & {'fy', 'fiscal'})
    parsed = [parse_fiscal_year(v, allow_bare=hint) for v in vals]
    ok = [p for p in parsed if p]
    if len(ok) < 2 or len(ok) / len(vals) < 0.9 or len({p[1] for p in ok}) < 2:
        return []
    if not hint and not any(p[1][0] in ('fy4', 'fy2') or (p[1][0] == 'fyr' and p[1][1]) for p in ok):
        return []
    ex = _ex(v for v, p in zip(vals, parsed) if p)
    note = '; a range such as 2023-24 is read as the fiscal year that ends in 2024' + ('' if len(ok) == len(vals) else f"; {len(vals) - len(ok)} other value(s) stay unchanged")
    labels = (('full', 'FY2024'), ('short', 'FY24'), ('range', '2023-24'), ('year', '2024 (the year the fiscal year ends)'))
    return [make_entry(f"'{col}' writes fiscal years in different layouts ({ex}); standardize them all as {label}{note}", None, 0.7,
                       [_cast(col, f'fy:{key}', 'str')]) for key, label in labels]


_DURATION_HINT = {'duration', 'elapsed', 'length', 'runtime', 'spent', 'dwell', 'wait', 'lead', 'handle', 'tenure', 'period', 'ttl', 'age', 'time'}


def _duration_entries(col, vals, make_entry):
    parsed = [parse_duration(v) for v in vals]
    ok = [p for p in parsed if p]
    if len(ok) < 2 or len(ok) / len(vals) < 0.9:
        return []
    styles = {p[1] for p in ok}
    rich = bool(styles & {'compound', 'iso'})
    if len(styles) < 2 or not (rich or (styles & {'clock2', 'clock3'} and set(_tokens(col)) & _DURATION_HINT)):
        return []
    ex = _ex(v for v, p in zip(vals, parsed) if p)
    note = ' (H:MM is read as hours:minutes)' if 'clock2' in styles else ''
    note += '' if len(ok) == len(vals) else f"; {len(vals) - len(ok)} unreadable value(s) become empty"
    out = []
    for key, label, to in (('min', 'decimal minutes', 'float'), ('hours', 'decimal hours', 'float'), ('sec', 'seconds', 'float'),
                           ('hms', 'H:MM:SS text', 'str'), ('words', 'text like 1h 30m', 'str'), ('iso', 'ISO 8601 durations like PT1H30M', 'str')):
        out.append(make_entry(f"'{col}' mixes duration styles ({ex}); standardize every duration as {label}{note}", None, 0.75, [_cast(col, f'dur:{key}', to)]))
    return out


def _feet_inch_entries(col, vals, make_entry):
    parsed = [parse_feet_inches(v) for v in vals]
    ok = [p for p in parsed if p and p[0] is not None]
    if len(ok) / len(vals) < 0.9 or not any(p[1] in ('ftin', 'ft') for p in ok):
        return []
    ex = _ex(v for v, p in zip(vals, parsed) if p)
    note = '' if len(ok) == len(vals) else f"; {len(vals) - len(ok)} unreadable value(s) become empty"
    out = []
    for key, label, to in (('in', 'inches', 'float'), ('cm', 'centimeters', 'float'), ('m', 'meters', 'float'), ('ft', 'decimal feet', 'float'), ('text', "feet and inches text like 5'10\"", 'str')):
        out.append(make_entry(f"'{col}' holds feet-and-inches measurements ({ex}); convert every value to {label}{note}", None, 0.75, [_cast(col, f'ftin:{key}', to)]))
    return out


def _fraction_entries(col, vals, make_entry):
    if _time_like(col):
        return []
    parsed = [parse_fraction(v) for v in vals]
    ok = [p for p in parsed if p]
    if len(ok) / len(vals) < 0.9 or not any(p[1] == 'frac' for p in ok):
        return []
    ex = _ex(v for v, p in zip(vals, parsed) if p and p[1] == 'frac')
    note = '' if len(ok) == len(vals) else f"; {len(vals) - len(ok)} unreadable value(s) become empty"
    return [make_entry(f"'{col}' holds fractions and mixed numbers ({ex}); convert every value to a decimal number{note}", None, 0.75, [_cast(col, 'frac:dec', 'float')])]


def _crdr_entries(col, vals, make_entry):
    parsed = [parse_crdr(v) for v in vals]
    ok = [p for p in parsed if p]
    if len(ok) / len(vals) < 0.9 or sum(1 for p in ok if p[1]) / len(vals) < 0.5:
        return []
    ex = _ex(v for v, p in zip(vals, parsed) if p and p[1])
    note = '' if len(ok) == len(vals) else f"; {len(vals) - len(ok)} unreadable value(s) become empty"
    return [make_entry(f"'{col}' marks amounts with CR/DR suffixes ({ex}); convert to signed numbers with {plus} amounts positive and {minus} negative{note}", None, 0.75,
                       [_cast(col, f'crdr:{key}', 'float')])
            for key, plus, minus in (('dr_pos', 'debits (DR)', 'credits (CR)'), ('cr_pos', 'credits (CR)', 'debits (DR)'))]


# ---- unit-aware names

_EXTRA_UNIT_SLUG = {'km/h': 'kmh', 'm/s': 'mps', 'fl oz': 'floz'}
_SYMBOL_UNITS = {'%': 'pct', '$': 'usd', '\u20ac': 'eur', '\u00a3': 'gbp', 'percent': 'pct', 'pct': 'pct', 'usd': 'usd', 'eur': 'eur', 'gbp': 'gbp', 'cad': 'cad', 'jpy': 'jpy'}


def _unit_slug(canon: str) -> str:
    return _EXTRA_UNIT_SLUG.get(canon, re.sub(r'[^a-z0-9]', '', canon.lower()))


def _header_unit(col: str):
    """(canon unit or None, label, bracketed) when the column name already states a unit."""
    m = re.search(r'[(\[]\s*([^)\]]+?)\s*[)\]]\s*$', col)
    if m:
        raw = m.group(1).strip().lower()
        key = re.sub(r'[\s.\u00b0]', '', raw).replace('\u00b2', '2')
        row = _UNITS.get(key)
        if row:
            return row[1], raw, True
        if raw in _SYMBOL_UNITS:
            return None, _SYMBOL_UNITS[raw], True
        return None
    toks = _tokens(col)
    if len(toks) >= 2:
        last = toks[-1]
        row = _UNITS.get(last) if len(last) >= 2 else None
        if row and last not in {'in', 'to', 'at', 'on', 'id', 'pt', 'ac', 'ha', 'mi', 'sec', 'wk', 'day', 'days', 'min', 'mins', 'hr', 'hrs', 'hour', 'hours'}:
            return row[1], last, False
        if last in _SYMBOL_UNITS:
            return None, _SYMBOL_UNITS[last], False
    return None


def _unit_name_entries(col, vals, make_entry):
    header = _header_unit(col)
    reads = [_read_unit(v) for v in vals]
    ok = [r for r in reads if r]
    if header and header[2] and not ok and sum(1 for v in vals if _NUM_RE.fullmatch(v.replace(',', ''))) / len(vals) >= 0.9:
        base = re.sub(r'\s*[(\[][^)\]]*[)\]]\s*$', '', col).strip().replace(' ', '_')
        slug = _unit_slug(header[0]) if header[0] else header[1]
        new = f"{base}_{slug}"
        if new != col:
            return [make_entry(f"Rename column '{col}' to '{new}' so the unit stays in a plain, tool-friendly column name", None, 0.6,
                               [{'action': 'rename', 'column': col, 'params': {'new_name': new}}])]
        return []
    if len(ok) / len(vals) < 0.9 or len(ok) < 2:
        return []
    families = {r[2] for r in ok}
    units = {r[1] for r in ok}
    if len(families) != 1 or len(units) != 1:
        return []
    canon = next(iter(units))
    ex = _ex(v for v, r in zip(vals, reads) if r)
    note = '' if len(ok) == len(vals) else f"; {len(vals) - len(ok)} value(s) without a {canon} reading become empty"
    if header and header[0]:
        if header[0] == canon:
            return [make_entry(f"Values in '{col}' repeat the unit ({ex}) although the column name already says {canon}; keep only the numbers{note}", None, 0.75,
                               [{'action': 'cast', 'column': col, 'params': {'to': 'float', 'fmt': f'unit:{canon}'}}])]
        return [make_entry(f"Values in '{col}' are written in {canon} ({ex}) but the column name says {header[0]}; convert them to {header[0]} as plain numbers{note}", None, 0.75,
                           [{'action': 'cast', 'column': col, 'params': {'to': 'float', 'fmt': f'unit:{header[0]}'}}])]
    cast = {'action': 'cast', 'column': col, 'params': {'to': 'float', 'fmt': f'unit:{canon}'}}
    new = f"{col}_{_unit_slug(canon)}"
    return [
        make_entry(f"Every value in '{col}' carries the unit {canon} ({ex}); keep only the numbers and rename the column to '{new}' so the unit is not lost{note}", None, 0.8,
                   [cast, {'action': 'rename', 'column': col, 'params': {'new_name': new}}]),
        make_entry(f"Every value in '{col}' carries the unit {canon} ({ex}); keep only the numbers (mention {canon} in the column name yourself){note}", None, 0.7, [cast]),
    ]


# ---- industry codes

def _review(col, bad, total, label, make_entry, extra=''):
    return make_entry(f"{len(bad)} of {total} values in '{col}' fail the {label} check (e.g. {_ex(bad)}){extra}; verify them against the source. Nothing is changed automatically", None, 0.55)


def code_entries(col, vals, make_entry):
    toks = set(_tokens(col))
    joined = ''.join(_tokens(col))
    total = len(vals)

    def mostly(valid) -> bool:
        return sum(1 for v in vals if valid(v)) / total >= 0.5

    if toks & {'upc', 'ean', 'gtin', 'barcode', 'jan', 'upca', 'ean13', 'gtin14', 'gtin13', 'gtin12'} or joined.endswith(('upc', 'gtin')):
        clean = [_digits(v) for v in vals]
        if sum(1 for c in clean if c.isdigit() and 7 <= len(c) <= 14) / total < 0.9:
            return []
        valid_lengths = Counter(len(c) for c in clean if gtin_ok(c))
        target = (valid_lengths or Counter(len(c) for c in clean if len(c) in (8, 12, 13, 14))).most_common(1)
        target = target[0][0] if target else 13
        padded = [v for v, c in zip(vals, clean) if c.isdigit() and len(c) < target and gtin_ok(c.zfill(target)) and not gtin_ok(c)]
        separators = [v for v in vals if re.search(r'[\s-]', v)]
        entries = []
        if padded or separators:
            bits = []
            if separators:
                bits.append(f"strip spaces and hyphens ({_ex(separators, 2)})")
            if padded:
                bits.append(f"restore the lost leading zeros to {target} digits ({_ex(padded, 2)})")
            entries.append(make_entry(f"Clean up barcodes in '{col}': {' and '.join(bits)}", None, 0.8, [_cast(col, f'gtin:{target}', 'str')]))
        fixed = [_gtin_convert(v, str(target)) for v in vals]
        if mostly(lambda v: gtin_ok(_gtin_convert(v, str(target)))):
            bad = [v for v, f in zip(vals, fixed) if not gtin_ok(f)]
            if bad:
                entries.append(_review(col, bad, total, 'GS1 (UPC/EAN/GTIN)', make_entry))
        return entries
    if toks & {'isbn', 'isbn10', 'isbn13'}:
        clean = [_digits(v).upper() for v in vals]
        if sum(1 for c in clean if len(c) in (10, 13)) / total < 0.9:
            return []
        entries = []
        if any(re.search(r'[\s-]', v) for v in vals):
            entries.append(make_entry(f"Remove hyphens and spaces from ISBNs in '{col}'", None, 0.8,
                                      [{'action': 'regex_replace', 'column': col, 'params': {'pattern': r'[\s-]', 'replace': ''}}]))
        if any(len(c) == 10 for c in clean) and any(len(c) == 13 for c in clean):
            entries.append(make_entry(f"'{col}' mixes ISBN-10 and ISBN-13; convert every valid ISBN-10 to ISBN-13", None, 0.75, [_cast(col, 'isbn:13', 'str')]))
        valid_isbn = lambda v: isbn_ok(_digits(v).upper())
        if mostly(valid_isbn):
            bad = [v for v in vals if not valid_isbn(v)]
            if bad:
                entries.append(_review(col, bad, total, 'ISBN', make_entry))
        return entries
    if toks & {'ndc', 'ndc10', 'ndc11'}:
        parsed = [ndc_to_11(v) for v in vals]
        if sum(1 for p in parsed if p) / total < 0.7:
            return []
        if all(p == v.strip() for v, p in zip(vals, parsed) if p):
            return []
        left = total - sum(1 for p in parsed if p)
        note = f"; {left} value(s) in another layout (such as 10 digits without hyphens) stay unchanged because the segments cannot be placed" if left else ''
        ex = _ex(v for v, p in zip(vals, parsed) if p and p != v.strip())
        return [make_entry(f"'{col}' mixes National Drug Code layouts ({ex}); standardize every readable NDC as {label}{note}", None, 0.75, [_cast(col, f'ndc:{key}', 'str')])
                for key, label in (('digits', '11 digits (5-4-2 without hyphens)'), ('hyphen', 'hyphenated 5-4-2'))]
    if toks & {'hs', 'hts', 'htsus', 'hscode', 'tariff', 'harmonized', 'hsn'} or ('commodity' in toks and 'code' in toks):
        parsed = [hs_digits(v) for v in vals]
        if sum(1 for p in parsed if p) / total < 0.9:
            return []
        shapes = {re.sub(r'\d', '9', v) for v in vals}
        odd = [v for v in vals if len(re.sub(r'\D', '', v)) % 2]
        entries = []
        if len(shapes) > 1 or odd:
            lost = f"; {len(odd)} odd-length code(s) such as {_ex(odd, 2)} get the leading zero they lost" if odd else ''
            ex = _ex(vals)
            entries = [make_entry(f"'{col}' writes tariff (HS) codes in different layouts ({ex}); standardize every code as {label}{lost}", None, 0.75, [_cast(col, f'hs:{key}', 'str')])
                       for key, label in (('digits', 'digits only'), ('dotted', 'dotted groups (8517.12.00)'))]
        bad = [v for v, p in zip(vals, parsed) if p and not hs_chapter_ok(p)]
        if bad:
            entries.append(make_entry(f"{len(bad)} HS code(s) in '{col}' start with a chapter outside 01-97 (e.g. {_ex(bad)}); verify them. Nothing is changed automatically", None, 0.55))
        return entries
    if toks & {'iban'}:
        if sum(1 for v in vals if re.fullmatch(r'[A-Za-z]{2}\d{2}[A-Za-z0-9 ]{11,40}', v.strip())) / total < 0.9:
            return []
        entries = []
        if any(v != re.sub(r'\s', '', v).upper() for v in vals):
            entries.append(make_entry(f"Standardize IBANs in '{col}' to uppercase without spaces", None, 0.8, [_cast(col, 'iban:compact', 'str')]))
        bad = [v for v in vals if not iban_ok(v)]
        if bad and mostly(iban_ok):
            entries.append(_review(col, bad, total, 'IBAN (mod-97)', make_entry))
        return entries
    if toks & {'routing', 'aba', 'rtn'}:
        clean = [_digits(v) for v in vals]
        if sum(1 for c in clean if re.fullmatch(r'\d{8,9}', c)) / total < 0.9:
            return []
        entries = []
        short = [v for v, c in zip(vals, clean) if len(c) == 8 and aba_ok('0' + c)]
        if short:
            entries.append(make_entry(f"Restore the leading zero on {len(short)} 8-digit routing number(s) in '{col}' (e.g. {_ex(short, 2)}); each passes the ABA check once padded", None, 0.8,
                                      [{'action': 'regex_replace', 'column': col, 'params': {'pattern': r'^(\d{8})$', 'replace': r'0\1'}}]))
        padded = ['0' + c if len(c) == 8 else c for c in clean]
        if sum(1 for c in padded if aba_ok(c)) / total >= 0.5:
            bad = [v for v, c in zip(vals, padded) if not aba_ok(c)]
            if bad:
                entries.append(_review(col, bad, total, 'ABA routing-number', make_entry))
        return entries
    if toks & {'npi'}:
        clean = [_digits(v) for v in vals]
        if sum(1 for c in clean if re.fullmatch(r'\d{10}', c)) / total < 0.9:
            return []
        bad = [v for v, c in zip(vals, clean) if not npi_ok(c)]
        return [_review(col, bad, total, 'NPI (Luhn)', make_entry)] if bad and mostly(lambda v: npi_ok(_digits(v))) else []
    if toks & {'card', 'cc', 'ccn', 'pan', 'creditcard', 'cardnumber'} and 'type' not in toks and 'holder' not in toks:
        clean = [_digits(v) for v in vals]
        if sum(1 for c in clean if re.fullmatch(r'\d{13,19}', c)) / total < 0.9:
            return []
        bad = [v for v, c in zip(vals, clean) if not luhn_ok(c)]
        return [_review(col, bad, total, 'card-number (Luhn)', make_entry)] if bad and mostly(lambda v: luhn_ok(_digits(v))) else []
    if toks & {'vin'}:
        if sum(1 for v in vals if re.fullmatch(r'[A-Za-z0-9]{17}', v.strip())) / total < 0.9:
            return []
        entries = []
        if any(v != v.upper() for v in vals):
            entries.append(make_entry(f"Uppercase the vehicle identification numbers in '{col}'", None, 0.8, [{'action': 'normalize', 'column': col, 'params': {'case': 'upper'}}]))
        if mostly(vin_ok):
            bad = [v for v in vals if not vin_ok(v)]
            if bad:
                entries.append(_review(col, bad, total, 'VIN check-digit', make_entry))
        return entries
    return []


def exclusive_entries(col: str, vals: list[str], make_entry: MakeEntry, identifier_name: bool = False):
    """(kind, entries) when the column matches one of the niche formats, else None."""
    if not vals:
        return None
    for kind, fn in (('identifier', code_entries), ('numeric', _unit_name_entries), ('date', _serial_entries), ('date', _quarter_entries),
                     ('date', _fiscal_entries), ('numeric', _duration_entries), ('numeric', _feet_inch_entries), ('numeric', _fraction_entries),
                     ('numeric', _crdr_entries)):
        if identifier_name and fn is not code_entries and fn is not _unit_name_entries:
            continue
        entries = fn(col, vals, make_entry)
        if entries:
            return kind, entries
    return None


# ---------------------------------------------------------------- additive entries

def _sentinel_entries(col, vals, make_entry):
    entries = []
    nums = [(v, float(v)) for v in vals if _NUM_RE.fullmatch(v)]
    if len(nums) / len(vals) >= 0.8 and len(nums) >= 6:
        counts = Counter(x for _, x in nums)
        for x, c in counts.items():
            digits = str(abs(int(x))) if x == int(x) else ''
            if not digits or not (re.fullmatch(r'9{3,}', digits) or (x < 0 and re.fullmatch(r'9{2,}', digits))):
                continue
            others = [y for y in counts.elements() if y != x]
            if len(others) < 5:
                continue
            if (x < 0 and min(others) > x / 5) or (x > 0 and max(others) < x / 5):
                pattern = rf"^\s*{re.escape(str(int(x)))}(?:\.0+)?\s*$"
                entries.append(make_entry(
                    f"'{col}' uses {int(x)} as a placeholder for missing data ({c} value(s); every other value lies between {_num_text(min(others))} and {_num_text(max(others))}); mark them blank so sums and averages are not skewed",
                    None, 0.7, [{'action': 'regex_replace', 'column': col, 'params': {'pattern': pattern, 'replace': ''}}]))
    date_like = sum(1 for v in vals if re.fullmatch(r'\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}(?:[ T].*)?', v))
    if date_like / len(vals) >= 0.5:
        sentinels = [v for v in vals if re.match(r'^(?:9999-12-31|0000-00-00|1900-01-01|1899-12-30|12/31/9999|01/01/1900|1/1/1900)(?:[ T].*)?$', v)]
        if sentinels and len(sentinels) / len(vals) <= 0.3:
            distinct = list(dict.fromkeys(s.split(' ')[0].split('T')[0] for s in sentinels))
            pattern = r'^\s*(?:' + '|'.join(re.escape(s) for s in distinct) + r')(?:[ T].*)?$'
            entries.append(make_entry(f"'{col}' uses placeholder dates ({', '.join(distinct)}) for missing data ({len(sentinels)} value(s)); mark them blank so ranges and ages are not skewed",
                                      None, 0.7, [{'action': 'regex_replace', 'column': col, 'params': {'pattern': pattern, 'replace': ''}}]))
    return entries


_NAME_WORDS = {'name', 'fullname', 'firstname', 'lastname', 'surname', 'givenname', 'middlename', 'contact', 'patient', 'customer', 'employee', 'author',
               'owner', 'manager', 'member', 'student', 'teacher', 'driver', 'guest', 'agent', 'cardholder', 'passenger', 'first', 'last', 'given', 'family'}
_NOT_PERSON = {'company', 'org', 'organization', 'business', 'product', 'file', 'user', 'username', 'host', 'brand', 'city', 'state', 'country', 'street',
               'school', 'team', 'store', 'department', 'dept', 'vendor', 'supplier', 'account', 'item', 'category', 'project', 'campaign', 'bank',
               'hospital', 'clinic', 'domain', 'table', 'column', 'sheet', 'region', 'location', 'venue', 'event', 'plan', 'service', 'site', 'carrier'}
_NAME_VALUE = re.compile(r"^[^\W\d_][^\W\d_ .'\u2019,\-]*(?:[ .'\u2019,\-]+[^\W\d_][^\W\d_ .'\u2019,\-]*)*\.?$")


def is_person_name_column(col: str) -> bool:
    toks = set(_tokens(col))
    return bool(toks & _NAME_WORDS) and not (toks & _NOT_PERSON)


def _name_entries(col, vals, make_entry):
    if not is_person_name_column(col) or sum(1 for v in vals if _NAME_VALUE.match(v)) / len(vals) < 0.8:
        return []
    entries = []
    shouty = [v for v in vals if _shouty(v)]
    if shouty:
        shown = [f"{v!r} -> {name_case(v)!r}" for v in dict.fromkeys(shouty)][:3]
        entries.append(make_entry(f"{len(shouty)} name(s) in '{col}' are all upper- or lower-case ({'; '.join(shown)}); convert them to proper name casing (handles Mc, O', van/de particles and suffixes; names already in mixed case are untouched)",
                                  None, 0.7, [_cast(col, 'name:case', 'str')]))
    toks = set(_tokens(col))
    if not toks & {'first', 'firstname', 'last', 'lastname', 'surname', 'given', 'givenname', 'family', 'middlename'}:
        comma = [v for v in vals if _comma_name(v)]
        plain = [v for v in vals if ',' not in v and len(v.split()) >= 2]
        if comma and plain:
            entries += [make_entry(f"'{col}' mixes 'Last, First' ({_ex(comma, 2)}) and 'First Last' ({_ex(plain, 2)}) name formats; write every name as {label}", None, 0.7, [_cast(col, f'name:{key}', 'str')])
                        for key, label in (('first_last', "'First Last'"), ('last_first', "'Last, First'"))]
    return entries


_ADDR_HEADER = {'address', 'addr', 'street', 'address1', 'address2', 'addressline1', 'addressline2', 'mailing', 'billing', 'shipping'}
_ADDR_SKIP = {'email', 'ip', 'mac', 'web', 'url', 'type', 'country', 'state', 'city', 'zip', 'postal'}


def _address_entries(col, vals, make_entry):
    toks = set(_tokens(col))
    if not toks & _ADDR_HEADER or toks & _ADDR_SKIP:
        return []
    shaped = [v for v in vals if re.search(r'\d', v) and ' ' in v]
    if len(shaped) / len(vals) < 0.6:
        return []
    entries = []
    forms: dict[tuple[str, str], set[str]] = {}
    for v in shaped:
        for _, token, kind, family in _address_slots(v)[1]:
            forms.setdefault((kind, family), set()).add(token.strip(',').lower())
    mixed = {k: f for k, f in forms.items() if len(f) >= 2}
    if mixed:
        ex = '/'.join(sorted(next(iter(mixed.values())))[:3])
        changed_abbr = sum(1 for v in shaped if normalize_address(v, 'abbr') != v)
        changed_full = sum(1 for v in shaped if normalize_address(v, 'full') != v)
        for style, label, count in (('abbr', 'USPS-style abbreviations (St, Ave, N, Apt)', changed_abbr), ('full', 'fully spelled-out words (Street, Avenue, North, Apartment)', changed_full)):
            entries.append(make_entry(f"Street addresses in '{col}' spell the same words differently ({ex}); standardize them to {label} ({count} value(s) change; only the street type, direction and unit words are touched)",
                                      None, 0.7, [_cast(col, f'addr:{style}', 'str')]))
    tail = [v for v in vals if re.search(r',\s*[A-Za-z .]+,?\s+[A-Z]{2}\.?\s+\d{5}(?:-\d{4})?$', v)]
    if len(tail) / len(vals) >= 0.5:
        entries.append(make_entry(f"'{col}' holds full mailing addresses (street, city, state and ZIP in one cell, e.g. {_ex(tail, 2)}); consider keeping the street here and storing city, state and ZIP in their own columns. No values are changed automatically",
                                  None, 0.45))
    return entries


def _privacy_entries(col, vals, make_entry):
    toks = set(_tokens(col))
    total = len(vals)
    spec = None
    if toks & {'ssn', 'sin', 'socialsecurity'} or 'social' in toks and 'security' in toks or sum(1 for v in vals if re.fullmatch(r'\d{3}-?\d{2}-?\d{4}', v)) / total >= 0.8:
        spec = ('Social Security numbers', r'^\D*\d{3}\D*\d{2}\D*(\d{4})\s*$', r'***-**-\1', '123-45-6789 -> ***-**-6789')
    elif toks & {'card', 'cc', 'ccn', 'pan', 'creditcard', 'cardnumber'} and 'type' not in toks and sum(1 for v in vals if re.fullmatch(r'\d{13,19}', _digits(v))) / total >= 0.8:
        spec = ('payment card numbers', r'^.*(\d{4})\s*$', r'**** **** **** \1', '4111 1111 1111 1111 -> **** **** **** 1111')
    elif toks & {'iban', 'account', 'acct', 'accountnumber', 'routing'} and not toks & {'name', 'type', 'id', 'status', 'manager', 'owner'} and sum(1 for v in vals if re.fullmatch(r'[A-Za-z]{0,2}[\d\s-]{6,}[\dA-Za-z]*', v)) / total >= 0.8:
        spec = ('bank account numbers', r'^.*(\d{4})\s*$', r'****\1', '12345678 -> ****5678')
    elif toks & {'mrn', 'passport', 'license', 'licence', 'dl', 'nationalid', 'taxid', 'ein', 'tin'} or {'medical', 'record'} <= toks or {'tax', 'id'} <= toks or {'national', 'id'} <= toks:
        if sum(1 for v in vals if len(v) >= 6) / total >= 0.8:
            spec = ('sensitive identifiers', r'^.*(.{4})$', r'****\1', 'A1234567 -> ****4567')
    elif toks & {'dob', 'birthdate', 'birthday', 'born'} or ('birth' in toks and toks & {'date', 'day'}):
        if sum(1 for v in vals if re.search(r'\b(?:19|20)\d{2}\b', v)) / total >= 0.8:
            spec = ('birth dates', r'^.*?\b((?:19|20)\d{2})\b.*$', r'\1', '1984-07-09 -> 1984 (year only)')
    elif toks & {'ip', 'ipaddress'} and sum(1 for v in vals if re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', v)) / total >= 0.8:
        spec = ('IP addresses', r'^(\d{1,3}\.\d{1,3}\.\d{1,3})\.\d{1,3}$', r'\1.0', '192.168.4.77 -> 192.168.4.0')
    elif toks & {'phone', 'mobile', 'telephone', 'tel', 'cell', 'fax'} and sum(1 for v in vals if len(re.sub(r'\D', '', v)) >= 7) / total >= 0.8:
        spec = ('phone numbers', r'^.*(\d{4})\s*$', r'***-***-\1', '(212) 555-0199 -> ***-***-0199')
    elif toks & {'email', 'mail'} and sum(1 for v in vals if re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', v)) / total >= 0.8:
        spec = ('email addresses', r'^(.)[^@]*(@.*)$', r'\1***\2', 'jane.doe@example.com -> j***@example.com')
    if not spec:
        return []
    label, pattern, replace, example = spec
    return [
        make_entry(f"Optional privacy: mask the {label} in '{col}' ({example}); the masked values replace the originals in the cleaned output, and Undo restores them before you export",
                   None, 0.4, [{'action': 'regex_replace', 'column': col, 'params': {'pattern': pattern, 'replace': replace}}]),
        make_entry(f"Optional privacy: remove the '{col}' column ({label}) from the cleaned output entirely", None, 0.35, [{'action': 'drop_column', 'column': col, 'params': {}}]),
    ]


_CORP = {'inc', 'llc', 'ltd', 'corp', 'co', 'company', 'corporation', 'limited', 'plc', 'gmbh', 'sa', 'incorporated'}
_FUZZY_SKIP = re.compile(r'(^|_)(id|uuid|guid|code|sku|email|phone|zip|postal|url|date|time|hash|key|number|num|no)($|_)', re.I)


def _norm_label(s: str) -> str:
    s = unicodedata.normalize('NFKD', s).casefold().replace('&', ' and ')
    s = ''.join(c for c in s if not unicodedata.combining(c))
    words = [w for w in re.sub(r'[^\w\s]', ' ', s).split() if w not in _CORP]
    return ' '.join(words)


def _fuzzy_entries(col, raw_values, make_entry):
    if _FUZZY_SKIP.search(col) or is_person_name_column(col):
        return []
    counts = Counter(raw_values)
    if not 3 <= len(counts) <= 300 or len(raw_values) < 6 or len(counts) / len(raw_values) > 0.8:
        return []
    if sum(1 for v in counts if re.search(r'[A-Za-z]', v)) / len(counts) < 0.8:
        return []
    keys: dict[str, list[str]] = {}
    for raw in counts:
        keys.setdefault(_norm_label(raw), []).append(raw)
    parent = {k: k for k in keys}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    ordered = sorted(k for k in keys if k)
    for i, a in enumerate(ordered):
        if len(a) < 8:
            continue
        for b in ordered[i + 1:]:
            if len(b) < 8 or abs(len(a) - len(b)) > 2 or a[0] != b[0] or re.findall(r'\d+', a) != re.findall(r'\d+', b):
                continue
            matcher = SequenceMatcher(None, a, b)
            if matcher.real_quick_ratio() >= 0.92 and matcher.quick_ratio() >= 0.92 and matcher.ratio() >= 0.92:
                parent[find(a)] = find(b)
    clusters: dict[str, list[str]] = {}
    for k, raws in keys.items():
        clusters.setdefault(find(k), []).extend(raws)
    mapping: dict[str, str] = {}
    examples = []
    for raws in clusters.values():
        if len(raws) < 2 or len({r.casefold().strip() for r in raws}) < 2:
            continue
        best = max(raws, key=lambda r: (counts[r], r != r.lower() and r != r.upper(), len(r)))
        for r in raws:
            if r != best:
                mapping[r] = best
                examples.append(f"{r!r} -> {best!r}")
    if not mapping:
        return []
    return [make_entry(f"'{col}' has {len(mapping)} near-duplicate spelling(s) of values already in the column ({'; '.join(examples[:3])}); merge each into the most common spelling",
                       None, 0.6, [{'action': 'map', 'column': col, 'params': {'mapping': mapping}}])]


def extra_entries(col: str, vals: list[str], raw_values: list[str], kind: str, make_entry: MakeEntry) -> list[dict[str, Any]]:
    """Additive suggestions appended after the column's main analysis."""
    out: list[dict[str, Any]] = []
    for fn in (_sentinel_entries, _name_entries, _address_entries, _privacy_entries):
        try:
            out.extend(fn(col, vals, make_entry))
        except Exception:
            continue
    if kind not in ('identifier', 'date', 'date_review', 'numeric', 'locale_number_review'):
        try:
            out.extend(_fuzzy_entries(col, raw_values, make_entry))
        except Exception:
            pass
    return out


# ---------------------------------------------------------------- dataset-level checks

_DATE_FORMATS = ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%m/%d/%Y', '%Y/%m/%d', '%d.%m.%Y', '%m/%d/%Y %H:%M', '%m/%d/%Y %I:%M %p')


def _column_dates(rows, col):
    texts = [str(r.get(col)).strip() if r.get(col) is not None else '' for r in rows]
    present = [t for t in texts if t]
    if len(present) < 5:
        return None
    best = None
    for fmt in _DATE_FORMATS:
        hits = 0
        for t in present:
            try:
                datetime.strptime(t.split('.')[0] if '%S' in fmt else t, fmt)
                hits += 1
            except ValueError:
                pass
        if hits / len(present) >= 0.9 and (best is None or hits > best[1]):
            best = (fmt, hits)
    if not best:
        return None
    out = []
    for t in texts:
        try:
            out.append(datetime.strptime(t.split('.')[0] if '%S' in best[0] else t, best[0]) if t else None)
        except ValueError:
            out.append(None)
    return out


_ORDERED_PAIRS = (
    (('order', 'ordered'), ('ship', 'shipped', 'shipping')), (('order', 'ordered'), ('deliver', 'delivered', 'delivery', 'received')),
    (('ship', 'shipped', 'shipping'), ('deliver', 'delivered', 'delivery', 'received')), (('created', 'create'), ('updated', 'modified', 'changed', 'closed')),
    (('opened', 'open'), ('closed', 'close', 'resolved')), (('admit', 'admitted', 'admission'), ('discharge', 'discharged')),
    (('hire', 'hired'), ('termination', 'terminated', 'separation')), (('birth', 'dob', 'born'), ('hire', 'hired', 'enrollment')),
    (('issue', 'issued'), ('expiry', 'expiration', 'expires', 'expire')), (('invoice', 'invoiced'), ('due', 'paid')),
    (('checkin', 'check_in'), ('checkout', 'check_out')), (('pickup', 'pick_up'), ('dropoff', 'drop_off')), (('departure', 'depart'), ('arrival', 'arrive')),
)


def _residual(name: str, words) -> str | None:
    for w in sorted(words, key=len, reverse=True):
        if w in name:
            return re.sub(r'[^a-z0-9]', '', name.replace(w, '', 1))
    return None


def _number(value):
    if value is None or str(value).strip() == '':
        return None
    return parse_mixed_number(str(value).strip(), 'us')


def cross_column_entries(rows: list[dict[str, Any]], columns: list[str], make_entry: MakeEntry) -> list[dict[str, Any]]:
    """Review-only notes where related columns disagree with each other."""
    out: list[dict[str, Any]] = []
    lower = {c: str(c).lower() for c in columns}
    date_cache: dict[str, Any] = {}

    def dates(c):
        if c not in date_cache:
            date_cache[c] = _column_dates(rows, c)
        return date_cache[c]

    seen_pairs: set[tuple[str, str]] = set()
    for a_words, b_words in _ORDERED_PAIRS:
        for a in columns:
            ra = _residual(lower[a], a_words)
            if ra is None:
                continue
            for b in columns:
                if a == b or (a, b) in seen_pairs or _residual(lower[b], b_words) != ra:
                    continue
                da, db = dates(a), dates(b)
                if not da or not db:
                    continue
                pairs = [(x, y) for x, y in zip(da, db) if x and y]
                bad = [(x, y) for x, y in pairs if x > y]
                if len(pairs) >= 5 and bad and len(bad) / len(pairs) >= 0.02:
                    seen_pairs.add((a, b))
                    out.append(make_entry(f"Review {len(bad)} of {len(pairs)} row(s) where '{a}' is later than '{b}' (e.g. {bad[0][0]:%Y-%m-%d} > {bad[0][1]:%Y-%m-%d}); the dates should normally run in the other order. No values are changed automatically", None, 0.55))

    def pick(words, avoid=()):
        found = [c for c in columns if set(_tokens(c)) & words and not set(_tokens(c)) & set(avoid)]
        return found[0] if len(found) == 1 else None

    qty = pick({'qty', 'quantity'})
    price = pick({'price', 'rate'}, {'total', 'tax'})
    total = pick({'total', 'amount', 'extended'}, {'tax', 'discount', 'sub', 'subtotal'})
    if qty and price and total:
        checked = bad = 0
        sample = []
        for r in rows:
            q, p, t = _number(r.get(qty)), _number(r.get(price)), _number(r.get(total))
            if None in (q, p, t):
                continue
            checked += 1
            if abs(q * p - t) > max(0.011, 0.005 * abs(t)):
                bad += 1
                sample.append(f"{q:g} x {p:g} != {t:g}")
        if checked >= 5 and bad and bad / checked <= 0.2:
            out.append(make_entry(f"Review {bad} of {checked} row(s) where '{qty}' x '{price}' does not equal '{total}' (e.g. {_ex(sample, 2)}); the rest of the rows agree. No values are changed automatically", None, 0.55))
    sub = pick({'subtotal'})
    tax = pick({'tax', 'vat'}, {'rate', 'id', 'exempt'})
    grand = pick({'total', 'grandtotal'}, {'sub', 'subtotal', 'tax'})
    if sub and tax and grand:
        ship = pick({'shipping', 'freight', 'delivery'})
        disc = pick({'discount'})
        best = None
        for label, parts in (('subtotal + tax', ((sub, 1), (tax, 1))),):
            variants = [parts]
            if ship:
                variants.append(parts + ((ship, 1),))
            if disc:
                variants.append(parts + ((disc, -1),))
            if ship and disc:
                variants.append(parts + ((ship, 1), (disc, -1)))
            for combo in variants:
                checked = bad = 0
                sample = []
                for r in rows:
                    vals = [_number(r.get(c)) for c, _ in combo]
                    g = _number(r.get(grand))
                    if g is None or None in vals:
                        continue
                    checked += 1
                    computed = sum(v * s for v, (_, s) in zip(vals, combo))
                    if abs(computed - g) > max(0.011, 0.005 * abs(g)):
                        bad += 1
                        sample.append(f"{computed:g} != {g:g}")
                if checked >= 5 and (best is None or bad / checked < best[0]):
                    best = (bad / checked, bad, checked, combo, sample)
        if best and best[1] and best[0] <= 0.2:
            formula = ' '.join(('+' if s > 0 else '-') + f" '{c}'" for c, s in best[3]).lstrip('+ ')
            out.append(make_entry(f"Review {best[1]} of {best[2]} row(s) where {formula} does not equal '{grand}' (e.g. {_ex(best[4], 2)}); the rest of the rows agree. No values are changed automatically", None, 0.55))
    full = pick({'fullname', 'name'}, {'first', 'last', 'user', 'company'})
    first = pick({'first', 'firstname', 'given', 'givenname'})
    last = pick({'last', 'lastname', 'surname', 'family'})
    if full and first and last:
        checked = bad = 0
        sample = []
        for r in rows:
            f, a, b = (str(r.get(c) or '').strip() for c in (full, first, last))
            if not (f and a and b):
                continue
            checked += 1
            if _norm_label(f"{a} {b}") != _norm_label(name_first_last(f)):
                bad += 1
                sample.append(f"{f} vs {a} {b}")
        if checked >= 5 and bad and bad / checked <= 0.2:
            out.append(make_entry(f"Review {bad} of {checked} row(s) where '{full}' does not match '{first}' + '{last}' (e.g. {_ex(sample, 2)}). No values are changed automatically", None, 0.5))
    return out


def fuzzy_row_entries(rows: list[dict[str, Any]], columns: list[str], make_entry: MakeEntry) -> list[dict[str, Any]]:
    """Rows that become identical once case, spacing and punctuation are ignored (exact duplicates are reported elsewhere)."""
    cols = [c for c in columns if not _FUZZY_SKIP.search(str(c)) or re.search(r'email|phone', str(c), re.I)]
    cols = [c for c in cols if not re.search(r'(^|_)(id|uuid|guid|index|row|seq|created|updated|timestamp)($|_)', str(c), re.I)]
    if len(cols) < 2 or len(rows) < 2:
        return []

    def cell(c, v):
        s = '' if v is None else str(v)
        return re.sub(r'\D', '', s) if re.search(r'phone|mobile|tel', str(c), re.I) else _norm_label(s)

    groups: dict[tuple, list[tuple]] = {}
    for r in rows:
        raw = tuple('' if r.get(c) is None else str(r.get(c)) for c in cols)
        groups.setdefault(tuple(cell(c, r.get(c)) for c in cols), []).append(raw)
    extra = [(k, g) for k, g in groups.items() if len(set(g)) > 1 and any(k)]
    if not extra:
        return []
    count = sum(len(set(g)) - 1 for _, g in extra)
    first_row = None
    sample = ' / '.join(repr(' '.join(v)[:60]) for v in list(dict.fromkeys(extra[0][1]))[:2])
    return [make_entry(f"Found {count} row(s) that repeat another row once case, spacing and punctuation are ignored (e.g. {sample}). Review them before removing anything; no rows are changed automatically", None, 0.5)]
