import sys, json
sys.path.insert(0, r"C:\Users\Elijah\FalconBroom")
from fbroom.workflow_rules import recipe_from_plain_english
from fbroom.engine import Cleaner, _read_table, _string_transform_column
import logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s %(name)s: %(message)s')

inst = 'capitalize username'
source = 'data/uploads/combined_whitespace_case_b7e41079.csv'
cleaner = Cleaner()
profile = cleaner.profile(source)
recipe = recipe_from_plain_english(inst, profile, source, 'out.csv')

try:
    rj = recipe.model_dump() if hasattr(recipe, 'model_dump') else (recipe.dict() if hasattr(recipe, 'dict') else recipe)
    print('RECIPE_JSON_STR:')
    print(json.dumps(rj, indent=2, ensure_ascii=False))
except Exception as e:
    print('RECIPE DUMP ERROR', e)

pv = cleaner.preview_recipe(recipe, n=10)
print('\nPREVIEW:')
print(json.dumps(pv, indent=2, default=str))

raw = _read_table(source)
print('\nRAW_COLUMNS:', list(raw.columns) if hasattr(raw, 'columns') else raw.columns)

try:
    after = _string_transform_column(raw, 'username', case='capitalize')
    try:
        print('\nAFTER_HEAD:', after.head(10).to_dicts())
    except Exception:
        try:
            print('\nAFTER_HEAD pandas:', after.head(10).to_pandas().to_dict('records'))
        except Exception as e:
            print('\nAFTER HEAD ERR', e)
except Exception as e:
    print('\nSTRING TRANSFORM ERR', e)
