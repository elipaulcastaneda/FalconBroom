from pathlib import Path
s=Path('fbroom/main.py').read_text()
lines=s.splitlines()
up_to=1929
try_count=0
except_count=0
for i,l in enumerate(lines[:up_to]):
    if 'try:' in l:
        try_count+=1
    if 'except' in l:
        except_count+=1
print('try_count',try_count,'except_count',except_count)
for idx in range(up_to-10,up_to+1):
    print(idx+1, lines[idx])
