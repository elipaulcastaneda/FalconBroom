#!/usr/bin/env python3
import os
import json
from pathlib import Path
from datetime import datetime, timezone
import hashlib

OUT_DIR = Path('data') / 'outputs'
HIST_DIR = Path('data') / 'history'
HIST_DIR.mkdir(parents=True, exist_ok=True)

# load existing history entries to avoid duplicates (by output_path)
existing = {}
for p in HIST_DIR.glob('*.json'):
    try:
        j = json.loads(p.read_text(encoding='utf-8'))
        op = j.get('output_path') or j.get('output') or j.get('output_path')
        if op:
            existing[str(Path(op).resolve())] = p
    except Exception:
        continue

created = []
for p in OUT_DIR.iterdir():
    if not p.is_file():
        continue
    name = p.name.lower()
    # heuristic: treat files with 'join' in filename as join exports
    if 'join' not in name:
        continue
    opath = str(p.resolve())
    if opath in existing:
        continue
    # create a history record
    mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat().replace('+00:00','Z')
    # id based on sha1 of path+mtime
    h = hashlib.sha1((opath + mtime).encode('utf-8')).hexdigest()[:12]
    rec = {
        'id': f'join_{h}',
        'type': 'join',
        'status': 'success',
        'started_at': mtime,
        'finished_at': mtime,
        'output_path': str(p.as_posix()),
        'filename': p.name,
    }
    outp = HIST_DIR / f"{rec['id']}.json"
    outp.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding='utf-8')
    created.append(str(outp))

print('Created', len(created), 'history entries')
for c in created:
    print(' -', c)
