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
