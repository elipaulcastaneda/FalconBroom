import sys
from pathlib import Path

LOG = Path("run_uv_lifespan_off.log")
if not LOG.exists():
    print("log not found:", LOG)
    sys.exit(0)

data = LOG.read_bytes()
found = 0
patterns = [
    b"join-preview",
    b"join_preview",
    b"/join-preview",
    b"/join",
    b"join:",
    b"join ",
]

# helper to build utf-16le and be variants for ascii pattern
def to_utf16_variants(pat: bytes):
    try:
        s = pat.decode('ascii')
    except Exception:
        return []
    le = ''.join([c + '\x00' for c in s]).encode('latin-1')
    be = ''.join(['\x00' + c for c in s]).encode('latin-1')
    return [pat, le, be]

search_patterns = []
for p in patterns:
    search_patterns.extend(to_utf16_variants(p))

# also include lowercase ascii-only and uppercase variants
search_patterns = list(dict.fromkeys(search_patterns))

for pat in search_patterns:
    pos = 0
    while True:
        idx = data.find(pat, pos)
        if idx == -1:
            break
        found += 1
        start = max(0, idx - 2000)
        end = min(len(data), idx + 2000)
        window = data[start:end]
        print(f"--- occurrence #{found} at byte {idx}, pattern={pat[:20]!r} ---")
        # Attempt to decode the window with likely encodings
        for enc, label in (("utf-16le", "utf-16le"), ("utf-16be", "utf-16be"), ("utf-8", "utf-8"), ("latin-1", "latin-1")):
            try:
                s = window.decode(enc)
            except Exception as e:
                print(f"decode {label} failed: {e}")
                continue
            print(f"--- decoded as {label} ---")
            low_s = s.lower()
            # show context around the first occurrence of the ascii token
            marker = None
            for token in ("join-preview", "join_preview", "/join-preview", "/join", "join:", "join "):
                p = low_s.find(token)
                if p >= 0:
                    marker = p
                    break
            if marker is not None:
                left = max(0, marker - 400)
                right = min(len(s), marker + 400)
                print(s[left:right].replace('\r\n', '\n'))
            else:
                # print a short head of the decoded chunk
                print(s[:800].replace('\r\n', '\n'))
        pos = idx + 1

if found == 0:
    print("no join-related occurrences found in log (searched ascii and utf-16 variants)")
else:
    print(f"found {found} occurrences")
