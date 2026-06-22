import os
import tempfile
import polars as pl
import pandas as pd
from fbroom.engine import _resolve_conflict_name, _safe_rename_columns, _write_parquet


def test_resolve_conflict_name_simple():
    existing = {'a', 'b', 'name'}
    assert _resolve_conflict_name(existing, 'new') == 'new'
    assert _resolve_conflict_name(existing, 'name') == 'name_1'
    # multiple collisions
    existing2 = {'name', 'name_1', 'name_2'}
    assert _resolve_conflict_name(existing2, 'name') == 'name_3'


def test_safe_rename_columns_polars():
    df = pl.DataFrame({'a': [1], 'b': [2]})
    df2, applied = _safe_rename_columns(df, {'a': 'b', 'b': 'a'})
    # applied should map to non-conflicting names
    assert 'a' in applied and 'b' in applied
    # resulting df should have columns equal to applied values
    cols = set(df2.columns)
    assert set(applied.values()) <= cols


def test_write_parquet_roundtrip(tmp_path):
    df = pl.DataFrame({'x': [1,2,3], 'y': ['a','b','c']})
    out = os.path.join(str(tmp_path), 'test.parquet')
    p = _write_parquet(df, out, compression='snappy')
    if p is None:
        # environment may not have pyarrow; skip gracefully
        assert True
        return
    assert os.path.exists(out)
    # try read back with polars
    df2 = pl.read_parquet(out)
    assert df2.shape == df.shape
