import sys
p='run_uv_lifespan_off.log'
found=False
for enc in ('utf-8','utf-16','utf-16-le','latin-1'):
    try:
        with open(p,'rb') as fh:
            data=fh.read()
        text = data.decode(enc)
        if '/join-preview incoming spec' in text or '/join-export incoming spec' in text or 'Join failed' in text:
            print('DECODE',enc)
            for line in text.splitlines():
                if '/join-preview incoming spec' in line or '/join-export incoming spec' in line or 'Join failed' in line:
                    print(line)
            found=True
            break
    except Exception:
        continue
if not found:
    print('no join lines found in any encoding')
