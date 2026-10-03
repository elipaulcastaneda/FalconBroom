import json
from pathlib import Path
p = Path('run_uv_lifespan_off.log')
if not p.exists():
    print('log not found:', p)
    raise SystemExit(1)
encs = ['utf-8','utf-16','utf-16-le','utf-16-be','latin-1']
data = p.read_bytes()
text = None
used_enc = None
for e in encs:
    try:
        text = data.decode(e)
        if '/join-preview incoming spec:' in text or '/join-export incoming spec:' in text or 'Join failed' in text:
            used_enc = e
            break
    except Exception:
        continue
if text is None:
    print('No readable text with expected markers in encodings:', encs)
    raise SystemExit(1)
print('decoded with', used_enc)
markers = []
for m in ['/join-preview incoming spec:', '/join-export incoming spec:']:
    start = 0
    while True:
        idx = text.find(m, start)
        if idx == -1:
            break
        markers.append((m, idx))
        start = idx + 1
if not markers:
    print('no markers found')
    raise SystemExit(0)
# sort by index
markers.sort(key=lambda x: x[1])
for i, (m, idx) in enumerate(markers):
    print('\n--- OCCURRENCE', i+1, m, 'at', idx, '---')
    window_start = max(0, idx-2000)
    window_end = min(len(text), idx+2000)
    snippet = text[window_start:window_end]
    print('\n--- WINDOW (truncated) ---\n')
    print(snippet)
    # attempt to extract JSON object starting after marker
    after = text[idx + len(m):]
    # find first '{'
    jstart = after.find('{')
    if jstart == -1:
        print('\nNo JSON object found after marker.')
        continue
    jstart_abs = idx + len(m) + jstart
    # balance braces
    depth = 0
    jend = None
    for pos in range(jstart_abs, len(text)):
        ch = text[pos]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                jend = pos
                break
    if jend is None:
        print('\nCould not find end of JSON object; showing tail...')
        print(text[jstart_abs:jstart_abs+2000])
        continue
    jtext = text[jstart_abs:jend+1]
    try:
        obj = json.loads(jtext)
        print('\n--- PARSED JSON ---')
        print(json.dumps(obj, indent=2)[:4000])
    except Exception as e:
        print('\nFailed to parse JSON:', e)
        print('RAW JSON TEXT (truncated):')
        print(jtext[:2000])
print('\nDone')
