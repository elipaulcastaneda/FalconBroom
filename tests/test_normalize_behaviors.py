import sys
import json
import pandas as pd

from fbroom.engine import _string_transform_column


def _df_from_list(vals, col_name="col"):
    return pd.DataFrame({col_name: vals})


def test_trim_only():
    df = _df_from_list(["  Alice  ", " Bob", "Charlie  "], "name")
    out = _string_transform_column(df, "name", case="trim")
    assert list(out["name"]) == ["Alice", "Bob", "Charlie"]


def test_lower_with_whitespace_and_unicode():
    df = _df_from_list(["  Émilie  ", "ALICE", " bob "], "name")
    out = _string_transform_column(df, "name", case="lower")
    assert list(out["name"]) == ["émilie", "alice", "bob"]


def test_upper_with_whitespace_and_unicode():
    df = _df_from_list(["  émilie  ", "alice", " Bob "], "name")
    out = _string_transform_column(df, "name", case="upper")
    assert list(out["name"]) == ["ÉMILIE", "ALICE", "BOB"]


def test_capitalize_and_title():
    df = _df_from_list(["  o'connor  ", "MARY ann", "jean-luc"], "name")
    out_cap = _string_transform_column(df, "name", case="capitalize")
    out_title = _string_transform_column(df, "name", case="title")
    assert list(out_cap["name"]) == ["O'connor", "Mary ann", "Jean-luc"]
    assert list(out_title["name"]) == ["O'Connor", "Mary Ann", "Jean-Luc"]
