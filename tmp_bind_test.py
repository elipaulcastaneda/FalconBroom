import socket, sys, traceback
s = socket.socket()
try:
    s.bind(('127.0.0.1', 3010))
    print('OK')
    s.listen(1)
except Exception as e:
    print('ERR', e)
    traceback.print_exc()
    sys.exit(1)
finally:
    try:
        s.close()
    except:
        pass
