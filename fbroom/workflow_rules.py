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
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .recipe_schema import CleaningStep, JoinSpec, Recipe


ACTION_ALIASES = {
    "drop_column": ["drop", "remove", "delete", "exclude"],
    "impute": ["fill", "impute", "replace missing", "fill missing", "populate missing"],
    "normalize": ["normalize", "standardize", "lowercase", "uppercase", "trim", "clean up"],
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
    for action, aliases in ACTION_ALIASES.items():
        if any(alias in t for alias in aliases):
            return action
    return None


def infer_columns_from_text(text: str, profile: Dict[str, Dict[str, Any]], top_n: int = 3) -> List[Tuple[str, float, str]]:
    """Return likely columns from a free-form instruction and column profile.

    Output is a list of (column_name, score, reason).
    """
    t = _norm(text)
    scored: List[Tuple[str, float, str]] = []
    for column, meta in profile.items():
        score = 0.0
        reason_parts: List[str] = []

        column_score = _match_score(t, column)
        if column_score > 0.5:
            score += column_score * 0.7
            reason_parts.append(f"instruction mentions '{column}'")

        for token_group, keywords in COLUMN_KEYWORDS.items():
            if any(k in t for k in keywords):
                if any(k in _norm(column) for k in keywords):
                    score += 0.6
                    reason_parts.append(f"column name matches {token_group} keywords")
                    break

        nulls = int(meta.get("nulls", 0) or 0)
        unique = int(meta.get("unique", 0) or 0)
        dtype = _norm(str(meta.get("dtype", "")))

        if nulls > 0 and any(k in t for k in ["missing", "blank", "empty", "null", "fill", "impute"]):
            score += min(0.6, 0.1 + nulls / 10_000)
            reason_parts.append("column has missing values")
        if unique <= 1 and any(k in t for k in ["constant", "same", "duplicate", "remove"]):
            score += 0.35
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
    # detect any explicit column names mentioned in the instruction (exact whole-word matches)
    t = _norm(text)
    mentioned: List[str] = []
    for column in _profile_column_names(profile):
        try:
            if re.search(rf"\b{re.escape(column)}\b", text, flags=re.IGNORECASE):
                mentioned.append(column)
        except Exception:
            continue

    candidates = infer_columns_from_text(text, profile, top_n=5)
    suggested_columns = [column for column, _, _ in candidates]
    # prefer explicitly mentioned columns when available
    if mentioned:
        suggested_columns = mentioned + [c for c in suggested_columns if c not in mentioned]

    steps: List[CleaningStep] = []
    t = _norm(text)
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
            steps.append(CleaningStep(action="join", params={"left": left, "right": right, "keys": [key.strip()], "fuzzy": True}))
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
        move_simple = re.compile(r"(?:put|move|copy)\s+all\s+(string|text|numeric|numerical|number|numbers)\s*(?:values|entries)?(?:\s+of\s+the|\s+of)?\s*([a-zA-Z0-9_ ]+?)\s*(?:column)?\s*(?:except\s+([^,\.]+?)\s*)?.*?(?:in|into|to)\s+([a-zA-Z0-9_ ]+)", flags=re.IGNORECASE)
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
        for column in suggested_columns[:3]:
            steps.append(CleaningStep(action="normalize", column=column, params={"case": "lower" if any(k in t for k in ["lower", "lowercase"]) else "preserve"}))
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
        # Default behavior for vague instructions: target the most problematic columns.
        for column, _, _ in suggest_columns_to_clean(profile, top_n=3):
            meta = profile.get(column, {})
            if int(meta.get("nulls", 0) or 0) > 0:
                steps.append(CleaningStep(action="impute", column=column, params={"strategy": _default_strategy_for_column(meta)}))
            else:
                steps.append(CleaningStep(action="normalize", column=column, params={"case": "lower"}))

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
        reason = ""
        confidence = 0.5
        if step.column and step.column in profile:
            meta = profile[step.column]
            nulls = int(meta.get("nulls", 0) or 0)
            dtype = _norm(str(meta.get("dtype", "")))
            if step.action == "impute":
                strategy = step.params.get("strategy") if step.params else None
                if not strategy:
                    strategy = _default_strategy_for_column(meta)
                    step.params = dict(step.params or {}, strategy=strategy)
                reason = f"{step.column} has {nulls} missing values; use {strategy} for {dtype or 'unknown'} data."
                confidence = 0.8 if nulls > 0 else 0.6
            elif step.action == "normalize":
                reason = f"{step.column} appears to be a text column, so normalization is safe and deterministic."
                confidence = 0.75 if any(token in dtype for token in ["str", "utf", "object"]) else 0.6
            elif step.action == "drop_column":
                reason = f"{step.column} is explicit in the recipe and will be removed exactly."
                confidence = 0.95
            else:
                reason = f"{step.action} on {step.column} is applied literally as specified."
                confidence = 0.7
        else:
            if step.action == "impute":
                step.params = dict(step.params or {}, strategy=step.params.get("strategy") if step.params else "median")
                reason = "No column profile match found; defaulting to the first plausible imputation strategy."
                confidence = 0.35
            elif step.action == "normalize":
                reason = "No matching column profile found; normalization is applied only if the column exists."
                confidence = 0.4
            elif step.action == "drop_column":
                reason = "No profile match found; drop is only safe when the column name is exact."
                confidence = 0.35
            else:
                reason = "This step is underspecified and only a deterministic literal execution is possible."
                confidence = 0.3

        explanations.append(ExplainedStep(step=step, reason=reason, confidence=confidence))

    return explanations


def recipe_summary(recipe: Recipe) -> Dict[str, Any]:
    return {
        "sources": recipe.sources,
        "cleaning_steps": [step.model_dump() if hasattr(step, "model_dump") else step.dict() for step in recipe.cleaning_steps],
        "joins": [join.model_dump() if hasattr(join, "model_dump") else join.dict() for join in recipe.joins or []],
        "outputs": recipe.outputs,
    }
