import pytest
try:
    import polars as pl
except Exception:
    pl = None

from fbroom.engine import _cast_column


@pytest.mark.skipif(pl is None, reason="polars not available")
def test_cast_to_int_polars():
    df = pl.DataFrame({"num": ["1", "2", "x", "4"]})
    res, info = _cast_column(df, "num", "int")
    assert info and info.get("to_type") == "int"
    vals = res.select(pl.col("num")).to_series().to_list()
    assert len(vals) == 4
    # ensure original numeric entries are still present (either as strings or ints)
    for expected in ("1", "2", "4"):
        assert any(str(v) == expected for v in vals)
