import requests, json, sys
BACK='http://127.0.0.1:3009'
ups = requests.get(BACK+'/uploads').json().get('uploads',[])
if len(ups)<2:
    print('need >=2 uploads', len(ups)); sys.exit(2)
left=ups[0]['path']; right=ups[1]['path']
insL = requests.post(BACK+'/inspect', json={'path': left, 'offset':0, 'limit':1}).json()
insR = requests.post(BACK+'/inspect', json={'path': right, 'offset':0, 'limit':1}).json()
colsL = insL.get('inspection',{}).get('columns', [])
colsR = insR.get('inspection',{}).get('columns', [])
key = next((c for c in colsL if c in colsR), colsL[0] if colsL else (colsR[0] if colsR else None))
print('left', left)
print('right', right)
print('key', key)
payload={'left_path': left, 'right_path': right, 'left_on':[key], 'right_on':[key], 'join_type':'inner', 'sample':2, 'conflict_resolution':{'strategy':'suffix_right'}}
try:
    r = requests.post(BACK+'/join-export', json=payload)
    print('status', r.status_code)
    try:
        print(json.dumps(r.json(), indent=2))
    except Exception:
        print(r.text)
except Exception as e:
    print('request error', e)
