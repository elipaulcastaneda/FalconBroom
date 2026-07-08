import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from fbroom import engine
from fbroom.engine import _read_table, _cast_column

src = 'samples/test_datasets/sales_inconsistent_dates.csv'
df = _read_table(src)
print('Columns:', df.columns)
col = 'order_date'
res = _cast_column(df, col, 'datetime', '%Y-%m-%d')
if isinstance(res, tuple):
    df_new, info = res
    print('Info:', info)
else:
    df_new = res
print('Before head:', df.head(10).to_dicts())
print('After head:', df_new.head(10).to_dicts())

# If polars, also inspect types
try:
    import polars as pl
    print('Dtypes before:', df.dtypes)
    print('Dtypes after:', df_new.dtypes)
except Exception:
    pass

print('\n-- Inspect pandas parsing directly --')
try:
    import pandas as _pd
    pd_df = df.to_pandas()
    print('pandas dtype before:', pd_df[col].dtype)
    try:
        pd_parsed = _pd.to_datetime(pd_df[col], format='%Y-%m-%d', errors='coerce', infer_datetime_format=True)
    except Exception as e:
        pd_parsed = _pd.to_datetime(pd_df[col], errors='coerce', infer_datetime_format=True, dayfirst=False)
    print('parsed head:', pd_parsed.head(10).tolist())
    print('parsed dtype:', pd_parsed.dtype)
except Exception as e:
    print('pandas parse failed', e)
