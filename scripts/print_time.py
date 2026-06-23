import time, datetime
print('time.time():', int(time.time()))
print('utc now:', datetime.datetime.now(datetime.timezone.utc).isoformat())
