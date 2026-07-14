import sys
import os
import json
import re

# ensure workspace root is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fbroom.workflow_rules import recipe_from_plain_english, infer_action, infer_columns_from_text
from fbroom.engine import Cleaner

import sys

text = "Make sure the first letter in any given entry of username are uppercase and the rest of the entry lowercase"
src = sys.argv[1] if len(sys.argv) > 1 else r"data/uploads/combined_whitespace_case_77604fa5_9712_7adc.csv"

print('USING_SOURCE:', src)

c = Cleaner()
profile = c.profile(src)

print("PROFILE_COLUMNS:")
for k in profile.keys():
    print(" -", k)

# print actual CSV header
try:
    import csv
    with open(src, newline='') as fh:
        rdr = csv.reader(fh)
        hdr = next(rdr, None)
        print('\nCSV_HEADER:', hdr)
except Exception as e:
    print('\nCSV_HEADER: error reading header -', e)

action = infer_action(text)
print("\nINFERRED_ACTION:", action)

mentioned = []
for col in profile.keys():
    try:
        if re.search(rf"\\b{re.escape(col)}\\b", text, flags=re.IGNORECASE):
            mentioned.append(col)
    except Exception:
        pass
print("\nMENTIONED_EXACT:", mentioned)

cands = infer_columns_from_text(text, profile, top_n=5)
print("\nCANDIDATES_FROM_TEXT:")
print(json.dumps(cands, indent=2))

cands_username = infer_columns_from_text('username', profile, top_n=5)
print("\nCANDIDATES_FOR_'username':")
print(json.dumps(cands_username, indent=2))

r = recipe_from_plain_english(text, profile, src, "output_from_text.csv")
print("\nGENERATED_RECIPE:")
print(json.dumps(r.dict(), indent=2))

pv = c.preview_recipe(r, n=5)
print("\nSTEPS:", [(s.action, s.column) for s in r.cleaning_steps])
print('\nPREVIEW BEFORE:')
print(json.dumps(pv.get('before'), indent=2, ensure_ascii=False))
print('\nPREVIEW AFTER:')
print(json.dumps(pv.get('after'), indent=2, ensure_ascii=False))
print('\nWARNINGS:')
print(json.dumps(pv.get('warnings', []), indent=2))
