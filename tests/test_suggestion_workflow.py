from pathlib import Path
import csv
import pytest
from fastapi.testclient import TestClient
import polars as pl

from fbroom.ingest import _analyze_column
from fbroom.main import app
from fbroom.engine import _cast_column


def _disable_upload_analysis(monkeypatch, ingest_module):
    monkeypatch.setattr(ingest_module, "Cleaner", lambda: None)
    monkeypatch.setattr(ingest_module, "generate_plain_language_suggestions", lambda metadata: [])
    monkeypatch.setattr(ingest_module, "compute_extended_diags", lambda *args: None)


def test_identifier_and_year_suggestions_keep_transformations_structured():
    years = _analyze_column("construction_year", ["2003yrs", "2018yrs"] * 10)
    year_steps = years["entries"][0]["json_steps"]
    assert [step["action"] for step in year_steps] == ["regex_replace", "cast"]
    assert year_steps[-1]["params"]["to"] == "int"

    postal = _analyze_column("postal_code", ["02134", "11111-2222"] * 10)
    zip4 = next(entry for entry in postal["entries"] if "ZIP+4" in entry["text"])
    assert zip4["json_steps"][0]["action"] == "regex_replace"
    assert zip4["json_steps"][0]["column"] == "postal_code"


def test_abbreviation_columns_use_uppercase_without_lowercasing_full_state_names():
    state_codes = _analyze_column("state", ["oh", "PA", "ny"] * 10)
    case_entry = next(entry for entry in state_codes["entries"] if "uppercase" in entry["text"])
    assert case_entry["json_steps"][0]["params"]["case"] == "upper"

    country_codes = _analyze_column("country_code", ["us", "CA", "gb"] * 10)
    code_entry = next(entry for entry in country_codes["entries"] if "uppercase" in entry["text"])
    assert code_entry["json_steps"][0]["params"]["case"] == "upper"

    state_names = _analyze_column("state_name", ["Ohio", "ohio", "Pennsylvania", "pennsylvania"] * 5)
    assert not any(entry.get("json_steps", [{}])[0].get("params", {}).get("case") == "lower" for entry in state_names["entries"])

    organizations = _analyze_column("organization", ["ibm", "IBM", "nasa", "NASA"] * 5)
    org_case = next(entry for entry in organizations["entries"] if "uppercase" in entry["text"])
    assert org_case["json_steps"][0]["params"]["case"] == "upper"

    organization_codes = _analyze_column("organization_code", ["3m", "B2B", "3M", "b2b"] * 5)
    code_case = next(entry for entry in organization_codes["entries"] if "uppercase" in entry["text"])
    assert code_case["json_steps"][0]["params"]["case"] == "upper"

    organization_names = _analyze_column("organization", ["Acme", "ACME", "Northwind", "NORTHWIND"] * 5)
    assert all("json_steps" not in entry for entry in organization_names["entries"])


def test_numeric_and_identifier_edge_cases_are_conservative():
    mixed_units = _analyze_column("measurement", ["10kg", "5lb", "12kg", "8lb"] * 5)
    assert mixed_units["kind"] in ("mixed", "numeric")
    assert any("mixes mass units" in entry["text"] for entry in mixed_units["entries"])

    currency = _analyze_column("price", ["$1,200.50", "$25.00"] * 10)
    assert currency["entries"][0]["json_steps"][0]["action"] == "cast"

    placeholders = _analyze_column("amount", ["10", "N/A", "15", "unknown"] * 5)
    assert placeholders["entries"][0]["json_steps"][0]["action"] == "regex_replace"

    ambiguous_dates = _analyze_column("date", ["01/02/2024", "03/04/2024"] * 10)
    assert ambiguous_dates["kind"] == "date_review"
    assert all("json_steps" not in entry for entry in ambiguous_dates["entries"])

    locale_amounts = _analyze_column("amount", ["1.234,56", "25.00"] * 10)
    assert any("mixes" in entry["text"] for entry in locale_amounts["entries"])
    currency_locale = _analyze_column("amount", ["€1.234,56", "€25,00"] * 10)
    assert currency_locale["kind"] == "locale_number_review"

    phone = _analyze_column("phone", ["+1 (212) 555-0199", "+44 20 7946 0958"] * 10)
    assert any(e["json_steps"][0]["params"].get("pattern") == r"[\s().-]" for e in phone["entries"] if "json_steps" in e)

    flags = _analyze_column("is_active", ["Yes", "no", "1", "0"] * 10)
    boolean_map = next(entry for entry in flags["entries"] if "true/false" in entry["text"])
    assert boolean_map["json_steps"][0]["params"]["mapping"] == {
        "Yes": "true", "no": "false", "1": "true", "0": "false",
    }

    flags = _analyze_column("is_enabled", ["Enabled", "off", "active", "disabled"] * 10)
    extended_bool_map = next(entry for entry in flags["entries"] if "true/false" in entry["text"])
    assert extended_bool_map["json_steps"][0]["params"]["mapping"] == {
        "Enabled": "true", "off": "false", "active": "true", "disabled": "false",
    }

    casted, _ = _cast_column(pl.DataFrame({"amount": ["(250.00)", "$1,200.50"]}), "amount", "float")
    assert casted["amount"].to_list() == [-250.0, 1200.5]


def test_consistent_dates_and_nonnumeric_markers_get_safe_steps():
    iso_dates = _analyze_column("created_at", ["2025-01-02", "2025-03-04"] * 10)
    assert iso_dates["entries"][0]["json_steps"][0]["params"] == {"to": "datetime", "fmt": "%Y-%m-%d"}

    us_dates = _analyze_column("created_at", ["12/31/2025", "01/15/2026"] * 10)
    assert us_dates["entries"][0]["json_steps"][0]["params"]["fmt"] == "%m/%d/%Y"

    placeholders = _analyze_column("category", ["retail", "unknown", "wholesale", "TBD"] * 5)
    marker_entry = next(entry for entry in placeholders["entries"] if "does not impute" in entry["text"])
    assert marker_entry["json_steps"][0]["action"] == "regex_replace"


def test_percentage_fraction_is_explicit_and_metric_outliers_are_review_only():
    percentages = _analyze_column("completion_rate", ["10%", "25%"] * 10)
    fraction = next(entry for entry in percentages["entries"] if "decimal fractions" in entry["text"])
    assert [step["action"] for step in fraction["json_steps"]] == ["regex_replace", "cast", "numeric_transform"]
    assert fraction["json_steps"][-1]["params"] == {"operation": "divide", "factor": 100}

    age_values = [str(value) for value in range(1, 21)] + ["10000"]
    age = _analyze_column("age", age_values)
    outlier = next(entry for entry in age["entries"] if "statistical outlier" in entry["text"])
    assert "json_steps" not in outlier


def test_potential_key_missing_values_and_cross_column_ranges_are_review_only(tmp_path):
    customer_ids = _analyze_column("customer_id", ["C1", "C2", None] * 10)
    missing_note = next(entry for entry in customer_ids["entries"] if "blank or recognized missing" in entry["text"])
    assert "json_steps" not in missing_note

    source = tmp_path / "ranges.csv"
    rows = ["start_date,end_date"]
    rows.extend(["2025-03-01,2025-02-01"] * 5)
    rows.extend(["2025-01-01,2025-02-01"] * 25)
    source.write_text("\n".join(rows), encoding="utf-8")
    from fbroom.ingest import generate_plain_language_suggestions

    suggestions = generate_plain_language_suggestions({"path": str(source), "normalized_path": str(source)})
    range_note = next(entry for entry in suggestions if "sampled row(s)" in entry["text"] and "start_date" in entry["text"])
    assert "json_steps" not in range_note


def test_exact_duplicate_rows_generate_reviewable_dedupe_step(tmp_path):
    from fbroom.ingest import generate_plain_language_suggestions

    source = tmp_path / "duplicate_rows.csv"
    source.write_text("id,value\n1,a\n2,b\n1,a\n", encoding="utf-8")
    suggestions = generate_plain_language_suggestions({"path": str(source)})
    duplicate = next(entry for entry in suggestions if "exact duplicate row" in entry["text"])
    assert duplicate["json_steps"] == [{"action": "deduplicate", "column": None, "params": {}}]

    response = TestClient(app).post("/recipe-from-text", json={
        "instruction": duplicate["text"],
        "source_path": str(source),
        "suggested_steps": duplicate["json_steps"],
    })
    assert response.status_code == 200, response.text
    assert response.json()["recipe"]["cleaning_steps"][0]["action"] == "deduplicate"
    assert len(response.json()["preview"]["after"]) == 2


def test_source_upload_with_string_keys_retains_suggestion_prompts(tmp_path, monkeypatch):
    import fbroom.main as main_module

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(main_module, "UPLOAD_DIR", upload_dir)
    rows = ["customer_key,amount"]
    rows.extend(f"C-{100 + i},{'TBD' if i == 3 else i * 10}" for i in range(12))

    response = TestClient(app).post(
        "/upload",
        files={"file": ("source.csv", "\n".join(rows).encode("utf-8"), "text/csv")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["row_count"] == len(rows)
    assert Path(payload["normalized_path"]).exists()
    suggestion = next(
        item for item in payload["plain_language_suggestions"]
        if "Treat TBD in 'amount' as missing" in item["text"]
    )
    assert [step["action"] for step in suggestion["json_steps"]] == ["regex_replace", "cast"]
    assert suggestion["machine_template"] and suggestion["instruction"]
    assert suggestion["category"] == "fix"


def test_source_upload_streams_large_payload_and_reports_exact_size(tmp_path, monkeypatch):
    import fbroom.main as main_module

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(main_module, "UPLOAD_DIR", upload_dir)
    payload = b"x" * (1024 * 1024 + 137)
    uploaded_path = None

    def fake_convert_uploaded_file(source_path, output_dir):
        nonlocal uploaded_path
        uploaded_path = Path(source_path)
        return {
            "path": str(uploaded_path),
            "normalized_path": str(uploaded_path),
            "source_path": str(uploaded_path),
            "source_kind": "csv",
            "row_count": 0,
            "warnings": [],
        }

    monkeypatch.setattr(main_module, "convert_uploaded_file", fake_convert_uploaded_file)
    response = TestClient(app).post(
        "/upload",
        files={"file": ("large.csv", payload, "text/csv")},
    )

    assert response.status_code == 200, response.text
    assert response.json()["size"] == len(payload)
    assert uploaded_path is not None
    assert uploaded_path.read_bytes() == payload


def test_source_upload_and_metadata_refresh_with_integer_keys_retains_suggestions(tmp_path, monkeypatch):
    import fbroom.main as main_module

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(main_module, "UPLOAD_DIR", upload_dir)
    rows = ["customer_id,amount"]
    rows.extend(f"{1000 + i},{'TBD' if i == 3 else i * 10}" for i in range(12))
    client = TestClient(app)

    response = client.post(
        "/upload",
        files={"file": ("integer_source.csv", "\n".join(rows).encode("utf-8"), "text/csv")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["row_count"] == len(rows)
    immediate = next(
        item for item in payload["plain_language_suggestions"]
        if "Treat TBD in 'amount' as missing" in item["text"]
    )
    assert immediate["category"] == "fix"
    inspected = client.post("/profile", json={"path": payload["normalized_path"]})
    assert inspected.status_code == 200, inspected.text
    assert "customer_id" in inspected.json()["profile"]

    refreshed = client.post("/metadata", json={"path": payload["normalized_path"]})
    assert refreshed.status_code == 200, refreshed.text
    suggestions = refreshed.json()["metadata"]["plain_language_suggestions"]
    prompt = next(item for item in suggestions if "Treat TBD in 'amount' as missing" in item["text"])
    assert prompt["json_steps"] == immediate["json_steps"]
    assert prompt["instruction"] == immediate["instruction"]
    assert prompt["category"] == "fix"


def test_convert_uploaded_file_streams_rows_with_existing_csv_output(tmp_path, monkeypatch):
    import fbroom.ingest as ingest_module

    _disable_upload_analysis(monkeypatch, ingest_module)
    source = tmp_path / "source.csv"
    source.write_text("alpha,beta\none,two\n", encoding="utf-8")

    result = ingest_module.convert_uploaded_file(source, tmp_path)

    with Path(result["normalized_path"]).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == [
            "source_kind", "source_name", "source_path", "unit_kind", "container_name",
            "sheet_name", "slide_number", "paragraph_index", "table_index", "row_index",
            "column_index", "cell_label", "text", "style_json", "notes",
        ]
        rows = list(reader)

    assert result["row_count"] == 2
    assert [row["text"] for row in rows] == ["alpha,beta", "one,two"]
    assert [row["row_index"] for row in rows] == ["1", "2"]
    assert [row["style_json"] for row in rows] == [
        '{"delimiter": ",", "fields": 2}',
        '{"delimiter": ",", "fields": 2}',
    ]


def test_convert_uploaded_file_preserves_placeholder_for_empty_extraction(tmp_path, monkeypatch):
    import fbroom.ingest as ingest_module

    _disable_upload_analysis(monkeypatch, ingest_module)
    monkeypatch.setattr(ingest_module, "_extract_text_rows", lambda source_path, source_kind, rows: None)
    source = tmp_path / "empty.txt"
    source.write_text("unused", encoding="utf-8")

    result = ingest_module.convert_uploaded_file(source, tmp_path)

    with Path(result["normalized_path"]).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert result["row_count"] == 1
    assert "added a placeholder row" in result["warnings"][-1]
    assert rows[0]["unit_kind"] == "placeholder"
    assert rows[0]["text"] == ""


def test_convert_uploaded_file_does_not_publish_partial_rows_after_extraction_error(tmp_path, monkeypatch):
    import fbroom.ingest as ingest_module

    source = tmp_path / "broken.xlsx"
    source.write_bytes(b"not a workbook")
    target = tmp_path / f"broken_{source.stat().st_size}_{source.stat().st_mtime_ns & 0xFFFF:x}.csv"
    target.write_bytes(b"existing output")

    def fail_after_one_row(source_path, rows):
        ingest_module._append_row(rows, source_kind="xlsx", text="partial")
        raise RuntimeError("extraction failed")

    monkeypatch.setattr(ingest_module, "_extract_workbook_rows", fail_after_one_row)
    with pytest.raises(RuntimeError, match="extraction failed"):
        ingest_module.convert_uploaded_file(source, tmp_path)

    assert target.read_bytes() == b"existing output"
    assert not list(tmp_path.glob(f".{target.name}.*.tmp"))


def test_analyze_columns_samples_evenly_and_counts_duplicates_across_full_table(tmp_path, monkeypatch):
    import fbroom.ingest as ingest_module

    values = list(range(5001)) + [0]
    source = tmp_path / "large_values.csv"
    source.write_text("value\n" + "\n".join(map(str, values)) + "\n", encoding="utf-8")
    captured = {}

    def capture_sample(column, sample_values):
        captured[column] = sample_values
        return None

    monkeypatch.setattr(ingest_module, "_analyze_column", capture_sample)
    result = ingest_module._analyze_columns(str(source))

    indices = [index * (len(values) - 1) // 4999 for index in range(5000)]
    assert captured["value"] == [values[index] for index in indices]
    assert "Found 2 exact duplicate row(s)" in result["__dataset__"]["entries"][0]["text"]


def test_convert_uploaded_file_reuses_frame_with_identical_analysis_outputs(tmp_path, monkeypatch):
    import fbroom.engine as engine_module
    import fbroom.ingest as ingest_module

    source = tmp_path / "analysis.csv"
    source.write_text(
        "customer_id,amount\n" + "\n".join(f"{1000 + i},{'TBD' if i == 3 else i * 10}" for i in range(12)) + "\n",
        encoding="utf-8",
    )
    read_count = 0
    original_read = ingest_module._read_table

    def counted_read(path):
        nonlocal read_count
        read_count += 1
        return original_read(path)

    monkeypatch.setattr(ingest_module, "_read_table", counted_read)
    monkeypatch.setattr(engine_module, "_read_table", counted_read)
    monkeypatch.setattr(ingest_module, "compute_extended_diags", lambda *args: None)

    shared_result = ingest_module.convert_uploaded_file(source, tmp_path)
    shared_reads = read_count
    read_count = 0

    reference = dict(shared_result)
    cleaner = ingest_module.Cleaner()
    reference["profile"] = cleaner.profile(shared_result["normalized_path"])
    reference["inspect_preview"] = cleaner.inspect_source(shared_result["normalized_path"], offset=0, limit=20)
    reference["diagnostics"] = reference["inspect_preview"].get("diagnostics")
    reference["plain_language_suggestions"] = ingest_module.generate_plain_language_suggestions(reference)
    path_only_reads = read_count

    output_keys = ("profile", "inspect_preview", "plain_language_suggestions", "row_count", "warnings")
    assert {key: shared_result.get(key) for key in output_keys} == {key: reference.get(key) for key in output_keys}
    assert shared_reads == 1
    assert path_only_reads > shared_reads


def test_repeated_potential_keys_are_flagged_without_auto_deduplication():
    result = _analyze_column("customer_id", ["C-1", "C-2", "C-1"] * 10)
    duplicate_note = next(entry for entry in result["entries"] if "Potential key field" in entry["text"])
    assert "json_steps" not in duplicate_note
    assert "no rows will be removed" in duplicate_note["text"].lower()
    assert all(entry.get("json_steps", [{}])[0].get("action") != "deduplicate" for entry in result["entries"])


def test_missing_numeric_suggestions_do_not_hide_cast_failures(tmp_path):
    from fbroom.ingest import generate_plain_language_suggestions

    source = tmp_path / "amounts.csv"
    source.write_text("amount\n" + "10\n20\n" * 9 + "TBD\nunknown\n", encoding="utf-8")
    safe = generate_plain_language_suggestions({
        "path": str(source),
        "diagnostics": {"amount": {"missing_count": 0}},
    })
    assert any("Treat TBD, unknown" in entry["text"] for entry in safe)

    mixed_source = tmp_path / "mixed.csv"
    mixed_source.write_text("amount\n" + "10\n20\n" * 10 + "N/A\n" + "bad\n" * 12, encoding="utf-8")
    mixed = generate_plain_language_suggestions({
        "path": str(mixed_source),
        "diagnostics": {"amount": {"missing_count": 2}},
    })
    assert not any(entry["text"].startswith("Convert 'amount' to numeric") for entry in mixed)


def test_recipe_generation_uses_selected_structured_steps(tmp_path):
    source = tmp_path / "postal.csv"
    source.write_text("zipcode\n" + "\n".join(["11111-2222", "02134"] * 10), encoding="utf-8")
    step = {
        "action": "regex_replace",
        "column": "zipcode",
        "params": {"pattern": r"^(\d{5})-\d{4}$", "replace": r"\1"},
    }

    response = TestClient(app).post(
        "/recipe-from-text",
        json={
            "instruction": "prose should not be reparsed into a different action",
            "source_path": str(source),
            "suggested_steps": [step],
        },
    )

    assert response.status_code == 200, response.text
    recipe = response.json()["recipe"]
    assert recipe["cleaning_steps"] == [step]
    preview = response.json()["preview"]
    assert preview["before"][0]["zipcode"] == "11111-2222"
    assert preview["after"][0]["zipcode"] == "11111"

    output = tmp_path / "clean.csv"
    recipe["outputs"] = [{"path": str(output)}]
    applied = TestClient(app).post("/apply", json=recipe)
    assert applied.status_code == 200, applied.text
    assert output.exists()
    assert "11111" in output.read_text(encoding="utf-8")


@pytest.mark.parametrize("step", [
    {"action": "impute", "column": "amount", "params": {"strategy": "row_mean", "sources": ["missing_feature"]}},
    {"action": "conditional", "column": "amount", "params": {"value": 0, "condition": {"column": "missing_condition", "op": ">", "value": 10}}},
    {"action": "swap_by_types", "column": None, "params": {"moves": [{"source": "label", "target": "missing_target", "type": "string"}]}},
    {"action": "join", "column": None, "params": {"left": "left.csv", "right": "right.csv", "keys": ["missing_key"]}},
])
def test_recipe_generation_rejects_unknown_nested_column_references(tmp_path, step):
    source = tmp_path / "nested.csv"
    source.write_text("amount,label\n1,alpha\n2,beta\n", encoding="utf-8")

    response = TestClient(app).post("/recipe-from-text", json={
        "instruction": "apply selected structured step",
        "source_path": str(source),
        "suggested_steps": [step],
    })

    assert response.status_code == 400
    assert "unknown column" in response.json()["detail"].lower()


@pytest.mark.parametrize("majority,minority,target_type", [
    ("text", "numeric", "numeric"),
    ("numeric", "text", "string"),
])
def test_mixed_type_minority_suggests_removal_with_evidence(tmp_path, majority, minority, target_type):
    from fbroom.ingest import generate_plain_language_suggestions

    source = tmp_path / "minority.csv"
    majority_values = [f"label-{i}" if majority == "text" else str(i + 10) for i in range(35)]
    minority_values = [str(i + 100) if minority == "numeric" else f"bad-{i}" for i in range(5)]
    source.write_text("value\n" + "\n".join(majority_values + minority_values), encoding="utf-8")

    suggestions = generate_plain_language_suggestions({"path": str(source), "normalized_path": str(source)})
    suggestion = next(entry for entry in suggestions if entry.get("source") == "mixed_type_minority")
    from fbroom.step_catalog import parse_instruction

    assert suggestion["json_steps"] == [{
        "action": "remove_by_type",
        "column": "value",
        "params": {"target_type": target_type, "replacement": ""},
    }]
    assert suggestion["machine_template"]
    assert suggestion["instruction"]
    assert parse_instruction(suggestion["instruction"], ["value"]) == suggestion["json_steps"]


def test_cross_column_type_swap_suggestion_is_previewable_and_ids_are_excluded(tmp_path):
    from fbroom.ingest import generate_plain_language_suggestions

    source = tmp_path / "swapped.csv"
    rows = ["label,amount,id"]
    rows.extend(f"{i * 10},item-{i},ID-{i}" for i in range(12))
    rows.extend(f"item-{i},{i * 10},ID-{i}" for i in range(60))
    rows.extend(f"item-{i},{i * 10},12345" for i in range(5))
    source.write_text("\n".join(rows), encoding="utf-8")

    suggestions = generate_plain_language_suggestions({"path": str(source), "normalized_path": str(source)})
    swap = next(entry for entry in suggestions if entry.get("source") == "cross_column_type_swap")
    step = swap["json_steps"][0]
    from fbroom.step_catalog import parse_instruction

    assert step["action"] == "swap_by_types"
    assert step["params"]["moves"] == [
        {"source": "label", "target": "amount", "type": "numeric", "exceptions": []},
        {"source": "amount", "target": "label", "type": "string", "exceptions": []},
    ]
    assert parse_instruction(swap["instruction"], ["label", "amount", "id"]) == [step]
    assert not any(
        entry.get("source") == "mixed_type_minority"
        and entry["json_steps"][0]["column"] in {"label", "amount"}
        for entry in suggestions
    )
    assert not any(
        entry.get("source") == "mixed_type_minority" and entry["json_steps"][0]["column"] == "id"
        for entry in suggestions
    )

    response = TestClient(app).post("/recipe-from-text", json={
        "instruction": swap["instruction"],
        "source_path": str(source),
        "suggested_steps": [step],
    })
    assert response.status_code == 200, response.text
    after = response.json()["preview"]["after"]
    assert after[0]["label"] == "item-0"
    assert after[0]["amount"] == "0"


CITIES = ["Flint", "Dallas", "Houston", "Austin", "Denver"]


def _zip_transfer(tmp_path, name, misplaced_city, extra=""):
    from fbroom.ingest import generate_plain_language_suggestions

    rows = ["zipcode,city"]
    rows.extend(f"{CITIES[i % 5]},{misplaced_city(i)}" for i in range(8))
    rows.extend(f"{10000 + i:05d},{CITIES[i % 5]}" for i in range(40))
    if extra:
        rows.append(extra)
    source = tmp_path / name
    source.write_text("\n".join(rows), encoding="utf-8")
    return source, generate_plain_language_suggestions({"path": str(source), "normalized_path": str(source)})


def test_zip_text_is_swapped_with_zip_numbers_in_similar_column(tmp_path):
    from fbroom.step_catalog import parse_instruction

    source, suggestions = _zip_transfer(tmp_path, "zip_swap.csv", lambda i: f"{20000 + i:05d}")
    entry = next(item for item in suggestions if item.get("source") == "cross_column_shape_swap")
    step = entry["json_steps"][0]
    assert step["action"] == "swap_by_types"
    assert {move["source"] for move in step["params"]["moves"]} == {"zipcode", "city"}
    assert parse_instruction(entry["instruction"], ["zipcode", "city"]) == [step]
    assert not any("are text, not ZIP codes" in item["text"] for item in suggestions)

    response = TestClient(app).post("/recipe-from-text", json={
        "instruction": entry["instruction"], "source_path": str(source), "suggested_steps": [step],
    })
    assert response.status_code == 200, response.text
    first = response.json()["preview"]["after"][0]
    assert first["zipcode"] == "20000" and first["city"] == "Flint"


def test_zip_text_is_moved_when_similar_column_is_blank_on_those_rows(tmp_path):
    _, suggestions = _zip_transfer(tmp_path, "zip_move.csv", lambda i: "")
    step = next(item for item in suggestions if item.get("source") == "zip_text_transfer")["json_steps"][0]
    assert step["action"] == "move_by_type"
    assert step["params"]["source"] == "zipcode" and step["params"]["target"] == "city"


def _write_pairs(tmp_path, name, header, crossed, home):
    path = tmp_path / name
    path.write_text("\n".join([header] + [f"{a},{b}" for a, b in crossed + home]), encoding="utf-8")
    return path


def _shape_swap_step(path):
    from fbroom.ingest import generate_plain_language_suggestions

    suggestions = generate_plain_language_suggestions({"path": str(path), "normalized_path": str(path)})
    entry = next(item for item in suggestions if item.get("source") == "cross_column_shape_swap")
    return suggestions, entry, entry["json_steps"][0]


def test_zip_and_city_values_swap_on_corresponding_rows_including_zip4(tmp_path):
    from fbroom.step_catalog import parse_instruction

    cities = ["Flint", "Dallas", "Houston", "Austin", "Denver", "Reno"]
    crossed = [(cities[i % 6], f"{20000 + i:05d}") for i in range(6)]
    crossed += [(cities[i % 6], f"{30000 + i}-{1000 + i}") for i in range(3)]
    crossed += [(cities[i % 6], f"{4000 + i}X") for i in range(3)]
    home = [(f"{10000 + i:05d}", cities[i % 6]) for i in range(40)]
    path = _write_pairs(tmp_path, "zip_city_swap.csv", "zipcode,city", crossed, home)

    suggestions, entry, step = _shape_swap_step(path)
    assert step["action"] == "swap_by_types"
    assert not any("are text, not ZIP codes" in item["text"] for item in suggestions)
    assert not any(item.get("source", "").startswith("zip_text_transfer") for item in suggestions)
    assert parse_instruction(entry["instruction"], ["zipcode", "city"]) == [step]

    response = TestClient(app).post("/recipe-from-text", json={
        "instruction": entry["instruction"], "source_path": str(path), "suggested_steps": [step],
    })
    assert response.status_code == 200, response.text
    recipe = response.json()["recipe"]
    output = tmp_path / "swapped.csv"
    recipe["outputs"] = [{"path": str(output)}]
    assert TestClient(app).post("/apply", json=recipe).status_code == 200
    result = pl.read_csv(output, infer_schema=False)
    assert result["zipcode"].to_list()[:12] == [b for _, b in crossed]
    assert result["city"].to_list()[:12] == [a for a, _ in crossed]
    assert result["zipcode"].to_list()[12:] == [a for a, _ in home]
    assert result["city"].to_list()[12:] == [b for _, b in home]


def test_shape_swap_is_general_not_zip_specific(tmp_path):
    names = ["Alice Smith", "Bob Jones", "Carol White", "Dan Brown", "Erin Green", "Frank Hall"]
    crossed = [(f"{200 + i}-555-01{i:02d}", names[i % 6]) for i in range(12)]
    home = [(names[i % 6], f"{300 + i % 7}-555-02{i:02d}") for i in range(60)]
    path = _write_pairs(tmp_path, "phone_name.csv", "name,phone", crossed, home)
    _, entry, step = _shape_swap_step(path)
    assert {move["source"] for move in step["params"]["moves"]} == {"name", "phone"}
    from fbroom.engine import _read_table, _swap_by_types

    result, info = _swap_by_types(_read_table(str(path)), step["params"]["moves"], "")
    assert info["rows_changed"] == 12
    assert result["name"].to_list()[0] == names[0]
    assert result["phone"].to_list()[0] == "200-555-0100"


def test_text_columns_that_only_differ_in_casing_are_not_offered_as_swaps(tmp_path):
    import random
    from fbroom.ingest import generate_plain_language_suggestions

    random.seed(7)
    columns = {
        "builder_name": ["Cedar Ridge Homes", "Valley Stone Homes", "Summit House Builders", "Blue Oak Construction"],
        "city": ["Orlando", "Salt Lake City", "Boston", "Atlanta", "Austin"],
        "state": ["Massachusetts", "New Jersey", "Georgia", "Utah", "Texas"],
        "home_type": ["single-family", "townhouse", "Town home", "condo"],
    }

    def messy(value):
        return random.choice([value, value.lower(), value.upper(), value.title()])

    rows = [",".join(columns)]
    for _ in range(2000):
        rows.append(",".join(messy(random.choice(values)) for values in columns.values()))
    path = tmp_path / "clean_but_messy_case.csv"
    path.write_text("\n".join(rows), encoding="utf-8")
    suggestions = generate_plain_language_suggestions({"path": str(path), "normalized_path": str(path)})
    assert not any(item.get("source") in ("cross_column_shape_swap", "cross_column_type_swap") for item in suggestions)


def test_genuinely_swapped_text_columns_are_still_found_and_fixed_only_on_those_rows(tmp_path):
    import random
    from fbroom.engine import _read_table, _swap_by_types

    random.seed(11)
    builders = ["Cedar Ridge Homes", "Valley Stone Homes", "Summit House Builders", "Blue Oak Construction"]
    cities = ["Orlando", "Salt Lake City", "Boston", "Atlanta", "Austin"]
    pairs = [(random.choice(builders), random.choice(cities)) for _ in range(300)]
    swapped_rows = set(range(10, 310, 25))
    rows = ["builder_name,city"]
    for index, (builder, city) in enumerate(pairs):
        rows.append(f"{city},{builder}" if index in swapped_rows else f"{builder},{city}")
    path = tmp_path / "text_swap.csv"
    path.write_text("\n".join(rows), encoding="utf-8")
    _, entry, step = _shape_swap_step(path)
    assert set(entry["columns"]) == {"builder_name", "city"}
    result, info = _swap_by_types(_read_table(str(path)), step["params"]["moves"], "")
    assert result["builder_name"].to_list() == [b for b, _ in pairs]
    assert result["city"].to_list() == [c for _, c in pairs]
    assert info["rows_changed"] == len(swapped_rows)


def test_shape_swap_does_not_fire_for_street_addresses_in_the_other_column(tmp_path):
    from fbroom.ingest import generate_plain_language_suggestions

    cities = ["Flint", "Dallas", "Houston", "Austin", "Denver", "Reno"]
    crossed = [(cities[i % 6], f"{9000 + i} Maple St") for i in range(8)]
    home = [(f"{10000 + i:05d}", cities[i % 6]) for i in range(40)]
    path = _write_pairs(tmp_path, "zip_address.csv", "zipcode,city", crossed, home)
    suggestions = generate_plain_language_suggestions({"path": str(path), "normalized_path": str(path)})
    assert not any(item.get("source") == "cross_column_shape_swap" for item in suggestions)


def test_messy_but_correct_dates_counts_and_costs_are_not_offered_as_swaps(tmp_path):
    import random
    from fbroom.ingest import generate_plain_language_suggestions

    random.seed(3)
    dates = ["2024-08-26", "2023-12-10", "Apr 17", "03/04/2024", "5 May 2023", "2023", "2024", "2025", "March 2022"]
    costs = ["USD 359975", "USD 1007000", "1.156.250", "$420,000", "355000", "512000.50"]
    rows = ["start_date,bedrooms,stories,lot_size_sqft,actual_cost_usd"]
    for _ in range(1500):
        rows.append(",".join([
            random.choice(dates), random.choice(["2", "3", "4", "5", "4675.0"] * 12 + ["2024-08-26", "2023-12-10"]),
            random.choice(["1", "2", "2.5", "3.5", "1.5"]), random.choice(["5000", "7200.5", "9100", "1.5"]),
            random.choice(costs),
        ]))
    path = tmp_path / "messy_correct.csv"
    path.write_text("\n".join(rows), encoding="utf-8")
    suggestions = generate_plain_language_suggestions({"path": str(path), "normalized_path": str(path)})
    assert not any(item.get("source") == "cross_column_shape_swap" for item in suggestions)


def test_dates_and_numbers_really_swapped_are_still_found(tmp_path):
    dates = ["2024-08-26", "2023-12-10", "2022-01-05", "2021-07-19"]
    crossed = [(str(2 + i % 4), dates[i % 4]) for i in range(8)]
    home = [(dates[i % 4], str(2 + i % 4)) for i in range(40)]
    path = _write_pairs(tmp_path, "date_beds.csv", "start_date,bedrooms", crossed, home)
    _, entry, _ = _shape_swap_step(path)
    assert set(entry["columns"]) == {"start_date", "bedrooms"}


def _suggestions_for(tmp_path, name, columns, rows=1200, seed=5):
    import random
    from fbroom.ingest import generate_plain_language_suggestions

    random.seed(seed)
    lines = [",".join(columns)]
    for _ in range(rows):
        lines.append(",".join(random.choice(values) for values in columns.values()))
    path = tmp_path / name
    path.write_text("\n".join(lines), encoding="utf-8")
    return generate_plain_language_suggestions({"path": str(path), "normalized_path": str(path)})


def _swap_sources(suggestions):
    return [item for item in suggestions if item.get("source") in ("cross_column_shape_swap", "cross_column_type_swap")]


def test_phone_zip_place_and_date_columns_in_mixed_formats_are_not_swaps(tmp_path):
    suggestions = _suggestions_for(tmp_path, "formats.csv", {
        "home_phone": ["555-123-4567", "(555) 123-4567", "555.123.4567", "+1 555 123 4567", "5551234567", "123-4567"],
        "mobile_phone": ["(212) 555-0199", "212-555-0199", "+44 20 7946 0958", "2125550199", "212 555 0199"],
        "zip": ["84101", "10001-1234", "M5V 3L9", "SW1A 1AA", "90210", "60601"],
        "city": ["Salt Lake City", "Toronto", "London", "Beverly Hills", "Chicago", "São Paulo", "Zürich", "St. Louis"],
        "birth_place": ["Mumbai", "Lagos", "Osaka", "Texas", "Canada", "Orlando, FL", "Nairobi"],
        "start_date": ["2024-08-26", "Apr 17", "03/04/2024", "5 May 2023", "2023", "March 2022"],
        "end_date": ["2025-01-02", "12/31/2024", "Jan 3, 2025", "2025", "June 2025"],
    })
    assert not _swap_sources(suggestions)


def test_temperature_weather_binary_and_company_columns_are_not_swaps(tmp_path):
    suggestions = _suggestions_for(tmp_path, "weather.csv", {
        "temp_f": ["72°F", "68 F", "75.5°F", "90", "55 °F", "-4°F"],
        "temp_c": ["22°C", "20 C", "24.5°C", "32", "13 °C", "-20°C"],
        "conditions": ["sunny", "Partly cloudy", "RAIN", "snow", "Overcast", "thunderstorm"],
        "is_active": ["yes", "no", "Y", "N", "TRUE", "false", "1", "0"],
        "has_garage": ["1", "0", "yes", "no", "true", "false"],
        "company": ["Acme Inc", "Valley Stone Homes", "Globex LLC", "NASA", "Summit House Builders", "Initech Ltd."],
        "agency": ["University of Utah", "Red Cross Society", "City Housing Authority", "NASA", "Salt Lake Hospital"],
        "year_built": ["1999", "2005", "2012", "1987", "2020"],
        "bedrooms": ["2", "3", "4", "5"],
    })
    assert not _swap_sources(suggestions)


def test_real_swap_between_zip_text_and_world_city_is_still_found(tmp_path):
    cities = ["Toronto", "London", "Zürich", "Mumbai", "Nairobi", "Osaka"]
    crossed = [(cities[i % 6], f"{20000 + i:05d}") for i in range(8)]
    home = [(f"{10000 + i:05d}", cities[i % 6]) for i in range(40)]
    path = _write_pairs(tmp_path, "zip_world_city.csv", "zipcode,city", crossed, home)
    _, entry, _ = _shape_swap_step(path)
    assert set(entry["columns"]) == {"zipcode", "city"}


def test_place_gazetteer_recognizes_world_places_and_rejects_words():
    from fbroom.semantics import is_place, value_kinds

    for name in ("Orlando", "SALT LAKE CITY", "St. Louis", "Zürich", "Tokyo", "Texas", "TX", "Canada", "Orlando, FL"):
        assert is_place(name), name
    for word in ("Cedar Ridge Homes", "single-family", "builder", "12345", ""):
        assert not is_place(word), word
    assert "temperature" in value_kinds("72°F")
    assert "weather" in value_kinds("Partly cloudy")
    assert "company" in value_kinds("Acme Inc")
    assert "binary" in value_kinds("Yes")


def test_swap_apply_step_is_unchanged_by_value_meaning_checks(tmp_path):
    from fbroom.engine import _read_table, _swap_by_types

    cities = ["Flint", "Dallas", "Houston", "Austin", "Denver", "Reno"]
    crossed = [(cities[i % 6], f"{20000 + i:05d}") for i in range(8)]
    home = [(f"{10000 + i:05d}", cities[i % 6]) for i in range(40)]
    path = _write_pairs(tmp_path, "unchanged.csv", "zipcode,city", crossed, home)
    _, _, step = _shape_swap_step(path)
    result, info = _swap_by_types(_read_table(str(path)), step["params"]["moves"], "")
    assert info["rows_changed"] == 8
    assert result["zipcode"].to_list()[:8] == [b for _, b in crossed]
    assert result["city"].to_list()[:8] == [a for a, _ in crossed]


def test_swap_pair_only_fills_blank_cells_for_unmatched_rows():
    from fbroom.engine import _swap_by_types

    frame = pl.DataFrame({
        "zipcode": ["Flint", "Dallas", "Austin", "10001"],
        "city": ["20001", "Keep This", "", "Reno"],
    })
    moves = [
        {"source": "zipcode", "target": "city", "type": "pattern", "pattern": "[A-Z][a-z]+", "exceptions": []},
        {"source": "city", "target": "zipcode", "type": "pattern", "pattern": r"\d+", "exceptions": []},
    ]
    result, info = _swap_by_types(frame, moves, "")
    assert result["zipcode"].to_list() == ["20001", "Dallas", "", "10001"]
    assert result["city"].to_list() == ["Flint", "Keep This", "Austin", "Reno"]
    assert info["rows_changed"] == 2


def test_zip_text_transfer_is_blocked_with_reason_when_target_has_text(tmp_path):
    _, conflict = _zip_transfer(tmp_path, "zip_conflict.csv", lambda i: "Elsewhere")
    assert not any(item.get("source") == "zip_text_transfer" for item in conflict)
    blocked = next(item for item in conflict if item.get("source") == "zip_text_transfer_blocked")
    assert "'city'" in blocked["text"] and "overwrite" in blocked["text"]
    assert "json_steps" not in blocked
    assert not any("are text, not ZIP codes" in item["text"] for item in conflict)


def test_zip_text_transfer_cleans_zip4_and_trailing_letters_first(tmp_path):
    _, cleaned = _zip_transfer(tmp_path, "zip_zip4.csv", lambda i: "", extra="11111-2222,Flint\n1234X,Dallas")
    steps = next(item for item in cleaned if item.get("source") == "zip_text_transfer")["json_steps"]
    assert [step["action"] for step in steps] == ["regex_replace", "regex_replace", "move_by_type"]


def test_row_aligned_move_requires_matching_values_and_blank_target(tmp_path):
    from fbroom.ingest import generate_plain_language_suggestions
    from fbroom.step_catalog import parse_instruction

    source = tmp_path / "move.csv"
    rows = ["narrative,amount"]
    rows.extend(f"{100 + i}," for i in range(5))
    rows.extend(f"label-{i},{i * 10}" for i in range(35))
    source.write_text("\n".join(rows), encoding="utf-8")

    suggestions = generate_plain_language_suggestions({"path": str(source), "normalized_path": str(source)})
    move = next(entry for entry in suggestions if entry.get("source") == "row_aligned_type_transfer")
    step = move["json_steps"][0]
    assert step["action"] == "move_by_type"
    assert step["params"]["source"] == "narrative"
    assert step["params"]["target"] == "amount"
    assert parse_instruction(move["instruction"], ["narrative", "amount"]) == [step]
    assert not any(
        entry.get("source") == "mixed_type_minority"
        and entry["json_steps"][0]["column"] == "narrative"
        for entry in suggestions
    )


def _drop_rows_suggestions(path):
    from fbroom.ingest import generate_plain_language_suggestions

    suggestions = generate_plain_language_suggestions({"path": str(path), "normalized_path": str(path)})
    return {
        (item["json_steps"][0]["column"], item["json_steps"][0]["params"]["issue"]): item
        for item in suggestions
        if item.get("json_steps") and item["json_steps"][0]["action"] == "drop_rows"
    }


def test_scan_offers_drop_rows_for_each_measurable_issue(tmp_path):
    rows = ["amount,email,created_at,state"]
    for i in range(40):
        amount = "TBD" if i == 3 else ("" if i == 7 else ("abc" if i == 11 else str(i * 10)))
        email = "not-an-email" if i == 5 else f"user{i}@example.com"
        created = "2025-02-30" if i == 9 else f"2025-01-{i % 28 + 1:02d}"
        rows.append(f"{amount},{email},{created},{['oh', 'PA'][i % 2]}")
    path = tmp_path / "drop_rows.csv"
    path.write_text("\n".join(rows), encoding="utf-8")

    found = _drop_rows_suggestions(path)
    assert found[("amount", "blank")]["json_steps"][0]["params"] == {"issue": "blank"}
    assert found[("amount", "not_numeric")]["json_steps"][0]["params"] == {"issue": "not_numeric"}
    assert found[("amount", "values")]["json_steps"][0]["params"] == {"issue": "values", "values": ["TBD"]}
    assert found[("email", "not_matches")]["json_steps"][0]["params"]["issue"] == "not_matches"
    assert found[("created_at", "not_date")]["json_steps"][0]["params"] == {"issue": "not_date", "fmt": "%Y-%m-%d"}
    assert "state" not in {column for column, _ in found}
    assert "1 row(s)" in found[("amount", "blank")]["text"]
    assert "whole row" in found[("amount", "blank")]["text"]
    for entry in found.values():
        assert entry["machine_template"] and entry["instruction"]


def test_every_suggestion_is_labelled_with_a_category_and_column_for_grouping(tmp_path):
    from fbroom.ingest import generate_plain_language_suggestions

    rows = ["amount,email,state"]
    for i in range(40):
        rows.append(f"{'TBD' if i == 3 else i * 10},{'bad' if i == 5 else f'u{i}@example.com'},{['oh', 'PA'][i % 2]}")
    rows.append("10,u1@example.com,oh")
    rows.append("10,u1@example.com,oh")
    path = tmp_path / "labels.csv"
    path.write_text("\n".join(rows), encoding="utf-8")
    suggestions = generate_plain_language_suggestions({"path": str(path), "normalized_path": str(path)})

    assert suggestions
    allowed = {"fix", "move", "remove", "drop_rows", "review"}
    for item in suggestions:
        assert item["category"] in allowed
        assert "column" in item and isinstance(item["columns"], list)
        if not item.get("json_steps"):
            assert item["category"] == "review"
        if item.get("json_steps") and item["json_steps"][0]["action"] == "drop_rows":
            assert item["category"] == "drop_rows" and item["column"] == item["json_steps"][0]["column"]
    assert any(item["category"] == "review" and item["column"] == "email" for item in suggestions)
    duplicates = next(item for item in suggestions if item["json_steps"][:1] and item["json_steps"][0]["action"] == "deduplicate")
    assert duplicates["category"] == "remove" and duplicates["column"] is None


def test_swap_suggestion_is_a_move_category_that_names_both_columns(tmp_path):
    cities = ["Flint", "Dallas", "Houston", "Austin", "Denver", "Reno"]
    crossed = [(cities[i % 6], f"{20000 + i:05d}") for i in range(6)]
    home = [(f"{10000 + i:05d}", cities[i % 6]) for i in range(40)]
    path = _write_pairs(tmp_path, "labels_swap.csv", "zipcode,city", crossed, home)
    _, entry, _ = _shape_swap_step(path)
    assert entry["category"] == "move"
    assert set(entry["columns"]) == {"zipcode", "city"}


def test_drop_rows_alternative_is_not_offered_when_it_would_remove_most_rows(tmp_path):
    path = tmp_path / "mostly_blank.csv"
    path.write_text("amount,other\n" + "\n".join(f"{'' if i % 4 else i},{i}" for i in range(40)), encoding="utf-8")
    assert ("amount", "blank") not in _drop_rows_suggestions(path)


def test_dropping_rows_is_previewed_applied_and_validated(tmp_path):
    from fbroom.step_catalog import parse_instruction

    rows = ["amount,label"] + [f"{'TBD' if i in (3, 12) else i * 10},row{i}" for i in range(30)]
    path = tmp_path / "drop_apply.csv"
    path.write_text("\n".join(rows), encoding="utf-8")
    step = {"action": "drop_rows", "column": "amount", "params": {"issue": "not_numeric"}}
    assert parse_instruction("drop rows where amount is not a number", ["amount", "label"]) == [step]

    client = TestClient(app)
    response = client.post("/recipe-from-text", json={
        "instruction": "drop rows where amount is not a number", "source_path": str(path),
    })
    assert response.status_code == 200, response.text
    generated = response.json()
    assert generated["recipe"]["cleaning_steps"] == [step]
    warning = next(item for item in generated["preview"]["warnings"] if item["step"] == "drop_rows")
    assert warning["rows_changed"] == 2

    recipe = generated["recipe"]
    output = tmp_path / "dropped.csv"
    recipe["outputs"] = [{"path": str(output)}]
    assert client.post("/apply", json=recipe).status_code == 200
    result = pl.read_csv(output, infer_schema=False)
    assert result.height == 28 and "TBD" not in result["amount"].to_list()

    unknown = client.post("/recipe-from-text", json={
        "instruction": "apply selected step", "source_path": str(path),
        "suggested_steps": [{"action": "drop_rows", "column": "missing", "params": {"issue": "blank"}}],
    })
    assert unknown.status_code == 400


def test_drop_blank_rows_preserves_python_whitespace_and_float_nan_behavior():
    from fbroom.engine import _drop_rows

    text_values = [None, "", " \t", "\x1c", "\u00a0", "value", "  ", " \x1c ", "NaN"]
    numeric_values = [0.0, float("nan"), 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    category_values = ["keep", "  ", "keep", "keep", "keep", "keep", "keep", None, "keep"]
    enum_values = ["keep", "", "keep", "keep", "keep", "keep", "keep", "keep", "keep"]
    frame = pl.DataFrame({
        "text": text_values,
        "numeric": numeric_values,
        "category": category_values,
        "enum_category": enum_values,
    }, strict=False)

    def old_blank(value):
        if value is None or (isinstance(value, float) and value != value):
            text = ""
        else:
            text = str(value).strip()
        return text == ""

    frame = frame.with_columns(
        pl.col("category").cast(pl.Categorical),
        pl.col("enum_category").cast(pl.Enum(["", "keep"])),
    )
    columns = ("text", "numeric", "category", "enum_category")
    column_values = [frame.get_column(column).to_list() for column in columns]
    drop_flags = [any(old_blank(values[index]) for values in column_values) for index in range(frame.height)]
    expected = frame.filter(pl.Series([not flag for flag in drop_flags]))
    result, info = _drop_rows(frame, columns, {"issue": "blank"})

    assert result.equals(expected)
    assert info["rows_dropped"] == sum(drop_flags)
    assert info["sample_positions"] == [row_index + 1 for row_index, flag in enumerate(drop_flags) if flag][:20]


def test_drop_rows_runs_before_casts_so_bad_values_are_removed_not_nulled(tmp_path):
    path = tmp_path / "order.csv"
    path.write_text("amount\n" + "\n".join(["10", "bad", "30"] * 5), encoding="utf-8")
    steps = [
        {"action": "cast", "column": "amount", "params": {"to": "float"}},
        {"action": "drop_rows", "column": "amount", "params": {"issue": "not_numeric"}},
    ]
    response = TestClient(app).post("/recipe-from-text", json={
        "instruction": "apply selected steps", "source_path": str(path), "suggested_steps": steps,
    })
    assert response.status_code == 200, response.text
    assert [s["action"] for s in response.json()["recipe"]["cleaning_steps"]] == ["drop_rows", "cast"]
    assert all(row["amount"] is not None for row in response.json()["preview"]["after"])


def test_preview_and_apply_match_for_numeric_and_unicode_steps(tmp_path):
    source = tmp_path / "mixed.csv"
    source.write_text("amount,label,rate\n-1.236,ＡＢＣ,10%\n2.345,Ｆｏｏ,25%\n", encoding="utf-8")
    steps = [
        {"action": "numeric_transform", "column": "amount", "params": {"operation": "abs"}},
        {"action": "unicode_normalize", "column": "label", "params": {"form": "NFKC"}},
        {"action": "regex_replace", "column": "rate", "params": {"pattern": r"%$", "replace": ""}},
        {"action": "cast", "column": "rate", "params": {"to": "float"}},
        {"action": "numeric_transform", "column": "rate", "params": {"operation": "divide", "factor": 100}},
    ]
    client = TestClient(app)
    response = client.post("/recipe-from-text", json={
        "instruction": "apply selected structured transforms",
        "source_path": str(source),
        "suggested_steps": steps,
    })
    assert response.status_code == 200, response.text
    generated = response.json()
    assert generated["preview"]["after"][0]["amount"] == 1.236
    assert generated["preview"]["after"][0]["label"] == "ABC"
    assert generated["preview"]["after"][0]["rate"] == 0.1

    recipe = generated["recipe"]
    output = tmp_path / "applied.csv"
    recipe["outputs"] = [{"path": str(output)}]
    applied = client.post("/apply", json=recipe)
    assert applied.status_code == 200, applied.text
    result = pl.read_csv(output)
    assert result["amount"].to_list() == [1.236, 2.345]
    assert result["label"].to_list() == ["ABC", "Foo"]
    assert result["rate"].to_list() == [0.1, 0.25]


def _gate(left, right, left_values, right_values, crossed, home=200):
    from fbroom.semantics import column_kinds, swap_is_safe

    return swap_is_safe(left, right, left_values, right_values, crossed, home, column_kinds(left_values), column_kinds(right_values))


def test_swap_gate_header_veto_blocks_values_that_already_suit_their_column():
    years = ["2023", "2024", "2025"] * 40
    dates = ["2024-08-26", "2023-12-10", "2022-01-05"] * 40
    crossed = list(range(15))
    assert not _gate("start_date", "end_date", dates[:], years[:], crossed)
    left = ["2023"] * 15 + ["2024-08-26"] * 105
    right = ["2024-08-26"] * 15 + ["12"] * 105
    assert not _gate("start_date", "bedrooms", left, right, crossed)


def test_swap_gate_accepts_a_clean_consistent_swap_and_rejects_scattered_noise():
    zips = [f"{10000 + i}" for i in range(120)]
    cities = ["Dallas", "Houston", "Austin", "Denver", "Reno", "Boston"] * 20
    left = cities[:12] + zips[12:]
    right = zips[:12] + cities[12:]
    assert _gate("zipcode", "city", left, right, list(range(12)))
    mixed_left = ["Dallas", "42", "x@y.com", "12/05/2020", "Reno", "$5"] * 20
    mixed_right = ["(555) 123-4567", "Boston", "12345", "yes", "n/a", "2.5"] * 20
    assert not _gate("zipcode", "city", mixed_left, mixed_right, list(range(12)))


def test_swap_gate_needs_more_evidence_for_unrecognised_columns():
    left = [f"lorem ipsum {i}" for i in range(120)]
    right = [f"dolor sit {i}" for i in range(120)]
    assert not _gate("alpha", "beta", left, right, list(range(6)), home=400)
    assert not _gate("alpha", "beta", left, right, list(range(12)), home=30)