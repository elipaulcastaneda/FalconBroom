from fastapi.testclient import TestClient
import polars as pl

from fbroom.ingest import _analyze_column
from fbroom.main import app
from fbroom.engine import _cast_column


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
    assert mixed_units["kind"] == "mixed"
    assert all("json_steps" not in entry for entry in mixed_units["entries"])

    currency = _analyze_column("price", ["$1,200.50", "$25.00"] * 10)
    assert currency["entries"][0]["json_steps"][0]["action"] == "cast"

    placeholders = _analyze_column("amount", ["10", "N/A", "15", "unknown"] * 5)
    assert placeholders["entries"][0]["json_steps"][0]["action"] == "regex_replace"

    ambiguous_dates = _analyze_column("date", ["01/02/2024", "03/04/2024"] * 10)
    assert ambiguous_dates["kind"] == "date_review"
    assert all("json_steps" not in entry for entry in ambiguous_dates["entries"])

    locale_amounts = _analyze_column("amount", ["1.234,56", "25.00"] * 10)
    assert locale_amounts["kind"] == "locale_number_review"
    assert all("json_steps" not in entry for entry in locale_amounts["entries"])
    currency_locale = _analyze_column("amount", ["€1.234,56", "€25,00"] * 10)
    assert currency_locale["kind"] == "locale_number_review"

    phone = _analyze_column("phone", ["+1 (212) 555-0199", "+44 20 7946 0958"] * 10)
    assert phone["entries"][0]["json_steps"][0]["params"]["pattern"] == r"[\s().-]"

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

    suggestions = generate_plain_language_suggestions({"path": str(source)})
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
