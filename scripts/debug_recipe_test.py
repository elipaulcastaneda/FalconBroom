import json
import sys
import os
# ensure project root is on sys.path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fbroom.workflow_rules import recipe_from_plain_english

profile = {
    'order_date': {'dtype': 'object', 'nulls': 0, 'unique': 100},
    'order_id': {'dtype': 'object', 'nulls': 0, 'unique': 200},
    'delivery_date': {'dtype': 'object', 'nulls': 0, 'unique': 50},
}

r = recipe_from_plain_english('Normalize order_date to ISO format YYYY-MM-DD', profile, 'samples/test_datasets/sales_inconsistent_dates.csv', 'data/outputs/test_out.csv')
print(json.dumps(r.dict(), indent=2))
