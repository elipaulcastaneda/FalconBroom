import csv

from fastapi.testclient import TestClient

from fbroom.engine import _read_table
from fbroom.main import app


def test_leading_zero_numeric_columns_stay_text(tmp_path):
    src = tmp_path / "zips.csv"
    with open(src, "w", newline="", encoding="utf-8") as h:
        w = csv.writer(h)
        w.writerow(["id", "zip_code", "qty"])
        for i in range(60):
            w.writerow([i, ["03030", "12345", "07870", "98101"][i % 4], i])
    df = _read_table(str(src))
    assert df["zip_code"].to_list()[:2] == ["03030", "12345"]
    assert df["qty"].dtype.is_numeric()


def test_padded_zip_survives_patch_and_second_pass(tmp_path):
    src = tmp_path / "zips.csv"
    with open(src, "w", newline="", encoding="utf-8") as h:
        w = csv.writer(h)
        w.writerow(["id", "zip_code", "name"])
        for i in range(60):
            w.writerow([i, ["3030", "12345", "7870", "98101"][i % 4], f"n{i}"])
    client = TestClient(app)
    pad = [{"action": "regex_replace", "column": "zip_code", "params": {"pattern": r"^(\d{4})$", "replace": r"0\1"}}]
    recipe = client.post("/recipe-from-text", json={"instruction": "x", "source_path": str(src), "suggested_steps": pad}).json()["recipe"]
    patched = client.post("/apply-patch", json={"path": str(src), "patches": [], "recipe": recipe}).json()["patched_path"]
    again = [{"action": "normalize", "column": "name", "params": {"trim": True, "case": "upper"}}]
    recipe2 = client.post("/recipe-from-text", json={"instruction": "x", "source_path": patched, "suggested_steps": again}).json()["recipe"]
    out = tmp_path / "out.csv"
    recipe2["outputs"] = [{"path": str(out)}]
    client.post("/apply", json=recipe2)
    with open(out, encoding="utf-8", newline="") as h:
        zips = [r["zip_code"] for r in csv.DictReader(h)]
    assert zips[:4] == ["03030", "12345", "07870", "98101"]
