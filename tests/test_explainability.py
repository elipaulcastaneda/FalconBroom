from fbroom.workflow_rules import explain_recipe
from fbroom.engine import Cleaner, _read_table
from pathlib import Path
import json
from uuid import uuid4


def test_recipe_explainability_preview(tmp_path):
    # prepare a small CSV source
    src_dir = Path("data/demo")
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / f"explain_src_{uuid4().hex[:6]}.csv"
    src.write_text("name,age\nAlice,30\nBob,25\nCarlos,40\nDana,35\nEve,28\n")

    # create a recipe that normalizes `name` to lowercase
    rid = f"test_explain_{uuid4().hex[:6]}"
    recipe = {
        "sources": [{"path": str(src)}],
        "cleaning_steps": [{"action": "normalize", "column": "name", "params": {"case": "lower"}}],
        "joins": [],
        "outputs": [{"path": "out.csv"}]
    }
    recipedir = Path("data") / "recipes"
    recipedir.mkdir(parents=True, exist_ok=True)
    dest = recipedir / f"{rid}.json"
    payload = {"id": rid, "name": rid, "created_at": "now", "status": "draft", "expected_columns": ["name","age"], "recipe": recipe}
    dest.write_text(json.dumps(payload), encoding="utf-8")

    # call explain_recipe rule directly
    cleaner = Cleaner()
    profile = cleaner.profile(str(src))
    from fbroom.recipe_schema import Recipe as RecipeModel
    recipe_model = RecipeModel(**recipe)
    explanations = explain_recipe(recipe_model, profile)
    assert isinstance(explanations, list)
    assert len(explanations) == 1
    exp = explanations[0]
    assert exp.step.action == "normalize"
