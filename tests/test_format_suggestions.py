import csv
import io

from fastapi.testclient import TestClient

from fbroom.ingest import generate_plain_language_suggestions
from fbroom.main import app


def _run(tmp_path, header, values, other=None):
    """Suggest for one column, apply the first suggestion that touches it, and return (suggestions, output rows)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([header, "pad"])
    for index, value in enumerate(values):
        writer.writerow([value, index])
    source = tmp_path / "in.csv"
    source.write_text(buffer.getvalue(), encoding="utf-8")
    suggestions = generate_plain_language_suggestions({"path": str(source), "normalized_path": str(source)})
    return source, [s for s in suggestions if header in str(s.get("text", "")) and s.get("json_steps")]


def _apply(tmp_path, source, steps):
    client = TestClient(app)
    response = client.post("/recipe-from-text", json={"instruction": "x", "source_path": str(source), "suggested_steps": steps})
    assert response.status_code == 200, response.text
    recipe = response.json()["recipe"]
    output = tmp_path / "out.csv"
    recipe["outputs"] = [{"path": str(output)}]
    assert client.post("/apply", json=recipe).status_code == 200
    return list(csv.DictReader(output.open(encoding="utf-8")))


def test_magnitudes_swiss_and_trailing_minus_become_numbers(tmp_path):
    values = ["1.2M", "500K", "$3.5B", "2k", "1.234k", "12", "7.5M", "800", "3M", "9.9k", "1B", "250K"]
    source, found = _run(tmp_path, "revenue", values)
    assert found, "expected a suggestion for magnitude notation"
    rows = _apply(tmp_path, source, found[0]["json_steps"])
    got = [float(r["revenue"]) for r in rows]
    assert got == [1200000, 500000, 3500000000, 2000, 1234, 12, 7500000, 800, 3000000, 9900, 1000000000, 250000]

    swiss = ["1'234.50", "2'000.00", "15.25", "999.99", "10'500.00", "3.00", "7'777.77", "88.80", "1'000.00", "5.55"]
    source, found = _run(tmp_path, "price", swiss)
    rows = _apply(tmp_path, source, found[0]["json_steps"])
    assert [float(r["price"]) for r in rows][:3] == [1234.5, 2000.0, 15.25]

    minus = ["100-", "250.50-", "75", "30", "12-", "9", "44", "81-", "6", "70"]
    source, found = _run(tmp_path, "balance", minus)
    rows = _apply(tmp_path, source, found[0]["json_steps"])
    assert [float(r["balance"]) for r in rows][:5] == [-100, -250.5, 75, 30, -12]


def test_currency_codes_and_comma_decimals_are_read(tmp_path):
    codes = ["USD 359975", "EUR 1200", "450", "GBP 99", "1500 USD", "300", "CAD 77", "88", "USD 5", "60"]
    source, found = _run(tmp_path, "cost", codes)
    rows = _apply(tmp_path, source, found[0]["json_steps"])
    assert [float(r["cost"]) for r in rows][:5] == [359975, 1200, 450, 99, 1500]

    euro = ["1.234,56", "2.000,00", "15,25", "999,99", "10.500,10", "3,00", "7.777,77", "88,80", "1.000,00", "5,55"]
    source, found = _run(tmp_path, "amount", euro)
    assert any("comma decimals" in s["text"] for s in found)
    steps = next(s for s in found if "comma decimals" in s["text"])["json_steps"]
    rows = _apply(tmp_path, source, steps)
    assert [float(r["amount"]) for r in rows][:3] == [1234.56, 2000.0, 15.25]


def test_more_date_layouts_are_parsed(tmp_path):
    for header, values, expected in (
        ("shipped", ["31.12.2023", "01.02.2024", "15.03.2024", "20.11.2023", "05.05.2022"], "2023-12-31"),
        ("period", ["Mar 2024", "Apr 2024", "May 2024", "Jun 2024", "Jul 2024"], "2024-03-01"),
        ("ts", ["03/14/2024 02:30 PM", "03/15/2024 09:05 AM", "04/01/2024 11:59 PM", "05/20/2024 12:00 PM", "06/30/2024 01:15 AM"], "2024-03-14"),
        ("hired_on", ["20240131", "20230615", "20220301", "20211111", "20200229"], "2024-01-31"),
    ):
        source, found = _run(tmp_path, header, values)
        cast = next((s for s in found if s["json_steps"][0]["action"] == "cast"), None)
        assert cast, f"no date suggestion for {header}"
        rows = _apply(tmp_path, source, cast["json_steps"])
        assert rows[0][header].startswith(expected), (header, rows[0])


def test_ids_and_short_columns_are_handled_by_kind(tmp_path):
    # Leading zeros survive: a column of codes is never offered a numeric cast.
    source, found = _run(tmp_path, "mrn", ["00123", "00456", "00789", "01011", "02222"])
    assert not any(s["json_steps"][0]["action"] == "cast" for s in found)

    # Fewer than ten values still get suggestions, and 'unknown'/'-' count as missing.
    source, found = _run(tmp_path, "weight", ["10", "20", "unknown", "-", "30"])
    assert found and any("missing" in s["text"] for s in found)

    # Yes/no style words are standardised even when the column name says nothing.
    source, found = _run(tmp_path, "result", ["Pass", "Fail", "pass", "FAIL", "Pass", "fail"])
    assert any("true/false" in s["text"] for s in found)


def test_metres_are_not_read_as_millions(tmp_path):
    source, found = _run(tmp_path, "height", ["12 m", "15 m", "9 m", "30 m", "8 m", "21 m"])
    assert not any("magnitude" in s["text"] for s in found)


def test_mixed_date_layouts_offer_a_choice_of_target(tmp_path):
    values = ["2024-01-05", "03/25/2024", "31.12.2023", "2022-07-04", "14/02/2024", "Mar 05, 2021", "2020-11-30", "2019-06-15"]
    source, found = _run(tmp_path, "signup", values)
    offers = [s for s in found if "=>" in str(s["json_steps"][0]["params"].get("fmt", ""))]
    assert len(offers) >= 4 and all("mixes" in s["text"] for s in offers)
    iso = next(s for s in offers if s["json_steps"][0]["params"]["fmt"].endswith("=>%Y-%m-%d"))
    rows = _apply(tmp_path, source, iso["json_steps"])
    assert [r["signup"] for r in rows][:4] == ["2024-01-05", "2024-03-25", "2023-12-31", "2022-07-04"]
    us = next(s for s in offers if s["json_steps"][0]["params"]["fmt"].endswith("=>%m/%d/%Y"))
    assert _apply(tmp_path, source, us["json_steps"])[0]["signup"] == "01/05/2024"


def test_single_date_layout_is_not_called_mixed(tmp_path):
    source, found = _run(tmp_path, "signup", ["2024-01-05", "2024-02-10", "2023-03-01", "2022-04-04"])
    assert not any("mixes" in s["text"] for s in found)


def test_mixed_phone_styles_offer_a_choice(tmp_path):
    values = ["(555) 123-4567", "555-987-6543", "555.222.3333", "+1 555 444 5555", "5558889999"]
    source, found = _run(tmp_path, "phone", values)
    offers = [s for s in found if "several styles" in s["text"]]
    assert len(offers) == 4
    dashed = next(s for s in offers if "555-123-4567" in s["text"].split("to ")[-1])
    assert [r["phone"] for r in _apply(tmp_path, source, dashed["json_steps"])] == [
        "555-123-4567", "555-987-6543", "555-222-3333", "555-444-5555", "555-888-9999"]
    source, found = _run(tmp_path, "phone", ["555-123-4567", "555-987-6543", "555-222-3333"])
    assert not any("several styles" in s["text"] for s in found)


def test_mixed_yes_no_spellings_offer_a_choice(tmp_path):
    source, found = _run(tmp_path, "active", ["Yes", "y", "No", "N", "true", "FALSE", "yes", "no"])
    offers = [s for s in found if "mixes spellings" in s["text"]]
    assert len(offers) == 3
    yn = next(s for s in offers if s["text"].endswith("Y/N"))
    assert [r["active"] for r in _apply(tmp_path, source, yn["json_steps"])][:6] == ["Y", "Y", "N", "N", "Y", "N"]


def _offers(found, needle):
    return [s for s in found if needle in s["text"]]


def _pick(offers, text):
    return next(s for s in offers if text in s["text"])


def test_mixed_number_notations_and_currencies(tmp_path):
    values = ["1,234.50", "1.234,50", "$99", "€1.000,25", "12", "3,5", "2,000.75", "7.25"]
    source, found = _run(tmp_path, "amount", values)
    offers = _offers(found, "mixes")
    assert offers
    plain = next(s for s in offers if s["json_steps"][0]["params"]["fmt"] == "num:us")
    rows = _apply(tmp_path, source, plain["json_steps"])
    assert [float(r["amount"]) for r in rows] == [1234.5, 1234.5, 99, 1000.25, 12, 3.5, 2000.75, 7.25]
    two = next(s for s in offers if s["json_steps"][0]["params"]["fmt"] == "num:us:2")
    assert [r["amount"] for r in _apply(tmp_path, source, two["json_steps"])][:4] == ["1234.50", "1234.50", "99.00", "1000.25"]


def test_mixed_currency_symbols_only(tmp_path):
    source, found = _run(tmp_path, "price", ["$5", "€6", "£7.50", "$8", "USD 9"])
    offers = _offers(found, "currency markers")
    assert offers and "no currency conversion" in offers[0]["text"]
    rows = _apply(tmp_path, source, offers[0]["json_steps"])
    assert [float(r["price"]) for r in rows] == [5, 6, 7.5, 8, 9]


def test_mixed_decimal_places(tmp_path):
    source, found = _run(tmp_path, "weight", ["1.50", "2.25", "3", "4.10", "5.75", "6.00", "7.5", "8.20", "9.30", "2.40"])
    offers = _offers(found, "decimal places")
    assert len(offers) >= 4
    rows = _apply(tmp_path, source, _pick(offers, "exactly 2")["json_steps"])
    assert [r["weight"] for r in rows][:3] == ["1.50", "2.25", "3.00"]
    source, found = _run(tmp_path, "weight", ["1.5", "2.5", "3.5"])
    assert not _offers(found, "decimal places")


def test_mixed_units_are_converted_to_the_chosen_unit(tmp_path):
    source, found = _run(tmp_path, "load", ["5 kg", "5kg", "11 lb", "2 kgs", "22 lbs", "7 kg"])
    offers = _offers(found, "mixes mass units")
    kg = _pick(offers, "to kg")
    rows = _apply(tmp_path, source, kg["json_steps"])
    assert [round(float(r["load"]), 2) for r in rows] == [5, 5, 4.99, 2, 9.98, 7]
    lb = _pick(offers, "to lb")
    assert round(float(_apply(tmp_path, source, lb["json_steps"])[0]["load"]), 2) == 11.02
    source, found = _run(tmp_path, "temp_reading", ["20 C", "68 F", "25 C", "77 F", "30 C"])
    c = _pick(_offers(found, "mixes temperature"), "to c ")
    assert [round(float(r["temp_reading"]), 1) for r in _apply(tmp_path, source, c["json_steps"])] == [20, 20, 25, 25, 30]


def test_international_phone_styles(tmp_path):
    values = ["+44 20 7946 0958", "020 7946 0123", "+44 (0)161 496 0000", "0044 117 496 0000", "07911 123456"]
    source, found = _run(tmp_path, "phone", values)
    offers = _offers(found, "several styles")
    uk = _pick(offers, "United Kingdom")
    assert [r["phone"] for r in _apply(tmp_path, source, uk["json_steps"])] == [
        "+442079460958", "+442079460123", "+441614960000", "+441174960000", "+447911123456"]
    assert _pick(offers, "digits only")


def test_postal_code_styles(tmp_path):
    source, found = _run(tmp_path, "postal_code", ["K1A 0B1", "k1a0b1", "M5V-2T6", "V6B 1A1", "h2x1y4"])
    offers = _offers(found, "postal codes in different styles")
    assert len(offers) == 2
    spaced = _pick(offers, "with a space")
    assert [r["postal_code"] for r in _apply(tmp_path, source, spaced["json_steps"])] == ["K1A 0B1", "K1A 0B1", "M5V 2T6", "V6B 1A1", "H2X 1Y4"]
    source, found = _run(tmp_path, "postcode", ["SW1A 1AA", "sw1a1aa", "M1 1AE", "EC1A1BB"])
    uk = _pick(_offers(found, "postal codes in different styles"), "without a space")
    assert [r["postcode"] for r in _apply(tmp_path, source, uk["json_steps"])] == ["SW1A1AA", "SW1A1AA", "M11AE", "EC1A1BB"]
    source, found = _run(tmp_path, "zip", ["12345", "123456789", "98765-4321", "54321"])
    plus4 = _pick(found, "as ZIP+4 with a hyphen")
    assert _apply(tmp_path, source, plus4["json_steps"])[1]["zip"] == "12345-6789"


def test_mixed_state_and_country_spellings(tmp_path):
    source, found = _run(tmp_path, "state", ["CA", "Calif.", "California", "N.Y.", "New York", "tx", "Texas"])
    offers = _offers(found, "mixes state spellings")
    abbr = _pick(offers, "abbreviation")
    assert [r["state"] for r in _apply(tmp_path, source, abbr["json_steps"])] == ["CA", "CA", "CA", "NY", "NY", "TX", "TX"]
    full = _pick(offers, "full name")
    assert _apply(tmp_path, source, full["json_steps"])[3]["state"] == "New York"
    source, found = _run(tmp_path, "country", ["US", "USA", "United States", "UK", "Germany", "DE", "Canada"])
    iso3 = _pick(_offers(found, "mixes country spellings"), "3-letter")
    assert [r["country"] for r in _apply(tmp_path, source, iso3["json_steps"])] == ["USA", "USA", "USA", "GBR", "DEU", "DEU", "CAN"]
    source, found = _run(tmp_path, "state", ["CA", "TX", "NY", "CA"])
    assert not _offers(found, "mixes state spellings")


def test_other_boolean_words_offer_matching_labels(tmp_path):
    source, found = _run(tmp_path, "status_check", ["Pass", "passed", "Fail", "failed", "Pass", "fail"])
    pf = _pick(_offers(found, "mixes spellings"), "Pass/Fail")
    assert [r["status_check"] for r in _apply(tmp_path, source, pf["json_steps"])] == ["Pass", "Pass", "Fail", "Fail", "Pass", "Fail"]
    source, found = _run(tmp_path, "switch", ["On", "off", "ON", "Off", "on"])
    assert any(s["json_steps"][0]["action"] == "map" for s in found)


def test_mixed_datetimes_and_zones(tmp_path):
    values = ["2024-01-05 14:30:00", "03/25/2024 02:30 PM", "2024-02-10T08:15:00", "05.06.2024 23:59", "2024-07-04"]
    source, found = _run(tmp_path, "logged", values)
    iso = _pick(_offers(found, "date-time layouts"), "YYYY-MM-DD HH:MM:SS")
    rows = _apply(tmp_path, source, iso["json_steps"])
    assert [r["logged"] for r in rows] == ["2024-01-05 14:30:00", "2024-03-25 14:30:00", "2024-02-10 08:15:00", "2024-06-05 23:59:00", "2024-07-04 00:00:00"]
    zoned = ["2024-01-05T10:00:00-05:00", "2024-01-05 16:30:00Z", "2024-01-05 09:00:00 PST", "2024-01-05T18:00:00+02:00", "2024-01-06 01:00:00 UTC"]
    source, found = _run(tmp_path, "event_time", zoned)
    offers = _offers(found, "time zones")
    utc = _pick(offers, "converted to UTC")
    rows = _apply(tmp_path, source, utc["json_steps"])
    assert [r["event_time"] for r in rows][:4] == ["2024-01-05 15:00:00", "2024-01-05 16:30:00", "2024-01-05 17:00:00", "2024-01-05 16:00:00"]
    local = _pick(offers, "keeping each clock time")
    assert _apply(tmp_path, source, local["json_steps"])[0]["event_time"] == "2024-01-05 10:00:00"
    source, found = _run(tmp_path, "logged", ["2024-01-05 14:30:00", "2024-01-06 09:00:00", "2024-01-07 10:10:00"])
    assert not _offers(found, "mixes")
