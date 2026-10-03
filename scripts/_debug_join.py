import pandas as pd

L='samples/test_datasets/customers.csv'
R='samples/test_datasets/orders.csv'
print('reading',L,R)
l=pd.read_csv(L)
r=pd.read_csv(R)
print('LEFT COLS:', l.columns.tolist())
print('RIGHT COLS:', r.columns.tolist())
try:
    j = pd.merge(l, r, left_on=['customer_id'], right_on=['customer_id'], how='inner')
    print('JOINED LEN:', len(j))
    print(j.head().to_csv(index=False))
except Exception as e:
    import traceback
    traceback.print_exc()
    print('EXC:', repr(e))
