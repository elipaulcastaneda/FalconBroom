from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from .connectors import resolve_source
from .engine import Cleaner
from .ingest import convert_uploaded_file
from .recipe_schema import Recipe
from .workflow_rules import (
    explain_recipe,
    infer_columns_from_text,
    infer_action,
    recipe_from_plain_english,
    suggest_columns_to_clean,
    suggest_join_rules,
)

app = FastAPI(title="FalconBroom Prototype API")

# Allow the Vite dev server and Tauri webview during local development.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?)$|^(tauri://localhost)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

cleaner = Cleaner()
UPLOAD_DIR = Path("data") / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class SourceSpec(BaseModel):
    path: str


class SourceInspectSpec(BaseModel):
    path: str
    offset: int = 0
    limit: int = 100


class TextRecipeSpec(BaseModel):
    instruction: str
    source_path: str
    output_path: str = "output.csv"


class JoinSuggestionSpec(BaseModel):
    left_path: str
    right_path: str


class SourceResolveSpec(BaseModel):
    path: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    try:
        filename = file.filename or "upload"
        source_path = Path(filename)
        suffix = source_path.suffix.lower() or ".bin"
        stem = source_path.stem.strip() or "upload"
        safe_stem = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in stem)
        target = UPLOAD_DIR / f"{safe_stem}_{uuid4().hex[:8]}{suffix}"

        contents = await file.read()
        if not contents:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        target.write_bytes(contents)
        conversion = convert_uploaded_file(target, UPLOAD_DIR)
        return {
            "path": conversion["path"],
            "normalized_path": conversion["normalized_path"],
            "source_path": conversion["source_path"],
            "source_kind": conversion["source_kind"],
            "name": file.filename,
            "size": len(contents),
            "row_count": conversion["row_count"],
            "warnings": conversion["warnings"],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/profile")
def profile(spec: SourceSpec):
    try:
        profile = cleaner.profile(spec.path)
        return {"profile": profile}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/inspect")
def inspect(spec: SourceInspectSpec):
    try:
        inspection = cleaner.inspect_source(spec.path, offset=spec.offset, limit=spec.limit)
        return {"inspection": inspection}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/resolve-source")
def resolve_source_endpoint(spec: SourceResolveSpec):
    try:
        resolved = resolve_source(spec.path)
        return {"source": resolved.to_dict()}
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


@app.post("/recipe-from-text")
def recipe_from_text(spec: TextRecipeSpec):
    try:
        profile = cleaner.profile(spec.source_path)
        recipe = recipe_from_plain_english(spec.instruction, profile, spec.source_path, spec.output_path)
        explanations = explain_recipe(recipe, profile)
        return {
            "instruction": spec.instruction,
            "action": infer_action(spec.instruction),
            "column_candidates": infer_columns_from_text(spec.instruction, profile),
            "recipe": recipe.model_dump() if hasattr(recipe, "model_dump") else recipe.dict(),
            "explanations": [
                {
                    "step": exp.step.model_dump() if hasattr(exp.step, "model_dump") else exp.step.dict(),
                    "reason": exp.reason,
                    "confidence": exp.confidence,
                }
                for exp in explanations
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/cleaning-suggestions")
def cleaning_suggestions(spec: SourceSpec):
    try:
        profile = cleaner.profile(spec.path)
        return {
            "suggestions": [
                {"column": col, "score": score, "reason": reason}
                for col, score, reason in suggest_columns_to_clean(profile)
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/join-suggestions")
def join_suggestions(spec: JoinSuggestionSpec):
    try:
        left_profile = cleaner.profile(spec.left_path)
        right_profile = cleaner.profile(spec.right_path)
        joins = suggest_join_rules(left_profile, right_profile, left_name=spec.left_path, right_name=spec.right_path)
        return {
            "joins": [join.model_dump() if hasattr(join, "model_dump") else join.dict() for join in joins]
        }
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
