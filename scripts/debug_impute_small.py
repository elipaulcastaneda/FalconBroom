import tempfile, os
import numpy as np
import polars as pl
from fbroom.engine import _read_table, _impute_missing

header = ['ID','HEIGHT','WEIGHT','AGE']
rows = []
rng = np.random.RandomState(0)
for i in range(50):
    h = 150 + rng.randn() * 10
    w = 60 + rng.randn() * 8
    age = 0.5 * h + 0.3 * w + 2 + rng.randn() * 0.1
    rows.append([i, round(h,3), round(w,3), round(age,3)])
for i in range(5): rows[i][3] = ''

with tempfile.TemporaryDirectory() as td:
    path = os.path.join(td, 'reg.csv')
    with open(path,'w',encoding='utf-8') as f:
        f.write(','.join(header)+'\n')
        for r in rows:
            f.write(','.join(str(x) for x in r)+'\n')
    df = _read_table(path)
    print('DF schema:', df.dtypes)
    print('Before missing count:', df.select(pl.col('AGE').is_null()).to_series().to_list()[:10])
    df2, info = _impute_missing(df, 'AGE', {'strategy':'regression','sources':['HEIGHT','WEIGHT']})
    print('Info:', info)
    print('After missing count sample:', df2.select(pl.col('AGE').is_null()).to_series().to_list()[:10])
