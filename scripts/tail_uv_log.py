p='run_uv_lifespan_off.log'
try:
    b=open(p,'rb').read()
    try:
        s=b.decode('utf-16')
    except Exception:
        s=b.decode('utf-8',errors='replace')
    print(s[-2000:])
except Exception as e:
    print('error', e)
