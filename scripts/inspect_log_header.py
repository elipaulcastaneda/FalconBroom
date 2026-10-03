from pathlib import Path
LOG = Path('run_uv_lifespan_off.log')
if not LOG.exists():
    print('log not found', LOG)
    raise SystemExit(1)
b = LOG.read_bytes()[:256]
print('len:', len(b))
print('hex:', b[:32].hex())
print('repr:', repr(b[:120]))

# also show potential utf-16 patterns
pairs = [b[i:i+2] for i in range(0, min(len(b), 64), 2)]
print('first 32 bytes as 2-byte pairs:')
print(' '.join([p.hex() for p in pairs]))
