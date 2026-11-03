"""Suggestions that bring one column's mixed layouts of the same kind of value to a single layout the user picks.

Each target layout is offered as its own alternative, so choosing a suggestion is choosing the layout. They are built
from ordinary steps; mixed dates use a `cast` whose `fmt` is "source|source=>target" (see `engine._multi_dates`).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable

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


BOOLEAN_TARGETS = (
    ('true', 'false', 'true/false'),
    ('Yes', 'No', 'Yes/No'),
    ('Y', 'N', 'Y/N'),
    ('1', '0', '1/0'),
)


def boolean_choice_entries(col: str, mapping: dict[str, str], make_entry: Callable[..., dict[str, Any]]) -> list[dict[str, Any]]:
    """Alternatives to the true/false mapping, offered only when the column mixes spellings of yes and no."""
    spellings = {raw.strip().lower() for raw in mapping}
    if len(spellings) < 3:
        return []
    entries = []
    for yes, no, label in BOOLEAN_TARGETS[1:]:
        chosen = {raw: (yes if polarity == 'true' else no) for raw, polarity in mapping.items()}
        entries.append(make_entry(f"'{col}' mixes spellings of yes and no; standardize every value to {label}", None, 0.7,
                                  [{'action': 'map', 'column': col, 'params': {'mapping': chosen}}]))
    return entries
