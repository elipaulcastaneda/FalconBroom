#!/usr/bin/env python3
import glob
import subprocess
import sys
files = sorted(glob.glob('tests/**/*.py', recursive=True))
if not files:
    print('No test files found')
    sys.exit(0)
failures = []
for f in files:
    print('--- Running', f)
    try:
        res = subprocess.run([sys.executable, '-m', 'pytest', '-q', f], check=False)
    except Exception as e:
        print('ERROR running', f, e)
        failures.append((f, 'exception'))
        continue
    if res.returncode != 0:
        failures.append((f, res.returncode))
        print('>>> FAILED', f, 'rc=', res.returncode)
    else:
        print('PASS', f)

print('\nSummary:')
print('Total test files:', len(files))
print('Failures:', len(failures))
for f,code in failures:
    print(' -', f, code)
sys.exit(1 if failures else 0)
