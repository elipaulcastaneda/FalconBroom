import tempfile
import os
import json
import numpy as np
from fbroom.engine import Cleaner
from fbroom.recipe_schema import Recipe, CleaningStep


def write_csv(path, rows, header):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(','.join(header) + '\n')
        for r in rows:
            f.write(','.join(str(x) for x in r) + '\n')


def test_regression_impute():
    # Create a small dataset where AGE = 0.5*HEIGHT + 0.3*WEIGHT + 2
    header = ['ID', 'HEIGHT', 'WEIGHT', 'AGE']
    rows = []
    rng = np.random.RandomState(0)
    for i in range(50):
        h = 150 + rng.randn() * 10
        w = 60 + rng.randn() * 8
        age = 0.5 * h + 0.3 * w + 2 + rng.randn() * 0.1
        rows.append([i, round(h, 3), round(w, 3), round(age, 3)])
    # Introduce missing AGE values
    for i in range(5):
        rows[i][3] = ''

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, 'reg.csv')
        write_csv(path, rows, header)
        cleaner = Cleaner()
        profile = cleaner.profile(path)
        # Build a recipe that requests regression imputation via direct spec
        recipe = Recipe(sources=[{"path": path}], cleaning_steps=[CleaningStep(action='impute', column='AGE', params={'strategy': 'regression', 'sources': ['HEIGHT', 'WEIGHT']})], outputs=[{"path": 'out.csv'}])
        out = cleaner.preview_recipe(recipe, n=60)
        # preview returns dict with 'before' and 'after'
        assert out is not None
        after = out.get('after')
        assert after is not None
        # Check that previously-missing AGE (first 5 rows) are now numeric/non-empty
        missing_filled = 0
        for r in after:
            # robustly find ID key regardless of case
            id_key = next((k for k in (r.keys() or []) if k.lower() == 'id'), None)
            if id_key is None:
                continue
            idx = int(r.get(id_key))
            if idx < 5:
                # find age key
                age_key = next((k for k in (r.keys() or []) if k.lower() == 'age'), None)
                v = r.get(age_key) if age_key else None
                if v is not None and str(v).strip() != '':
                    missing_filled += 1
        assert missing_filled >= 4, f"Expected most missing ages filled, got {missing_filled}"
