from fbroom.workflow_rules import recipe_from_plain_english
import json
profile={}
text = "Move all non-numerical values from postal_code to city, then move all values from col_6 to postal_code, then drop col_6."
rec = recipe_from_plain_english(text, profile, 'data/uploads/addresses_inconsistent_formats_25aa3e1c_14162_39c4.csv', 'out.csv')
print(json.dumps(json.loads(rec.json()), indent=2))
