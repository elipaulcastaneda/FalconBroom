import json
import traceback
import sys
import os
# ensure repo root is on sys.path so local package `fbroom` can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from fbroom.engine import Cleaner
from fbroom.workflow_rules import recipe_from_plain_english, infer_action

path = 'samples/test_data/badger_sightings.csv'

cleaner = Cleaner()

out = {}
try:
    profile = cleaner.profile(path)
    out['profile_keys'] = list(profile.keys())[:10]
    inspection = cleaner.inspect_source(path, offset=0, limit=200)
    out['sample_rows_count'] = len(inspection.get('rows', [])) if isinstance(inspection, dict) else 0
    suggestions = cleaner.suggest_fixes(profile, sample_rows=inspection.get('rows') if isinstance(inspection, dict) else None, path=path)
    out['suggestions'] = suggestions
except Exception as e:
    out['error_profile_suggest'] = str(e)
    out['error_profile_suggest_tb'] = traceback.format_exc()

# Test recipe generation from plain english
instructions = [
    "Move zip codes from the address column to zipcode column",
    "Coerce construction_year to integer and impute missing with median",
    "Replace 'N/A' with null in zipcode",
]

recipes = []
previews = []
for instr in instructions:
    try:
        rec = recipe_from_plain_english(instr, profile, path, output_path=None)
        try:
            # try to get dict representation
            try:
                rd = rec.model_dump()
            except Exception:
                try:
                    rd = rec.dict()
                except Exception:
                    rd = json.loads(json.dumps(rec, default=str))
        except Exception:
            rd = str(rec)
        recipes.append({'instruction': instr, 'recipe': rd})
        # attempt preview using dict + source_path fallback
        try:
            pv = cleaner.preview_recipe(rd, n=5, source_path=path)
            previews.append({'instruction': instr, 'preview': pv})
        except Exception as e:
            previews.append({'instruction': instr, 'preview_error': str(e)})
    except Exception as e:
        recipes.append({'instruction': instr, 'error': str(e), 'tb': traceback.format_exc()})

out['recipes'] = recipes
out['previews'] = previews
print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
