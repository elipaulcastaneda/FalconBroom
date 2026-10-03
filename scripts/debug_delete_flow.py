#!/usr/bin/env python3
"""
Debug helper: sign up a temp user, login, request delete token, then call delete_all.
"""
import json
import urllib.request
import urllib.error
import uuid
import sys

BASE = 'http://127.0.0.1:3009'

def post(path, data, headers=None):
    url = BASE + path
    b = json.dumps(data).encode('utf-8')
    req = urllib.request.Request(url, data=b, method='POST')
    req.add_header('Content-Type', 'application/json')
    if headers:
        for k,v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.getcode(), r.read().decode('utf-8'), dict(r.getheaders())
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8') if e.fp else ''
        return e.code, body, dict(e.headers.items())
    except Exception as e:
        return None, str(e), {}

def main():
    uid = 'dev_' + uuid.uuid4().hex[:8]
    email = f"{uid}@example.com"
    password = 'Passw0rd!23'
    print('signup', uid)
    code, body, headers = post('/signup', {'username': uid, 'email': email, 'password': password})
    print('signup ->', code, body)
    if code is None:
        print('signup error, abort')
        return
    print('login')
    code, body, headers = post('/login', {'username': uid, 'password': password})
    print('login ->', code, body)
    if code != 200:
        print('login failed, abort')
        return
    j = json.loads(body)
    access = j.get('access_token')
    print('access token present?', bool(access))
    if not access:
        print('no access token, abort')
        return
    auth = {'Authorization': f'Bearer {access}'}
    print('request delete token')
    code, body, headers = post('/admin/history/request_delete_token', {}, headers=auth)
    print('request_delete_token ->', code, body)
    if code != 200:
        print('request delete token failed, abort')
        return
    j = json.loads(body)
    token = j.get('delete_token')
    print('delete_token:', token)
    if not token:
        return
    print('call delete_all')
    headers2 = dict(auth)
    headers2['X-Delete-Token'] = token
    code, body, headers = post('/history/delete_all', {}, headers=headers2)
    print('delete_all ->', code, body)

if __name__ == '__main__':
    main()
