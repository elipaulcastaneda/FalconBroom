import json
import logging

import pytest

from fbroom.ingest import _analyze_column, generate_plain_language_suggestions
from fbroom.step_catalog import (
    TEMPLATE_ACTIONS,
    parse_instruction,
    parse_machine_template,
    step_to_template,
    step_to_text,
    steps_to_template,
    steps_to_text,
)
from fbroom.workflow_rules import recipe_from_plain_english

logging.disable(logging.CRITICAL)

COLUMNS = ["amount", "rate", "status", "state", "created_at", "email", "name", "notes", "zip", "id", "weight kg"]
PROFILE = {c: {"dtype": "str", "nulls": 1, "unique": 5} for c in COLUMNS}

# One representative of every step shape the suggestion generator and the engine share.
STEPS = [
    {"action": "normalize", "column": "state", "params": {"case": "upper"}},
    {"action": "normalize", "column": "email", "params": {"case": "trim"}},
    {"action": "normalize", "column": "name", "params": {"case": "lower"}},
    {"action": "normalize", "column": "name", "params": {"case": "title"}},
    {"action": "cast", "column": "amount", "params": {"to": "float"}},
    {"action": "cast", "column": "amount", "params": {"to": "int"}},
    {"action": "cast", "column": "created_at", "params": {"to": "datetime", "fmt": "%Y-%m-%d"}},
    {"action": "cast", "column": "created_at", "params": {"to": "datetime", "fmt": "%m/%d/%Y"}},
    {"action": "regex_replace", "column": "amount", "params": {"pattern": r"\s*kg$", "replace": ""}},
    {"action": "regex_replace", "column": "zip", "params": {"pattern": r"^(\d{5})-\d{4}$", "replace": r"\1"}},
    {"action": "regex_replace", "column": "zip", "params": {"pattern": r"^(\d{4})$", "replace": r"0\1"}},
    {"action": "regex_replace", "column": "notes", "params": {"pattern": r"(?i)^(?:N/A|unknown)$", "replace": ""}},
    {"action": "regex_replace", "column": "email", "params": {"pattern": r"[\s().-]", "replace": ""}},
    {"action": "map", "column": "status", "params": {"mapping": {"Yes": "true", "no": "false"}}},
    {"action": "numeric_transform", "column": "rate", "params": {"operation": "divide", "factor": 100}},
    {"action": "numeric_transform", "column": "amount", "params": {"operation": "multiply", "factor": 2.5}},
    {"action": "numeric_transform", "column": "amount", "params": {"operation": "round", "ndigits": 2}},
    {"action": "numeric_transform", "column": "amount", "params": {"operation": "abs"}},
    {"action": "impute", "column": "amount", "params": {"strategy": "median"}},
    {"action": "impute", "column": "amount", "params": {"strategy": "mean"}},
    {"action": "impute", "column": "status", "params": {"strategy": "mode"}},
    {"action": "impute", "column": "amount", "params": {"strategy": "constant", "value": 0}},
    {"action": "deduplicate", "column": None, "params": {}},
    {"action": "deduplicate", "column": "email", "params": {}},
    {"action": "deduplicate", "column": ["email", "name"], "params": {}},
    {"action": "drop_column", "column": "notes", "params": {}},
    {"action": "rename", "column": "notes", "params": {"new_name": "remarks"}},
    {"action": "unicode_normalize", "column": "name", "params": {"form": "NFKC"}},
    {"action": "unicode_normalize", "column": "name", "params": {"form": "NFKD", "remove_diacritics": True}},
    {"action": "replace", "column": "notes", "params": {"old": "N/A", "new": ""}},
    {"action": "move_by_type", "column": None, "params": {"source": "zip", "target": "name", "type": "string", "exceptions": [], "replacement": ""}},
    {"action": "move_by_type", "column": None, "params": {"source": "amount", "target": "rate", "type": "numeric", "exceptions": [], "replacement": ""}},
    {"action": "cast", "column": "weight kg", "params": {"to": "float"}},
    {"action": "drop_rows", "column": "email", "params": {"issue": "blank"}},
    {"action": "drop_rows", "column": "amount", "params": {"issue": "not_numeric"}},
    {"action": "drop_rows", "column": "created_at", "params": {"issue": "not_date"}},
    {"action": "drop_rows", "column": "created_at", "params": {"issue": "not_date", "fmt": "%m/%d/%Y"}},
    {"action": "drop_rows", "column": "notes", "params": {"issue": "values", "values": ["N/A", "unknown"]}},
    {"action": "drop_rows", "column": "zip", "params": {"issue": "matches", "pattern": r"\D+"}},
    {"action": "drop_rows", "column": "email", "params": {"issue": "not_matches", "pattern": r"[^\s@]+@[^\s@]+\.[^\s@]+"}},
]

LEGACY_TEMPLATE_STEPS = [
    {"action": "bucketize", "column": "amount", "params": {"buckets": [{"min": 0.0, "max": 5.0, "label": "small"}, {"min": 5.0, "max": None, "label": "large"}]}},
    {"action": "conditional", "column": "status", "params": {"value": "review", "condition": {"column": "amount", "op": ">", "value": 10.0}}},
    {"action": "conditional", "column": None, "params": {"condition": {"column": "amount", "op": ">", "value": 10.0}, "action_text": "set status to review"}},
    {"action": "remove_by_type", "column": "notes", "params": {"target_type": "numeric", "replacement": "", "exceptions": ["date"]}},
    {"action": "swap_by_types", "column": None, "params": {"moves": [{"source": "name", "target": "notes", "type": "numeric", "exceptions": ["date"]}, {"source": "notes", "target": "name", "type": "string", "exceptions": []}], "replacement": ""}},
    {"action": "join", "column": None, "params": {"left": "customers.csv", "right": "orders.csv", "keys": ["customer_id"], "fuzzy": True}},
    {"action": "impute", "column": "amount", "params": {"strategy": "from_column", "source": "rate", "group_by": "state"}},
    {"action": "impute", "column": "amount", "params": {"strategy": "regression", "sources": ["rate", "weight kg"], "model": "ridge", "group_by": "state"}},
    {"action": "impute", "column": "amount", "params": {"strategy": "ffill"}},
    {"action": "impute", "column": "amount", "params": {"strategy": "bfill"}},
    {"action": "impute", "column": "amount", "params": {"strategy": "row_mean", "sources": ["rate", "weight kg"]}},
    {"action": "impute", "column": "amount", "params": {"strategy": "mean", "source": "rate", "group_by": "state"}},
    {"action": "numeric_transform", "column": "rate", "params": {"operation": "scale", "factor": 2.5}},
    {"action": "move_by_type", "column": None, "params": {"source": "notes", "target": "name", "type": "string", "exceptions": ["date"], "replacement": "moved"}},
    {"action": "join", "column": None, "params": {"left": "customers.csv", "right": "orders.csv", "keys": ["customer_id"], "fuzzy": True, "threshold": 0.92, "algorithm": "minhash", "num_perm": 256}},
    {"action": "regex_replace", "column": "notes", "params": {"pattern": "a.*b", "replace": "x", "flags": "IGNORECASE"}},
    {"action": "drop_rows", "column": ["email", "name"], "params": {"issue": "blank"}},
    {"action": "drop_rows", "column": "notes", "params": {"issue": "values", "values": ['say "hi"', "a,b"]}},
]

ROUND_TRIP_ACTIONS = {
    step["action"] for step in STEPS + LEGACY_TEMPLATE_STEPS
}
SCAN_SUPPORTED_ACTIONS = {
    "normalize", "impute", "cast", "regex_replace", "map", "numeric_transform",
    "deduplicate", "drop_column", "drop_rows", "unicode_normalize", "move_by_type", "remove_by_type", "swap_by_types", "rename",
}
PARSER_ONLY_ACTIONS = {"bucketize", "conditional", "join", "replace"}


def _norm(step):
    return json.loads(json.dumps(step, sort_keys=True))


@pytest.mark.parametrize("step", STEPS, ids=lambda s: f"{s['action']}-{json.dumps(s['params'], sort_keys=True)[:40]}-{s['column']}")
def test_every_step_round_trips_through_template_and_english(step):
    template = step_to_template(step)
    assert template, f"no template for {step}"
    assert _norm(parse_machine_template(template)) == _norm(step)

    text = step_to_text(step)
    assert text, f"no instruction for {step}"
    assert _norm(parse_instruction(text, COLUMNS)[0]) == _norm(step)

    recipe = recipe_from_plain_english(text, PROFILE, "x.csv", "o.csv")
    assert [_norm({"action": s.action, "column": s.column, "params": s.params}) for s in recipe.cleaning_steps] == [_norm(step)]


@pytest.mark.parametrize("step", LEGACY_TEMPLATE_STEPS, ids=lambda s: s["action"] + "-template")
def test_legacy_actions_round_trip_through_machine_template(step):
    template = step_to_template(step)
    assert template, f"no template for {step}"
    assert _norm(parse_machine_template(template)) == _norm(step)
    assert _norm(parse_instruction(template, COLUMNS)[0]) == _norm(step)


@pytest.mark.parametrize("step", LEGACY_TEMPLATE_STEPS, ids=lambda s: s["action"] + "-english")
def test_legacy_actions_round_trip_through_english_instruction(step):
    text = step_to_text(step)
    assert text, f"no instruction for {step}"
    assert _norm(parse_instruction(text, COLUMNS)[0]) == _norm(step)
    recipe = recipe_from_plain_english(text, PROFILE, "x.csv", "o.csv")
    assert [_norm({"action": item.action, "column": item.column, "params": item.params}) for item in recipe.cleaning_steps] == [_norm(step)]


def test_every_canonical_template_action_has_round_trip_coverage():
    assert ROUND_TRIP_ACTIONS == set(TEMPLATE_ACTIONS) - {"coerce"}
    assert SCAN_SUPPORTED_ACTIONS.isdisjoint(PARSER_ONLY_ACTIONS)
    assert SCAN_SUPPORTED_ACTIONS | PARSER_ONLY_ACTIONS == ROUND_TRIP_ACTIONS


def test_coerce_template_is_a_cast_alias():
    assert parse_machine_template("coerce column=amount to=float") == {
        "action": "cast", "column": "amount", "params": {"to": "float"},
    }


def test_multi_step_instruction_matches_structured_steps():
    steps = [
        {"action": "regex_replace", "column": "amount", "params": {"pattern": r"\s*kg$", "replace": ""}},
        {"action": "cast", "column": "amount", "params": {"to": "float"}},
        {"action": "numeric_transform", "column": "amount", "params": {"operation": "divide", "factor": 100}},
    ]
    assert [_norm(s) for s in parse_instruction(steps_to_text(steps), COLUMNS)] == [_norm(s) for s in steps]
    lines = steps_to_template(steps).splitlines()
    assert [_norm(parse_machine_template(line)) for line in lines] == [_norm(s) for s in steps]


@pytest.mark.parametrize("phrase,expected", [
    ("remove duplicate rows", ("deduplicate", None)),
    ("remove duplicates based on email", ("deduplicate", "email")),
    ("divide rate by 100", ("numeric_transform", "rate")),
    ("convert rate from percent to decimal", ("numeric_transform", "rate")),
    ("cast amount to float", ("cast", "amount")),
    ("convert created_at to datetime using format %m/%d/%Y", ("cast", "created_at")),
    ("treat N/A and unknown as blank in notes", ("regex_replace", "notes")),
    ("map 'yes' to 'true', 'no' to 'false' in status", ("map", "status")),
    ("rename notes to remarks", ("rename", "notes")),
    ("normalize unicode in name", ("unicode_normalize", "name")),
    ("fill missing amount with median", ("impute", "amount")),
])
def test_parser_handles_what_it_previously_misread(phrase, expected):
    recipe = recipe_from_plain_english(phrase, PROFILE, "x.csv", "o.csv")
    assert len(recipe.cleaning_steps) == 1, [s.action for s in recipe.cleaning_steps]
    step = recipe.cleaning_steps[0]
    assert (step.action, step.column) == expected


def test_unrecognized_clause_falls_back_instead_of_guessing():
    assert parse_instruction("cast amount to float and sing a song", COLUMNS) is None
    assert parse_instruction("divide missing_column by 5", COLUMNS) is None
    assert parse_instruction("divide amount by 0", COLUMNS) is None


def test_unsafe_values_use_lossless_encoded_fallbacks():
    quote_in_pattern = {"action": "regex_replace", "column": "notes", "params": {"pattern": 'a"b', "replace": ""}}
    template = step_to_template(quote_in_pattern)
    text = step_to_text(quote_in_pattern)
    assert _norm(parse_machine_template(template)) == _norm(quote_in_pattern)
    assert _norm(parse_instruction(text, COLUMNS)[0]) == _norm(quote_in_pattern)
    ambiguous_map = {"action": "map", "column": "status", "params": {"mapping": {"a,b": "c"}}}
    assert _norm(parse_machine_template(step_to_template(ambiguous_map))) == _norm(ambiguous_map)


def _all_suggestions():
    rows = ["id,status,rate,amount,state,created_at,notes,name,zip,start_date,end_date"]
    for i in range(60):
        rows.append(",".join([
            str(i % 55), ["Yes", "no"][i % 2], f"{10 + i % 5}%", f"{i}kg", ["oh", "PA"][i % 2],
            "2025-01-%02d" % (i % 28 + 1), ["N/A", "ok"][i % 2], f"n{i % 40}",
            ["02134", "10001-1234", "2343"][i % 3], "2025-03-01", "2025-02-01" if i % 3 == 0 else "2025-04-01",
        ]))
    return rows


def test_every_executable_suggestion_is_readable_by_the_parser(tmp_path):
    source = tmp_path / "mixed.csv"
    source.write_text("\n".join(_all_suggestions()), encoding="utf-8")
    suggestions = generate_plain_language_suggestions({"path": str(source)})
    executable = [s for s in suggestions if s.get("json_steps")]
    assert executable
    profile = {c: {"dtype": "str", "nulls": 0, "unique": 5} for c in
               ["id", "status", "rate", "amount", "state", "created_at", "notes", "name", "zip", "start_date", "end_date"]}
    for suggestion in executable:
        steps = suggestion["json_steps"]
        assert {step["action"] for step in steps} <= SCAN_SUPPORTED_ACTIONS
        template = suggestion.get("machine_template")
        instruction = suggestion.get("instruction")
        assert template or instruction, f"suggestion cannot be typed manually: {suggestion['text']}"
        if template:
            parsed = [parse_machine_template(line) for line in template.splitlines()]
            assert [_norm(p) for p in parsed] == [_norm(s) for s in steps], suggestion["text"]
        if instruction:
            recipe = recipe_from_plain_english(instruction, profile, "x.csv", "o.csv")
            assert [_norm({"action": s.action, "column": s.column, "params": s.params}) for s in recipe.cleaning_steps] == [_norm(s) for s in steps], suggestion["text"]


def test_uploaded_data_emits_each_evidence_supported_action_only(tmp_path):
    rows = ["number,rate,is_active,state,notes,created_at,flag,mixed,label,amount,unicode_label,weight"]
    for i in range(60):
        if i < 30 or i >= 42:
            label, amount = f"item-{i}", str(i * 10)
        else:
            label, amount = str(i * 10), f"item-{i}"
        rows.append(",".join([
            str(i), f"{10 + i % 5}%", ["Yes", "no", "1", "0"][i % 4], ["oh", "PA"][i % 2],
            ["N/A", "ok"][i % 2], f"2025-01-{i % 28 + 1:02d}", "constant",
            f"label-{i}" if i < 55 else str(i), label, amount, "ＡＢＣ" if i % 2 else "Ｆｏｏ", f"{i + 3} kg",
        ]))
    source = tmp_path / "action_inventory.csv"
    source.write_text("\n".join(rows), encoding="utf-8")
    scanned = generate_plain_language_suggestions({
        "path": str(source),
        "normalized_path": str(source),
        "row_count": 60,
        "diagnostics": {
            "number": {"missing_count": 1},
            "flag": {"unique_count": 1, "row_count": 60, "missing_count": 0},
        },
    })

    move_source = tmp_path / "move_evidence.csv"
    move_source.write_text(
        "narrative,amount\n" + "\n".join([f"{100 + i}," for i in range(5)] + [f"label-{i},{i * 10}" for i in range(35)]),
        encoding="utf-8",
    )
    scanned.extend(generate_plain_language_suggestions({"path": str(move_source), "normalized_path": str(move_source)}))

    duplicate_source = tmp_path / "duplicate_evidence.csv"
    duplicate_source.write_text("id,value\n1,a\n2,b\n1,a", encoding="utf-8")
    scanned.extend(generate_plain_language_suggestions({"path": str(duplicate_source)}))

    actions = {step["action"] for suggestion in scanned for step in suggestion.get("json_steps", [])}
    assert actions == SCAN_SUPPORTED_ACTIONS
    assert actions.isdisjoint(PARSER_ONLY_ACTIONS)


def test_parser_capabilities_all_have_a_suggestion_path(tmp_path):
    source = tmp_path / "caps.csv"
    constant = ["id,flag,left,spare"] + [f"{i},same,{'abc' if i % 2 else '12'}," for i in range(40)]
    source.write_text("\n".join(constant), encoding="utf-8")
    suggestions = generate_plain_language_suggestions({
        "path": str(source),
        "normalized_path": str(source),
        "diagnostics": {"flag": {"unique_count": 1, "row_count": 40, "missing_count": 0}},
    })
    actions = {step["action"] for s in suggestions for step in s.get("json_steps", [])}
    assert "drop_column" in actions
    assert "move_by_type" not in actions
    for s in suggestions:
        if s.get("source") == "date_normalization":
            assert "json_steps" not in s
