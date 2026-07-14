from fbroom.workflow_rules import recipe_from_plain_english
from fbroom.engine import Cleaner
import json
profile={}
text = "Move all non-numerical values from postal_code to city, then move all values from col_6 to postal_code, then drop col_6."
source_path = 'data/uploads/addresses_inconsistent_formats_25aa3e1c_14162_39c4.csv'
recipe = recipe_from_plain_english(text, profile, source_path, 'output_from_text.csv')
print('RECIPE_JSON:')
try:
	# pydantic v2 compatibility
	print(recipe.model_dump())
except Exception:
	print(json.dumps(json.loads(recipe.json()), indent=2))
print('\nRUNNING PREVIEW...')
cleaner = Cleaner()
preview = cleaner.preview_recipe(recipe, n=20)
print(json.dumps(preview, indent=2))
