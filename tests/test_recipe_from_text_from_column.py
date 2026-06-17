from fbroom.workflow_rules import recipe_from_plain_english


def test_recipe_from_text_from_column():
    text = "Impute missing values in the observation_date column by inputting the values of the date column"
    profile = {
        "observation_date": {"dtype": "str", "nulls": 10, "unique": 5},
        "date": {"dtype": "str", "nulls": 0, "unique": 100},
    }
    recipe = recipe_from_plain_english(text, profile, source_path="/tmp/source.csv", output_path="/tmp/out.csv")
    # expect an impute step using from_column source
    assert recipe.cleaning_steps, "No cleaning steps produced"
    found = False
    for step in recipe.cleaning_steps:
        if step.action == "impute":
            params = step.params or {}
            if params.get("strategy") == "from_column" and params.get("source") == "date":
                # ensure target column matches observation_date
                assert step.column == "observation_date"
                found = True
                break
    assert found, f"Expected from_column impute step not found in {recipe.cleaning_steps}"
