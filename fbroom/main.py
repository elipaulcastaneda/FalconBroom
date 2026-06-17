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
from .engine import Cleaner, _read_table
import csv
import shutil
import hashlib
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
import traceback

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
    regression_model: Optional[str] = None
    regression_features: Optional[list] = None
    regression_group_by: Optional[str] = None
    treat_as_missing: Optional[list] = None


class JoinSuggestionSpec(BaseModel):
    left_path: str
    right_path: str


class SourceResolveSpec(BaseModel):
    path: str


class PatchSpec(BaseModel):
    path: str
    patches: list


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
        # Build a profile from the materialized source. If the ingestion
        # produced long-form rows (unit_kind/text), prefer a reconstructed
        # table profile so the parser sees the wide table columns rather than
        # the low-level extraction columns like `text`/`notes`.
        try:
            inspection = cleaner.inspect_source(spec.source_path, offset=0, limit=200)
            cols = inspection.get("columns") or []
            rows = inspection.get("rows") or []
            if cols and isinstance(cols, list) and len(cols) > 0 and not (set(cols) <= set(cleaner.META_KEYS) or set(cols) == {"text", "style_json", "notes"}):
                # build a lightweight profile from the reconstructed rows
                profile = {}
                for c in cols:
                    vals = [r.get(c) for r in rows if isinstance(r, dict)]
                    total = len(vals)
                    missing = sum(1 for v in vals if v is None or (isinstance(v, str) and v.strip() == ""))
                    unique = len(set([v for v in vals if v is not None and v != ""]))
                    dtype = "str"
                    profile[c] = {"dtype": dtype, "nulls": missing, "unique": unique}
            else:
                profile = cleaner.profile(spec.source_path)
        except Exception:
            profile = cleaner.profile(spec.source_path)
        # pass optional regression parameters to the parser only when features are provided
        reg_opts = None
        if spec.regression_features and isinstance(spec.regression_features, list) and len(spec.regression_features) > 0:
            reg_opts = {
                "model": spec.regression_model or None,
                "features": spec.regression_features,
                "group_by": spec.regression_group_by or None,
            }
        recipe = recipe_from_plain_english(spec.instruction, profile, spec.source_path, spec.output_path, regression_options=reg_opts)
        explanations = explain_recipe(recipe, profile)
        # ensure parser output does not include metadata-targeting steps
        try:
            recipe_dict = recipe.model_dump() if hasattr(recipe, "model_dump") else recipe.dict()
            def _is_meta_column(col):
                try:
                    if not col or not isinstance(col, str):
                        return False
                    if col in cleaner.META_KEYS:
                        return True
                    if col.startswith("_"):
                        return True
                    return False
                except Exception:
                    return False

            # Filter out any cleaning steps that target metadata or columns not present
            # in the source profile. Use the profile we built above (which may be
            # reconstructed from the long-form inspection) so generated steps that
            # reference reconstructed columns are preserved rather than being
            # dropped by comparing against the low-level extraction profile.
            src_profile = profile
            valid_columns = set(src_profile.keys())

            def _is_valid_step(s):
                if not s or not isinstance(s, dict):
                    return False
                col = s.get("column")
                if _is_meta_column(col):
                    return False
                # allow steps without a column (e.g., joins) to pass
                if not col:
                    return True
                return col in valid_columns

            recipe_dict["cleaning_steps"] = [s for s in recipe_dict.get("cleaning_steps", []) if _is_valid_step(s)]
        except Exception:
            recipe_dict = recipe.model_dump() if hasattr(recipe, "model_dump") else recipe.dict()

        return {
            "instruction": spec.instruction,
            "action": infer_action(spec.instruction),
            "column_candidates": infer_columns_from_text(spec.instruction, profile),
            "recipe": recipe_dict,
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
    data = json.loads(p.read_text(encoding="utf-8"))
    # sanitize loaded recipe: remove any cleaning steps that target metadata columns
    try:
        recipe = data.get("recipe") or {}
        steps = recipe.get("cleaning_steps") or []
        def _is_meta_column(col):
            try:
                if not col or not isinstance(col, str):
                    return False
                if col in cleaner.META_KEYS:
                    return True
                if col.startswith("_"):
                    return True
                return False
            except Exception:
                return False

        filtered = [s for s in steps if not (s and _is_meta_column(s.get("column")))]
        recipe["cleaning_steps"] = filtered
        data["recipe"] = recipe
    except Exception:
        pass
    return data


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
    # ensure recipe is approved before allowing a run
    status = data.get("status")
    if status != "approved":
        raise HTTPException(status_code=403, detail="Recipe must be approved before running")

    # record run (only after approval)
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
        result = cleaner.apply_recipe_from_spec(recipe_obj)
        out_path = result.get("written") if isinstance(result, dict) else None
        diagnostics = result.get("diagnostics") if isinstance(result, dict) else None
        # Ensure output is copied into the canonical outputs directory so the
        # download endpoint (which restricts to outputs) can retrieve it.
        if out_path:
            try:
                out_p = Path(out_path)
                if not out_p.exists():
                    # if runner returned a relative path that wasn't created,
                    # fallback to a canonical filename in outputs
                    out_path = str(OUTPUTS_DIR / f"{rid}_{run_id}.csv")
                else:
                    resolved_out = out_p.resolve()
                    outputs_root = OUTPUTS_DIR.resolve()
                    if not str(resolved_out).startswith(str(outputs_root)):
                        # copy into outputs dir to ensure restricted download can access
                        dest = OUTPUTS_DIR / f"{rid}_{run_id}_{out_p.name}"
                        try:
                            shutil.copyfile(str(resolved_out), str(dest))
                            out_path = str(dest)
                        except Exception:
                            # if copy fails, fall back to writing into outputs via _write_csv
                            try:
                                # attempt to read and rewrite using engine helpers
                                df_for_copy = _read_table(src)
                                _write_csv(df_for_copy, str(OUTPUTS_DIR / f"{rid}_{run_id}.csv"))
                                out_path = str(OUTPUTS_DIR / f"{rid}_{run_id}.csv")
                            except Exception:
                                out_path = str(OUTPUTS_DIR / f"{rid}_{run_id}.csv")
            except Exception:
                out_path = str(OUTPUTS_DIR / f"{rid}_{run_id}.csv")
        else:
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
        if diagnostics:
            run_record["diagnostics"] = diagnostics
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


@app.get("/inspections")
def list_inspections():
    out = []
    for p in INSPECTIONS_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append({"id": data.get("id"), "path": data.get("path"), "created_at": data.get("created_at"), "file": str(p)})
        except Exception:
            continue
    return {"inspections": sorted(out, key=lambda r: r.get("created_at", ""), reverse=True)}


@app.get("/inspections/{insp_id}")
def get_inspection(insp_id: str):
    p = INSPECTIONS_DIR / f"{insp_id}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Inspection not found")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/uploads")
def list_uploads():
    out = []
    for p in UPLOAD_DIR.iterdir():
        try:
            if p.is_file():
                st = p.stat()
                out.append({"name": p.name, "path": str(p), "size": st.st_size, "modified_at": datetime.utcfromtimestamp(st.st_mtime).isoformat() + "Z"})
        except Exception:
            continue
    return {"uploads": sorted(out, key=lambda r: r.get("modified_at", ""), reverse=True)}


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
        # Resolve provided paths first so we can give a clear 404 when files
        # are missing instead of bubbling up a FileNotFoundError as a 500.
        left_res = resolve_source(spec.left_path)
        right_res = resolve_source(spec.right_path)
        if not left_res.exists:
            raise HTTPException(status_code=404, detail=f"Left source not found: {spec.left_path}")
        if not right_res.exists:
            raise HTTPException(status_code=404, detail=f"Right source not found: {spec.right_path}")

        left_profile = cleaner.profile(left_res.materialized_path or left_res.path)
        right_profile = cleaner.profile(right_res.materialized_path or right_res.path)
        joins = suggest_join_rules(left_profile, right_profile, left_name=spec.left_path, right_name=spec.right_path)
        return {
            "joins": [join.model_dump() if hasattr(join, "model_dump") else join.dict() for join in joins]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/apply")
def apply_recipe(recipe: Recipe):
    try:
        # Debug: log incoming recipe payload (truncated) to help diagnose 500s
        try:
            raw = recipe.json() if hasattr(recipe, 'json') else str(recipe)
            print(f"INCOMING /apply payload: {raw[:2000]}")
        except Exception:
            print("INCOMING /apply payload: <unserializable recipe>")
        # validate source exists before attempting to run heavy transforms
        src = recipe.sources[0]["path"] if recipe.sources else None
        if not src:
            raise HTTPException(status_code=400, detail="Recipe missing source path")
        if not Path(src).exists():
            raise HTTPException(status_code=400, detail=f"Source path not found: {src}")
        out = cleaner.apply_recipe_from_spec(recipe)
        return {"result": out}
    except Exception as e:
        traceback.print_exc()
        # Friendly message for common runtime error when polars isn't installed
        if isinstance(e, RuntimeError) and "Polars not installed" in str(e):
            raise HTTPException(status_code=500, detail=str(e))
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/apply-patch")
def apply_patch(spec: PatchSpec):
    try:
        # read source table as list of dicts
        df = _read_table(spec.path)
        rows = df.to_dicts()
        # apply patches: each patch is {row: int, column: str, value: any}
        applied = 0
        for p in spec.patches:
            try:
                r = int(p.get("row", -1))
                col = p.get("column")
                val = p.get("value")
            except Exception:
                continue
            if r < 0 or r >= len(rows):
                continue
            # ensure column exists; if not, create it
            if col not in rows[r]:
                # add column with empty values for previous rows
                for rr in rows:
                    rr.setdefault(col, "")
            rows[r][col] = val
            applied += 1

        out_name = f"patched_{Path(spec.path).stem}_{uuid4().hex[:8]}.csv"
        out_path = OUTPUTS_DIR / out_name
        # determine column order (preserve original df.columns, but include any new columns appended)
        cols = list(df.columns)
        for r in rows:
            for c in r.keys():
                if c not in cols:
                    cols.append(c)

        with open(out_path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(cols)
            for row in rows:
                writer.writerow([row.get(c, "") for c in cols])

        # also copy into uploads for visibility/reuse
        upload_name = out_name
        upload_path = UPLOAD_DIR / upload_name
        shutil.copyfile(out_path, upload_path)

        # gather metadata
        stat = upload_path.stat()
        size = stat.st_size
        modified_at = datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z"

        return {"patched_path": str(out_path.resolve()), "upload_path": str(upload_path.resolve()), "applied": applied, "size": size, "modified_at": modified_at}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/uploads/delete")
def delete_upload(payload: dict):
    try:
        path = payload.get("path")
        if not path:
            raise HTTPException(status_code=400, detail="Missing path")
        p = Path(path)
        resolved = p.resolve()
        if not str(resolved).startswith(str(UPLOAD_DIR.resolve())):
            raise HTTPException(status_code=403, detail="Delete restricted to uploads directory")
        if not resolved.exists():
            raise HTTPException(status_code=404, detail="File not found")
        resolved.unlink()
        return {"deleted": str(resolved)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/uploads/duplicates")
def find_upload_duplicates():
    try:
        groups = {}
        for p in UPLOAD_DIR.iterdir():
            if not p.is_file():
                continue
            try:
                sz = p.stat().st_size
            except Exception:
                sz = None
            groups.setdefault(sz, []).append(p)

        dup_groups = []
        for sz, files in groups.items():
            if len(files) < 2:
                continue
            # compute quick sha256 for same-size files to confirm duplicates
            hashes = {}
            for f in files:
                try:
                    h = hashlib.sha256()
                    with open(f, "rb") as fh:
                        while True:
                            chunk = fh.read(8192)
                            if not chunk:
                                break
                            h.update(chunk)
                    digest = h.hexdigest()
                    hashes.setdefault(digest, []).append(str(f.resolve()))
                except Exception:
                    continue
            for digest, paths in hashes.items():
                if len(paths) > 1:
                    dup_groups.append({"hash": digest, "paths": paths, "size": sz})

        return {"duplicates": dup_groups}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/preview")
def preview_recipe(recipe: Recipe, recipe_id: Optional[str] = None, n: Optional[int] = None):
    try:
        # Debug: log incoming recipe payload (truncated) and recipe_id
        try:
            raw = recipe.json() if hasattr(recipe, 'json') else str(recipe)
            print(f"INCOMING /preview payload (recipe_id={recipe_id}): {raw[:2000]}")
        except Exception:
            print(f"INCOMING /preview payload (recipe_id={recipe_id}): <unserializable recipe>")
        # validate source path early to provide clearer errors to the UI
        src = recipe.sources[0]["path"] if recipe.sources else None
        if not src:
            raise HTTPException(status_code=400, detail="Recipe missing source path")
        if not Path(src).exists():
            raise HTTPException(status_code=400, detail=f"Source path not found: {src}")
        out = cleaner.preview_recipe(recipe, n=(n if n is not None else 5))
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
        traceback.print_exc()
        if isinstance(e, RuntimeError) and "Polars not installed" in str(e):
            raise HTTPException(status_code=500, detail=str(e))
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e))
