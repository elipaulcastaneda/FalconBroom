import tempfile, os, json
import numpy as np
from fbroom.engine import Cleaner
from fbroom.recipe_schema import Recipe, CleaningStep

def write_csv(path, rows, header):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(','.join(header) + '\n')
        for r in rows:
            f.write(','.join(str(x) for x in r) + '\n')

header = ['ID','HEIGHT','WEIGHT','AGE']
rows = []
rng = np.random.RandomState(0)
for i in range(50):
    h = 150 + rng.randn() * 10
    w = 60 + rng.randn() * 8
    age = 0.5 * h + 0.3 * w + 2 + rng.randn() * 0.1
    rows.append([i, round(h,3), round(w,3), round(age,3)])
for i in range(5): rows[i][3] = ''

with tempfile.TemporaryDirectory() as td:
    path = os.path.join(td, 'reg.csv')
    write_csv(path, rows, header)
    cleaner = Cleaner()
    recipe = Recipe(sources=[{"path": path}], cleaning_steps=[CleaningStep(action='impute', column='AGE', params={'strategy':'regression','sources':['HEIGHT','WEIGHT']})], outputs=[{"path":'out.csv'}])
    out = cleaner.preview_recipe(recipe, n=10)
    print('OUT:', json.dumps(out, indent=2))
