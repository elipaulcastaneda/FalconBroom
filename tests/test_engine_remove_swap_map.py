import pytest
from fbroom.engine import _remove_by_type, _swap_by_types, _map_values
try:
    import polars as pl
except Exception:
    pl = None


@pytest.mark.skipif(pl is None, reason="polars not available")
def test_remove_by_type_numeric():
    df = pl.DataFrame({"a": ["1", "x", "(2)", "$3"], "b": ["foo", "bar", "baz", "qux"]})
    res, info = _remove_by_type(df, "a", {"target_type": "numeric", "replacement": ""})
    assert info and info.get("rows_changed") == 3
    # check that non-numeric 'x' remains
    vals = res.select(pl.col("a")).to_series().to_list()
    assert vals[1] == "x"


@pytest.mark.skipif(pl is None, reason="polars not available")
def test_swap_by_types_basic():
    df = pl.DataFrame({"src": ["1", "a", "2"], "tgt": ["x", "2", "y"]})
    moves = [{"source": "src", "target": "tgt", "type": "numeric", "exceptions": []}]
    res, info = _swap_by_types(df, moves, replacement="")
    assert info and info.get("rows_changed") >= 1


@pytest.mark.skipif(pl is None, reason="polars not available")
def test_map_values_vectorized():
    df = pl.DataFrame({"col": ["a", "b", "c", "d"]})
    mapping = {"a": "A", "b": "B"}
    res = _map_values(df, "col", mapping)
    vals = res.select(pl.col("col")).to_series().to_list()
    assert vals[0] == "A" and vals[1] == "B" and vals[2] == "c"
