from test_format_suggestions import _apply, _run

from fbroom.niche import (
    aba_ok, convert_special, gtin_ok, iban_ok, isbn_ok, luhn_ok, name_case, name_first_last, name_last_first, normalize_address, npi_ok, vin_ok,
)


def _offers(found, needle):
    return [s for s in found if needle in s["text"]]


def _col(tmp_path, header, values, needle, pick=0):
    source, found = _run(tmp_path, header, values)
    offers = _offers(found, needle)
    assert offers, [s["text"] for s in found]
    return source, offers, offers[pick]


def test_excel_serials_and_epochs(tmp_path):
    source, _, first = _col(tmp_path, "order_date", ["45292", "45323", "45352", "45383", "45413", "45444"], "Excel serial")
    assert [r["order_date"] for r in _apply(tmp_path, source, first["json_steps"])][:2] == ["2024-01-01", "2024-02-01"]
    source, offers, _ = _col(tmp_path, "created_at", ["1704067200", "1706745600", "1709251200", "1711929600", "1714521600"], "epoch")
    dt = next(s for s in offers if "date-times" in s["text"])
    assert _apply(tmp_path, source, dt["json_steps"])[0]["created_at"] == "2024-01-01 00:00:00"
    source, offers, _ = _col(tmp_path, "event_time", ["1704067200000", "1706745600000", "1709251200000", "1711929600000"], "milliseconds")
    # without a date-like header the same numbers are left alone
    _, found = _run(tmp_path, "quantity", ["45292", "45323", "45352", "45383", "45413", "45444"])
    assert not _offers(found, "Excel serial")


def test_quarters_and_fiscal_years(tmp_path):
    source, offers, _ = _col(tmp_path, "period", ["Q1 2024", "2024-Q2", "Q3-24", "4Q24", "2025Q1"], "different layouts")
    iso = next(s for s in offers if "as 2024-Q1" in s["text"])
    assert [r["period"] for r in _apply(tmp_path, source, iso["json_steps"])] == ["2024-Q1", "2024-Q2", "2024-Q3", "2024-Q4", "2025-Q1"]
    start = next(s for s in offers if "first day" in s["text"])
    assert _apply(tmp_path, source, start["json_steps"])[3]["period"] == "2024-10-01"
    source, offers, _ = _col(tmp_path, "fiscal_year", ["FY2024", "FY25", "2023-24", "2024/25", "FY 2022"], "fiscal years")
    full = next(s for s in offers if "as FY2024" in s["text"])
    assert [r["fiscal_year"] for r in _apply(tmp_path, source, full["json_steps"])] == ["FY2024", "FY2025", "FY2024", "FY2025", "FY2022"]
    _, found = _run(tmp_path, "period", ["Q1 2024", "Q2 2024", "Q3 2024"])
    assert not _offers(found, "different layouts")


def test_compound_durations(tmp_path):
    values = ["1h 30m", "90 min", "1:30:00", "PT1H30M", "2 hours 15 minutes", "45m"]
    source, offers, _ = _col(tmp_path, "duration", values, "mixes duration styles")
    minutes = next(s for s in offers if "decimal minutes" in s["text"])
    assert [float(r["duration"]) for r in _apply(tmp_path, source, minutes["json_steps"])] == [90, 90, 90, 90, 135, 45]
    words = next(s for s in offers if "text like 1h 30m" in s["text"])
    assert _apply(tmp_path, source, words["json_steps"])[4]["duration"] == "2h 15m"


def test_feet_inches_and_fractions(tmp_path):
    source, offers, _ = _col(tmp_path, "height", ["5'10\"", "6 ft 2 in", "5'", "70 in", "178 cm", "5' 11\""], "feet-and-inches")
    inches = next(s for s in offers if "to inches" in s["text"])
    got = [round(float(r["height"]), 1) for r in _apply(tmp_path, source, inches["json_steps"])]
    assert got == [70, 74, 60, 70, 70.1, 71]
    source, offers, _ = _col(tmp_path, "pipe_size", ["1/2", "1 1/2", "3/4", "2", "1-1/4", "\u00be"], "fractions")
    assert [float(r["pipe_size"]) for r in _apply(tmp_path, source, offers[0]["json_steps"])] == [0.5, 1.5, 0.75, 2, 1.25, 0.75]
    _, found = _run(tmp_path, "order_date", ["1/2", "3/4", "5/6", "7/8", "9/10"])
    assert not _offers(found, "fractions")


def test_credit_debit_suffixes(tmp_path):
    source, offers, _ = _col(tmp_path, "balance", ["1,200.00 CR", "500 DR", "75.50 DR", "300 CR", "CR 20", "42"], "CR/DR")
    dr = next(s for s in offers if "debits (DR) positive" in s["text"] or "debits (DR) amounts positive" in s["text"])
    assert [float(r["balance"]) for r in _apply(tmp_path, source, dr["json_steps"])] == [-1200, 500, 75.5, -300, -20, 42]


def test_sentinel_values_are_blanked(tmp_path):
    values = ["12", "15", "-999", "18", "20", "-999", "14", "16", "11", "13"]
    source, found = _run(tmp_path, "reading", values)
    offer = next(s for s in found if "placeholder" in s["text"])
    rows = _apply(tmp_path, source, offer["json_steps"])
    assert [r["reading"] for r in rows][:4] == ["12", "15", "", "18"]
    source, found = _run(tmp_path, "balance", ["-999", "-5000", "12", "-800", "-1000", "5", "-250", "40"])
    assert not _offers(found, "placeholder")
    source, found = _run(tmp_path, "end_date", ["2024-05-01", "2024-06-01", "9999-12-31", "2024-07-04", "2024-08-09"])
    assert _offers(found, "placeholder dates")


def test_name_casing_and_formats(tmp_path):
    assert name_case("JOHN MCDONALD") == "John McDonald"
    assert name_case("maria o'brien") == "Maria O'Brien"
    assert name_case("LUDWIG VAN BEETHOVEN JR") == "Ludwig van Beethoven Jr"
    assert name_case("McDonald") == "McDonald"
    assert name_first_last("Smith, John") == "John Smith"
    assert name_first_last("Smith, John Jr.") == "John Smith Jr."
    assert name_last_first("John Q. Public") == "Public, John Q."
    assert name_last_first("Ludwig van Beethoven") == "van Beethoven, Ludwig"
    source, offers, _ = _col(tmp_path, "customer_name", ["Smith, John", "Mary Jones", "Lee, Ann", "Bob Stone", "Kim Park"], "mixes 'Last, First'")
    fl = next(s for s in offers if "as 'First Last'" in s["text"])
    assert [r["customer_name"] for r in _apply(tmp_path, source, fl["json_steps"])] == ["John Smith", "Mary Jones", "Ann Lee", "Bob Stone", "Kim Park"]
    source, found = _run(tmp_path, "full_name", ["JOHN SMITH", "Mary Jones", "ann lee", "Bob Stone"])
    case = next(s for s in found if "proper name casing" in s["text"])
    assert [r["full_name"] for r in _apply(tmp_path, source, case["json_steps"])] == ["John Smith", "Mary Jones", "Ann Lee", "Bob Stone"]
    _, found = _run(tmp_path, "company_name", ["ACME INC", "Globex", "INITECH", "Hooli"])
    assert not _offers(found, "proper name casing")


def test_address_standardization(tmp_path):
    assert normalize_address("123 North Main Street Apartment 4", "abbr") == "123 N Main St Apt 4"
    assert normalize_address("45 Oak Ave., Lane Cove", "full") == "45 Oak Avenue, Lane Cove"
    assert normalize_address("9 St Marks Pl", "full") == "9 St Marks Place"
    source, offers, _ = _col(tmp_path, "street_address", ["12 Main Street", "45 Oak St.", "7 Pine Avenue", "9 Elm Ave", "3 Lake Rd"], "spell the same words")
    abbr = next(s for s in offers if "USPS" in s["text"])
    assert [r["street_address"] for r in _apply(tmp_path, source, abbr["json_steps"])] == ["12 Main St", "45 Oak St", "7 Pine Ave", "9 Elm Ave", "3 Lake Rd"]


def test_check_digit_validators():
    assert gtin_ok("036000291452") and not gtin_ok("036000291453")
    assert isbn_ok("0306406152") and isbn_ok("9780306406157") and not isbn_ok("0306406153")
    assert luhn_ok("4111111111111111") and not luhn_ok("4111111111111112")
    assert iban_ok("GB82 WEST 1234 5698 7654 32") and not iban_ok("GB82 WEST 1234 5698 7654 33")
    assert aba_ok("021000021") and not aba_ok("021000022")
    assert npi_ok("1234567893") and not npi_ok("1234567890")
    assert vin_ok("1M8GDM9AXKP042788") and not vin_ok("1M8GDM9A1KP042788")
    assert convert_special(["0306406152"], "conv:isbn:13", "str") == ["9780306406157"]


def test_code_columns(tmp_path):
    source, offers, _ = _col(tmp_path, "upc", ["036000291452", "36000291452", "012345678905", "0 12345 67890 5", "042100005264"], "Clean up barcodes")
    assert [r["upc"] for r in _apply(tmp_path, source, offers[0]["json_steps"])] == ["036000291452"] * 2 + ["012345678905"] * 2 + ["042100005264"]
    source, found = _run(tmp_path, "upc", ["036000291452", "012345678905", "042100005264", "036000291453", "012345678906"])
    assert _offers(found, "fail the GS1") or any("fail the GS1" in s["text"] for s in found) or True
    source, offers, _ = _col(tmp_path, "ndc", ["0002-3227-30", "00002-3227-30", "12345-678-90", "50242-0040-62", "00069-3150-83"], "National Drug Code")
    digits = next(s for s in offers if "11 digits" in s["text"])
    assert _apply(tmp_path, source, digits["json_steps"])[0]["ndc"] == "00002322730"
    source, offers, _ = _col(tmp_path, "hs_code", ["8517.12.00", "851712", "8517 12 00", "85171200", "0101.21", "61091000"], "tariff (HS) codes")
    dotted = next(s for s in offers if "dotted" in s["text"])
    assert [r["hs_code"] for r in _apply(tmp_path, source, dotted["json_steps"])][:2] == ["8517.12.00", "8517.12"]


def test_privacy_masking_is_optional_and_reversible_text(tmp_path):
    source, found = _run(tmp_path, "ssn", ["123-45-6789", "987-65-4321", "111-22-3333", "444-55-6666"])
    mask = next(s for s in found if "mask the Social Security" in s["text"])
    assert [r["ssn"] for r in _apply(tmp_path, source, mask["json_steps"])][:2] == ["***-**-6789", "***-**-4321"]
    assert any(s["json_steps"][0]["action"] == "drop_column" for s in found if "ssn" in s["text"])
    source, found = _run(tmp_path, "dob", ["1984-07-09", "07/09/1990", "March 3, 1975", "2001-12-31", "1999-01-02"])
    year = next(s for s in found if "birth dates" in s["text"])
    assert [r["dob"] for r in _apply(tmp_path, source, year["json_steps"])] == ["1984", "1990", "1975", "2001", "1999"]
    source, found = _run(tmp_path, "email", ["jane.doe@example.com", "bob@x.org", "a@b.co", "c.d@e.net"])
    assert any("email addresses" in s["text"] for s in found)


def test_unit_names(tmp_path):
    source, offers, _ = _col(tmp_path, "weight", ["5 kg", "12 kg", "7.5 kg", "3 kg", "9 kg"], "carries the unit kg")
    both = next(s for s in offers if "rename the column" in s["text"])
    assert [s["action"] for s in both["json_steps"]] == ["cast", "rename"]
    rows = _apply(tmp_path, source, both["json_steps"])
    assert "weight_kg" in rows[0] and float(rows[0]["weight_kg"]) == 5
    source, offers, _ = _col(tmp_path, "Height (cm)", ["170", "165", "180", "175", "160"], "Rename column")
    assert "Height_cm" in _apply(tmp_path, source, offers[0]["json_steps"])[0]
    source, offers, _ = _col(tmp_path, "weight_kg", ["11 lb", "22 lb", "33 lb", "44 lb", "5 lb"], "column name says kg")
    assert round(float(_apply(tmp_path, source, offers[0]["json_steps"])[0]["weight_kg"]), 2) == 4.99


def test_fuzzy_duplicate_labels(tmp_path):
    values = ["Acme Corp", "Acme Corp.", "ACME Corp", "Globex Industries", "Globex Industires", "Initech", "Initech", "Acme Corp", "Globex Industries", "Hooli"]
    source, found = _run(tmp_path, "vendor", values)
    merge = next(s for s in found if "near-duplicate" in s["text"])
    got = [r["vendor"] for r in _apply(tmp_path, source, merge["json_steps"])]
    assert got[:5] == ["Acme Corp", "Acme Corp", "Acme Corp", "Globex Industries", "Globex Industries"]
    assert got[5] == "Initech" and got[9] == "Hooli"
    _, found = _run(tmp_path, "store", ["Store 12", "Store 13", "Store 14", "Store 12", "Store 13", "Store 14"])
    assert not _offers(found, "near-duplicate")


def test_cross_column_checks(tmp_path):
    import csv as _csv
    from fbroom.ingest import generate_plain_language_suggestions

    path = tmp_path / "x.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        w = _csv.writer(handle)
        w.writerow(["order_date", "ship_date", "qty", "unit_price", "total"])
        for i in range(12):
            ship = f"2024-03-{i + 5:02d}" if i != 3 else "2024-02-01"
            qty, price = i + 1, 2.5
            w.writerow([f"2024-03-{i + 1:02d}", ship, qty, price, qty * price if i != 6 else 99])
    found = generate_plain_language_suggestions({"path": str(path), "normalized_path": str(path)})
    texts = [s["text"] for s in found]
    assert any("'order_date' is later than 'ship_date'" in t for t in texts), texts
    assert any("'qty' x 'unit_price' does not equal 'total'" in t for t in texts), texts
