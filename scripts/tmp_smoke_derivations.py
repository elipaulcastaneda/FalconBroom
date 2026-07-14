"""Smoke test for _apply_derivations safe evaluator.
Creates small pandas and polars DataFrames and applies derivation rules.
"""
import json
try:
    import polars as pl
except Exception:
    pl = None
import pandas as pd
import fbroom.engine as eng

print('polars available:', pl is not None)

rows = [
    {'first': 'John', 'last': 'Doe', 'age': 20, 'country': 'US'},
    {'first': 'Jane', 'last': 'Smith', 'age': 16, 'country': 'US'},
    {'first': 'Ana', 'last': 'G', 'age': 30, 'country': 'CA'},
]

pd_df = pd.DataFrame(rows)
rules = [
    {'if': "age >= 18 and country == 'US'", 'then': {'col': 'is_adult_us', 'value': 'True'}},
    {'if': None, 'then': {'col': 'fullname', 'value': "first + ' ' + last"}},
]

print('\n-- pandas test --')
res = eng._apply_derivations(pd_df, params={'rules': rules})
def _safe_print(df):
    try:
        if pl is not None and isinstance(df, pl.DataFrame):
            obj = df.to_dicts()
        elif hasattr(df, 'to_dict'):
            obj = df.to_dict(orient='records')
        else:
            obj = df
        print(json.dumps(obj, ensure_ascii=True, default=str))
    except Exception:
        try:
            print(repr(df))
        except Exception:
            print('<<unprintable result>>')

_safe_print(res)

if pl is not None:
    print('\n-- polars test --')
    pl_df = pl.DataFrame(rows)
    res2 = eng._apply_derivations(pl_df, params={'rules': rules})
    _safe_print(res2)

print('\nSmoke test completed')
