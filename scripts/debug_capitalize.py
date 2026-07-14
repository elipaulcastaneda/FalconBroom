import sys,json
sys.path.insert(0, r"C:\Users\Elijah\FalconBroom")
from fbroom.workflow_rules import recipe_from_plain_english
from fbroom.engine import Cleaner, _read_table, _string_transform_column
import logging
logging.basicConfig(level=logging.DEBUG)

inst='capitalize username'
source='data/uploads/combined_whitespace_case_b7e41079.csv'
cleaner=Cleaner()
profile=cleaner.profile(source)
recipe=recipe_from_plain_english(inst, profile, source,'out.csv')
try:
    rj = recipe.model_dump() if hasattr(recipe,'model_dump') else recipe.dict()
    print('RECIPE:', json.dumps(rj, indent=2))
except Exception:
    print('RECIPE DUMP ERROR')

pv = cleaner.preview_recipe(recipe, n=10)
print('PREVIEW:', json.dumps(pv, indent=2, default=str))

raw = _read_table(source)
print('RAW COLUMNS:', raw.columns)
after = _string_transform_column(raw, 'username', case='capitalize')
# print head
try:
    print('AFTER HEAD:', after.head(10).to_dicts())
except Exception:
    try:
        print('AFTER HEAD pandas:', after.head(10).to_pandas().to_dict('records'))
    except Exception as e:
        print('AFTER HEAD ERR', e)
