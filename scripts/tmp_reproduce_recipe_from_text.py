from fbroom.workflow_rules import recipe_from_plain_english
from fbroom.engine import Cleaner
import json

instruction = "Move all non-numerical values from postal_code to city, then move all values from col_6 to postal_code, then drop col_6."
source_path = r"data/uploads/addresses_inconsistent_formats_14ebccf8_14162_4a58.csv"

c = Cleaner()
# replicate the backend inspection->profile logic
try:
    inspection = c.inspect_source(source_path, offset=0, limit=200)
    cols = inspection.get('columns') or []
    rows = inspection.get('rows') or []
    profile = {}
    if cols and isinstance(cols, list) and len(cols) > 0 and not (set(cols) <= set(c.META_KEYS) or set(cols) == {"text", "style_json", "notes"}):
        for col in cols:
            vals = [r.get(col) for r in rows if isinstance(r, dict)]
            total = len(vals)
            missing = sum(1 for v in vals if v is None or (isinstance(v, str) and v.strip() == ""))
            unique = len(set([v for v in vals if v is not None and v != ""]))
            dtype = "str"
            profile[col] = {"dtype": dtype, "nulls": missing, "unique": unique}
    else:
        profile = c.profile(source_path)
except Exception:
    profile = c.profile(source_path)

recipe = recipe_from_plain_english(instruction, profile, source_path, 'output_from_text.csv')
print('TYPE:', type(recipe))
try:
    print(json.dumps(recipe.model_dump(), indent=2))
except Exception:
    try:
        print(json.dumps(recipe, indent=2))
    except Exception:
        print(recipe)
