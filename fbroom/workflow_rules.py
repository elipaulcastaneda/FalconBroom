"""Deterministic, rules-based workflow helpers for FalconBroom.

These helpers intentionally cover the common, explainable cases without AI:
- map simple English instructions to recipe steps
- infer likely columns from data profiles and column names
- suggest join keys and fuzzy matching rules from schema overlap
- explain underspecified steps by filling in defaults and rationale

Anything beyond these rules is where AI becomes useful rather than required.
"""

from __future__ import annotations

import re
import os
import csv
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .recipe_schema import CleaningStep, JoinSpec, Recipe


ACTION_ALIASES = {
    "drop_column": ["drop", "remove", "delete", "exclude"],
    "impute": ["fill", "impute", "replace missing", "fill missing", "populate missing"],
    # single, consolidated normalize alias set (include common case/trim variants)
    "normalize": ["normalize", "standardize", "lowercase", "uppercase", "trim", "strip", "clean up", "capitalize", "capitalise", "capitalized", "title case", "titlecase", "proper case"],
    "replace": ["replace", "substitute", "swap"],
    "map": ["map", "map values", "remap", "translate"],
    "regex_replace": ["regex", "regular expression", "regex replace", "replace regex"],
    "bucketize": ["bucketize", "bucket", "bin", "bucketize column", "binning"],
    "conditional": ["if", "when", "conditional", "set when"],
    "fuzzy_join": ["fuzzy join", "fuzzy match", "approximate join", "fuzzy merge"],
    "deduplicate": ["deduplicate", "dedupe", "remove duplicates", "duplicate"],
    "rename": ["rename", "alias", "change name"],
    "join": ["join", "merge", "match", "combine"],
}

COLUMN_KEYWORDS = {
    "email": ["email", "e-mail", "mail"],
    "phone": ["phone", "mobile", "cell", "tel", "telephone"],
    "name": ["name", "full name", "first name", "last name", "customer", "contact"],
    "address": ["address", "street", "city", "state", "zip", "postal"],
    "id": ["id", "identifier", "key", "uuid", "sku", "order", "customer id", "product id"],
    "date": ["date", "time", "timestamp", "created", "updated"],
    "amount": ["amount", "price", "total", "revenue", "cost", "value"],
}

DEFAULT_REPAIR_STRATEGIES = {
    "numeric": "median",
    "string": "empty_string",
    "boolean": "false",
    "date": "forward_fill",
}


@dataclass
class ExplainedStep:
    step: CleaningStep
    reason: str
    confidence: float


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _profile_column_names(profile: Dict[str, Dict[str, Any]]) -> List[str]:
    return list(profile.keys())


def _match_score(haystack: str, needle: str) -> float:
    haystack = _norm(haystack)
    needle = _norm(needle)
    if not haystack or not needle:
        return 0.0
    if haystack == needle:
        return 1.0
    if needle in haystack:
        return 0.95
    return SequenceMatcher(None, haystack, needle).ratio()


def infer_action(text: str) -> Optional[str]:
    t = _norm(text)
    # If instruction contains sequential clauses joined by 'then' or ';', parse each clause in order
    try:
        parts = [p.strip() for p in re.split(r'(?i)\bthen\b|;', text) if p and p.strip()]
        if len(parts) > 1:
            all_steps: List[CleaningStep] = []
            for p in parts:
                print('DEBUG: parsing clause ->', repr(p))
                parsed = False
                # 1) typed move with optional 'non-' prefix: e.g. "move all non-numerical values from postal_code to city"
                # allow optional 'all' and optional 'values/entries' so phrasing like
                # 'move non-numerical values from X to Y' or 'move non-numerical from X to Y'
                # accept a wide range of clarifying type words so users can say
                # 'numeric', 'number', 'digits', 'string', 'text', 'letters', 'alpha', 'alphabetic', 'alphanumeric', 'chars', 'characters', 'date', etc.
                m_typed = re.search(r"(?i)(?:move|put|copy)\s+(?:all\s+)?(?P<neg>non[-\s]?|not\s+)?(?P<type>numeric|numerical|number|numbers|digits|string|text|letters|alpha|alphabetic|alphanumeric|chars|characters|date|dates)\s*(?:values|entries)?\s*(?:in|from|of)?\s*(?P<src>[A-Za-z0-9_]+)\s*(?:column)?\s*(?:to|into|in)\s*(?P<tgt>[A-Za-z0-9_]+)", p)
                if m_typed:
                    neg = bool(m_typed.group('neg'))
                    typ = m_typed.group('type').lower()
                    src = m_typed.group('src')
                    tgt = m_typed.group('tgt')
                    # invert type on negation (non-numerical => string)
                    if neg and typ in ('numeric', 'numerical', 'number', 'numbers'):
                        typ_out = 'string'
                    elif neg and typ in ('string', 'text', 'letters'):
                        typ_out = 'numeric'
                    else:
                        typ_out = 'numeric' if typ in ('numeric', 'numerical', 'number', 'numbers') else 'string'
                    params = {"source": src, "target": tgt, "type": typ_out, "exceptions": [], "replacement": ""}
                    all_steps.append(CleaningStep(action="move_by_type", column=None, params=params))
                    parsed = True

                # 2) move all values (no type) -> emit two moves to capture numeric and string variants
                if not parsed:
                    # accept variants like 'move values from A to B', 'move A to B', or 'move all values from A to B'
                    m_all = re.search(r"(?i)(?:move|put|copy)\s+(?:all\s+)?(?:values\s*)?(?:in|from|of)?\s*(?P<src>[A-Za-z0-9_]+)\s*(?:column)?\s*(?:to|into|in)\s*(?P<tgt>[A-Za-z0-9_]+)", p)
                    if m_all:
                        src = m_all.group('src')
                        tgt = m_all.group('tgt')
                        all_steps.append(CleaningStep(action="move_by_type", column=None, params={"source": src, "target": tgt, "type": "string", "exceptions": [], "replacement": ""}))
                        all_steps.append(CleaningStep(action="move_by_type", column=None, params={"source": src, "target": tgt, "type": "numeric", "exceptions": [], "replacement": ""}))
                        parsed = True

                # 3) drop column shorthand: 'drop col_6' or 'drop column col_6'
                if not parsed:
                    m_drop = re.search(r"(?i)drop\s+(?:column\s+)?(?P<col>[A-Za-z0-9_]+)", p)
                    if m_drop:
                        col = m_drop.group('col')
                        all_steps.append(CleaningStep(action="drop_column", column=col, params={}))
                        parsed = True

                # 4) fallback: attempt to parse the clause recursively
                if not parsed:
                    try:
                        sub = recipe_from_plain_english(p, profile, source_path, output_path, regression_options)
                        if sub and getattr(sub, 'cleaning_steps', None):
                            all_steps.extend([s for s in sub.cleaning_steps])
                    except Exception:
                        continue

            if all_steps:
                print('DEBUG: combined steps count ->', len(all_steps))
                # ensure steps are plain dicts for Pydantic consumption
                serial_steps = []
                for s in all_steps:
                    try:
                        if hasattr(s, 'model_dump'):
                            serial_steps.append(s.model_dump())
                        elif hasattr(s, 'dict'):
                            serial_steps.append(s.dict())
                        else:
                            serial_steps.append(dict(s))
                    except Exception:
                        try:
                            serial_steps.append(dict(s))
                        except Exception:
                            # fallback: minimal representation
                            serial_steps.append({ 'action': getattr(s, 'action', None), 'column': getattr(s, 'column', None), 'params': getattr(s, 'params', {}) })
                print('DEBUG: serial_steps ->', serial_steps)
                # Return the serializable step list to the caller for recipe construction
                return serial_steps
    except Exception:
        pass
    # fallback: if text contains common normalize tokens, prefer normalize action
    try:
        if any(k in t for k in ["normalize", "lower", "uppercase", "lowercase", "trim", "strip", "capitalize", "title"]):
            return "normalize"
    except Exception:
        pass
    try:
        t = re.sub(r"non[- ]?numeric|non[- ]?numerical|non ?numeric", "string", t, flags=re.IGNORECASE)
    except Exception:
        pass

    # match aliases as whole words/phrases to avoid accidental substring matches
    for action, aliases in ACTION_ALIASES.items():
        for alias in aliases:
            try:
                if re.search(r"\b" + re.escape(alias) + r"\b", t, flags=re.IGNORECASE):
                    return action
            except Exception:
                if alias in t:
                    return action
    return None


def infer_columns_from_text(text: str, profile: Dict[str, Dict[str, Any]], top_n: int = 3, heuristics: Optional[Dict[str, float]] = None) -> List[Tuple[str, float, str]]:
    """Return likely columns from a free-form instruction and column profile.

    Output is a list of (column_name, score, reason).
    """
    t = _norm(text)
    scored: List[Tuple[str, float, str]] = []
    # heuristics may override component weights
    heur = heuristics or {}
    name_weight = float(heur.get("name_weight", 0.7))
    keyword_weight = float(heur.get("keyword_weight", 0.6))
    nulls_scale = float(heur.get("nulls_scale", 1.0))
    unique_scale = float(heur.get("unique_scale", 1.0))

    for column, meta in profile.items():
        score = 0.0
        reason_parts: List[str] = []

        column_score = _match_score(t, column)
        if column_score > 0.5:
            score += column_score * name_weight
            reason_parts.append(f"instruction mentions '{column}'")

        for token_group, keywords in COLUMN_KEYWORDS.items():
            if any(k in t for k in keywords):
                if any(k in _norm(column) for k in keywords):
                    score += keyword_weight
                    reason_parts.append(f"column name matches {token_group} keywords")
                    break

        nulls = int(meta.get("nulls", 0) or 0)
        unique = int(meta.get("unique", 0) or 0)
        dtype = _norm(str(meta.get("dtype", "")))

        if nulls > 0 and any(k in t for k in ["missing", "blank", "empty", "null", "fill", "impute"]):
            score += min(0.6, 0.1 + nulls / 10_000) * nulls_scale
            reason_parts.append("column has missing values")
        if unique <= 1 and any(k in t for k in ["constant", "same", "duplicate", "remove"]):
            score += 0.35 * unique_scale
            reason_parts.append("column is constant or near-constant")
        if any(k in t for k in ["lowercase", "uppercase", "trim", "normalize", "standardize"]):
            if "str" in dtype or "utf" in dtype or "object" in dtype:
                score += 0.25
                reason_parts.append("string cleanup requested")

        if score > 0:
            scored.append((column, round(min(score, 1.0), 3), "; ".join(reason_parts) or "heuristic match"))

    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:top_n]


def suggest_columns_to_clean(profile: Dict[str, Dict[str, Any]], action: Optional[str] = None, top_n: int = 5) -> List[Tuple[str, float, str]]:
    """Suggest columns that should likely be cleaned even when the user is vague."""
    scored: List[Tuple[str, float, str]] = []
    for column, meta in profile.items():
        score = 0.0
        reasons: List[str] = []
        nulls = int(meta.get("nulls", 0) or 0)
        unique = int(meta.get("unique", 0) or 0)
        dtype = _norm(str(meta.get("dtype", "")))

        if nulls > 0:
            score += min(0.7, 0.1 + nulls / 1000)
            reasons.append(f"{nulls} nulls")
        if unique == 1:
            score += 0.4
            reasons.append("constant value")
        if any(k in column.lower() for k in ["email", "phone", "name", "address", "city", "state"]):
            score += 0.35
            reasons.append("common text column")
        if action == "normalize" and ("str" in dtype or "utf" in dtype or "object" in dtype):
            score += 0.25
            reasons.append("string normalization likely")
        if action == "impute" and nulls > 0:
            score += 0.25
            reasons.append("imputation target")
        if action == "deduplicate" and any(k in column.lower() for k in ["id", "email", "sku", "order"]):
            score += 0.3
            reasons.append("identity column for dedupe")

        if score > 0:
            scored.append((column, round(min(score, 1.0), 3), "; ".join(reasons)))

    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:top_n]


def _default_strategy_for_column(meta: Dict[str, Any]) -> str:
    dtype = _norm(str(meta.get("dtype", "")))
    if any(token in dtype for token in ["int", "float", "double", "decimal", "numeric"]):
        return DEFAULT_REPAIR_STRATEGIES["numeric"]
    if any(token in dtype for token in ["bool", "boolean"]):
        return DEFAULT_REPAIR_STRATEGIES["boolean"]
    if any(token in dtype for token in ["date", "time", "timestamp"]):
        return DEFAULT_REPAIR_STRATEGIES["date"]
    return DEFAULT_REPAIR_STRATEGIES["string"]


def recipe_from_plain_english(text: str, profile: Dict[str, Dict[str, Any]], source_path: str, output_path: str, regression_options: Optional[dict] = None) -> Recipe:
    """Map a plain-English instruction into a deterministic recipe when possible."""
    action = infer_action(text)
    # If infer_action returned a pre-serialized list of steps for multi-clause inputs, build Recipe
    try:
        if isinstance(action, list):
            try:
                return Recipe.model_validate({"sources": [{"path": source_path}], "cleaning_steps": action, "joins": [], "outputs": [{"path": output_path}]})
            except Exception:
                return Recipe(sources=[{"path": source_path}], cleaning_steps=action, joins=[], outputs=[{"path": output_path}])
    except Exception:
        pass
    # detect any explicit column names mentioned in the instruction (exact whole-word matches)
    t = _norm(text)
    # helper: parse condition text into engine condition dicts
    def _parse_condition_text(cond_text: str):
        s = cond_text.strip()
        if not s:
            return None
        # split on top-level ' or ' then ' and '
        if re.search(r"\s+or\s+", s, flags=re.IGNORECASE):
            parts = [p.strip() for p in re.split(r"\s+or\s+", s, flags=re.IGNORECASE) if p.strip()]
            return {"op": "or", "conds": [_parse_condition_text(p) for p in parts]}
        if re.search(r"\s+and\s+", s, flags=re.IGNORECASE):
            parts = [p.strip() for p in re.split(r"\s+and\s+", s, flags=re.IGNORECASE) if p.strip()]
            return {"op": "and", "conds": [_parse_condition_text(p) for p in parts]}
        # not prefix
        mnot = re.match(r"^not\s+(.+)$", s, flags=re.IGNORECASE)
        if mnot:
            inner = mnot.group(1).strip()
            return {"op": "not", "cond": _parse_condition_text(inner)}
        # comparison
        m = re.search(r"([a-zA-Z0-9_\.]+)\s*(>=|<=|==|=|!=|<>|>|<)\s*([\'\"\w\-\.]+)", s)
        if m:
            col, op, val = m.groups()
            # try numeric
            try:
                if re.match(r"^-?\d+\.\d+$", val):
                    v = float(val)
                elif re.match(r"^-?\d+$", val):
                    v = int(val)
                else:
                    v = re.sub(r"^[\'\"]|[\'\"]$", "", val)
            except Exception:
                v = re.sub(r"^[\'\"]|[\'\"]$", "", val)
            return {"column": col, "op": op, "value": v}
        # contains / in
        m2 = re.search(r"([a-zA-Z0-9_\.]+)\s+contains\s+[\'\"]?(.+?)[\'\"]?$", s, flags=re.IGNORECASE)
        if m2:
            col, val = m2.groups()
            return {"column": col, "op": "contains", "value": val}
        m3 = re.search(r"([a-zA-Z0-9_\.]+)\s+in\s*\(?([a-zA-Z0-9_\',\s]+)\)?", s, flags=re.IGNORECASE)
        if m3:
            col, lst = m3.groups()
            parts = [re.sub(r"^[\'\"]|[\'\"]$", "", p.strip()) for p in re.split(r",|\band\b", lst) if p.strip()]
            vals = []
            for p in parts:
                try:
                    if re.match(r"^-?\d+\.\d+$", p):
                        vals.append(float(p))
                    elif re.match(r"^-?\d+$", p):
                        vals.append(int(p))
                    else:
                        vals.append(p)
                except Exception:
                    vals.append(p)
            return {"column": col, "op": "in", "value": vals}
        # fallback: no parse
        return None
    mentioned: List[str] = []
    for column in _profile_column_names(profile):
        try:
            if re.search(rf"\b{re.escape(column)}\b", text, flags=re.IGNORECASE):
                mentioned.append(column)
        except Exception:
            continue

    # Resolve colloquial/implicit names to actual profile columns using fuzzy inference
    def _resolve_name(name: str) -> str:
        if not name:
            return name
        if name in profile:
            return name
        # handle common colloquial synonyms
        try:
            nlow = name.lower()
            # map common postal synonyms to columns containing 'postal' or 'zip' or 'post'
            postal_syns = ['zip', 'zip code', 'postcode', 'postal', 'postal code', 'zip_code']
            for syn in postal_syns:
                if syn in nlow:
                    # prefer profile columns that contain 'postal' or 'zip' or 'post'
                    for col in profile.keys():
                        lowc = col.lower()
                        if 'postal' in lowc or 'zip' in lowc or 'post' in lowc:
                            return col
        except Exception:
            pass
        # try fuzzy inference from the text fragment
        try:
            matches = infer_columns_from_text(name, profile, top_n=1)
            if matches:
                return matches[0][0]
        except Exception:
            pass
        return name

    resolved_mentioned = [_resolve_name(m) for m in mentioned]

    candidates = infer_columns_from_text(text, profile, top_n=5)
    suggested_columns = [column for column, _, _ in candidates]
    # prefer explicitly mentioned columns when available
    if mentioned:
        suggested_columns = mentioned + [c for c in suggested_columns if c not in mentioned]

    # Pre-check: explicit 'first letter' capitalization requests (catch common phrasings)
    try:
        cap_match = re.search(r"first\s+letter|only\s+the\s+first|first\s+character|first\s+char|capitalize\s+first|first\s+letter\s+capital", t, flags=re.IGNORECASE)
        if cap_match or ("first" in t and ("uppercase" in t or "capital" in t)):
            # build preview columns from reconstructed table (if available)
            preview_cols = []
            try:
                from .engine import _read_table, Cleaner
                df_raw = _read_table(source_path)
                recon = Cleaner()._reconstruct_table_from_df(df_raw, offset=0, limit=1)
                if recon and isinstance(recon, list) and len(recon) > 0:
                    preview_cols = [k for k in recon[0].keys()]
            except Exception:
                preview_cols = []

            # choose explicit mentioned column if present
            col = resolved_mentioned[0] if resolved_mentioned else (suggested_columns[0] if suggested_columns else None)
            # if chosen col isn't in preview/header, try to pick a suggested column that is present in preview
            if col and preview_cols and col not in preview_cols:
                found = None
                for c in suggested_columns:
                    if c in preview_cols:
                        found = c
                        break
                if found:
                    col = found
                else:
                    # prefer username-like keys from preview when available
                    for k in preview_cols:
                        lk = k.lower()
                        if 'username' == lk or lk == 'user' or 'user' in lk or ('name' in lk and not any(x in lk for x in ('first','last','firstname','lastname'))):
                            col = k
                            break
            # fallback: try fuzzy inference for common username-like columns
            if not col:
                try:
                    inferred = infer_columns_from_text('username', profile, top_n=1)
                    if inferred:
                        col = inferred[0][0]
                except Exception:
                    # heuristic scan for 'user'/'name' tokens in profile keys
                    for c in profile.keys():
                        lowc = c.lower()
                        if 'user' in lowc or ('name' in lowc and 'first' not in lowc and 'last' not in lowc):
                            col = c
                            break
            # check actual file header: if chosen col isn't in the CSV header, prefer a username-like header
            try:
                with open(source_path, newline='', encoding='utf-8') as fh:
                    reader = csv.reader(fh)
                    hdr = next(reader, None)
                    if hdr:
                        hdr_norm = [h.strip().lower() for h in hdr]
                        if col and col.lower() not in hdr_norm:
                            for i, h in enumerate(hdr_norm):
                                if 'user' in h or ('name' in h and not any(x in h for x in ('first','last','firstname','lastname'))):
                                    col = hdr[i]
                                    break
            except Exception:
                pass
            # If still no column or the chosen column isn't in the profile, try reading the source CSV header
            if (not col) or (col not in profile):
                try:
                    with open(source_path, newline='', encoding='utf-8') as fh:
                        reader = csv.reader(fh)
                        hdr = next(reader, None)
                        if hdr:
                            # map normalized header -> original
                            hdr_map = { re.sub(r'[^a-z0-9]', '', h.lower()): h for h in hdr }
                            for norm_h, orig_h in hdr_map.items():
                                if norm_h == 'username' or norm_h == 'user' or 'user' in norm_h or ('name' in norm_h and not any(x in norm_h for x in ('firstname','lastname','first','last'))):
                                    col = orig_h
                                    break
                except Exception:
                    pass
            # If still no column, try reconstructing a wide table (preview) to detect surfaced keys like 'username'
            if (not col) or (col not in profile):
                try:
                    # local import to avoid circular at module import time
                    from .engine import _read_table, Cleaner
                    df_raw = _read_table(source_path)
                    recon = Cleaner()._reconstruct_table_from_df(df_raw, offset=0, limit=1)
                    if recon and isinstance(recon, list) and len(recon) > 0:
                        first = recon[0]
                        # prefer exact 'username' key, else look for 'user'/'name' heuristics
                        if 'username' in first:
                            col = 'username'
                        else:
                            for k in first.keys():
                                lk = k.lower()
                                if 'user' in lk or ('name' in lk and not any(x in lk for x in ('first','last','firstname','lastname'))):
                                    col = k
                                    break
                except Exception:
                    pass
            if col:
                steps.append(CleaningStep(action="normalize", column=col, params={"case": "capitalize"}))
                return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])
    except Exception:
        pass

    steps: List[CleaningStep] = []
    # Special-case: "remove the '-' character from X column" should be a regex_replace,
    # not a column drop. Handle quoted or unquoted dash/hyphen/minus/dash-word forms.
    try:
        m_remove_char = re.search(r"remove\s+(?:the\s+)?['\"]?([-–—]|dash|hyphen|minus)['\"]?\s+(?:character\s+)?(?:from|in|of)\s+(?:the\s+)?([a-zA-Z0-9_ ]+)(?:\s+column)?", text, flags=re.IGNORECASE)
        if m_remove_char:
            char = m_remove_char.group(1)
            col_raw = m_remove_char.group(2).strip()
            col = _resolve_name(col_raw)
            # map common words to literal characters
            if char.lower() in ('dash', 'hyphen'):
                pat = r"-"
            elif char.lower() == 'minus':
                pat = r"-"
            else:
                # normalize unicode dashes to simple hyphen in the pattern
                pat = re.escape(char)
            steps.append(CleaningStep(action="regex_replace", column=col, params={"pattern": pat, "replace": ""}))
            return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])
    except Exception:
        pass

    # Special-case: "remove any letters" or "remove letters" in a column -> regex_replace letters
    try:
        m_remove_letters = re.search(r"remove\s+(?:any\s+)?(?:letters|alphabetic(?:\s+characters)?)\s+(?:in|from|of)\s+(?:the\s+)?([a-zA-Z0-9_ ]+)(?:\s+column)?", text, flags=re.IGNORECASE)
        if m_remove_letters:
            col_raw = m_remove_letters.group(1).strip()
            col = _resolve_name(col_raw)
            # remove contiguous ASCII letters; keep digits, punctuation, decimals
            steps.append(CleaningStep(action="regex_replace", column=col, params={"pattern": r"[A-Za-z]+", "replace": ""}))
            return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])
    except Exception:
        pass
    # Detect numeric transform requests like making negatives positive or rounding
    try:
        m_neg = re.search(r"make\s+(?:all\s+)?negative\s+(?:number\s+)?values\s+(?:in|of)\s+(?:the\s+)?([a-zA-Z0-9_ ]+)(?:\s+column)?\s+positive", text, flags=re.IGNORECASE)
        if not m_neg:
            m_neg = re.search(r"turn\s+(?:all\s+)?negative\s+(?:number\s+)?values\s+(?:in|of)\s+(?:the\s+)?([a-zA-Z0-9_ ]+)(?:\s+column)?\s+positive", text, flags=re.IGNORECASE)
        if m_neg:
            col_raw = m_neg.group(1).strip()
            col = _resolve_name(col_raw)
            steps.append(CleaningStep(action="numeric_transform", column=col, params={"operation": "abs"}))
            return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])

        m_round = re.search(r"round\s+(?:the\s+)?([a-zA-Z0-9_ ]+?)\s+(?:column\s*)?(?:to\s+the\s+nearest|to)\s+(?:the\s+)?(?:(\d+)\s+decimal\s+places|(tenths|hundredths|thousandths|ones|tens|hundreds|thousands|millions|billions|millionths|billionths))", text, flags=re.IGNORECASE)
        if m_round:
            col_raw = m_round.group(1).strip()
            col = _resolve_name(col_raw)
            ndigits = None
            if m_round.group(2):
                try:
                    ndigits = int(m_round.group(2))
                except Exception:
                    ndigits = None
            else:
                unit = (m_round.group(3) or "").lower()
                unit_map = {
                    'tenths': 1,
                    'hundredths': 2,
                    'thousandths': 3,
                    'ones': 0,
                    'tens': -1,
                    'hundreds': -2,
                    'thousands': -3,
                    'millions': -6,
                    'billions': -9,
                    'millionths': 6,
                    'billionths': 9,
                }
                ndigits = unit_map.get(unit, None)
            if ndigits is None:
                ndigits = 0
            steps.append(CleaningStep(action="numeric_transform", column=col, params={"operation": "round", "ndigits": ndigits}))
            return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])
    except Exception:
        pass
    t = _norm(text)
    # Early detection: catch move-like instructions that name multiple columns
    try:
        if re.search(r"\b(?:put|move|copy|transfer)\b", t, flags=re.IGNORECASE) and len(resolved_mentioned) >= 2 and not re.search(r"columns?", t, flags=re.IGNORECASE):
            # order mentioned columns by position in the original text
            positions = []
            for col in mentioned:
                try:
                    pos = [m.start() for m in re.finditer(re.escape(col), text, flags=re.IGNORECASE)][0]
                except Exception:
                    pos = text.lower().find(col.lower())
                positions.append((col, pos))
            positions.sort(key=lambda x: x[1])
            cols_ordered = [ _resolve_name(c) for c, _ in positions if c ]
            src_cols = cols_ordered[:-1]
            tgt_col = cols_ordered[-1]
            # infer type hint from text
            if re.search(r"non|not", t) and re.search(r"num|digit|number|numeric", t):
                typ = "string"
            elif re.search(r"num|digit|number|numeric", t):
                typ = "numeric"
            elif re.search(r"string|text|char|alpha", t):
                typ = "string"
            else:
                typ = "string"
            for s in src_cols:
                params = {"source": s, "target": tgt_col, "type": typ, "exceptions": [], "replacement": ""}
                steps.append(CleaningStep(action="move_by_type", column=None, params=params))
            return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])
    except Exception:
        pass
    # Detect explicit date-normalization requests like:
    # "Convert the order_date column to YYYY-MM-DD" or
    # "Make all the values in the order_date column year-month-day"
    try:
        # Look for explicit format mentions in the instruction
        if any(k in t for k in ['yyyy', 'yyyy-mm-dd', 'year-month-day', 'iso', 'mm/dd/yyyy', 'dd/mm/yyyy']):
            # Prefer exact column mentions from the profile (whole-word match)
            # Allow multiple explicit mentions (e.g., 'order_date and delivery_date')
            target_cols: List[str] = []
            for c in profile.keys():
                try:
                    if re.search(rf"\b{re.escape(c)}\b", text, flags=re.IGNORECASE):
                        target_cols.append(c)
                except Exception:
                    continue

            # If no explicit mentions, prefer columns that contain 'date'
            if not target_cols:
                date_cols = [c for c in profile.keys() if 'date' in c.lower()]
                if len(date_cols) == 1:
                    target_cols = [date_cols[0]]
                elif len(date_cols) > 1:
                    # try to infer a prefix like 'order' from the instruction
                    m = re.search(r'([a-zA-Z0-9_]+)[\s`_]?date', text, flags=re.IGNORECASE)
                    if m:
                        candidate = m.group(1).strip().lower()
                        for dc in date_cols:
                            if candidate in dc.lower():
                                target_cols = [dc]
                                break
                    if not target_cols:
                        # default to all date-like columns when ambiguous
                        target_cols = date_cols

            # fallback to fuzzy inference for a single target when still empty
            if not target_cols:
                inferred = infer_columns_from_text(text, profile, top_n=1)
                if inferred:
                    target_cols = [inferred[0][0]]

            if target_cols:
                # determine requested output format
                fmt_raw = ''
                mfmt = re.search(r'(yyyy[-/ ]?mm[-/ ]?dd|year[- ]month[- ]?day|iso|mm[/-]?dd[/-]?yyyy|dd[/-]?mm[/-]?yyyy)', text, flags=re.IGNORECASE)
                if mfmt:
                    fmt_raw = mfmt.group(1).lower()
                else:
                    fmt_raw = 'yyyy-mm-dd'

                if 'year-month-day' in fmt_raw or 'yyyy-mm-dd' in fmt_raw or 'iso' in fmt_raw or 'yyyy' in fmt_raw:
                    fmt = '%Y-%m-%d'
                elif 'mm/dd' in fmt_raw or re.search(r'mm\W?dd\W?yyyy', fmt_raw):
                    fmt = '%m/%d/%Y'
                elif 'dd/mm' in fmt_raw or re.search(r'dd\W?mm\W?yyyy', fmt_raw):
                    fmt = '%d/%m/%Y'
                else:
                    fmt = '%Y-%m-%d'

                params = {'to_type': 'datetime', 'format': fmt}
                if any(k in t for k in ['set null', 'set to null', 'null for invalid', 'invalid to null', 'set to none']):
                    params['invalid_action'] = 'set_null'
                # detect explicit parse-error column requested
                em = re.search(r'([a-zA-Z0-9_]+_parse_error)', text, flags=re.IGNORECASE)
                if em:
                    err_col = em.group(1)
                    params['on_invalid'] = {'add_column': err_col, 'value': 'unparseable'}
                else:
                    # only add a parse-error column if user asked for parse/error handling
                    if any(k in t for k in ['parse error', 'parse_error', 'parse-error', 'parse error column', 'parse_error column']):
                        # only auto-add parse_error column when a single target column is specified
                        if len(target_cols) == 1:
                            params['on_invalid'] = {'add_column': f"{target_cols[0]}_parse_error", 'value': 'unparseable'}

                # attach step: if multiple target columns, set column to list to apply to each
                step_column = target_cols if len(target_cols) > 1 else target_cols[0]
                steps.append(CleaningStep(action='cast', column=step_column, params=params))
                return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])
    except Exception:
        pass
    # Precedence: detect explicit regex/map/bucketize patterns before generic 'replace' detection
    # regex replace pattern
    rex = re.findall(r"(?:replace\s+)?(?:regex|regular expression)\s+[\'\"](.+?)[\'\"]\s+with\s+[\'\"](.+?)[\'\"]\s+(?:in|on)\s+([a-zA-Z0-9_ ]+)", t)
    if rex:
        pat, repl, col = rex[0]
        steps.append(CleaningStep(action="regex_replace", column=col.strip(), params={"pattern": pat, "replace": repl}))
        return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])

    # bucketize pattern (precedence)
    bcol = re.search(r"(?:in|on)\s+([a-zA-Z0-9_ ]+)", t)
    ranges = re.findall(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\s*:\s*([a-zA-Z0-9_ -]+)", t)
    if ranges and bcol:
        buckets = []
        for lo, hi, label in ranges:
            buckets.append({"min": float(lo), "max": float(hi), "label": label.strip()})
    elif action == "conditional":
        # patterns like: set COLUMN to VALUE when OTHER > 10
        cond_match = re.findall(r"set\s+([a-zA-Z0-9_ ]+)\s+to\s+([\w\'\" -]+)\s+when\s+([a-zA-Z0-9_ ]+)\s*(>=|<=|>|<|==)\s*(\d+(?:\.\d+)?)", t)
        if cond_match:
            col, val, cond_col, op, num = cond_match[0]
            steps.append(CleaningStep(action="conditional", column=col.strip(), params={"value": val.strip().strip('"\''), "condition": {"column": cond_col.strip(), "op": op, "value": float(num)}}))
    elif action == "fuzzy_join":
        # fuzzy join requests are mapped to a join step with fuzzy flag
        # Try to extract two dataset names if present
        join_match = re.findall(r"join\s+([a-zA-Z0-9_\.\/-]+)\s+with\s+([a-zA-Z0-9_\.\/-]+)\s+on\s+([a-zA-Z0-9_ ]+)", t)
        if join_match:
            left, right, key = join_match[0]
            steps.append(CleaningStep(action="join", column=None, params={"left": left, "right": right, "keys": [key.strip()], "fuzzy": True}))
            return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])
        greater = re.findall(r">\s*(\d+(?:\.\d+)?)\s*:\s*([a-zA-Z0-9_ -]+)", t)
        for val, label in greater:
            buckets.append({"min": float(val), "max": None, "label": label.strip()})
        lesser = re.findall(r"<\s*(\d+(?:\.\d+)?)\s*:\s*([a-zA-Z0-9_ -]+)", t)
        for val, label in lesser:
            buckets.append({"min": None, "max": float(val), "label": label.strip()})
        if buckets:
            # strip accidental column mention from labels
            colname = bcol.group(1).strip()
            for b in buckets:
                if isinstance(b.get('label'), str) and b['label'].endswith(f" in {colname}"):
                    b['label'] = b['label'][: -len(f" in {colname}")].strip()
            steps.append(CleaningStep(action="bucketize", column=colname, params={"buckets": buckets}))
            return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])

    # map pattern
    pairs = re.findall(r"([\w\+\-\.]+)\s*(?:->|to)\s*([\w\+\-\.]+)", t)
    mcol = re.search(r"(?:in|on)\s+([a-zA-Z0-9_ ]+)", t)
    if pairs and mcol:
        mapping = {k: v for k, v in pairs}
        steps.append(CleaningStep(action="map", column=mcol.group(1).strip(), params={"mapping": mapping}))
        return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])

    # Detect requests to remove values of a certain data type within a column
    # e.g. "remove all string entries from host_since" or
    # "remove all numerical entries from host_name"
    # also handle exception clauses like "except dates" or "save for dates"
    # allow an optional exception clause like 'except dates' or 'save for dates' between type and source
    pattern = re.compile(r"remove\s+all\s+(string|text|numeric|numerical|number|numbers)\s*(?:entries\s*)?(?:\s*(?:except|save\s+for|but\s+keep)\s+([^,\.]+?)\s*)?(?:from|in)\s+([a-zA-Z0-9_ ]+?)(?=\s+column\b|\s+and\b|$|,|\.)", flags=re.IGNORECASE)
    it = pattern.finditer(t)
    found = False
    for m in it:
        found = True
        typ = m.group(1).lower()
        exceptions_clause = (m.group(2) or "").strip()
        col = m.group(3).strip()
        if typ in ("numeric", "numerical", "number", "numbers"):
            target_type = "numeric"
        else:
            target_type = "string"

        # parse exceptions captured inline (e.g., 'except dates' or 'save for dates')
        exceptions = []
        if exceptions_clause:
            if re.search(r"dates?|date", exceptions_clause):
                exceptions.append("date")

        params = {"target_type": target_type, "replacement": ""}
        if exceptions:
            params["exceptions"] = exceptions

        steps.append(CleaningStep(action="remove_by_type", column=col, params=params))
    if found:
        return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])

    # Detect move/copy requests like:
    # "Put all numerical values of the host_name column in the host_since column"
    # Allow multiple clauses joined by 'and' and optional exception clauses like 'except dates'.
    # First, detect explicit swap/switch phrasing and produce swap_by_types steps.
    # e.g. "Switch values in columns host_name and host_since in rows where the value of host_name is numerical or a date and where the value of host_since is text (except for dates)"
    # Accept past-tense/inflected verbs and looser 'swapped' phrasing
    swap_patterns = [
        # verb-first: swap A and B, flip A and B
        r"\b(?:swap|swapped|switch|switched|exchange|exchanged|flip|flipped)\b\s+([a-zA-Z0-9_ ]+?)\s*(?:and|,)\s*([a-zA-Z0-9_ ]+?)\b",
        # verbose: swap columns A and B
        r"\b(?:swap|swapped|switch|switched|exchange|exchanged|flip|flipped)\b.*?columns?\s+([a-zA-Z0-9_ ]+?)\s*(?:and|,)\s*([a-zA-Z0-9_ ]+?)\b",
        # between phrasing
        r"\b(?:swap|swapped|switch|switched|exchange|exchanged|flip|flipped)\b.*?between\s+([a-zA-Z0-9_ ]+?)\s*(?:and|,)\s*([a-zA-Z0-9_ ]+?)\b",
        # 'A with B' phrasing
        r"\b([a-zA-Z0-9_ ]+?)\s+with\s+([a-zA-Z0-9_ ]+?)\b",
        # passive: A and B should be swapped / swapped where...
        r"\b([a-zA-Z0-9_ ]+?)\s+and\s+([a-zA-Z0-9_ ]+?)\s+(?:should\s+be\s+)?swapped\b",
        r"\b([a-zA-Z0-9_ ]+?)\s+and\s+([a-zA-Z0-9_ ]+?)\s+swapped\b",
        # values/contents phrasing: swap values between A and B, swap contents of A and B
        r"\b(?:swap|swapped|switch|switched|exchange|exchanged)\b.*?values?\s*(?:between|of)\s*([a-zA-Z0-9_ ]+?)\s*(?:and|,)\s*([a-zA-Z0-9_ ]+?)\b",
        r"\b(?:swap|swapped|exchange|exchanged)\b.*?contents?\s*(?:of\s*)?([a-zA-Z0-9_ ]+?)\s*(?:and|,)\s*([a-zA-Z0-9_ ]+?)\b",
        # transpose/invert synonyms
        r"\b(?:transpose|transposed|invert|inverted|reverse|reversed)\b.*?([a-zA-Z0-9_ ]+?)\s*(?:and|,)\s*([a-zA-Z0-9_ ]+?)\b",
    ]
    swap_match = None
    for pat in swap_patterns:
        m = re.search(pat, t, flags=re.IGNORECASE)
        if m:
            swap_match = m
            break

    if swap_match:
        try:
            col_a = swap_match.group(1).strip()
            col_b = swap_match.group(2).strip()

            def resolve_col(name):
                nm = name.strip()
                if nm in profile:
                    return nm
                matches = infer_columns_from_text(nm, profile, top_n=1)
                return matches[0][0] if matches else nm

            col_a = resolve_col(col_a)
            col_b = resolve_col(col_b)

            # detect per-column type phrases anywhere in the instruction
            def extract_clause_types(colname):
                # look for 'value of COL is ...' or 'COL is ...'
                patterns = [
                    rf"(?:value of\s+)?\b{re.escape(colname)}\b\s*(?:is|looks like|contains|appears to be)\s*([^,;\)]+)",
                    rf"(numeric|number|numerical|digits|date|time|timestamp|string|text|alpha|address|phone|email)\s+(?:values\s+)?(?:of\s+)?\b{re.escape(colname)}\b",
                    rf"(?:values\s+of\s+)?\b{re.escape(colname)}\b\s*(?:are|is)?\s*(numeric|date|text|string)",
                ]
                txt = t
                for p in patterns:
                    mm = re.search(p, txt, flags=re.IGNORECASE)
                    if mm:
                        # return the matched descriptor (could be 'numerical or a date')
                        return mm.group(1).strip()
                return None

            cond_a = extract_clause_types(col_a)
            cond_b = extract_clause_types(col_b)

            def parse_types(typestr):
                if not typestr:
                    return []
                types = []
                ts = typestr.lower()
                if re.search(r"num|digit|number|numeric", ts):
                    types.append("numeric")
                if re.search(r"date|time|timestamp", ts):
                    types.append("date")
                if re.search(r"text|string|alpha|letter|address|phone|email", ts):
                    types.append("string")
                return types

            types_a = parse_types(cond_a) or []
            types_b = parse_types(cond_b) or []

            # if only one side specified, infer complement types
            if types_a and not types_b:
                types_b = ["string"]
            if types_b and not types_a:
                types_a = ["numeric"]
            if not types_a and not types_b:
                # fallback: assume numeric in a and string in b
                types_a = ["numeric"]
                types_b = ["string"]

            # detect exceptions like 'except dates' globally for each col
            exc_a = []
            exc_b = []
            if re.search(rf"{re.escape(col_a)}[\s\S]{{0,60}}except\s+([^,\.]+)", t, flags=re.IGNORECASE):
                em = re.search(rf"{re.escape(col_a)}[\s\S]{{0,60}}except\s+([^,\.]+)", t, flags=re.IGNORECASE)
                if em and re.search(r"date", em.group(1), flags=re.IGNORECASE):
                    exc_a.append("date")
            if re.search(rf"{re.escape(col_b)}[\s\S]{{0,60}}except\s+([^,\.]+)", t, flags=re.IGNORECASE):
                em = re.search(rf"{re.escape(col_b)}[\s\S]{{0,60}}except\s+([^,\.]+)", t, flags=re.IGNORECASE)
                if em and re.search(r"date", em.group(1), flags=re.IGNORECASE):
                    exc_b.append("date")

            moves = []
            for ta in types_a:
                moves.append({"source": col_a, "target": col_b, "type": ta, "exceptions": exc_a or []})
            for tb in types_b:
                moves.append({"source": col_b, "target": col_a, "type": tb, "exceptions": exc_b or []})

            if moves:
                steps.append(CleaningStep(action="swap_by_types", column=None, params={"moves": moves, "replacement": ""}))
                return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])
        except Exception:
            pass

        # Detect explicit move instructions with multiple source columns, e.g.
        # "Put all non-numerical values in the postal_code and col_6 columns into the city column"
        try:
            for m_multi in re.finditer(r"put\s+all\s+([a-zA-Z0-9_\- ]+?)\s+values\s+(?:in|from|of)?\s+([a-zA-Z0-9_ ,and]+?)\s+columns?\s*(?:into|in|to)?\s*(?:the\s+)?([a-zA-Z0-9_ ]+)\s+column", t, flags=re.IGNORECASE):
                type_token = (m_multi.group(1) or "").strip()
                src_list = m_multi.group(2) or ""
                tgt_raw = (m_multi.group(3) or "").strip()
                tgt = _resolve_name(tgt_raw)

                # normalize type hints
                tt = type_token.lower()
                if re.search(r"non|not", tt) and re.search(r"num|digit|number|numeric", tt):
                    typ = "string"
                elif re.search(r"num|digit|number|numeric", tt):
                    typ = "numeric"
                elif re.search(r"string|text|char|alpha", tt):
                    typ = "string"
                else:
                    # default to string when unsure
                    typ = "string"

                # split source list by commas/and
                parts = [re.sub(r"\b(the|columns|column)\b", "", p, flags=re.IGNORECASE).strip() for p in re.split(r",|\band\b", src_list) if p and p.strip()]
                for s in parts:
                    src_col = _resolve_name(s)
                    params = {"source": src_col, "target": tgt, "type": typ, "exceptions": [], "replacement": ""}
                    steps.append(CleaningStep(action="move_by_type", column=None, params=params))
                if parts:
                    return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])
        except Exception:
            pass

    # Try strict patterns first to capture '... values of the COL column in the COL column'
    moves = []
    strict1 = re.compile(r"(string|text|numeric|numerical|number|numbers)\s+(?:values|entries)?\s+of\s+the\s+([A-Za-z0-9_]+)\s+column\s*(?:except\s+([^,\.]+?)\s*)?(?:in|into|to)\s+the\s+([A-Za-z0-9_]+)\s+column", flags=re.IGNORECASE)
    strict2 = re.compile(r"(string|text|numeric|numerical|number|numbers)\s+(?:values|entries)?\s+of\s+([A-Za-z0-9_]+)\s*(?:column)?\s*(?:except\s+([^,\.]+?)\s*)?(?:in|into|to)\s+([A-Za-z0-9_]+)", flags=re.IGNORECASE)
    for pat in (strict1, strict2):
        for m in pat.finditer(t):
            typ = m.group(1).lower()
            src = m.group(2).strip()
            exceptions_clause = (m.group(3) or "").strip()
            tgt = m.group(4).strip()
            source_type = "numeric" if typ in ("numeric", "numerical", "number", "numbers") else "string"
            exceptions = []
            if exceptions_clause and re.search(r"date|dates", exceptions_clause, flags=re.IGNORECASE):
                exceptions.append("date")
            moves.append({"source": src, "target": tgt, "type": source_type, "exceptions": exceptions, "replacement": ""})

    # Fallback: split into clauses and try a relaxed pattern per clause
    if not moves:
        clauses = re.split(r"\band\b|,", t)
        move_simple = re.compile(r"(?:put|move|copy)\s+(?:all\s+)?(string|text|numeric|numerical|number|numbers)\s*(?:values|entries)?(?:\s+of\s+the|\s+of)?\s*([a-zA-Z0-9_ ]+?)\s*(?:column)?\s*(?:except\s+([^,\.]+?)\s*)?.*?(?:in|into|to)\s+([a-zA-Z0-9_ ]+)", flags=re.IGNORECASE)
        for cl in clauses:
            m = move_simple.search(cl)
            if not m:
                continue
            typ = m.group(1).lower()
            src = m.group(2).strip()
            src = re.sub(r"\b(the|column|columns)\b", "", src, flags=re.IGNORECASE).strip()
            exceptions_clause = (m.group(3) or "").strip()
            tgt = m.group(4).strip()
            tgt = re.sub(r"\b(the|column|columns)\b", "", tgt, flags=re.IGNORECASE).strip()
            source_type = "numeric" if typ in ("numeric", "numerical", "number", "numbers") else "string"
            exceptions = []
            if exceptions_clause and re.search(r"date|dates", exceptions_clause, flags=re.IGNORECASE):
                exceptions.append("date")
            moves.append({"source": src, "target": tgt, "type": source_type, "exceptions": exceptions, "replacement": ""})

    if moves:
        # If moves include reciprocal pairs between two columns, emit a single swap step
        if len(moves) >= 2:
            # find pairs where source/target are reversed
            for i in range(len(moves)):
                for j in range(i + 1, len(moves)):
                    a = moves[i]
                    b = moves[j]
                    if a["source"] == b["target"] and a["target"] == b["source"]:
                        # create a combined swap step for these two
                        steps.append(CleaningStep(action="swap_by_types", column=None, params={"moves": [a, b], "replacement": ""}))
                        # mark as consumed
                        moves[i] = None
                        moves[j] = None
        # add any remaining single-direction moves as move_by_type steps
        for m in moves:
            if not m:
                continue
            steps.append(CleaningStep(action="move_by_type", column=None, params=m))
        return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])

    # Extended conditional parsing: support multiple 'if ... then ...' and '... when ...' clauses
    try:
        # form: if CONDITION then ACTION; allow multiple occurrences
        for mm in re.finditer(r"(?:if|when)\s+(.+?)\s+then\s+(.+?)(?:;|$)", text, flags=re.IGNORECASE | re.DOTALL):
            cond_txt = mm.group(1)
            action_txt = mm.group(2).strip()
            cond = _parse_condition_text(cond_txt)
            # expand the action_txt into concrete steps and attach condition to each
            try:
                inner = recipe_from_plain_english(action_txt, profile, source_path, output_path)
                for inner_step in inner.cleaning_steps:
                    p = dict(inner_step.params or {})
                    p['condition'] = cond
                    steps.append(CleaningStep(action=inner_step.action, column=inner_step.column, params=p))
                # also keep an explicit conditional step for explainability/tests
                steps.append(CleaningStep(action="conditional", column=None, params={"condition": cond, "action_text": action_txt}))
            except Exception:
                params = {"condition": cond, "action_text": action_txt}
                steps.append(CleaningStep(action="conditional", column=None, params=params))
        # form: ACTION when CONDITION (e.g. 'normalize name when age > 18')
        for mm in re.finditer(r"(.+?)\s+when\s+(.+?)(?:;|$)", text, flags=re.IGNORECASE | re.DOTALL):
            action_txt = mm.group(1).strip()
            cond_txt = mm.group(2).strip()
            # avoid catching 'fill X with Y' earlier patterns as ACTION when CONDITION if already parsed
            if len(action_txt) < 3:
                continue
            cond = _parse_condition_text(cond_txt)
            try:
                inner = recipe_from_plain_english(action_txt, profile, source_path, output_path)
                for inner_step in inner.cleaning_steps:
                    p = dict(inner_step.params or {})
                    p['condition'] = cond
                    steps.append(CleaningStep(action=inner_step.action, column=inner_step.column, params=p))
                steps.append(CleaningStep(action="conditional", column=None, params={"condition": cond, "action_text": action_txt}))
            except Exception:
                params = {"condition": cond, "action_text": action_txt}
                steps.append(CleaningStep(action="conditional", column=None, params=params))
        if any(s.action == "conditional" for s in steps):
            return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])
    except Exception:
        pass

    # Special-case removal of letters/characters within values (not column drop).
    # e.g. "Remove any letters in the values in the quantity column" -> regex_replace
    try:
        m_letters = re.search(r"remove\s+(?:any|all|the)?\s*letters?\s+(?:in|from|of)\s+(?:the\s+)?([a-zA-Z0-9_ ]+)(?:\s+column)?", text, flags=re.IGNORECASE)
        if m_letters:
            col = _resolve_name(m_letters.group(1).strip())
            steps.append(CleaningStep(action="regex_replace", column=col, params={"pattern": r"[A-Za-z]+", "replace": ""}))
            return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])
    except Exception:
        pass

    if action == "drop_column":
        for column in suggested_columns[:1]:
            steps.append(CleaningStep(action="drop_column", column=column, params={}))
    elif action == "impute":
        # Patterns like:
        # "fill missing AGE with AGE_EST" -> from_column
        # "fill missing AGE with average of AGE_EST" -> mean/group_mean
        # "fill missing AGE by CITY" -> group_by
        explicit = re.findall(r"fill(?: missing)?\s+([a-zA-Z0-9_ ]+)\s+(?:with|from|using)\s+([a-zA-Z0-9_ ]+)", t)
        group_by = None
        gb = re.findall(r"by\s+([a-zA-Z0-9_ ]+)", t)
        if gb:
            group_by = gb[0].strip()

        # If the user explicitly mentioned both a target and a source column
        # by name in the instruction (e.g. 'observation_date' and 'date'),
        # prefer that mapping immediately to avoid noisy regex captures.
        if len(mentioned) >= 2:
            tgt = mentioned[0]
            src = mentioned[1]
            params = {"strategy": "from_column", "source": src}
            if group_by:
                params["group_by"] = group_by
            steps.append(CleaningStep(action="impute", column=tgt, params=params))
            return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])

        # First try the original, narrowly-targeted pattern for compatibility,
        # then fall back to broader patterns that accept more phrasing variants.
        # handle many phrasings for copying/filling from one column to another
        # initialize candidates; try narrow pattern first
        src = None
        tgt = None
        m_from_col = re.search(r"(?:impute|fill)(?: missing)?(?: values)?(?: in| of)?\s+(?:the\s+)?([a-zA-Z0-9_ ]+?)\s+column.*?(?:from|using|by inputting the values of)\s+(?:the\s+)?([a-zA-Z0-9_ ]+?)\s+column", t)
        if m_from_col:
            tgt = m_from_col.group(1).strip()
            src = m_from_col.group(2).strip()
        # examples matched:
        # - "Impute missing values in OBSERVATION_DATE column by inputting the values of the DATE column"
        # - "Fill observation_date from date column"
        # - "Use the date column to fill observation_date"
        # - "Copy date to observation_date"
        patterns = [
            r"(?:impute|fill|populate|copy|set)\s+(?:missing\s+values\s+in\s+|missing\s+)?(?:the\s+)?(?P<tgt>[a-zA-Z0-9_ ]+?)\s+(?:column)?\s*(?:with|from|using|by inputting the values of|by|to)\s+(?:the\s+)?(?P<src>[a-zA-Z0-9_ ]+?)\s*(?:column)?",
            r"(?:copy|move)\s+(?:the\s+)?(?P<src>[a-zA-Z0-9_ ]+?)\s+(?:column\s+)?(?:to|into)\s+(?:the\s+)?(?P<tgt>[a-zA-Z0-9_ ]+?)\s*(?:column)?",
            r"(?:use|using)\s+(?:the\s+)?(?P<src>[a-zA-Z0-9_ ]+?)\s+(?:column\s+)?(?:to|for|to fill|to populate)\s+(?:the\s+)?(?P<tgt>[a-zA-Z0-9_ ]+?)\s*(?:column)?",
            r"(?P<tgt>[a-zA-Z0-9_ ]+?)\s+(?:should\s+)?(?:be\s+)?(?:filled|populated)\s+from\s+(?:the\s+)?(?P<src>[a-zA-Z0-9_ ]+?)",
            r"(?P<src>[a-zA-Z0-9_ ]+?)\s+column\s+to\s+(?:the\s+)?(?P<tgt>[a-zA-Z0-9_ ]+?)",
        ]

        if not (tgt and src):
            for p in patterns:
                m = re.search(p, t)
                if m:
                    try:
                        src_candidate = m.group('src').strip()
                        tgt_candidate = m.group('tgt').strip()
                    except Exception:
                        src_candidate = None
                        tgt_candidate = None
                    if tgt_candidate:
                        tgt = tgt_candidate
                    if src_candidate:
                        src = src_candidate
                    break

        if tgt and src:
            # fuzzy-match to profile if needed
            if tgt not in profile:
                matches = infer_columns_from_text(tgt, profile, top_n=1)
                if matches:
                    tgt = matches[0][0]
                else:
                    tgt = None
            if src not in profile:
                matches = infer_columns_from_text(src, profile, top_n=1)
                if matches:
                    src = matches[0][0]
                else:
                    src = None
            if tgt and src:
                params = {"strategy": "from_column", "source": src}
                # detect explicit sentinel instructions like 'treat zeros as missing' or other forms
                sentinels = None
                m_treat = re.search(r"treat\s+(?:the\s+)?(.+?)\s+as\s+missing", t)
                if not m_treat:
                    m_treat = re.search(r"consider\s+(?:the\s+)?(.+?)\s+missing", t)
                if m_treat:
                    tok = m_treat.group(1).strip()
                    # handle common keywords
                    if re.search(r"zeros?|0s?", tok):
                        sentinels = [0, "0"]
                    else:
                        parts = re.split(r",|\band\b", tok)
                        vals = []
                        for p in parts:
                            v = p.strip().strip('"\'')
                            if v == '':
                                continue
                            # try numeric
                            try:
                                n = int(v) if re.match(r"^-?\d+$", v) else float(v) if re.match(r"^-?\d+\.\d+$", v) else None
                            except Exception:
                                n = None
                            if n is not None:
                                vals.append(n)
                            else:
                                vals.append(v)
                        if vals:
                            sentinels = vals
                if sentinels:
                    params["treat_as_missing"] = sentinels
                if group_by:
                    params["group_by"] = group_by
                steps.append(CleaningStep(action="impute", column=tgt, params=params))
                return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])

        if explicit:
            target, src = explicit[0]
            target = target.strip()
            src = src.strip()
            # normalize common trailing words like 'values' or 'column'
            target = re.sub(r"\bvalues?\b|\bcolumns?\b|\bcolumn\b|\bthe\b", "", target).strip()
            src = re.sub(r"\bvalues?\b|\bcolumns?\b|\bcolumn\b|\bthe\b", "", src).strip()
            # ensure target exists in profile; if not, try fuzzy match
            if target not in profile:
                matches = infer_columns_from_text(target, profile, top_n=1)
                if matches:
                    target = matches[0][0]
                else:
                    # target column not present in data; skip creating impute step
                    target = None
            # detect aggregate keywords
            if any(k in t for k in ["average", "mean", "median"]):
                strategy = "mean"
                params = {"strategy": strategy, "source": src}
                if group_by:
                    params["group_by"] = group_by
            # If explicit 'fill X with Y' was detected and both columns resolved,
            # create a from_column impute step by default (unless an aggregate
            # strategy was already set above). This ensures the parser only
            # touches the columns the user mentioned.
            if target and src:
                # fuzzy-match source if needed
                if src not in profile:
                    matches = infer_columns_from_text(src, profile, top_n=1)
                    if matches:
                        src = matches[0][0]
                    else:
                        src = None
                if src:
                    if 'params' not in locals() or params is None:
                        params = {"strategy": "from_column", "source": src}
                    if group_by:
                        params["group_by"] = group_by
                    steps.append(CleaningStep(action="impute", column=target, params=params))
                    return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])

        # If regression options are provided via API, prefer them for imputation
        if regression_options:
            # regression_options: {model: 'linear'|'ridge', features: [...], group_by: 'col'}
            feats = regression_options.get('features') or []
            model = regression_options.get('model') or 'linear'
            group_by = regression_options.get('group_by')
            # infer target column from instruction if possible; prefer explicitly mentioned columns
            target_col = suggested_columns[0] if suggested_columns else None
            if target_col and feats:
                # ensure features exist in profile, attempt to fuzzy-match names
                valid_feats = [f for f in feats if f in profile]
                if not valid_feats:
                    # try to infer each feature name from text using profile
                    inferred = []
                    for f in feats:
                        matches = infer_columns_from_text(f, profile, top_n=1)
                        if matches:
                            inferred.append(matches[0][0])
                    valid_feats = inferred
                if valid_feats:
                    params = {"strategy": "regression", "sources": valid_feats, "model": model}
                    if group_by:
                        params["group_by"] = group_by
                    steps.append(CleaningStep(action="impute", column=target_col, params=params))
                    return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])
                    params["group_by"] = group_by
            else:
                # default to copying from source column
                params = {"strategy": "from_column", "source": src}
                if group_by:
                    params["group_by"] = group_by
            if target:
                steps.append(CleaningStep(action="impute", column=target, params=params))
        else:
            if suggested_columns:
                column = suggested_columns[0]
                strategy = _default_strategy_for_column(profile.get(column, {}))
                if "median" in t or "average" in t or "mean" in t:
                    strategy = "median"
                if "ffill" in t or "forward" in t:
                    strategy = "forward_fill"
                steps.append(CleaningStep(action="impute", column=column, params={"strategy": strategy}))
    elif action == "normalize":
        # If the user explicitly mentioned columns, only normalize those.
        if mentioned:
            targets = mentioned
        else:
            # attempt to prefer columns present in reconstructed preview (e.g., 'username')
            preview_cols = []
            try:
                from .engine import _read_table, Cleaner
                df_raw = _read_table(source_path)
                recon = Cleaner()._reconstruct_table_from_df(df_raw, offset=0, limit=1)
                if recon and isinstance(recon, list) and len(recon) > 0:
                    preview_cols = [k for k in recon[0].keys()]
            except Exception:
                preview_cols = []

            # if username present in preview, target it explicitly
            if preview_cols:
                for k in preview_cols:
                    lk = k.lower()
                    if lk == 'username' or 'user' in lk or ('name' in lk and not any(x in lk for x in ('first','last','firstname','lastname'))):
                        targets = [k]
                        break
                else:
                    # prefer suggested columns that appear in preview, and favor string-like columns
                    prefs = [c for c in suggested_columns if c in preview_cols]
                    # prefer up to 5 targets for broader coverage when user is vague
                    targets = prefs[:5]
                    if not targets:
                        targets = suggested_columns[:5]
            else:
                # prefer string-like columns first, else fall back to suggested columns
                string_cols = [c for c, m in profile.items() if any(x in str(m.get('dtype','')).lower() for x in ('utf','str','object'))]
                if string_cols:
                    targets = [c for c in suggested_columns if c in string_cols][:5] or suggested_columns[:5]
                else:
                    targets = suggested_columns[:5]
            for column in targets:
                # detect explicit 'first letter' capitalization requests
                if re.search(r"first\s+letter|only\s+the\s+first|capitalize\s+first\s+letter|make\s+first\s+letter\s+capital", t, flags=re.IGNORECASE):
                    case = "capitalize"
                elif any(k in t for k in ["title case", "titlecase", "title case", "capitalize each word", "proper case"]):
                    case = "title"
                elif any(k in t for k in ["upper", "uppercase"]):
                    case = "upper"
                elif any(k in t for k in ["trim", "strip", "whitespace", "leading", "trailing"]):
                    # If user explicitly requests trim-only, perform trim
                    case = "trim"
                else:
                    case = "lower" if any(k in t for k in ["lower", "lowercase"]) else "preserve"
                # prepend a trim step if not already just trimming and the user likely meant whitespace cleanup
                try:
                    if case != 'trim' and any(k in t for k in ['trim', 'strip', 'whitespace']):
                        steps.append(CleaningStep(action="normalize", column=column, params={"case": "trim"}))
                except Exception:
                    pass
                steps.append(CleaningStep(action="normalize", column=column, params={"case": case}))
    elif action == "deduplicate":
        # Comprehensive impute phrase handling used by data analysts
        # Examples handled:
        # - fill missing AGE with AGE_EST
        # - fill missing AGE with average of AGE_EST
        # - fill AGE with 0
        # - forward fill AGE
        # - fill AGE using AGE1 and AGE2 (row mean)
        # - fill AGE per city with median of AGE_EST
        # - fill AGE with most common value in AGE_EST
        parsed = False
        # explicit "fill X with Y" family
        m = re.search(r"fill(?: missing)?\s+([a-zA-Z0-9_ ]+?)\s+(?:with|from|using)\s+(.+)$", t)
        if m:
            target = m.group(1).strip()
            rest = m.group(2).strip()
            # try to fuzzy-match target to profile
            if target not in profile:
                matches = infer_columns_from_text(target, profile, top_n=1)
                if matches:
                    target = matches[0][0]
                else:
                    target = None

            # detect group_by/per
            gbm = re.search(r"(?:by|per)\s+([a-zA-Z0-9_ ]+)$", rest)
            group_by = gbm.group(1).strip() if gbm else None

            # remove trailing 'by <group>' from rest for source parsing
            if group_by:
                rest = re.sub(r"(?:by|per)\s+[a-zA-Z0-9_ ]+$", "", rest).strip()

            # constants like '0' or quoted strings
            const_match = re.match(r"^['\"]?(\-?\d+(?:\.\d+)?)['\"]?$", rest)
            if const_match:
                value = float(const_match.group(1)) if '.' in const_match.group(1) else int(const_match.group(1))
                params = {"strategy": "constant", "value": value}
                if target:
                    steps.append(CleaningStep(action="impute", column=target, params=params))
                    parsed = True

            # forward/backward fill keywords
            if not parsed and any(k in rest for k in ["forward", "ffill", "forward_fill"]):
                params = {"strategy": "ffill"}
                if target:
                    steps.append(CleaningStep(action="impute", column=target, params=params))
                    parsed = True
            if not parsed and any(k in rest for k in ["backfill", "bfill", "backward"]):
                params = {"strategy": "bfill"}
                if target:
                    steps.append(CleaningStep(action="impute", column=target, params=params))
                    parsed = True

            # mode / most common
            if not parsed and any(k in rest for k in ["most common", "most frequent", "mode"]):
                srcs = re.findall(r"of\s+([a-zA-Z0-9_, and]+)", rest)
                source = None
                if srcs:
                    source = srcs[0].strip()
                else:
                    # if rest is a single column name
                    if rest in profile:
                        source = rest
                params = {"strategy": "mode"}
                if source:
                    params["source"] = source
                if group_by:
                    params["group_by"] = group_by
                if target:
                    steps.append(CleaningStep(action="impute", column=target, params=params))
                    parsed = True

            # mean/median
            if not parsed and any(k in rest for k in ["average", "mean", "median"]):
                agg = "mean" if any(k in rest for k in ["average", "mean"]) else "median"
                srcs = re.findall(r"of\s+([a-zA-Z0-9_, and]+)", rest)
                source = None
                if srcs:
                    source = srcs[0].strip()
                else:
                    if rest in profile:
                        source = rest
                params = {"strategy": agg}
                if source:
                    params["source"] = source
                if group_by:
                    params["group_by"] = group_by
                if target:
                    steps.append(CleaningStep(action="impute", column=target, params=params))
                    parsed = True

            # multiple sources -> row_mean
            if not parsed:
                # split on commas or ' and '
                parts = re.split(r"[,\s]+and[,\s]+|,\s*|\band\b", rest)
                parts = [p.strip() for p in parts if p.strip()]
                valid_srcs = [p for p in parts if p in profile]
                if len(valid_srcs) >= 2:
                    params = {"strategy": "row_mean", "sources": valid_srcs}
                    if target:
                        steps.append(CleaningStep(action="impute", column=target, params=params))
                        parsed = True

                # regression-based imputation: 'predict AGE from HEIGHT and WEIGHT using regression'
                if not parsed:
                    regm = re.search(r"predict\s+([a-zA-Z0-9_ ]+?)\s+(?:from|using)\s+([a-zA-Z0-9_, and]+)\s*(?:using\s+([a-zA-Z0-9_]+))?", t)
                    if regm:
                        target = regm.group(1).strip()
                        srcs_raw = regm.group(2)
                        model_type = regm.group(3) or 'linear'
                        parts = re.split(r",|and", srcs_raw)
                        parts = [p.strip() for p in parts if p.strip()]
                        valid_srcs = [p for p in parts if p in profile]
                        if target not in profile:
                            matches = infer_columns_from_text(target, profile, top_n=1)
                            if matches:
                                target = matches[0][0]
                            else:
                                target = None
                        if target and valid_srcs:
                            params = {"strategy": "regression", "sources": valid_srcs, "model": model_type}
                            steps.append(CleaningStep(action="impute", column=target, params=params))
                            parsed = True

            # default: if rest looks like a column name, copy from that column
            if not parsed and rest:
                src_candidate = rest.strip()
                if src_candidate in profile:
                    params = {"strategy": "from_column", "source": src_candidate}
                    if group_by:
                        params["group_by"] = group_by
                    if target:
                        steps.append(CleaningStep(action="impute", column=target, params=params))
                        parsed = True

        # if not explicit and user mentioned impute generally, target suggested_columns
        if not parsed and suggested_columns:
            column = suggested_columns[0]
            strategy = _default_strategy_for_column(profile.get(column, {}))
            # override by explicit keywords
            if "median" in t or "average" in t or "mean" in t:
                strategy = "median"
            if "ffill" in t or "forward" in t:
                strategy = "forward_fill"
            steps.append(CleaningStep(action="impute", column=column, params={"strategy": strategy}))
        if pairs and colm:
            mapping = {k: v for k, v in pairs}
            steps.append(CleaningStep(action="map", column=colm.group(1).strip(), params={"mapping": mapping}))
    elif action == "regex_replace":
        # replace regex 'pattern' with 'repl' in column
        rex = re.findall(r"(?:replace\s+)?(?:regex|regular expression)\s+[\'\"](.+?)[\'\"]\s+with\s+[\'\"](.+?)[\'\"]\s+(?:in|on)\s+([a-zA-Z0-9_ ]+)", t)
        if not rex:
            rex = re.findall(r"regex\s+replace\s+[\'\"](.+?)[\'\"]\s+with\s+[\'\"](.+?)[\'\"]\s+(?:in|on)\s+([a-zA-Z0-9_ ]+)", t)
        if rex:
            pat, repl, col = rex[0]
            steps.append(CleaningStep(action="regex_replace", column=col.strip(), params={"pattern": pat, "replace": repl}))
    elif action == "bucketize":
        # detect simple bucket specs like '0-5:low,6-10:mid,>10:high' and column
        colm = re.search(r"(?:in|on)\s+([a-zA-Z0-9_ ]+)", t)
        buckets = []
        if colm:
            # ranges like 0-5:low
            ranges = re.findall(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\s*:\s*([a-zA-Z0-9_ -]+)", t)
            for lo, hi, label in ranges:
                buckets.append({"min": float(lo), "max": float(hi), "label": label.strip()})
            # greater than patterns: >10:high
            greater = re.findall(r">\s*(\d+(?:\.\d+)?)\s*:\s*([a-zA-Z0-9_ -]+)", t)
            for val, label in greater:
                buckets.append({"min": float(val), "max": None, "label": label.strip()})
            # less than patterns: <5:tiny
            lesser = re.findall(r"<\s*(\d+(?:\.\d+)?)\s*:\s*([a-zA-Z0-9_ -]+)", t)
            for val, label in lesser:
                buckets.append({"min": None, "max": float(val), "label": label.strip()})
            if buckets:
                steps.append(CleaningStep(action="bucketize", column=colm.group(1).strip(), params={"buckets": buckets}))
    else:
        # For vague instructions, avoid auto-normalizing columns (don't lowercase strings).
        # Only suggest imputation for columns that clearly need it (have nulls).
        for column, _, _ in suggest_columns_to_clean(profile, top_n=3):
            meta = profile.get(column, {})
            if int(meta.get("nulls", 0) or 0) > 0:
                steps.append(CleaningStep(action="impute", column=column, params={"strategy": _default_strategy_for_column(meta)}))

    # Fallback: if the intent was normalize but no steps were produced by earlier heuristics,
    # create a conservative normalize step targeting the top suggested columns.
    try:
        if action == "normalize" and not steps:
            cols = []
            try:
                if mentioned:
                    cols = mentioned
                else:
                    cols = [c for c, _, _ in (infer_columns_from_text(text, profile, top_n=5) or [])]
            except Exception:
                cols = []
            if not cols and suggested_columns:
                cols = suggested_columns[:5]
            # default case: lower unless user requested upper/title/capitalize/trim
            case = "lower"
            try:
                if any(k in t for k in ["title case", "titlecase", "title", "proper case", "capitalize each word"]):
                    case = "title"
                elif any(k in t for k in ["capitalize", "capitalise", "first letter"]):
                    case = "capitalize"
                elif any(k in t for k in ["upper", "uppercase"]):
                    case = "upper"
                elif any(k in t for k in ["trim", "strip", "whitespace", "leading", "trailing"]):
                    case = "trim"
            except Exception:
                pass
            for c in cols:
                if case != 'trim' and any(k in t for k in ['trim', 'strip', 'whitespace']):
                    steps.append(CleaningStep(action="normalize", column=c, params={"case": "trim"}))
                steps.append(CleaningStep(action="normalize", column=c, params={"case": case}))
    except Exception:
        pass

    return Recipe(sources=[{"path": source_path}], cleaning_steps=steps, joins=[], outputs=[{"path": output_path}])


def suggest_join_rules(left_profile: Dict[str, Dict[str, Any]], right_profile: Dict[str, Dict[str, Any]], left_name: str = "left", right_name: str = "right") -> List[JoinSpec]:
    """Suggest deterministic join rules from schema overlap."""
    left_columns = list(left_profile.keys())
    right_columns = list(right_profile.keys())
    join_specs: List[JoinSpec] = []

    exact_matches = sorted(set(col for col in left_columns if col in right_columns))
    for col in exact_matches:
        if any(token in col.lower() for token in ["id", "email", "sku", "code", "key"]):
            join_specs.append(JoinSpec(left=left_name, right=right_name, keys=[col]))

    if not join_specs:
        scored_pairs: List[Tuple[float, str]] = []
        for left_col in left_columns:
            for right_col in right_columns:
                score = _match_score(left_col, right_col)
                if score >= 0.75:
                    scored_pairs.append((score, left_col))
        scored_pairs.sort(reverse=True)
        for _, col in scored_pairs[:3]:
            join_specs.append(JoinSpec(left=left_name, right=right_name, keys=[col]))

    if not join_specs:
        # Fallback fuzzy suggestions based on common identity words.
        identity_candidates = [
            col for col in left_columns if any(token in col.lower() for token in ["id", "email", "phone", "sku", "code", "name"])
        ]
        if identity_candidates:
            join_specs.append(JoinSpec(left=left_name, right=right_name, keys=[identity_candidates[0]]))

    return join_specs


def explain_recipe(recipe: Recipe, profile: Optional[Dict[str, Dict[str, Any]]] = None) -> List[ExplainedStep]:
    """Attach deterministic explanations and fill missing parameters where possible."""
    explanations: List[ExplainedStep] = []
    profile = profile or {}

    for step in recipe.cleaning_steps:
        # allow multi-column steps: produce one explanation per referenced column
        cols = step.column if isinstance(step.column, (list, tuple)) else ([step.column] if step.column is not None else [None])
        for col in cols:
            reason = ""
            confidence = 0.5
            if col and col in profile:
                meta = profile[col]
                nulls = int(meta.get("nulls", 0) or 0)
                dtype = _norm(str(meta.get("dtype", "")))
                if step.action == "impute":
                    strategy = step.params.get("strategy") if step.params else None
                    if not strategy:
                        strategy = _default_strategy_for_column(meta)
                        step.params = dict(step.params or {}, strategy=strategy)
                    reason = f"{col} has {nulls} missing values; use {strategy} for {dtype or 'unknown'} data."
                    confidence = 0.8 if nulls > 0 else 0.6
                elif step.action == "normalize":
                    reason = f"{col} appears to be a text column, so normalization is safe and deterministic."
                    confidence = 0.75 if any(token in dtype for token in ["str", "utf", "object"]) else 0.6
                elif step.action == "drop_column":
                    reason = f"{col} is explicit in the recipe and will be removed exactly."
                    confidence = 0.95
                else:
                    # Add richer explanations for non-impute/normalize/drop steps
                    if step.action == "cast":
                        fmt = step.params.get("format") if step.params else None
                        reason = f"Convert {col} to {step.params.get('to_type','datetime')}{(' with format '+fmt) if fmt else ''}."
                        confidence = 0.85
                    elif step.action == "move_by_type":
                        p = step.params or {}
                        reason = f"Move values of type {p.get('type','string')} from {p.get('source')} to {p.get('target')}."
                        confidence = 0.7
                    elif step.action == "swap_by_types":
                        reason = f"Swap/move values between columns as specified by types in the step."
                        confidence = 0.75
                    elif step.action == "remove_by_type":
                        reason = f"Remove values matching type criteria from {col}."
                        confidence = 0.7
                    elif step.action == "map":
                        reason = f"Map specific values in {col} according to provided mapping."
                        confidence = 0.8
                    elif step.action == "regex_replace":
                        reason = f"Apply regex replacement on {col} to clean/standardize patterns."
                        confidence = 0.8
                    elif step.action == "bucketize":
                        reason = f"Bucketize numeric ranges in {col} into labeled bins."
                        confidence = 0.8
                    elif step.action == "conditional":
                        reason = f"Conditionally set values in {col} based on the provided predicate."
                        confidence = 0.7
                    elif step.action == "join":
                        reason = f"Join datasets using keys; this step may be fuzzy if requested."
                        confidence = 0.75
                    elif step.action == "fuzzy_join":
                        reason = f"Perform a fuzzy/approximate join on the specified keys."
                        confidence = 0.7
                    elif step.action == "deduplicate":
                        reason = "Remove duplicate rows using the specified keys or heuristics."
                        confidence = 0.8
                    elif step.action == "regex_replace":
                        reason = f"Apply regex replacement to the target column to clean/standardize patterns."
                        confidence = 0.75
                    elif step.action == "bucketize":
                        reason = f"Bucketize numeric ranges into labeled bins as requested."
                        confidence = 0.75
                    elif step.action == "conditional":
                        reason = "This is a conditional step; apply the action only where the condition holds."
                        confidence = 0.6
                    elif step.action == "join" or step.action == "fuzzy_join":
                        reason = "Join datasets using specified keys; fuzzy matching may be used if requested."
                        confidence = 0.7
                    elif step.action == "deduplicate":
                        reason = "Remove duplicate rows using the specified keys or heuristics."
                        confidence = 0.8
                    elif step.action == "rename":
                        reason = "Rename columns as specified in the recipe."
                        confidence = 0.9
                    else:
                        reason = "This step is underspecified and only a deterministic literal execution is possible."
                        confidence = 0.3

            # attach explanation; include column information in the step for clarity
            s_copy = step
            if col is not None:
                # ensure the explained step references the specific column
                try:
                    s_copy = CleaningStep(action=step.action, column=col, params=step.params)
                except Exception:
                    s_copy = step
            explanations.append(ExplainedStep(step=s_copy, reason=reason, confidence=confidence))

    return explanations


def recipe_summary(recipe: Recipe) -> Dict[str, Any]:
    return {
        "sources": recipe.sources,
        "cleaning_steps": [step.model_dump() if hasattr(step, "model_dump") else step.dict() for step in recipe.cleaning_steps],
        "joins": [join.model_dump() if hasattr(join, "model_dump") else join.dict() for join in recipe.joins or []],
        "outputs": recipe.outputs,
    }
