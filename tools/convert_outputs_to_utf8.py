import sys
from pathlib import Path

files = [
    "debug_admin_call_output.txt",
    "debug_env_output.txt",
    "consent_jobs_output.txt",
]
encs = ["utf-8", "utf-16", "utf-16-le", "utf-16-be", "cp1252", "latin-1"]

for fn in files:
    p = Path(fn)
    if not p.exists():
        print(f"missing:{fn}")
        continue
    b = p.read_bytes()
    decoded = None
    used = None
    for e in encs:
        try:
            decoded = b.decode(e)
            used = e
            break
        except Exception:
            continue
    if decoded is None:
        # fallback: latin-1 to keep bytes
        decoded = b.decode('latin-1', errors='replace')
        used = 'latin-1-fallback'
    out = p.with_name(p.stem + "_utf8.txt")
    out.write_text(decoded, encoding='utf-8')
    print(f"wrote:{out} encoding:{used}")
