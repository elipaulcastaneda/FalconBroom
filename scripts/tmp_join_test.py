import requests, json, sys, io
BACK='http://127.0.0.1:3009'
ups = requests.get(BACK+'/uploads').json().get('uploads',[])
if len(ups)<2:
    print('need >=2 uploads', len(ups))
    sys.exit(2)
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
    r = requests.post(BACK+'/join-preview', json=payload)
    print('status', r.status_code)
    try:
        print(json.dumps(r.json(), indent=2))
    except Exception:
        print(r.text)
except Exception as e:
    print('request error', e)

# read log file as utf-16/utf8 fallback and print join-preview related lines
p='run_uv_lifespan_off.log'
for enc in ('utf-8','utf-16','utf-16-le','utf-16-be','latin-1'):
    try:
        with open(p,'r',encoding=enc) as f:
            s=f.read()
        lines=s.splitlines()
        matches=[l for l in lines if '/join-preview' in l or 'reading left materialized path' in l or 'left_df columns' in l or 'resolved left' in l]
        print('\n--- log matches decoded with',enc,'---')
        for m in matches[-200:]: print(m)
        break
    except Exception as e:
        #print('failed', enc, e)
        continue
