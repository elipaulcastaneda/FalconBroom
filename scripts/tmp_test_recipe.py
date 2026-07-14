from fbroom.recipe_schema import Recipe, CleaningStep
steps = [CleaningStep(action='move_by_type', column=None, params={'source':'postal_code','target':'city','type':'string','exceptions':[],'replacement':''})]
rec = Recipe(sources=[{'path':'x'}], cleaning_steps=steps, outputs=[{'path':'out'}])
print('MODEL DUMP:', rec.model_dump())
rec2 = Recipe(sources=[{'path':'x'}], cleaning_steps=[{'action':'move_by_type','column':None,'params':{'source':'postal_code','target':'city','type':'string'}}], outputs=[{'path':'out'}])
print('MODEL DUMP 2:', rec2.model_dump())
