import pandas as pd
import pytest
import polars as pl

from fbroom.engine import (
    _apply_derivations,
    _cross_column_consistency,
    _external_lookup,
    _iter_dtype_preserving_records,
)


def test_tuple_records_restore_integer_columns_without_casting_float_columns():
    frame = pd.DataFrame({
        "code": pd.Series([101, 202], dtype="int64"),
        "measure": pd.Series([1.5, 2.5], dtype="float64"),
        "label": ["a", "b"],
    })

    records = list(_iter_dtype_preserving_records(frame))

    assert records == [
        {"code": 101, "measure": 1.5, "label": "a"},
        {"code": 202, "measure": 2.5, "label": "b"},
    ]
    assert type(records[0]["code"]) is int
    assert type(records[0]["measure"]) is float


def test_tuple_records_preserve_nullable_integer_missing_values():
    frame = pd.DataFrame({
        "code": pd.Series([101, None], dtype="Int64"),
        "measure": pd.Series([1.5, 2.5], dtype="float64"),
    })

    records = list(_iter_dtype_preserving_records(frame))

    assert records[0]["code"] == 101 and type(records[0]["code"]) is int
    assert pd.isna(records[1]["code"])
    assert all(type(record["measure"]) is float for record in records)


def _signature(value):
    missing = value is pd.NA or value is pd.NaT or (isinstance(value, float) and value != value)
    return type(value).__name__, "<missing>" if missing else value


@pytest.mark.parametrize("name,frame", [
    ("float only", pd.DataFrame({"f": [1.5, float("nan"), 3.0]})),
    ("float with strings", pd.DataFrame({"f": [1.5, 2.5, 3.5], "s": ["a", "b", "c"]})),
    ("integers only", pd.DataFrame({"i": pd.Series([1, 2, 3], dtype="int64"), "j": pd.Series([4, 5, 6], dtype="int64")})),
    ("integer with strings", pd.DataFrame({"i": pd.Series([1, 2, 3], dtype="int64"), "s": ["a", "b", "c"]})),
    ("bool with float", pd.DataFrame({"b": [True, False, True], "f": [1.5, 2.5, 3.5]})),
    ("bool with strings", pd.DataFrame({"b": [True, False, True], "s": ["a", "b", "c"]})),
    ("datetime with float", pd.DataFrame({"d": pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03"]), "f": [1.5, 2.5, 3.5]})),
    ("float32 with float64", pd.DataFrame({"a": pd.Series([1.5, 2.5, 3.5], dtype="float32"), "b": [1.5, 2.5, 3.5]})),
    ("nullable float", pd.DataFrame({"f": pd.Series([1.5, None, 3.5], dtype="Float64")})),
    ("nullable integer with strings", pd.DataFrame({"i": pd.Series([1, None, 3], dtype="Int64"), "s": ["a", "b", "c"]})),
    ("category", pd.DataFrame({"c": pd.Categorical(["x", "y", "x"]), "f": [1.5, 2.5, 3.5]})),
    ("string dtype", pd.DataFrame({"s": pd.Series(["a", None, "c"], dtype="string"), "f": [1.5, 2.5, 3.5]})),
])
def test_tuple_records_match_iterrows_for_floats_and_other_dtypes(name, frame):
    old = [{k: _signature(v) for k, v in row.items()} for _, row in frame.iterrows()]
    new = [{k: _signature(v) for k, v in row.items()} for row in _iter_dtype_preserving_records(frame)]
    assert new == old, name


def test_only_documented_difference_is_integers_in_mixed_numeric_rows():
    frame = pd.DataFrame({"i": pd.Series([1, 2], dtype="int64"), "f": [1.5, 2.5]})
    old = [{k: _signature(v) for k, v in row.items()} for _, row in frame.iterrows()]
    new = [{k: _signature(v) for k, v in row.items()} for row in _iter_dtype_preserving_records(frame)]
    assert [row["i"][0] for row in old] == ["float", "float"]
    assert [row["i"][0] for row in new] == ["int", "int"]
    assert [row["f"] for row in new] == [row["f"] for row in old]


def test_string_key_rows_keep_existing_lookup_outputs(tmp_path, monkeypatch):
    import sqlite3

    frame = pd.DataFrame({"code": ["A101", "B202"], "actual": ["Wrong", "Right"], "measure": [1.5, 2.5]})
    assert list(_iter_dtype_preserving_records(frame)) == frame.to_dict(orient="records")

    mapping = tmp_path / "string_map.csv"
    mapping.write_text("code,expected\nA101,Boston\nB202,Right\n", encoding="utf-8")
    _, diagnostics = _cross_column_consistency(frame, {"rules": [
        {"lhs": "code", "rhs": "actual", "map_path": str(mapping)},
    ]})
    assert diagnostics[0]["mismatches"] == 1

    database = tmp_path / "string_lookup.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE lookup (code TEXT, expected TEXT)")
    connection.executemany("INSERT INTO lookup VALUES (?, ?)", [("A101", "North"), ("B202", "South")])
    connection.commit()
    connection.close()
    sqlite_result = _external_lookup(frame[["code", "measure"]], {
        "mode": "sqlite",
        "db": str(database),
        "query": "SELECT expected FROM lookup WHERE code='{code}'",
        "map": {"expected": "region"},
    })
    sqlite_values = sqlite_result["region"].to_list() if hasattr(sqlite_result["region"], "to_list") else sqlite_result["region"].tolist()
    assert sqlite_values == ["North", "South"]

    class Response:
        status_code = 200

        def __init__(self, value):
            self.value = value

        def json(self):
            return {"result": self.value}

    def fake_get(url, **kwargs):
        return Response(url.rsplit("/", 1)[-1])

    monkeypatch.setattr("httpx.get", fake_get)
    http_result = _external_lookup(frame[["code", "measure"]], {
        "mode": "http",
        "template": "https://lookup.test/{code}",
        "map": {"result": "resolved"},
        "cache_ttl": 0,
    })
    http_values = http_result["resolved"].to_list() if hasattr(http_result["resolved"], "to_list") else http_result["resolved"].tolist()
    assert http_values == ["A101", "B202"]


def test_derivation_fallback_uses_preserved_integer_scalars(monkeypatch):
    frame = pd.DataFrame({
        "count": pd.Series([1, 2], dtype="int64"),
        "measure": pd.Series([1.5, 2.5], dtype="float64"),
    })

    def fail_apply(*args, **kwargs):
        raise RuntimeError("force tuple fallback")

    monkeypatch.setattr(pd.DataFrame, "apply", fail_apply)
    result = _apply_derivations(frame, {"rules": [{"if": None, "then": {"col": "next_count", "value": "count + 1"}}]})

    assert result["next_count"].to_list() == [2, 3]
    assert result["next_count"].dtype == pl.Int64


def test_consistency_lookup_uses_integer_key_values(tmp_path):
    mapping = tmp_path / "numeric_map.csv"
    mapping.write_text("code,expected\n101,1\n202,2\n", encoding="utf-8")
    frame = pd.DataFrame({
        "code": pd.Series([101, 202], dtype="int64"),
        "actual": pd.Series([0, 0], dtype="int64"),
        "measure": pd.Series([1.5, 2.5], dtype="float64"),
    })

    _, diagnostics = _cross_column_consistency(frame, {"rules": [
        {"lhs": "code", "rhs": "actual", "map_path": str(mapping)},
    ]})

    assert diagnostics[0]["mismatches"] == 2


def test_sqlite_lookup_formats_integer_keys_without_decimal_suffix(tmp_path):
    import sqlite3

    database = tmp_path / "lookup.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE lookup (code TEXT, expected TEXT)")
    connection.executemany("INSERT INTO lookup VALUES (?, ?)", [("101", "one"), ("202", "two")])
    connection.commit()
    connection.close()

    frame = pd.DataFrame({
        "code": pd.Series([101, 202], dtype="int64"),
        "measure": pd.Series([1.5, 2.5], dtype="float64"),
    })
    result = _external_lookup(frame, {
        "mode": "sqlite",
        "db": str(database),
        "query": "SELECT expected FROM lookup WHERE code='{code}'",
        "map": {"expected": "result"},
    })

    values = result["result"].to_list() if hasattr(result["result"], "to_list") else result["result"].tolist()
    assert values == ["one", "two"]


def test_http_lookup_formats_integer_keys_without_decimal_suffix(monkeypatch):
    class Response:
        status_code = 200

        def __init__(self, value):
            self.value = value

        def json(self):
            return {"result": self.value}

    def fake_get(url, **kwargs):
        return Response(url.rsplit("/", 1)[-1])

    monkeypatch.setattr("httpx.get", fake_get)
    frame = pd.DataFrame({
        "code": pd.Series([101, 202], dtype="int64"),
        "measure": pd.Series([1.5, 2.5], dtype="float64"),
    })

    result = _external_lookup(frame, {
        "mode": "http",
        "template": "https://lookup.test/{code}",
        "map": {"result": "resolved"},
        "cache_ttl": 0,
    })

    values = result["resolved"].to_list() if hasattr(result["resolved"], "to_list") else result["resolved"].tolist()
    assert values == ["101", "202"]
