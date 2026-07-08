from pathlib import Path
p = Path('scripts/py_spy_stacks.txt')
out = Path('scripts/py_spy_stacks_decoded.txt')
raw = p.read_bytes()
for enc in ('utf-8', 'utf-16', 'utf-16le', 'utf-16be', 'latin-1'):
    try:
        s = raw.decode(enc)
        out.write_text(s, encoding='utf-8')
        print('decoded with', enc)
        break
    except Exception as e:
        # try next
        continue
else:
    out.write_bytes(raw)
    print('wrote raw bytes')
print('wrote', out)
