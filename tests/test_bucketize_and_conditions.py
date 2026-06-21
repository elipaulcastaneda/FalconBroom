import polars as pl
import pandas as pd
from fbroom.engine import _suggest_buckets, _conditional_transform


def test_suggest_buckets_quantile():
    df = pl.DataFrame({"v": list(range(1, 101))})
    buckets = _suggest_buckets(df, "v", strategy="quantile", n_buckets=4)
    assert isinstance(buckets, list)
    assert len(buckets) == 4
    # ensure buckets cover numeric range
    mins = [b.get("min") for b in buckets]
    maxs = [b.get("max") for b in buckets]
    assert min(mins) == 1.0
    assert max(maxs) == 100.0


def test_conditional_compound_polars():
    df = pl.DataFrame({"a": [1, 6, 7], "b": ["x", "x", "y"], "c": ["no", "no", "no"]})
    cond = {"op": "and", "conds": [{"column": "a", "op": ">", "value": 5}, {"column": "b", "op": "==", "value": "x"}]}
    out = _conditional_transform(df, "c", "hit", cond)
    # only middle row should be updated
    assert out.select(pl.col("c")).to_series().to_list() == ["no", "hit", "no"]


def test_conditional_compound_pandas():
    df = pd.DataFrame({"a": [1, 6, 7], "b": ["x", "x", "y"], "c": ["no", "no", "no"]})
    cond = {"op": "or", "conds": [{"column": "a", "op": ">", "value": 6}, {"column": "b", "op": "==", "value": "x"}]}
    out = _conditional_transform(df, "c", "ok", cond)
    assert out["c"].tolist() == ["ok", "ok", "ok"]
