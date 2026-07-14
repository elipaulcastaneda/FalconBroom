import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fbroom.workflow_rules import recipe_from_plain_english
from fbroom.engine import Cleaner
import json
import csv

# create test CSV without pandas
rows = [
    {'id':1,'amount': -10.5, 'currency':'USD'},
    {'id':2,'amount': 5.25, 'currency':'USD'},
    {'id':3,'amount': -3.0, 'currency':'EUR'},
]
path = 'data/tmp_numeric_test.csv'
with open(path, 'w', newline='', encoding='utf8') as fh:
    writer = csv.DictWriter(fh, fieldnames=['id','amount','currency'])
    writer.writeheader()
    for r in rows:
        writer.writerow(r)

text = "Make all the negative number values in the amount column positive"
profile = {
    'id':{'dtype':'int'},
    'amount':{'dtype':'float'},
    'currency':{'dtype':'str'}
}
recipe = recipe_from_plain_english(text, profile, path, 'out.csv')
print('Generated recipe:')
print(json.dumps(recipe.dict(), indent=2, default=str))
cleaner = Cleaner()
preview = cleaner.preview_recipe(recipe, n=10)
print('Preview before:')
print(preview['before'])
print('Preview after:')
print(preview['after'])
