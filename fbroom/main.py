from pathlib import Path
from uuid import uuid4
import json
from datetime import datetime
import shutil

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from .connectors import resolve_source
from .engine import Cleaner
from .ingest import convert_uploaded_file
from .ingest import _google_drive_access_token
from .recipe_schema import Recipe
from typing import Optional
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
RECIPES_DIR = Path("data") / "recipes"
RECIPES_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_DIR = Path("data") / "history"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR = Path("data") / "outputs"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
INSPECTIONS_DIR = Path("data") / "inspections"
INSPECTIONS_DIR.mkdir(parents=True, exist_ok=True)


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
        # persist full inspection JSON for auditing and later retrieval
        insp_id = f"inspection_{uuid4().hex[:8]}"
        dest = INSPECTIONS_DIR / f"{insp_id}.json"
        payload = {"id": insp_id, "created_at": datetime.utcnow().isoformat() + "Z", "path": spec.path, "inspection": inspection}
        dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return {"inspection": inspection, "dump_id": insp_id, "dump_path": str(dest)}
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


class SaveRecipeSpec(BaseModel):
    name: str
    recipe: Recipe


@app.post("/recipes")
def save_recipe(spec: SaveRecipeSpec):
    try:
        # capture a snapshot of source columns for schema-drift detection
        src = spec.recipe.sources[0]["path"] if spec.recipe.sources else None
        expected_columns = []
        if src:
            profile = cleaner.profile(src)
            expected_columns = list(profile.keys())

        rid = f"{spec.name}_{uuid4().hex[:8]}".replace(" ", "_")
        dest = RECIPES_DIR / f"{rid}.json"
        payload = {
            "id": rid,
            "name": spec.name,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "status": "draft",
            "expected_columns": expected_columns,
            "recipe": spec.recipe.model_dump() if hasattr(spec.recipe, "model_dump") else spec.recipe.dict(),
        }
        dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"id": rid, "path": str(dest)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/recipes")
def list_recipes():
    out = []
    for p in RECIPES_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append(data)
        except Exception:
            continue
    return {"recipes": out}


@app.get("/recipes/{rid}")
def get_recipe(rid: str):
    p = RECIPES_DIR / f"{rid}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Recipe not found")
    return json.loads(p.read_text(encoding="utf-8"))


@app.post("/recipes/{rid}/approve")
def approve_recipe(rid: str):
    p = RECIPES_DIR / f"{rid}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Recipe not found")
    data = json.loads(p.read_text(encoding="utf-8"))
    data["status"] = "approved"
    data["approved_at"] = datetime.utcnow().isoformat() + "Z"
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"id": rid, "status": "approved"}


@app.post("/recipes/{rid}/run")
def run_recipe(rid: str, export_format: str = "csv"):
    p = RECIPES_DIR / f"{rid}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Recipe not found")
    data = json.loads(p.read_text(encoding="utf-8"))
    recipe = data.get("recipe")
    # record run
    run_id = f"run_{uuid4().hex[:8]}"
    run_record = {
        "id": run_id,
        "recipe_id": rid,
        "started_at": datetime.utcnow().isoformat() + "Z",
        "status": "running",
        "export_format": export_format,
    }
    history_path = HISTORY_DIR / f"{run_id}.json"
    history_path.write_text(json.dumps(run_record, indent=2, ensure_ascii=False), encoding="utf-8")

    # run (apply)
    try:
        # normalize recipe dict back into Recipe model if needed
        recipe_obj = recipe
        if isinstance(recipe, dict):
            try:
                recipe_obj = Recipe.model_validate(recipe)  # pydantic v2
            except Exception:
                try:
                    recipe_obj = Recipe(**recipe)
                except Exception:
                    recipe_obj = recipe
        result = cleaner.apply_recipe_from_spec(recipe_obj)
        out_path = result.get("written") if isinstance(result, dict) else None
        if not out_path:
            out_path = str(OUTPUTS_DIR / f"{rid}_{run_id}.csv")
        # if export requested as xlsx, attempt conversion
        exported = out_path
        if export_format.lower() == "xlsx":
            try:
                from openpyxl import Workbook
                import csv as _csv

                wb = Workbook()
                ws = wb.active
                with open(out_path, newline='', encoding='utf-8') as fh:
                    reader = _csv.reader(fh)
                    for r in reader:
                        ws.append(r)
                xlsx_path = str(Path(out_path).with_suffix('.xlsx'))
                wb.save(xlsx_path)
                exported = xlsx_path
            except Exception as exc:
                # conversion failed; keep CSV but note warning
                run_record.setdefault("warnings", []).append(f"xlsx conversion failed: {exc}")

        # finalize run record
        run_record["status"] = "completed"
        run_record["finished_at"] = datetime.utcnow().isoformat() + "Z"
        run_record["output_path"] = exported
        history_path.write_text(json.dumps(run_record, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"run": run_record}
    except Exception as e:
        run_record["status"] = "failed"
        run_record["error"] = str(e)
        run_record["finished_at"] = datetime.utcnow().isoformat() + "Z"
        history_path.write_text(json.dumps(run_record, indent=2, ensure_ascii=False), encoding="utf-8")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/download")
def download(path: str):
    try:
        if not path:
            raise HTTPException(status_code=400, detail="Missing path")
        p = Path(path)
        resolved = p.resolve() if p.exists() else (Path(path).resolve())
        outputs_root = OUTPUTS_DIR.resolve()
        if not str(resolved).startswith(str(outputs_root)):
            raise HTTPException(status_code=403, detail="Download restricted to outputs directory")
        if not resolved.exists():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(resolved, filename=resolved.name)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/history")
def list_history():
    out = []
    for p in HISTORY_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append(data)
        except Exception:
            continue
    return {"history": sorted(out, key=lambda r: r.get("started_at", ""), reverse=True)}


@app.post("/history/{run_id}/rollback")
def rollback_run(run_id: str):
    p = HISTORY_DIR / f"{run_id}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    record = json.loads(p.read_text(encoding="utf-8"))
    output = record.get("output_path")
    if not output or not Path(output).exists():
        raise HTTPException(status_code=404, detail="Output not available for rollback")
    dest = OUTPUTS_DIR / f"rollback_{run_id}_{Path(output).name}"
    shutil.copyfile(output, dest)
    rollback_record = {
        "rollback_of": run_id,
        "performed_at": datetime.utcnow().isoformat() + "Z",
        "rollback_path": str(dest.resolve()),
    }
    rbpath = HISTORY_DIR / f"rollback_{run_id}.json"
    rbpath.write_text(json.dumps(rollback_record, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"rollback": rollback_record}


@app.post("/recipes/{rid}/export_sheets")
def export_recipe_to_sheets(rid: str, sheet_name: str = "Sheet1"):
    p = RECIPES_DIR / f"{rid}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Recipe not found")

    # find latest completed run for this recipe
    completed = []
    for h in HISTORY_DIR.glob("run_*.json"):
        try:
            data = json.loads(h.read_text(encoding="utf-8"))
            if data.get("recipe_id") == rid and data.get("status") == "completed":
                completed.append(data)
        except Exception:
            continue

    if not completed:
        raise HTTPException(status_code=400, detail="No completed run found for this recipe. Run the recipe first.")

    latest = sorted(completed, key=lambda r: r.get("finished_at", ""), reverse=True)[0]
    output_path = latest.get("output_path")

    export_id = f"export_{uuid4().hex[:8]}"
    token = _google_drive_access_token()
    record = {
        "id": export_id,
        "recipe_id": rid,
        "requested_at": datetime.utcnow().isoformat() + "Z",
        "output_path": output_path,
        "sheet_name": sheet_name,
    }

    if not token:
        record["status"] = "pending_auth"
        out = HISTORY_DIR / f"{export_id}.json"
        out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        raise HTTPException(status_code=501, detail="Google Drive token not configured. Set the env var to enable exports.")

    # Token present: record queued export (scaffold)
    record["status"] = "queued"
    out = HISTORY_DIR / f"{export_id}.json"
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"export_id": export_id, "status": "queued", "output_path": output_path}


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
def preview_recipe(recipe: Recipe, recipe_id: Optional[str] = None):
    try:
        out = cleaner.preview_recipe(recipe)
        # schema drift: compare expected columns from saved recipe (if provided)
        schema_warnings = []
        if recipe_id:
            stored = RECIPES_DIR / f"{recipe_id}.json"
            if stored.exists():
                try:
                    data = json.loads(stored.read_text(encoding="utf-8"))
                    expected = set(data.get("expected_columns", []))
                    # profile current source
                    src = recipe.sources[0]["path"] if recipe.sources else None
                    if src:
                        profile = cleaner.profile(src)
                        current = set(profile.keys())
                        missing = expected - current
                        added = current - expected
                        if missing:
                            schema_warnings.append({"type": "missing_columns", "columns": list(missing)})
                        if added:
                            schema_warnings.append({"type": "new_columns", "columns": list(added)})
                except Exception:
                    pass
        return {"preview": out, "schema_warnings": schema_warnings}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
