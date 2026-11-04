"""Turn a review-only suggestion into concrete, ready-to-use suggestions.

A review-only note has no recipe step because the right fix depends on the user's judgement.
Once the user chooses to review it, this module offers the plausible choices, each as an
ordinary suggestion with `json_steps` that the normal Use -> Generate -> Apply flow handles.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .engine import _read_table
from .standardize import _layouts_for, _split_zone

_QUOTED = re.compile(r"'([^']+)'")
_EMAIL_RE = r"[^\s@]+@[^\s@]+\.[^\s@]+"
_MAX_VALUES = 200


def _entry(text: str, steps: list[dict[str, Any]], category: str, column: str | None, confidence: float = 0.6) -> dict[str, Any]:
    return {
        "text": text,
        "source": "review_option",
        "category": category,
        "column": column,
        "columns": [column] if column else [],
        "confidence": confidence,
        "json_steps": steps,
        "json_step": steps[0],
    }


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, float) and value != value) or str(value).strip() == ""


def _is_numeric(series) -> bool:
    return bool(series.dtype.is_numeric())


def _is_temporal(series) -> bool:
    return bool(series.dtype.is_temporal())


def _column_options(df, col: str, text: str) -> list[dict[str, Any]]:
    values = df[col].to_list()
    total = len(values)
    missing = sum(1 for v in values if _blank(v))
    present = [v for v in values if not _blank(v)]
    options: list[dict[str, Any]] = []
    distinct = len({str(v) for v in present})
    series = df[col]

    if missing and present and not _is_temporal(series):
        if _is_numeric(series):
            ordered = sorted(float(v) for v in present)
            mid = ordered[len(ordered) // 2] if len(ordered) % 2 else (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2
            options.append(_entry(
                f"Fill the {missing} missing value(s) in '{col}' with the median ({mid:g})",
                [{"action": "impute", "column": col, "params": {"strategy": "median"}}], "fix", col))
            options.append(_entry(
                f"Fill the {missing} missing value(s) in '{col}' with 0",
                [{"action": "impute", "column": col, "params": {"strategy": "constant", "value": 0}}], "fix", col, 0.5))
        else:
            counted = Counter(str(v).strip() for v in present).most_common(2)
            unique_top = len(counted) == 1 or counted[0][1] > counted[1][1]
            common = f" ('{counted[0][0][:40]}')" if unique_top else ""
            options.append(_entry(
                f"Fill the {missing} missing value(s) in '{col}' with the most common value{common}",
                [{"action": "impute", "column": col, "params": {"strategy": "mode"}}], "fix", col))
            options.append(_entry(
                f"Fill the {missing} missing value(s) in '{col}' with 'Unknown'",
                [{"action": "impute", "column": col, "params": {"strategy": "constant", "value": "Unknown"}}], "fix", col, 0.5))
    if missing and present:
        options.append(_entry(
            f"Drop the {missing} row(s) where '{col}' is blank",
            [{"action": "drop_rows", "column": col, "params": {"issue": "blank"}}], "drop_rows", col, 0.4))
    if distinct <= 1 or (total and missing / total > 0.5):
        reason = "holds a single value" if distinct <= 1 and present else "is mostly empty"
        options.append(_entry(
            f"Drop column '{col}' (it {reason}); the column is removed from the output",
            [{"action": "drop_column", "column": col, "params": {}}], "remove", col, 0.4))
    if not options and present and not _is_numeric(series) and not _is_temporal(series):
        strings = [str(v) for v in present]
        if any(s != s.strip() for s in strings):
            options.append(_entry(f"Trim extra spaces around the values in '{col}'",
                                  [{"action": "normalize", "column": col, "params": {"case": "trim"}}], "fix", col))
        if any(s.strip() != s.strip().title() for s in strings):
            options.append(_entry(f"Show '{col}' in Title Case",
                                  [{"action": "normalize", "column": col, "params": {"case": "title"}}], "fix", col, 0.4))
    return options


def _key_options(df, col: str, text: str) -> list[dict[str, Any]]:
    if "repeated value" in text:
        return [_entry(
            f"Remove rows with a repeated '{col}', keeping the first row for each value",
            [{"action": "deduplicate", "column": col, "params": {}}], "remove", col, 0.5)]
    return [_entry(
        f"Drop the rows where '{col}' is blank",
        [{"action": "drop_rows", "column": col, "params": {"issue": "blank"}}], "drop_rows", col, 0.5)]


def _case_options(df, col: str, text: str) -> list[dict[str, Any]]:
    return [
        _entry(f"Show '{col}' in Title Case", [{"action": "normalize", "column": col, "params": {"case": "title"}}], "fix", col),
        _entry(f"Show '{col}' in UPPER CASE", [{"action": "normalize", "column": col, "params": {"case": "upper"}}], "fix", col, 0.5),
        _entry(f"Show '{col}' in lower case", [{"action": "normalize", "column": col, "params": {"case": "lower"}}], "fix", col, 0.4),
    ]


def _comma_options(df, col: str, text: str) -> list[dict[str, Any]]:
    cast = {"to": "float"}
    return [
        _entry(f"Read the comma in '{col}' as a thousands separator (3,975 becomes 3975)",
               [{"action": "regex_replace", "column": col, "params": {"pattern": ",", "replace": ""}},
                {"action": "cast", "column": col, "params": cast}], "fix", col),
        _entry(f"Read the comma in '{col}' as a decimal point (3,975 becomes 3.975)",
               [{"action": "regex_replace", "column": col, "params": {"pattern": ",", "replace": "."}},
                {"action": "cast", "column": col, "params": cast}], "fix", col, 0.5),
    ]


def _outlier_options(df, col: str, text: str) -> list[dict[str, Any]]:
    series = df[col]
    if not _is_numeric(series):
        return []
    nums = sorted(float(v) for v in series.to_list() if not _blank(v))
    if len(nums) < 8:
        return []
    q1, q3 = nums[len(nums) // 4], nums[(3 * len(nums)) // 4]
    spread = q3 - q1
    low, high = q1 - 1.5 * spread, q3 + 1.5 * spread
    flagged = [v for v in series.to_list() if not _blank(v) and not (low <= float(v) <= high)]
    if not flagged or len(flagged) > _MAX_VALUES:
        return []
    shown = ", ".join(sorted({str(v) for v in flagged}, key=len)[:3])
    return [_entry(
        f"Drop the {len(flagged)} row(s) where '{col}' is outside {low:g} to {high:g} (e.g. {shown})",
        [{"action": "drop_rows", "column": col, "params": {"issue": "values", "values": sorted({str(v) for v in flagged})}}],
        "drop_rows", col, 0.4)]


def _email_options(df, col: str, text: str) -> list[dict[str, Any]]:
    return [_entry(
        f"Drop the rows where '{col}' is not a valid email address",
        [{"action": "drop_rows", "column": col, "params": {"issue": "not_matches", "pattern": _EMAIL_RE}}], "drop_rows", col, 0.4)]


def _zip_text_options(df, col: str, text: str) -> list[dict[str, Any]]:
    return [_entry(
        f"Drop the rows where '{col}' is text with no digits (those values are not ZIP codes)",
        [{"action": "drop_rows", "column": col, "params": {"issue": "matches", "pattern": r"\D+"}}], "drop_rows", col, 0.4)]


def _day_first(layout: str) -> bool:
    return re.match(r"%d[/.\-]%m", layout) is not None


def _month_first(layout: str) -> bool:
    return re.match(r"%m[/.\-]%d", layout) is not None


def _date_options(df, col: str, text: str) -> list[dict[str, Any]]:
    raw = [str(v).strip() for v in df[col].to_list() if not _blank(v)][:5000]
    if len(raw) < 2:
        return []
    zones = [_split_zone(v) for v in raw]
    found = [_layouts_for(body) for body, _, _ in zones]
    if sum(1 for f in found if f) / len(raw) < 0.9:
        return []
    counts = Counter(layout for f in found for layout in f)
    has_zone = any(z for _, _, z in zones)
    timed = any("%H" in layout or "%I" in layout for layout in counts)
    targets = []
    if timed:
        targets.append(("%Y-%m-%d %H:%M:%S" + ("@utc" if has_zone else ""),
                        "YYYY-MM-DD HH:MM:SS" + (" converted to UTC" if has_zone else "")))
    targets.append(("%Y-%m-%d" + ("@utc" if has_zone else ""), "YYYY-MM-DD" + (" (the UTC date)" if has_zone else "")))
    by_order = {
        "month": ([l for l in counts if not _day_first(l)], "reading numeric dates as month/day"),
        "day": ([l for l in counts if not _month_first(l)], "reading numeric dates as day/month"),
    }
    ambiguous = by_order["month"][0] != by_order["day"][0]
    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    for order in ("month", "day"):
        layouts = sorted(by_order[order][0], key=lambda l: -counts[l])
        if not layouts:
            continue
        for target, label in targets:
            step = {"action": "cast", "column": col, "params": {"to": "datetime", "fmt": "|".join(layouts) + "=>" + target}}
            key = repr(step)
            if key in seen:
                continue
            seen.add(key)
            suffix = f", {by_order[order][1]}" if ambiguous else ""
            options.append(_entry(f"Standardize '{col}' to {label}{suffix}", [step], "fix", col, 0.6 if order == "month" else 0.5))
    return options

def review_options(path: str, text: str, column: str | None = None) -> list[dict[str, Any]]:
    """Concrete suggestions for one review-only note (empty list when none can be offered safely)."""
    df = _read_table(path)
    names = list(df.columns)
    if column not in names:
        column = next((q for q in _QUOTED.findall(text) if q in names), None)
    if column is None:
        return []
    lowered = text.lower()
    if lowered.startswith("review column"):
        found = _column_options(df, column, text)
    elif lowered.startswith("potential key field"):
        found = _key_options(df, column, text)
    elif "differ by case" in lowered:
        found = _case_options(df, column, text)
    elif "comma-decimal" in lowered:
        found = _comma_options(df, column, text)
    elif "statistical outlier" in lowered:
        found = _outlier_options(df, column, text)
    elif "email-shaped" in lowered:
        found = _email_options(df, column, text)
    elif "no automatic transfer" in lowered:
        found = _zip_text_options(df, column, text)
    elif "iso format" in lowered or ("date" in lowered and "ambiguous" in lowered):
        found = _date_options(df, column, text)
    elif lowered.startswith("review"):
        found = _column_options(df, column, text)
    else:
        found = []
    return found
