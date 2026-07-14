import sys
from pathlib import Path
# ensure repo root on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fbroom import engine
import pandas as pd

# build sample df
pdf = pd.DataFrame({'amt': ['$10.00', '20', None, '£30']})
print('Testing sample fallback (USD->EUR)')
res = engine._convert_currency_column(pdf, 'amt', params={'from':'USD','to':'EUR'})
print(res.head())

print('\nTesting explicit rate (2.0)')
res2 = engine._convert_currency_column(pdf, 'amt', params={'from':'USD','to':'EUR','rate':2.0,'out_col':'amt_conv'})
print(res2.head())

print('\nTesting exchangerate.host fetch (may fail if offline)')
try:
    res3 = engine._convert_currency_column(pdf, 'amt', params={'from':'USD','to':'EUR','provider':'exchangerate.host','out_col':'amt_live'})
    print(res3.head())
except Exception as e:
    print('fetch failed:', e)
