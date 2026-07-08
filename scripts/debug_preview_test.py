import json, sys, os
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from fbroom.workflow_rules import recipe_from_plain_english
from fbroom.engine import Cleaner

cleaner = Cleaner()
profile = cleaner.profile('samples/test_datasets/sales_inconsistent_dates.csv')
print('Profile keys:', list(profile.keys()))
r = recipe_from_plain_english('Normalize the values of order_date to the ISO format YYYY-MM-DD', profile, 'samples/test_datasets/sales_inconsistent_dates.csv', 'data/outputs/test_out.csv')
print('Recipe:', r.model_dump())
preview = cleaner.preview_recipe(r, n=10)
print('Preview before:\n', json.dumps(preview.get('before'), indent=2, ensure_ascii=False))
print('Preview after:\n', json.dumps(preview.get('after'), indent=2, ensure_ascii=False))
print('Warnings:', json.dumps(preview.get('warnings'), indent=2))
