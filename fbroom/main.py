from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from .engine import Cleaner
from .recipe_schema import Recipe

app = FastAPI(title="FalconBroom Prototype API")

cleaner = Cleaner()


class SourceSpec(BaseModel):
    path: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/profile")
def profile(spec: SourceSpec):
    try:
        profile = cleaner.profile(spec.path)
        return {"profile": profile}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/suggest")
def suggest(spec: SourceSpec):
    try:
        profile = cleaner.profile(spec.path)
        suggestions = cleaner.suggest_fixes(profile)
        return {"suggestions": suggestions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/apply")
def apply_recipe(recipe: Recipe):
    try:
        out = cleaner.apply_recipe_from_spec(recipe)
        return {"result": out}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/preview")
def preview_recipe(recipe: Recipe):
    try:
        out = cleaner.preview_recipe(recipe)
        return {"preview": out}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
