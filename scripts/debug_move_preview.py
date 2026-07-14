import sys
from pathlib import Path
# ensure repo root is on sys.path for local imports
ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from fbroom.workflow_rules import recipe_from_plain_english, explain_recipe
from fbroom.engine import Cleaner
from pathlib import Path
import json
texts = [
    "Put all non-numerical values in the postal_code and col_6 columns in the city column",
    "Put all non-numerical values in the postal_code and col_6 columns into the city column",
    "Put all string values in the postal_code and col_6 columns into the city column",
]
cleaner = Cleaner()
# sample path
src = Path('samples/test_datasets/addresses_inconsistent_formats.csv')
if not src.exists():
    print('Sample CSV not found:', src)
else:
    profile = cleaner.profile(str(src))
    print('Profile columns:', list(profile.keys()))
    for text in texts:
        print('\n=== TEXT:', text)
        r = recipe_from_plain_english(text, profile, str(src), 'out.csv')
        print('\nGenerated recipe JSON:\n')
        try:
            print(r.json(indent=2))
        except Exception:
            print(str(r))
        print('\nExplanation:')
        try:
            expl = explain_recipe(r, profile)
            for e in expl:
                print('-', e.reason, json.dumps(e.step.model_dump(), ensure_ascii=False))
        except Exception as e:
            print('explain_recipe error:', e)
        print('\nRunning preview...')
        preview = cleaner.preview_recipe(r, n=3)
        print('\nPreview warnings:')
        print(json.dumps(preview.get('warnings'), indent=2, ensure_ascii=False))
        print('\nPreview after rows:')
        print(json.dumps(preview.get('after'), indent=2, ensure_ascii=False))

    # --- manual recipe: move non-numeric (string) values from postal_code into city ---
    print('\n=== MANUAL RECIPE: move_by_type postal_code -> city')
    from fbroom.recipe_schema import Recipe, CleaningStep
    manual_steps = [
        CleaningStep(action='move_by_type', column=None, params={'source': 'postal_code', 'target': 'city', 'type': 'string', 'exceptions': [], 'replacement': ''}),
    ]
    r2 = Recipe(sources=[{'path': str(src)}], cleaning_steps=manual_steps, outputs=[{'path': 'out_manual.csv'}])
    print('\nManual recipe JSON:\n')
    try:
        print(r2.json(indent=2))
    except Exception:
        print(str(r2))
    print('\nPreview for manual recipe:')
    preview2 = cleaner.preview_recipe(r2, n=5)
    print('Warnings:', json.dumps(preview2.get('warnings'), indent=2, ensure_ascii=False))
    print('After rows:', json.dumps(preview2.get('after'), indent=2, ensure_ascii=False))
