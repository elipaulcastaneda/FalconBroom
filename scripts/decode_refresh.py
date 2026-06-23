import jwt, json, os

token = os.environ.get('REFRESH_TOKEN')
if not token:
    # try reading last saved refresh token
    try:
        token = open('data/last_refresh.txt','r',encoding='utf-8').read().strip()
    except Exception:
        token = None
secret = os.environ.get('JWT_SECRET', 'dev_test_secret')
print('Using secret:', secret)
try:
    payload = jwt.decode(token, secret, algorithms=['HS256'], options={'verify_exp': False}, leeway=60)
    print('Decoded payload:')
    print(json.dumps(payload, indent=2))
    jti = payload.get('jti')
    if jti:
        p = os.path.join('data','sessions', f'session_{jti}.json')
        print('Expected session file:', p)
        print('Exists:', os.path.exists(p))
        if os.path.exists(p):
            print('Session file contents:')
            print(open(p,'r',encoding='utf-8').read())
except Exception as e:
    print('Decode error:', repr(e))
