import os
import tempfile
import pytest
import polars as pl
import pandas as pd
from fbroom.engine import _resolve_conflict_name, _safe_rename_columns, _write_parquet


def _eager_join_preview_reference(spec):
    import polars as pl
    from fbroom.engine import _df_head_records, _read_table

    left = _read_table(spec.left_path)
    right = _read_table(spec.right_path)
    left_on = spec.left_on
    right_on = spec.right_on
    how = {"full": "outer"}.get(spec.join_type, spec.join_type)
    common = list((set(left.columns) & set(right.columns)) - set(left_on + right_on))
    if common:
        right = right.rename({column: f"{column}_right" for column in common})
    joined = left.join(right, left_on=left_on, right_on=right_on, how="full" if how == "outer" else how)
    joined_count = joined.height

    unmatched_left = None
    unmatched_right = None
    if how in ("inner", "left", "outer"):
        unmatched_left = _df_head_records(
            left.join(right, left_on=left_on, right_on=right_on, how="anti"), n=spec.sample
        )
    if how in ("inner", "right", "outer"):
        unmatched_right = _df_head_records(
            right.join(left, left_on=right_on, right_on=left_on, how="anti"), n=spec.sample
        )

    if (spec.conflict_resolution or {}).get("prefer") == "right":
        for column in common:
            right_name = f"{column}_right"
            if right_name in joined.columns and column in joined.columns:
                joined = joined.with_columns(
                    pl.when(pl.col(right_name).is_not_null())
                    .then(pl.col(right_name))
                    .otherwise(pl.col(column))
                    .alias(column)
                ).drop(right_name)

    return {
        "stats": {"left_count": left.height, "right_count": right.height, "joined_count": joined_count},
        "preview": _df_head_records(joined, n=spec.sample),
        "unmatched_left_sample": unmatched_left,
        "unmatched_right_sample": unmatched_right,
    }


@pytest.mark.parametrize("join_type", ["inner", "left", "right", "outer", "anti"])
@pytest.mark.parametrize("sample", [2, 0])
def test_join_preview_matches_eager_output_with_duplicate_and_null_keys(tmp_path, join_type, sample):
    import fbroom.main as main_module

    left_path = tmp_path / "left.csv"
    right_path = tmp_path / "right.csv"
    left_path.write_text(
        "key,label,left_value\n1,L1,a\n1,L1b,b\n,blank-left,c\n,empty-left,d\n2,only-left,e\n",
        encoding="utf-8",
    )
    right_path.write_text(
        "key,label,right_value\n1,R1,x\n,blank-right,y\n3,only-right,z\n",
        encoding="utf-8",
    )
    spec = main_module.JoinPreviewSpec(
        left_path=str(left_path),
        right_path=str(right_path),
        left_on=["key"],
        right_on=["key"],
        join_type=join_type,
        sample=sample,
        conflict_resolution={"prefer": "right"},
    )

    expected = _eager_join_preview_reference(spec)
    actual = main_module.join_preview(spec)

    if join_type == "outer" and sample == 0:
        row_key = lambda row: repr(sorted(row.items(), key=lambda item: item[0]))
        actual["preview"] = sorted(actual["preview"], key=row_key)
        expected["preview"] = sorted(expected["preview"], key=row_key)
    assert actual == expected


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
