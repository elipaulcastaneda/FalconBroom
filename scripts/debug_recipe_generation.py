from fbroom.workflow_rules import recipe_from_plain_english
from fbroom.engine import Cleaner
from pathlib import Path
text = "Normalize the values of order_date and delivery_date to the ISO format YYYY-MM-DD"
cleaner = Cleaner()
# choose an existing CSV in repo
candidates = [Path('samples/test_datasets/sales_inconsistent_dates.csv'), Path('data/tmp_replace_test.csv'), Path('data/tmp_impute_test.csv')]
src = None
for p in candidates:
    if p.exists():
        src = str(p)
        break
if not src:
    print('No sample CSV found to profile; list dir samples and data for debugging')
else:
    profile = cleaner.profile(src)
    r = recipe_from_plain_english(text, profile, src, 'out.csv')
    print('Generated recipe:')
    try:
        print(r.json())
    except Exception:
        print(str(r))
