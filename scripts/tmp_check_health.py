import requests, time
for i in range(10):
    try:
        r = requests.get('http://127.0.0.1:3009/health', timeout=2)
        print('health', r.status_code, r.text)
        break
    except Exception as e:
        print('wait', i, e)
        time.sleep(1)
else:
    print('no response')
