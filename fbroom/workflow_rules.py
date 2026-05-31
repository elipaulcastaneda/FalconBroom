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


def recipe_from_plain_english(text: str, profile: Dict[str, Dict[str, Any]], source_path: str, output_path: str) -> Recipe:
    """Map a plain-English instruction into a deterministic recipe when possible."""
    action = infer_action(text)
    candidates = infer_columns_from_text(text, profile, top_n=5)
    suggested_columns = [column for column, _, _ in candidates]

    steps: List[CleaningStep] = []
    t = _norm(text)

    if action == "drop_column":
        for column in suggested_columns[:1]:
            steps.append(CleaningStep(action="drop_column", column=column, params={}))
    elif action == "impute":
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
        for column, _, _ in suggest_columns_to_clean(profile, action="deduplicate", top_n=3):
            steps.append(CleaningStep(action="deduplicate", column=column, params={"scope": "rows"}))
    elif action == "rename":
        rename_matches = re.findall(r"rename\s+([a-zA-Z0-9_ ]+)\s+to\s+([a-zA-Z0-9_ ]+)", t)
        if rename_matches:
            old_name, new_name = rename_matches[0]
            steps.append(CleaningStep(action="rename", column=old_name.strip(), params={"new_name": new_name.strip()}))
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
