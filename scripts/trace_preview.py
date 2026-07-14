import sys
import json
sys.path.insert(0, r"C:\Users\Elijah\FalconBroom")
from fbroom.workflow_rules import recipe_from_plain_english
from fbroom.engine import Cleaner

source = "data/uploads/combined_whitespace_case_b7e41079.csv"
cleaner = Cleaner()
profile = cleaner.profile(source)

instr1 = "Normalize the username column"
recipe1 = recipe_from_plain_english(instr1, profile, source, "out.csv")
print('\n=== Recipe for normalize instr ===')
try:
    r1 = recipe1.model_dump() if hasattr(recipe1, 'model_dump') else recipe1.dict()
    print(json.dumps(r1, indent=2))
except Exception:
    print(str(recipe1))

preview1 = cleaner.preview_recipe(recipe1, n=5)
print('\n--- Preview (before/after) for normalize:')
print(json.dumps(preview1, indent=2, default=str))

instr2 = "Put all numerical values of the host_name column into the host_since column"
recipe2 = recipe_from_plain_english(instr2, profile, source, "out.csv")
print('\n=== Recipe for move instr ===')
try:
    r2 = recipe2.model_dump() if hasattr(recipe2, 'model_dump') else recipe2.dict()
    print(json.dumps(r2, indent=2))
except Exception:
    print(str(recipe2))

preview2 = cleaner.preview_recipe(recipe2, n=5)
print('\n--- Preview (before/after) for move:')
print(json.dumps(preview2, indent=2, default=str))
