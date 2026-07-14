from fbroom.recipe_schema import Recipe
from fbroom.engine import Cleaner
import json
source_path = 'data/uploads/addresses_inconsistent_formats_25aa3e1c_14162_39c4.csv'
steps = [
    {"action":"move_by_type","column":None,"params":{"source":"postal_code","target":"city","type":"string","exceptions":[],"replacement":""}},
    {"action":"move_by_type","column":None,"params":{"source":"col_6","target":"postal_code","type":"string","exceptions":[],"replacement":""}},
    {"action":"move_by_type","column":None,"params":{"source":"col_6","target":"postal_code","type":"numeric","exceptions":[],"replacement":""}},
    {"action":"drop_column","column":"col_6","params":{}}
]
recipe = Recipe(sources=[{"path": source_path}], cleaning_steps=steps, outputs=[{"path":"output_from_text.csv"}])
print('MANUAL RECIPE JSON:')
print(json.dumps(recipe.model_dump(), indent=2))
cleaner = Cleaner()
preview = cleaner.preview_recipe(recipe, n=20)
print('\nPREVIEW:')
print(json.dumps(preview, indent=2))
