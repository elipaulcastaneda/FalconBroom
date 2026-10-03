import tokenize
from io import BytesIO
s=open('fbroom/ingest.py','rb').read()
for tok in tokenize.tokenize(BytesIO(s).readline):
    if tok.start[0] in range(10,25):
        print(tok)
