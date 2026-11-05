import csv
import sqlite3

import pandas as pd
from fastapi.testclient import TestClient

from fbroom.main import app


def test_run_recipe_exports_every_format(tmp_path):
    src = tmp_path / "zips.csv"
    with open(src, "w", newline="", encoding="utf-8") as h:
        w = csv.writer(h)
        w.writerow(["id", "name", "zip"])
        w.writerows([[1, " ann ", "02134"], [2, "bob", "90210"], [3, "cy", ""]])
    client = TestClient(app)
    recipe = {
        "sources": [{"path": str(src)}],
        "cleaning_steps": [{"action": "normalize", "column": "name", "params": {"case": "title"}}],
        "joins": [],
        "outputs": [{"path": str(tmp_path / "out.csv")}],
    }
    rid = client.post("/recipes", json={"name": "fmt", "recipe": recipe}).json()["id"]
    assert client.post(f"/recipes/{rid}/approve").status_code == 200
    paths = {}
    for fmt, suffix in (("csv", ".csv"), ("xlsx", ".xlsx"), ("pkl", ".pkl"), ("sqlite", ".sqlite")):
        run = client.post(f"/recipes/{rid}/run?export_format={fmt}").json()["run"]
        assert run["output_path"].endswith(suffix), run
        assert client.get("/download", params={"path": run["output_path"]}).status_code == 200
        paths[fmt] = run["output_path"]
    frame = pd.read_pickle(paths["pkl"])
    assert list(frame["name"]) == ["Ann", "Bob", "Cy"] and len(frame) == 3
    assert list(frame["zip"].astype(str))[:2] == ["02134", "90210"]
    con = sqlite3.connect(paths["sqlite"])
    rows = con.execute("SELECT id, name, zip FROM data ORDER BY id").fetchall()
    con.close()
    assert [r[1] for r in rows] == ["Ann", "Bob", "Cy"] and rows[0][2] == "02134"
