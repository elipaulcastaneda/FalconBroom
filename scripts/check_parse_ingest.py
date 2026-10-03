import ast
s=open('fbroom/ingest.py','r',encoding='utf-8').read().splitlines()
for i in range(1,len(s)+1):
    try:
        ast.parse('\n'.join(s[:i]))
    except SyntaxError as e:
        print('failed at line', i, 'error at', e.lineno, e.msg)
        for j in range(max(0,i-5), i+2):
            if j < len(s):
                print(f"{j+1}: {s[j]}")
        break
else:
    print('parsed ok')
