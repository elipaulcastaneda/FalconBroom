import re
s = 'Make all the negative number values in the amount column positive'
pat = re.compile(r"make\s+(?:all\s+)?negative\s+(?:number\s+)?values\s+(?:in|of)\s+(?:the\s+)?([a-zA-Z0-9_ ]+)(?:\s+column)?\s+positive", flags=re.IGNORECASE)
m = pat.search(s)
print('match', bool(m), m.groups() if m else None)
