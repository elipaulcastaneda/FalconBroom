import csv

import pytest
from fastapi.testclient import TestClient

from fbroom.main import app


def _write(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as h:
        w = csv.writer(h)
        w.writerow(["id", "name", "note"])
        w.writerows(rows)


def _recipe(client, path, out):
    steps = [
        {"action": "normalize", "column": "name", "params": {"trim": True, "case": "lower"}},
        {"action": "drop_rows", "column": "note", "params": {"issue": "blank"}},
        {"action": "deduplicate", "column": None, "params": {}},
    ]
    recipe = client.post("/recipe-from-text", json={"instruction": "x", "source_path": str(path), "suggested_steps": steps}).json()["recipe"]
    recipe["outputs"] = [{"path": str(out)}]
    return recipe


def test_apply_selected_runs_whole_recipe_and_keeps_row_order(tmp_path):
    src = tmp_path / "in.csv"
    rows = []
    for i in range(400):
        rows.append([i, f" Name{i % 150} ", "" if i % 7 == 0 else f"n{i % 150}"])
    _write(src, rows)
    client = TestClient(app)

    outs = []
    for k in range(2):
        out = tmp_path / f"out{k}.csv"
        client.post("/apply", json=_recipe(client, src, out))
        outs.append(out.read_bytes())
    assert outs[0] == outs[1]

    recipe = _recipe(client, src, tmp_path / "unused.csv")
    res = client.post("/apply-patch", json={"path": str(src), "patches": [], "recipe": recipe})
    assert res.status_code == 200
    body = res.json()
    assert open(body["patched_path"], "rb").read() == outs[0]
    with open(body["patched_path"], encoding="utf-8", newline="") as h:
        got = list(csv.DictReader(h))
    assert len(got) < 400
