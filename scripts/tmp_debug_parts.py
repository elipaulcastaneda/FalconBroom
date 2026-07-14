import re
text = "Move all non-numerical values from postal_code to city, then move all values from col_6 to postal_code, then drop col_6."
parts = [p.strip() for p in re.split(r'(?i)\bthen\b|;', text) if p and p.strip()]
print('PARTS:')
for i,p in enumerate(parts):
    print(i, repr(p))
    m_typed = re.search(r"(?i)(?:move|put|copy)\s+all\s+(?P<neg>non[-\s]?|not\s+)?(?P<type>numeric|numerical|number|numbers|string|text|letters|date|dates)\s*(?:values|entries)?\s*(?:in|from|of)?\s*(?P<src>[A-Za-z0-9_]+)\s*(?:column)?\s*(?:to|into|in)\s*(?P<tgt>[A-Za-z0-9_]+)", p)
    print(' typed match:', bool(m_typed))
    if m_typed:
        print(' groups:', m_typed.groupdict())
    m_all = re.search(r"(?i)(?:move|put|copy)\s+all\s+values\s*(?:in|from|of)?\s*(?P<src>[A-Za-z0-9_]+)\s*(?:column)?\s*(?:to|into|in)\s*(?P<tgt>[A-Za-z0-9_]+)", p)
    print(' all-values match:', bool(m_all))
    if m_all:
        print(' groups:', m_all.groupdict())
    m_drop = re.search(r"(?i)drop\s+(?:column\s+)?(?P<col>[A-Za-z0-9_]+)", p)
    print(' drop match:', bool(m_drop))
    if m_drop:
        print(' groups:', m_drop.groupdict())
