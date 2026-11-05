"""Turn a review-only suggestion into concrete, ready-to-use suggestions.

A review-only note has no recipe step because the right fix depends on the user's judgement.
Once the user chooses to review it, this module offers the plausible choices, each as an
ordinary suggestion with `json_steps` that the normal Use -> Generate -> Apply flow handles.
"""
from __future__ import annotations

import csv
import os
import re
import tempfile
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



# ---------------------------------------------------------------- reuse of the normal suggestion generators

_SAMPLE = 5000
_SIMULATE_ROWS = 100_000
_cleaner = None


def _effect(col: str, values: list, steps: list[dict[str, Any]]) -> tuple[int, int, int]:
    """Run the steps on the whole column; returns (values changed, non-blank values emptied, rows dropped)."""
    global _cleaner
    from .engine import Cleaner
    if _cleaner is None:
        _cleaner = Cleaner()
    values = values[:_SIMULATE_ROWS]
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "column.csv")
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([col])
            for v in values:
                writer.writerow(["" if _blank(v) else str(v)])
        preview = _cleaner.preview_recipe({"sources": [{"path": path}], "cleaning_steps": steps}, n=0)
    before, after = preview.get("before") or [], preview.get("after") or []
    if len(before) != len(after):
        return 0, 0, len(before) - len(after)
    changed = emptied = 0
    for b, a in zip(before, after):
        old, new = b.get(col), a.get(col)
        if str(old if old is not None else "") != str(new if new is not None else ""):
            changed += 1
            if not _blank(old) and _blank(new):
                emptied += 1
    return changed, emptied, 0


def _subsets(vals: list[str]) -> dict[str, list[str]]:
    """Groups of values that fit one pattern, so the normal generators see a clean column."""
    from .ingest import _classify_cell, _value_shape, _DATE_FORMATS, _parses_with
    kinds = [_classify_cell(s) for s in vals]
    groups: dict[str, list[str]] = {}
    groups["numbers"] = [s for s, (k, _, _) in zip(vals, kinds) if k in ("numeric", "formatted", "affix")]
    for unit in {x for k, _, x in kinds if k == "affix"}:
        groups[f"unit:{unit}"] = [s for s, (k, _, x) in zip(vals, kinds) if k in ("numeric", "formatted") or (k == "affix" and x == unit)]
    groups["dates"] = [s for s in vals if any(p.fullmatch(s) and _parses_with(s, f) for f, p in _DATE_FORMATS)]
    for shape, count in Counter(_value_shape(s) for s in vals).most_common(3):
        if count >= 3:
            groups[f"shape:{shape}"] = [s for s in vals if _value_shape(s) == shape]
    return {k: v for k, v in groups.items() if 2 <= len(v) < len(vals)}


def _extra_options(col: str, vals: list[str]) -> list[dict[str, Any]]:
    from .ingest import _classify_cell
    n = len(vals)
    kinds = [_classify_cell(s)[0] for s in vals]
    options: list[dict[str, Any]] = []
    numberish = sum(1 for k in kinds if k in ("numeric", "formatted", "affix"))
    if numberish and numberish / n >= 0.5 and numberish < n:
        options.append(_entry(
            f"Drop the rows where '{col}' is not a number",
            [{"action": "drop_rows", "column": col, "params": {"issue": "not_numeric"}}], "drop_rows", col, 0.4))
    stray = [s for s, k in zip(vals, kinds) if k == "text" and re.search(r"\d", s) and re.fullmatch(r"[A-Za-z]{1,3}\d[\d.,]*|\d[\d.,]*[A-Za-z]{1,3}", s)]
    if stray and (numberish + len(stray)) / n >= 0.5:
        shown = ", ".join(repr(s) for s in list(dict.fromkeys(stray))[:3])
        letters = {"action": "regex_replace", "column": col, "params": {"pattern": r"[A-Za-z]+", "replace": ""}}
        options.append(_entry(f"Remove stray letters from the numbers in '{col}' ({len(stray)} values like {shown})", [letters], "fix", col, 0.5))
        options.append(_entry(f"Remove stray letters from '{col}' ({len(stray)} values like {shown}), then convert it to a number",
                              [letters, {"action": "cast", "column": col, "params": {"to": "float"}}], "fix", col, 0.45))
    day_month = [s for s in vals if re.fullmatch(r"\d{1,2}[- ][A-Za-z]{3,9}", s)]
    if len(day_month) >= max(2, n // 2):
        options.append(_entry(
            f"Rewrite the day-month values in '{col}' (e.g. {day_month[0]}) as month then day (Sep 30); the values have no year, so they stay text",
            [{"action": "regex_replace", "column": col, "params": {"pattern": r"^(\d{1,2})[- ]([A-Za-z]{3})[A-Za-z]*$", "replace": r"\2 \1"}}],
            "fix", col, 0.5))
    return options


def _relaxed_options(df, col: str) -> list[dict[str, Any]]:
    """Fix suggestions the generators would make if the column were cleaner, built from the values that fit."""
    from .ingest import _analyze_column
    values = df[col].to_list()
    vals = [str(v).strip() for v in values if not _blank(v)][:_SAMPLE]
    if len(vals) < 2:
        return []
    candidates: list[dict[str, Any]] = []
    for subset in _subsets(vals).values():
        try:
            result = _analyze_column(col, subset)
        except Exception:
            continue
        for entry in (result or {}).get("entries", []):
            steps = entry.get("json_steps") or []
            if steps and not any(s.get("action") == "drop_rows" for s in steps):
                candidates.append(_entry(entry["text"], steps, "fix", col, min(float(entry.get("confidence") or 0.5), 0.7)))
    candidates.extend(_extra_options(col, vals))
    kept: list[dict[str, Any]] = []
    for option in candidates:
        if any(s.get("action") == "drop_column" for s in option["json_steps"]):
            # Removing a column leaves no column to compare, and it never removes rows.
            kept.append(option)
            continue
        try:
            changed, emptied, dropped = _effect(col, values, option["json_steps"])
        except Exception:
            continue
        if not (changed or dropped):
            continue
        if dropped:
            option["text"] += f" (removes {dropped} row(s))"
        else:
            tail = f"changes {changed} value(s)" + (f" and empties {emptied} that do not fit" if emptied else "")
            option["text"] += f" [on the whole column this {tail}]"
        kept.append(option)
    return kept


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
    if not df[column].dtype.is_temporal():
        found = found + _relaxed_options(df, column)
    unique: dict[str, dict[str, Any]] = {}
    for option in found:
        unique.setdefault(repr(option["json_steps"]), option)
    return list(unique.values())