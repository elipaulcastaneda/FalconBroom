import json, sys, traceback
from pathlib import Path
# ensure workspace root is on sys.path so `fbroom` package imports work
root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))
from fastapi.testclient import TestClient
from fbroom.main import app

client = TestClient(app)

try:
    ups_resp = client.get('/uploads')
    ups = ups_resp.json().get('uploads', [])
    if len(ups) < 2:
        print('need >=2 uploads', len(ups))
        sys.exit(2)
    left = ups[0]['path']
    right = ups[1]['path']
    insL = client.post('/inspect', json={'path': left, 'offset': 0, 'limit': 1}).json()
    insR = client.post('/inspect', json={'path': right, 'offset': 0, 'limit': 1}).json()
    colsL = insL.get('inspection', {}).get('columns', [])
    colsR = insR.get('inspection', {}).get('columns', [])
    key = next((c for c in colsL if c in colsR), colsL[0] if colsL else (colsR[0] if colsR else None))
    print('left', left)
    print('right', right)
    print('key', key)
    payload = {'left_path': left, 'right_path': right, 'left_on': [key], 'right_on': [key], 'join_type': 'inner', 'sample': 2, 'conflict_resolution': {'strategy': 'suffix_right'}}
    r = client.post('/join-export', json=payload)
    print('status', r.status_code)
    try:
        print(json.dumps(r.json(), indent=2))
    except Exception:
        print(r.text)
except Exception as e:
    print('exception during test')
    traceback.print_exc()
    sys.exit(1)
