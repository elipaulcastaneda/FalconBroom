from fbroom.workflow_rules import infer_action
from fbroom.recipe_schema import Recipe
text = "Move all non-numerical values from postal_code to city, then move all values from col_6 to postal_code, then drop col_6."
res = infer_action(text)
print('TYPE:', type(res))
try:
    print('IS RECIPE INSTANCE:', isinstance(res, Recipe))
    print('MODEL DUMP:')
    print(res.model_dump())
except Exception as e:
    print('EXC WHEN DUMP:', e)
    print(res)
