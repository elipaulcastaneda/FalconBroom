import csv

from fastapi.testclient import TestClient

from fbroom.main import app


def _write(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as h:
        w = csv.writer(h)
        w.writerow(["id", "state", "score", "note"])
        w.writerows(rows)


def _apply(client, src, steps, out):
    recipe = client.post("/recipe-from-text", json={"instruction": "x", "source_path": str(src), "suggested_steps": steps}).json()["recipe"]
    assert len(recipe["cleaning_steps"]) == len(steps)
    recipe["outputs"] = [{"path": str(out)}]
    assert client.post("/apply", json=recipe).status_code == 200
    with open(out, encoding="utf-8", newline="") as h:
        return list(csv.DictReader(h))


def test_blank_column_note_offers_options_that_reach_the_csv(tmp_path):
    src = tmp_path / "data.csv"
    _write(src, [[i, "ca", "" if i % 4 == 0 else str(10 + i), "Alpha" if i % 3 else ""] for i in range(1, 41)])
    client = TestClient(app)
    note = "Review column 'score' (10 nulls); no automatic change is suggested without evidence of a specific value issue."
    options = client.post("/review-options", json={"path": str(src), "text": note, "column": "score"}).json()["options"]
    texts = [o["text"] for o in options]
    assert any("median" in t for t in texts) and any("Drop the 10 row(s)" in t for t in texts)
    for i, option in enumerate(options):
        assert option["json_steps"]
        rows = _apply(client, src, option["json_steps"], tmp_path / f"out{i}.csv")
        if option["category"] == "drop_rows":
            assert len(rows) == 30 and all(r["score"] for r in rows)
        elif option["category"] == "fix":
            assert len(rows) == 40 and all(r["score"] for r in rows)


def test_text_mode_fill_uses_a_value_even_when_blanks_are_most_common(tmp_path):
    src = tmp_path / "data.csv"
    _write(src, [[i, "ca", "1", "" if i <= 25 else f"n{i % 2}"] for i in range(1, 41)])
    client = TestClient(app)
    note = "Review column 'note' (25 nulls; common text column); no automatic change is suggested without evidence of a specific value issue."
    options = client.post("/review-options", json={"path": str(src), "text": note, "column": "note"}).json()["options"]
    common = next(o for o in options if "most common" in o["text"])
    rows = _apply(client, src, common["json_steps"], tmp_path / "out.csv")
    assert all(r["note"] for r in rows)


def test_unrecognized_note_has_no_options(tmp_path):
    src = tmp_path / "data.csv"
    _write(src, [[i, "ca", "1", "x"] for i in range(1, 12)])
    client = TestClient(app)
    res = client.post("/review-options", json={"path": str(src), "text": "Something else about 'state'", "column": "state"})
    assert res.json()["options"] == []
