from fbroom.workflow_rules import recipe_from_plain_english
from fbroom.engine import Cleaner
import json

variants = [
    "Move all non-numerical values from postal_code to city, then move all values from col_6 to postal_code, then drop col_6.",
    "Move non-numerical values from postal_code to city, then move values from col_6 to postal_code, then drop col_6.",
    "Move non-numerical from postal_code to city, move col_6 to postal_code, drop col_6.",
    "Move postal_code to city, then move col_6 to postal_code, then drop col_6.",
    "Move values from col_6 to postal_code and drop col_6.",
    "Move col_6 to postal_code then drop col_6."
]

c = Cleaner()
source = 'data/uploads/addresses_inconsistent_formats_14ebccf8_14162_4a58.csv'
# build profile like backend
try:
    inspection = c.inspect_source(source, offset=0, limit=200)
    cols = inspection.get('columns') or []
    rows = inspection.get('rows') or []
    profile = {}
    if cols and isinstance(cols, list) and len(cols) > 0 and not (set(cols) <= set(c.META_KEYS) or set(cols) == {"text", "style_json", "notes"}):
        for col in cols:
            vals = [r.get(col) for r in rows if isinstance(r, dict)]
            missing = sum(1 for v in vals if v is None or (isinstance(v, str) and v.strip() == ""))
            unique = len(set([v for v in vals if v is not None and v != ""]))
            profile[col] = {"dtype":"str","nulls":missing,"unique":unique}
    else:
        profile = c.profile(source)
except Exception:
    profile = c.profile(source)

for v in variants:
    print('\nINSTR:', v)
    r = recipe_from_plain_english(v, profile, source, 'output_from_text.csv')
    try:
        print(json.dumps(r.model_dump(), indent=2))
    except Exception:
        print(r)
