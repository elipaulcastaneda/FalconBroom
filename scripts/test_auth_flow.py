import requests
import random, string, time, sys
BASE = 'http://127.0.0.1:3009'

s = requests.Session()
username = 'ci_user_' + ''.join(random.choice('0123456789abcdef') for _ in range(6))
password = 'TestPass123!'
email = username + '@example.com'
print('Signup:', username)
resp = s.post(BASE + '/signup', json={'username': username, 'email': email, 'password': password})
print('signup', resp.status_code, resp.text)

# Login using session so cookies are captured
resp = s.post(BASE + '/login', json={'username': username, 'password': password, 'persistent': True})
print('\nlogin', resp.status_code, resp.text)
# show cookies
print('\ncookies:', s.cookies.get_dict())
# persist refresh cookie for inspection
try:
    import os
    os.makedirs('data', exist_ok=True)
    rt = s.cookies.get_dict().get('falconbroom_refresh')
    if rt:
        open('data/last_refresh.txt','w',encoding='utf-8').write(rt)
except Exception:
    pass

# Call refresh using the same session (sends cookie)
r2 = s.post(BASE + '/refresh')
print('\nrefresh', r2.status_code, r2.text)
if r2.ok:
    at = r2.json().get('access_token')
else:
    # fallback: use access token returned by login if present
    try:
        at = resp.json().get('access_token')
    except Exception:
        at = None

if not at:
    print('\nNo access token available; aborting')
    sys.exit(2)

# Call /me with Authorization
h = {'Authorization': 'Bearer ' + at}
me = s.get(BASE + '/me', headers=h)
print('\n/me', me.status_code, me.text)

if me.ok:
    print('\nSuccess: /me returned user info')
    sys.exit(0)
else:
    print('\nFailure: /me did not return 200')
    sys.exit(3)
