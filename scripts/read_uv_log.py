import sys
f='run_uv_lifespan_off.log'
try:
    b=open(f,'rb').read()
    try:
        s=b.decode('utf-16')
    except Exception:
        s=b.decode('utf-8',errors='replace')
    print(s)
except Exception as e:
    print('read error', e)
