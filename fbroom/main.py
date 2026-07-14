from pathlib import Path
from uuid import uuid4
import json
from datetime import datetime, timezone, timedelta
import shutil

from fastapi import FastAPI, File, HTTPException, UploadFile, Request, Response, WebSocket, WebSocketDisconnect
import logging
import os
from celery import Celery
import threading
import time
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from .connectors import resolve_source
from .engine import (
    Cleaner,
    _read_table,
    _suggest_buckets,
    _write_parquet,
    _map_values,
    _string_transform_column,
    _fill_null_column,
    _unicode_normalize_column,
    _regex_replace,
    _cast_column,
)
import re
import csv
import shutil
import hashlib
from .ingest import convert_uploaded_file
from .ingest import _google_drive_access_token
from .recipe_schema import Recipe
from typing import Optional, Any, List
import jwt
import smtplib
from email.message import EmailMessage
from .workflow_rules import (
    explain_recipe,
    infer_columns_from_text,
    infer_action,
    recipe_from_plain_english,
    suggest_columns_to_clean,
    suggest_join_rules,
)
import traceback
try:
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, Counter
except Exception:
    generate_latest = None
    CONTENT_TYPE_LATEST = 'text/plain; version=0.0.4'
    Counter = None
# pydantic models used below

app = FastAPI(title="FalconBroom Prototype API")
app.state.ready = False
# cache last generated recipe per source_path for preview fallback
app.state.generated_recipes_cache = {}

# Central logging configuration
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
LOG_FORMAT = os.environ.get('LOG_FORMAT', '%(asctime)s %(levelname)s %(name)s: %(message)s')
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), format=LOG_FORMAT)
# Ensure uvicorn and FastAPI logs are visible at the configured level
logging.getLogger('uvicorn').setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
logging.getLogger('uvicorn.error').setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
logging.getLogger('uvicorn.access').setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
logger = logging.getLogger(__name__)

# Prometheus counters for per-recipe runs (optional)
if Counter is not None:
    RECIPE_RUNS = Counter('fbroom_recipe_runs_total', 'Total recipe runs by recipe and status', ['recipe_id', 'status'])
else:
    RECIPE_RUNS = None


# -- Sensitive file encryption helpers -------------------------------------------------
def _get_fernet():
    try:
        from cryptography.fernet import Fernet
    except Exception:
        return None
    key = os.environ.get('DATA_ENC_KEY')
    key_file = os.environ.get('DATA_ENC_KEY_FILE')
    if key_file:
        try:
            kf = Path(key_file)
            if kf.exists():
                key = kf.read_text(encoding='utf-8').strip()
        except Exception:
            key = key
    if not key:
        return None
    try:
        return Fernet(key.encode('utf-8'))
    except Exception:
        try:
            # maybe key already bytes/base64
            return Fernet(key)
        except Exception:
            return None


def save_sensitive_bytes(path: Path, data: bytes) -> Path:
    f = _get_fernet()
    if f:
        out = Path(str(path) + '.enc') if not str(path).endswith('.enc') else path
        out.write_bytes(f.encrypt(data))
        try:
            out.chmod(0o600)
        except Exception:
            pass
        return out
    else:
        path.write_bytes(data)
        try:
            path.chmod(0o600)
        except Exception:
            pass
        return path


def save_sensitive_json(path: Path, obj) -> Path:
    b = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    return save_sensitive_bytes(path, b)


def load_sensitive_json(path: Path):
    if not path.exists():
        return None
    f = _get_fernet()
    try:
        # prefer reading bytes for encrypted files
        raw = path.read_bytes()
        if f:
            try:
                raw = f.decrypt(raw)
            except Exception:
                # if decryption fails, fall back to attempting utf-8 decode
                pass
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception:
            return None
    except Exception:
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return None

# -------------------------------------------------------------------------------------

# Application-wide JWT settings (cache a secret so tokens remain verifiable across calls)
JWT_SECRET = os.environ.get("JWT_SECRET") or uuid4().hex
JWT_ALGO = os.environ.get("JWT_ALGO") or "HS256"

# Allow the Vite dev server and Tauri webview during local development.
# Configure CORS: in production use explicit origins via `CORS_ALLOW_ORIGINS` env (comma-separated).
if os.environ.get('ENV') == 'production':
    origins = []
    ao = os.environ.get('CORS_ALLOW_ORIGINS')
    if ao:
        origins = [o.strip() for o in ao.split(',') if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
else:
    # Development: allow localhost and tauri schemes
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?)$|^(tauri://localhost)$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def _secure_headers_middleware(request: Request, call_next):
    # Enforce TLS in production
    if os.environ.get('ENV') == 'production':
        try:
            if request.url.scheme != 'https':
                return Response(status_code=400, content="HTTPS required")
        except Exception:
            pass
    resp = await call_next(request)
    # Standard security headers
    try:
        resp.headers['Strict-Transport-Security'] = 'max-age=63072000; includeSubDomains; preload'
        resp.headers['X-Frame-Options'] = 'DENY'
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        resp.headers['Referrer-Policy'] = 'no-referrer-when-downgrade'
        resp.headers['Permissions-Policy'] = 'interest-cohort=()'
        # Conservative CSP; adjust for your frontend origins if needed
        resp.headers['Content-Security-Policy'] = "default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'"
    except Exception:
        pass
    return resp

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
JOBS_DIR = Path("data") / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
PRIVACY_DIR = Path("data") / "privacy"
PRIVACY_DIR.mkdir(parents=True, exist_ok=True)


def _privacy_file(name: str) -> Path:
    return PRIVACY_DIR / name


def _load_privacy_json(name: str, default=None):
    p = _privacy_file(name)
    if not p.exists():
        return default if default is not None else {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def _save_privacy_json(name: str, data):
    p = _privacy_file(name)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _privacy_audit(event: str, payload: dict):
    rec = {"id": f"rec_{uuid4().hex[:8]}", "event": event, "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00','Z'), "payload": payload}
    logs = _load_privacy_json("privacy_records.json", []) or []
    logs.insert(0, rec)
    # keep most recent 1000
    _save_privacy_json("privacy_records.json", logs[:1000])
    # also write single record file for audit
    try:
        p = PRIVACY_DIR / f"{rec['id']}.json"
        p.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    try:
        _append_audit_event(PRIVACY_DIR, event, payload or {})
    except Exception:
        pass


def _append_audit_event(audit_dir: Path, event: str, payload: dict):
    """Append a tamper-evident audit record using an HMAC chain.
    Stores `audit.log` and a small `audit.prev` file containing the last HMAC.
    Requires `AUDIT_HMAC_KEY` env var to enable HMAC; otherwise falls back to plain append.
    """
    try:
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_file = audit_dir / "audit.log"
        prev_file = audit_dir / "audit.prev"
        now_ts = datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
        entry = {"event": event, "payload": payload or {}, "ts": now_ts}
        # compact deterministic JSON for HMAC
        raw = json.dumps(entry, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
        key = os.environ.get("AUDIT_HMAC_KEY")
        if key:
            try:
                import hmac, hashlib
                prev = prev_file.read_text(encoding='utf-8') if prev_file.exists() else ""
                mac = hmac.new(key.encode('utf-8'), (prev + raw).encode('utf-8'), hashlib.sha256).hexdigest()
                rec = {"data": entry, "hmac": mac}
                with open(audit_file, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                try:
                    prev_file.write_text(mac, encoding='utf-8')
                except Exception:
                    pass
                return True
            except Exception:
                # fall through to plain append
                pass
        # fallback plain append
        try:
            with open(audit_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return True
        except Exception:
            return False
    except Exception:
        return False


def _is_opted_out(user_id: Optional[str] = None, email: Optional[str] = None) -> bool:
    outs = _load_privacy_json("opt_outs.json", []) or []
    for o in outs:
        if user_id and o.get("user_id") and str(o.get("user_id")) == str(user_id):
            return True
        if email and o.get("email") and str(o.get("email")).lower() == str(email).lower():
            return True
    return False


# Connected websockets for broadcasting shared-upload updates
_connected_ws = set()

async def _broadcast_shared_update_message(message: dict):
    import asyncio
    to_remove = []
    text = json.dumps(message, default=str)
    for ws in list(_connected_ws):
        try:
            await ws.send_text(text)
        except Exception:
            try:
                await ws.close()
            except Exception:
                pass
            to_remove.append(ws)
    for ws in to_remove:
        _connected_ws.discard(ws)



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


class JoinPreviewSpec(BaseModel):
    left_path: str
    right_path: str
    left_on: Optional[list] = None
    right_on: Optional[list] = None
    join_type: Optional[str] = "inner"  # inner,left,right,outer,anti
    sample: Optional[int] = 10
    conflict_resolution: Optional[dict] = None


class JoinExportSpec(JoinPreviewSpec):
    export_format: Optional[str] = "csv"  # csv,xlsx,pandas,sql
    filename: Optional[str] = None
    target_user: Optional[str] = None


class SourceResolveSpec(BaseModel):
    path: str


class PatchSpec(BaseModel):
    path: str
    patches: list


class SnapshotCleanupSpec(BaseModel):
    days: int = 30


class SnapshotRollbackSpec(BaseModel):
    snapshot_path: str
    dest_name: Optional[str] = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Readiness endpoint: true when essential startup completed."""
    try:
        return {"ready": bool(getattr(app.state, 'ready', False))}
    except Exception:
        return {"ready": False}


@app.get("/metrics")
def metrics_endpoint():
    """Prometheus metrics endpoint (if prometheus_client installed)."""
    try:
        if generate_latest is None:
            raise HTTPException(status_code=501, detail="Prometheus metrics not available")
        data = generate_latest()
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="metrics error")


@app.get("/monitor")
def monitor_page():
    """Simple monitoring page that displays Prometheus metrics."""
    html = """
    <html><head><title>FalconBroom Monitor</title></head>
    <body>
    <h1>FalconBroom Monitor</h1>
    <div id="metrics">Loading metrics…</div>
    <script>
    async function load(){
      try{
        const r = await fetch('/metrics');
        const t = await r.text();
        document.getElementById('metrics').innerText = t;
      }catch(e){document.getElementById('metrics').innerText = 'Metrics not available: '+e}
    }
    load();
    </script>
    </body></html>
    """
    return Response(content=html, media_type='text/html')


@app.get('/lineage')
def lineage_page():
    """Simple lineage/ history viewer that lists recent run history files."""
    try:
        files = []
        for p in sorted(HISTORY_DIR.glob('run_*.json'), reverse=True)[:50]:
            try:
                data = json.loads(p.read_text(encoding='utf-8'))
            except Exception:
                data = {"id": p.name}
            files.append((p.name, data))
        rows = ''
        for name, data in files:
            rows += f"<li><strong>{name}</strong>: {data.get('status') or ''} - {data.get('started_at') or ''}</li>"
        html = f"<html><body><h1>Lineage / History</h1><ul>{rows}</ul></body></html>"
        return Response(content=html, media_type='text/html')
    except Exception:
        raise HTTPException(status_code=500, detail='lineage error')


@app.get('/admin/metrics')
def admin_metrics_tab(request: Request):
        """Admin-only metrics tab for the desktop app. Requires admin token or admin user."""
        # enforce admin access
        try:
                _require_admin(request)
        except HTTPException:
                raise
        except Exception:
                raise HTTPException(status_code=403, detail='Admin required')

        # serve a simple tab page that fetches /metrics
        html = """
        <html><head><title>Admin Metrics</title></head>
        <body>
        <h1>Admin Metrics</h1>
        <p>This tab is visible only to admins. It fetches the Prometheus metrics endpoint.</p>
        <pre id="metrics">Loading metrics…</pre>
        <script>
        async function load(){
            try{
                const token = window.localStorage.getItem('falconbroom_access_token') || '';
                const headers = token ? {'Authorization': 'Bearer ' + token} : {};
                const r = await fetch('/metrics', {headers: headers, credentials: 'include'});
                const t = await r.text();
                document.getElementById('metrics').innerText = t;
            }catch(e){document.getElementById('metrics').innerText = 'Metrics not available: '+e}
        }
        load();
        </script>
        </body></html>
        """
        return Response(content=html, media_type='text/html')


@app.post("/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
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
        # persist simple metadata (owner if authenticated)
        try:
            owner = None
            try:
                user = _require_auth(request)
                owner = {"user_id": user.get("id"), "username": user.get("username"), "email": user.get("email")}
            except Exception:
                user = None
            meta = Path(conversion["path"]).parent / (Path(conversion["path"]).name + ".meta.json")
            existing = {}
            if meta.exists():
                try:
                    existing = json.loads(meta.read_text(encoding="utf-8"))
                except Exception:
                    existing = {}
            if owner:
                existing["owner"] = owner
            existing.setdefault("uploaded_at", datetime.now(timezone.utc).isoformat().replace('+00:00','Z'))
            meta.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        # notify websocket clients of updated uploads list
        try:
            import asyncio
            asyncio.create_task(_broadcast_shared_update_message({"type": "uploads_changed"}))
        except Exception:
            pass
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
        payload = {"id": insp_id, "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00','Z'), "path": spec.path, "inspection": inspection}
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


@app.post("/debug/recipe_from_text")
def debug_recipe_from_text(spec: TextRecipeSpec):
    """Debug endpoint: return the deterministic recipe generated from plain English and a small preview.

    Useful for verifying the backend's NL->recipe logic from the frontend.
    """
    try:
        profile = cleaner.profile(spec.source_path)
        recipe = recipe_from_plain_english(spec.instruction, profile, spec.source_path, spec.output_path)
        # pydantic model to dict (support v1/v2)
        try:
            recipe_json = recipe.model_dump()
        except Exception:
            try:
                recipe_json = recipe.dict()
            except Exception:
                recipe_json = json.loads(json.dumps(recipe, default=str))

        preview = cleaner.preview_recipe(recipe, n=5)
        # also provide a full serialized JSON string for UI consumption
        try:
            recipe_json_str = json.dumps(recipe_json, indent=2, ensure_ascii=False)
            logger.debug('DEBUG RECIPE JSON: %s', recipe_json_str)
        except Exception:
            recipe_json_str = None
            logger.debug('DEBUG RECIPE JSON (non-serializable)')
        return {"recipe": recipe_json, "recipe_json_str": recipe_json_str, "preview": preview}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Pydantic models for bucket suggestion and parquet write endpoints
class Bucket(BaseModel):
    min: Optional[float] = None
    max: Optional[float] = None
    label: str


class SuggestBucketsResponse(BaseModel):
    buckets: list[Bucket]

    class Config:
        schema_extra = {
            "example": {
                "buckets": [
                    {"min": 1.0, "max": 25.0, "label": "low"},
                    {"min": 25.1, "max": 75.0, "label": "medium"},
                    {"min": 75.1, "max": 100.0, "label": "high"},
                ]
            }
        }


class WriteParquetSpec(BaseModel):
    path: str = Field(..., example="data/uploads/sample.csv")
    out_path: Optional[str] = Field(None, example="reports/output.parquet")
    compression: Optional[str] = Field(None, example="snappy")
    atomic: Optional[bool] = Field(True, example=True)


# Explainability/OpenAPI models
class ExplainedStepPreview(BaseModel):
    column: Optional[str] = None
    before: Optional[List[Any]] = None
    after: Optional[List[Any]] = None
    class Config:
        schema_extra = {
            "examples": {
                "normalize": {"summary": "Normalized text preview", "value": {"column": "name", "before": ["Alice", "Bob"], "after": ["alice", "bob"]}},
                "impute": {"summary": "Imputation preview", "value": {"column": "age", "before": [None, 25], "after": [30, 25]}},
                "map": {"summary": "Mapping preview", "value": {"column": "status", "before": ["A","B"], "after": ["active","blocked"]}},
            }
        }


class ExplainedStepResponse(BaseModel):
    step: dict
    reason: str
    confidence: float
    preview: Optional[ExplainedStepPreview] = None
    class Config:
        schema_extra = {
            "examples": {
                "normalize": {
                    "summary": "Normalize step",
                    "value": {
                        "step": {"action": "normalize", "column": "name", "params": {"case": "lower"}},
                        "reason": "name appears to be a text column, so normalization is safe and deterministic.",
                        "confidence": 0.75,
                        "preview": {"column": "name", "before": ["Alice", "Bob"], "after": ["alice", "bob"]},
                    },
                },
                "impute": {
                    "summary": "Impute step",
                    "value": {
                        "step": {"action": "impute", "column": "age", "params": {"strategy": "median"}},
                        "reason": "age has missing values; using median imputation.",
                        "confidence": 0.8,
                        "preview": {"column": "age", "before": [None, 25], "after": [30, 25]},
                    },
                },
                "map": {
                    "summary": "Map values",
                    "value": {
                        "step": {"action": "map", "column": "status", "params": {"mapping": {"A": "active", "B": "blocked"}}},
                        "reason": "status mapped from short codes to readable labels.",
                        "confidence": 0.85,
                        "preview": {"column": "status", "before": ["A","B"], "after": ["active","blocked"]},
                    },
                },
                "cast": {
                    "summary": "Cast to datetime",
                    "value": {
                        "step": {"action": "cast", "column": "ts", "params": {"to": "datetime", "fmt": "%Y-%m-%d"}},
                        "reason": "attempt to coerce column to datetime",
                        "confidence": 0.6,
                        "preview": {"column": "ts", "before": ["2025-01-01", "2025-02-02"], "after": ["2025-01-01T00:00:00", "2025-02-02T00:00:00"]},
                    },
                },
                "regex_replace": {
                    "summary": "Regex replace",
                    "value": {
                        "step": {"action": "regex_replace", "column": "desc", "params": {"pattern": r"\d+", "replace": "#"}},
                        "reason": "remove numeric tokens from descriptions",
                        "confidence": 0.7,
                        "preview": {"column": "desc", "before": ["item 123","foo 45"], "after": ["item #","foo #"]},
                    },
                },
                "drop_column": {
                    "summary": "Drop column",
                    "value": {
                        "step": {"action": "drop_column", "column": "legacy_id", "params": {}},
                        "reason": "legacy_id is deprecated and will be removed.",
                        "confidence": 0.9,
                        "preview": {"column": "legacy_id", "before": ["x1","x2"], "after": [None, None]},
                    },
                },
            }
        }


class ExplainResponse(BaseModel):
    id: str
    explanations: List[ExplainedStepResponse]
    class Config:
        schema_extra = {
            "examples": {
                "single_normalize": {
                    "summary": "Single normalize explanation",
                    "value": {
                        "id": "test_explain_abcdef",
                        "explanations": [
                            {
                                "step": {"action": "normalize", "column": "name", "params": {"case": "lower"}},
                                "reason": "name appears to be a text column, so normalization is safe and deterministic.",
                                "confidence": 0.75,
                                "preview": {"column": "name", "before": ["Alice", "Bob"], "after": ["alice", "bob"]},
                            }
                        ],
                    },
                },
                "multi_steps": {
                    "summary": "Multiple-step explanation",
                    "value": {
                        "id": "test_explain_multi",
                        "explanations": [
                            {
                                "step": {"action": "impute", "column": "age", "params": {"strategy": "median"}},
                                "reason": "age has missing values; using median imputation.",
                                "confidence": 0.8,
                                "preview": {"column": "age", "before": [None, 25], "after": [30, 25]},
                            },
                            {
                                "step": {"action": "map", "column": "status", "params": {"mapping": {"A": "active"}}},
                                "reason": "status mapped from short codes to readable labels.",
                                "confidence": 0.85,
                                "preview": {"column": "status", "before": ["A"], "after": ["active"]},
                            }
                        ],
                    },
                },
            }
        }


class WriteParquetResponse(BaseModel):
    path: str

    class Config:
        schema_extra = {"example": {"path": "data/outputs/sample_abc123.parquet"}}


class ExplanationsLogSpec(BaseModel):
    source_path: str
    recipe_id: str
    explanations: list


class ConsentSpec(BaseModel):
    user_id: Optional[str] = None
    consents: dict
    user_agent: Optional[str] = None
    ip: Optional[str] = None


def _consent_audit(action: str, payload: dict):
    """Append an audit entry for consent-related operations."""
    try:
        CONSENT_DIR = Path("data") / "consents"
        CONSENT_DIR.mkdir(parents=True, exist_ok=True)
        audit_file = CONSENT_DIR / "audit.log"
        # redact any tokens from payload to avoid leaking secrets in logs
        def _redact(d):
            try:
                if not isinstance(d, dict):
                    return d
                out = {}
                for k, v in d.items():
                    lk = k.lower() if isinstance(k, str) else k
                    if lk in ("refresh_token", "access_token", "token"):
                        out[k] = "<redacted>"
                    else:
                        out[k] = v
                return out
            except Exception:
                return d

        entry = {"action": action, "payload": _redact(payload or {}), "ts": datetime.now(timezone.utc).isoformat().replace('+00:00','Z')}
        try:
            _append_audit_event(CONSENT_DIR, action, _redact(payload or {}))
        except Exception:
            # fallback to plain write
            try:
                with open(audit_file, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception:
                pass
    except Exception:
        pass


# Celery initialization: if CELERY_BROKER_URL is not set, enable eager mode
CELERY_BROKER = os.getenv('CELERY_BROKER_URL')
if CELERY_BROKER:
    celery_app = Celery('fbroom_tasks', broker=CELERY_BROKER)
else:
    # run tasks eagerly (synchronous) when no broker configured — convenient for local dev/tests
    celery_app = Celery('fbroom_tasks', broker='memory://')
    celery_app.conf.task_always_eager = True



@app.get("/suggest-buckets", response_model=SuggestBucketsResponse)
def suggest_buckets(path: str, col: str, strategy: str = "quantile", n_buckets: int = 5):
    """Suggest numeric buckets for a column.

    Query params:
    - `path`: source path (materialized CSV/parquet)
    - `col`: column name to analyze
    - `strategy`: one of `quantile`|`equal`|`kmeans`
    - `n_buckets`: number of buckets to propose

    Returns a JSON object with `buckets` list of `{min,max,label}` entries.

    Example cURL (quantile):

    ```bash
    curl -G 'http://127.0.0.1:3009/suggest-buckets' \
      --data-urlencode 'path=data/demo/customers.csv' \
      --data-urlencode 'col=age' \
      --data-urlencode 'strategy=quantile' \
      --data-urlencode 'n_buckets=4'
    ```

    Example response:
    ```json
    {"buckets": [{"min":1.0,"max":25.0,"label":"b1"}, {"min":26.0,"max":50.0,"label":"b2"}]}
    ```
    """
    try:
        df = _read_table(path)
        buckets = _suggest_buckets(df, col, strategy=strategy, n_buckets=int(n_buckets))
        return {"buckets": buckets}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/write-parquet", response_model=WriteParquetResponse)
def write_parquet(spec: WriteParquetSpec):
    """Write a materialized source to Parquet.

    Body JSON fields (`WriteParquetSpec`):
    - `path` (required): source path to read (CSV/parquet)
    - `out_path` (optional): destination path (relative to `data/outputs` if not absolute)
    - `compression` (optional): snappy|gzip|zstd|brotli
    - `atomic` (optional): whether to write atomically (default true)

    Example cURL:

    ```bash
    curl -X POST 'http://127.0.0.1:3009/write-parquet' \
      -H 'Content-Type: application/json' \
      -d '{"path":"data/demo/customers.csv","out_path":"customers.parquet","compression":"snappy"}'
    ```

    Example response:
    ```json
    {"path": "data/outputs/customers_abc123.parquet"}
    ```
    """
    try:
        df = _read_table(spec.path)
        if not spec.out_path:
            out = OUTPUTS_DIR / f"{Path(spec.path).stem}_{uuid4().hex[:8]}.parquet"
        else:
            out = Path(spec.out_path)
            if not out.is_absolute():
                out = OUTPUTS_DIR / out
        out.parent.mkdir(parents=True, exist_ok=True)
        res = _write_parquet(df, str(out), compression=spec.compression, atomic=bool(spec.atomic))
        if not res:
            raise HTTPException(status_code=500, detail="Failed to write parquet file")
        return {"path": str(out)}
    except HTTPException:
        raise
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

            # attempt to allow steps that reference columns present in a reconstructed
            # wide table (from long-form extraction) even if the lightweight profile
            # built above did not include them.
            recon_columns = set()
            try:
                # try to reconstruct a wide table from the source and read its first row
                df_raw = _read_table(spec.source_path)
                recon_rows = cleaner._reconstruct_table_from_df(df_raw, offset=0, limit=1)
                if recon_rows and isinstance(recon_rows, list) and len(recon_rows) > 0:
                    recon_columns = set(recon_rows[0].keys())
            except Exception:
                recon_columns = set()

            def _is_valid_step(s):
                if not s or not isinstance(s, dict):
                    return False
                col = s.get("column")
                if _is_meta_column(col):
                    return False
                # allow steps without a column (e.g., joins) to pass
                if not col:
                    return True
                # valid if in the lightweight profile, or present in reconstructed preview
                return (col in valid_columns) or (col in recon_columns)

            recipe_dict["cleaning_steps"] = [s for s in recipe_dict.get("cleaning_steps", []) if _is_valid_step(s)]
        except Exception:
            recipe_dict = recipe.model_dump() if hasattr(recipe, "model_dump") else recipe.dict()

        # determine column candidates: prefer explicitly mentioned columns in the instruction
        try:
            tnorm = spec.instruction or ""
            mentioned_cols = []
            for c in (profile.keys() if isinstance(profile, dict) else []):
                try:
                    if re.search(rf"\b{re.escape(c)}\b", tnorm, flags=re.IGNORECASE):
                        mentioned_cols.append(c)
                except Exception:
                    continue
            if mentioned_cols:
                # present as [(col, score, reason), ...] to match infer_columns_from_text output
                column_candidates = [(c, 1.0, "explicitly mentioned in instruction") for c in mentioned_cols]
            else:
                column_candidates = infer_columns_from_text(spec.instruction, profile)
        except Exception:
            column_candidates = infer_columns_from_text(spec.instruction, profile)

        # cache the generated recipe so preview calls can fallback when UI omits steps
        try:
            if spec.source_path:
                # cache the generated recipe dict (store full dict)
                app.state.generated_recipes_cache[spec.source_path] = recipe_dict
                logger.debug('Cached generated recipe for %s', spec.source_path)
        except Exception as e:
            logger.debug('Failed to cache generated recipe: %s', e)

        try:
            recipe_json_str = json.dumps(recipe_dict, indent=2, ensure_ascii=False)
            logger.debug('Generated recipe returned: %s', recipe_json_str)
        except Exception:
            recipe_json_str = None
            logger.debug('Generated recipe returned (non-serializable)')

        return {
            "instruction": spec.instruction,
            "action": infer_action(spec.instruction),
            "column_candidates": column_candidates,
            "recipe": recipe_dict,
            "recipe_json_str": recipe_json_str,
            "explanations": [
                {
                    "step": exp.step.model_dump() if hasattr(exp.step, "model_dump") else exp.step.dict(),
                    "reason": exp.reason,
                    "confidence": exp.confidence,
                }
                for exp in explanations
            ],
            "server": {
                "pid": os.getpid(),
                "time": datetime.now(timezone.utc).isoformat(),
            },
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
            "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
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


def _apply_step_preview(df, step, n=5):
    """Apply a best-effort preview of a single CleaningStep to up to `n` rows.
    Returns dict with `column`, `before`, `after` lists when applicable.
    """
    try:
        col = getattr(step, "column", None) or (step.get("column") if isinstance(step, dict) else None)
    except Exception:
        col = None
    if not col:
        return {"column": None}

    # take a small sample
    try:
        sample = df.head(n)
    except Exception:
        try:
            sample = df[:n]
        except Exception:
            return {"column": col, "before": [], "after": []}

    before_vals = []
    try:
        before_vals = [r.get(col) for r in (sample.to_dicts() if hasattr(sample, 'to_dicts') else list(sample.to_dict('records')))]
    except Exception:
        try:
            before_vals = [r.get(col) for r in sample]
        except Exception:
            before_vals = []

    action = getattr(step, "action", None) or (step.get("action") if isinstance(step, dict) else None)
    params = getattr(step, "params", None) or (step.get("params") if isinstance(step, dict) else {}) or {}

    after_vals = list(before_vals)
    try:
        if action in ("normalize",):
            # prefer unicode normalize when requested
            if params.get("unicode") or params.get("remove_diacritics"):
                df2 = _unicode_normalize_column(sample, col, form=params.get("form", "NFKC"), remove_diacritics=bool(params.get("remove_diacritics", False)))
            else:
                case = params.get("case", "lower")
                df2 = _string_transform_column(sample, col, case=case)
            after_vals = [r.get(col) for r in (df2.to_dicts() if hasattr(df2, 'to_dicts') else list(df2.to_dict('records')))]
        elif action in ("impute", "fill"):
            val = params.get("value") if params.get("value") is not None else ""
            df2 = _fill_null_column(sample, col, val, strategy=params.get("strategy"))
            after_vals = [r.get(col) for r in (df2.to_dicts() if hasattr(df2, 'to_dicts') else list(df2.to_dict('records')))]
        elif action in ("map",):
            mapping = params.get("mapping") or params.get("map") or {}
            if mapping:
                df2 = _map_values(sample, col, mapping)
                after_vals = [r.get(col) for r in (df2.to_dicts() if hasattr(df2, 'to_dicts') else list(df2.to_dict('records')))]
        elif action in ("regex_replace", "replace"):
            pattern = params.get("pattern") or params.get("old")
            repl = params.get("replace") or params.get("new")
            flags = params.get("flags")
            if pattern is not None and repl is not None:
                df2 = _regex_replace(sample, col, pattern, repl, flags=flags)
                after_vals = [r.get(col) for r in (df2.to_dicts() if hasattr(df2, 'to_dicts') else list(df2.to_dict('records')))]
        elif action in ("cast",):
            to_type = params.get("to") or params.get("type")
            if to_type:
                df2, info = _cast_column(sample, col, to_type, fmt=params.get("fmt"), errors=params.get("errors", "coerce"))
                try:
                    after_vals = [r.get(col) for r in (df2.to_dicts() if hasattr(df2, 'to_dicts') else list(df2.to_dict('records')))]
                except Exception:
                    pass
        elif action in ("drop_column",):
            # show removal by returning after as list of None
            after_vals = [None for _ in before_vals]
        else:
            # unknown action: leave after as before
            after_vals = list(before_vals)
    except Exception:
        after_vals = list(before_vals)

    return {"column": col, "before": before_vals, "after": after_vals}




@app.get("/recipes/{rid}/explain", response_model=ExplainResponse)
def explain_recipe_endpoint(rid: str, sample_rows: int = 5):
    """Return step-level explanations and small before/after samples for a saved recipe."""
    p = RECIPES_DIR / f"{rid}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Recipe not found")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        recipe_dict = data.get("recipe") or {}
        # coerce into Recipe model via recipe_schema if available
        from .recipe_schema import Recipe as RecipeModel
        recipe = RecipeModel(**recipe_dict) if isinstance(recipe_dict, dict) else RecipeModel(**recipe_dict)

        # build profile from first source
        src = recipe.sources[0]["path"] if recipe.sources else None
        profile = cleaner.profile(src) if src else {}
        explanations = explain_recipe(recipe, profile)

        # load dataframe sample for previews
        df = _read_table(src) if src else None
        previews = []
        for exp in explanations:
            step = exp.step
            preview = _apply_step_preview(df, step, n=int(sample_rows)) if df is not None else {"column": getattr(step, 'column', None)}
            previews.append({
                "step": step.model_dump() if hasattr(step, 'model_dump') else (step.dict() if hasattr(step, 'dict') else step),
                "reason": exp.reason,
                "confidence": exp.confidence,
                "preview": preview,
            })

        return {"id": rid, "explanations": previews}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/recipes/{rid}/approve")
def approve_recipe(rid: str):
    p = RECIPES_DIR / f"{rid}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Recipe not found")
    data = json.loads(p.read_text(encoding="utf-8"))
    data["status"] = "approved"
    data["approved_at"] = datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"id": rid, "status": "approved"}


@app.post("/recipes/{rid}/run")
def run_recipe(rid: str, request: Request, export_format: str = "csv"):
    p = RECIPES_DIR / f"{rid}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Recipe not found")
    data = json.loads(p.read_text(encoding="utf-8"))
    recipe = data.get("recipe")
    # ensure recipe is approved before allowing a run
    status = data.get("status")
    if status != "approved":
        raise HTTPException(status_code=403, detail="Recipe must be approved before running")

    # schema validation / drift check: compare stored expected_columns (snapshot at save time)
    expected_columns = data.get("expected_columns") or []
    if expected_columns:
        try:
            # find source path from recipe
            src_path = None
            try:
                src_path = recipe.get("sources", [])[0].get("path")
            except Exception:
                src_path = None
            if src_path:
                drift = cleaner.validate_schema(src_path, expected_columns)
                # If critical columns are missing, prevent run
                if not drift.get("ok"):
                    # provide details to caller
                    raise HTTPException(status_code=409, detail={"message": "Schema drift detected", "drift": drift})
                else:
                    # attach drift info (extra columns) as run warning
                    extra = drift.get("extra") or []
                    if extra:
                        # attach a warning to history record later
                        pass
        except HTTPException:
            raise
        except Exception:
            # if validation fails unexpectedly, treat as non-fatal but record warning
            pass

    # record run (only after approval)
    run_id = f"run_{uuid4().hex[:8]}"
    # metrics: per-recipe run started
    try:
        if RECIPE_RUNS is not None:
            RECIPE_RUNS.labels(recipe_id=rid, status='started').inc()
    except Exception:
        pass
    run_record = {
        "id": run_id,
        "recipe_id": rid,
        "started_at": datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
        "status": "running",
        "export_format": export_format,
    }
    # attach owner if request is authenticated
    try:
        if request is not None:
            try:
                user = _require_auth(request)
                run_record["owner"] = {"user_id": user.get("id"), "username": user.get("username"), "email": user.get("email")}
            except Exception:
                pass
    except Exception:
        pass
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
        run_record["finished_at"] = datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
        run_record["output_path"] = exported
        if diagnostics:
            run_record["diagnostics"] = diagnostics
        history_path.write_text(json.dumps(run_record, indent=2, ensure_ascii=False), encoding="utf-8")
        # metrics: per-recipe run completed
        try:
            if RECIPE_RUNS is not None:
                RECIPE_RUNS.labels(recipe_id=rid, status='completed').inc()
        except Exception:
            pass
        return {"run": run_record}
    except Exception as e:
        run_record["status"] = "failed"
        run_record["error"] = str(e)
        run_record["finished_at"] = datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
        history_path.write_text(json.dumps(run_record, indent=2, ensure_ascii=False), encoding="utf-8")
        # metrics: per-recipe run failed
        try:
            if RECIPE_RUNS is not None:
                RECIPE_RUNS.labels(recipe_id=rid, status='failed').inc()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/history/snapshots/cleanup")
def cleanup_snapshots(spec: SnapshotCleanupSpec):
    try:
        res = cleaner.cleanup_snapshots(spec.days)
        return {"cleanup": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/history/snapshots/rollback")
def rollback_snapshot(spec: SnapshotRollbackSpec):
    try:
        snap = Path(spec.snapshot_path)
        # allow both absolute and snapshots dir-relative paths
        snaps_dir = Path("data") / "history" / "snapshots"
        if not snap.exists():
            candidate = snaps_dir / spec.snapshot_path
            if candidate.exists():
                snap = candidate
        if not snap.exists():
            raise HTTPException(status_code=404, detail=f"Snapshot not found: {spec.snapshot_path}")

        # copy into outputs as a rollback artifact
        dest_name = spec.dest_name or f"rollback_snapshot_{uuid4().hex[:8]}_{snap.name}"
        dest_path = OUTPUTS_DIR / dest_name
        shutil.copyfile(str(snap), str(dest_path))

        record = {"rollback_snapshot": str(snap), "performed_at": datetime.now(timezone.utc).isoformat().replace('+00:00','Z'), "rollback_path": str(dest_path)}
        out = HISTORY_DIR / f"rollback_snapshot_{uuid4().hex[:8]}.json"
        out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"rollback": record}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/recipes/{rid}/validate")
def validate_recipe_schema(rid: str):
    p = RECIPES_DIR / f"{rid}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Recipe not found")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        expected = data.get("expected_columns") or []
        recipe = data.get("recipe") or {}
        src = None
        try:
            src = recipe.get("sources", [])[0].get("path")
        except Exception:
            src = None
        if not src:
            raise HTTPException(status_code=400, detail="Recipe has no source path to validate against")
        res = cleaner.validate_schema(src, expected)
        return {"recipe_id": rid, "validation": res}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/download")
def download(path: str):
    try:
        if not path:
            raise HTTPException(status_code=400, detail="Missing path")
        p = Path(path)
        resolved = p.resolve() if p.exists() else (Path(path).resolve())
        outputs_root = OUTPUTS_DIR.resolve()
        uploads_root = UPLOAD_DIR.resolve()
        # allow downloads from outputs or uploads directories only
        ok = False
        try:
            r = str(resolved)
            if r.startswith(str(outputs_root)) or r.startswith(str(uploads_root)):
                ok = True
        except Exception:
            ok = False
        if not ok:
            raise HTTPException(status_code=403, detail="Download restricted to outputs or uploads directory")
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
    files = [p for p in UPLOAD_DIR.iterdir() if p.is_file()]

    # parse conversion metadata files into entries keyed by source base name
    entries = {}
    for p in files:
        try:
            if p.name.endswith('.meta.json'):
                continue
            if p.suffix != '.json':
                continue
            try:
                meta = json.loads(p.read_text(encoding='utf-8'))
            except Exception:
                continue
            source_name = meta.get('source_name') or (Path(meta.get('source_path') or '').name) or p.stem
            base = Path(source_name).stem
            # prefer unique key including base
            key = base
            norm = meta.get('normalized_path') or meta.get('path')
            norm_path = None
            if norm:
                try:
                    cand = Path(norm)
                    if cand.exists():
                        norm_path = cand
                    else:
                        matches = list(UPLOAD_DIR.glob(Path(norm).name + '*'))
                        if matches:
                            norm_path = matches[0]
                except Exception:
                    norm_path = None

            entry = entries.setdefault(key, {'key': key, 'name': source_name, 'related_files': [], 'metadata': [], 'row_count': None, 'warnings': []})
            entry['metadata'].append(str(p.resolve()))
            if meta.get('row_count') is not None:
                entry['row_count'] = meta.get('row_count')
            if meta.get('warnings'):
                entry['warnings'].extend(meta.get('warnings'))
            if norm_path:
                entry['normalized'] = str(norm_path.resolve())
            else:
                entry.setdefault('candidates', []).append(str(p.resolve()))
        except Exception:
            continue

    # associate files (csv, patched, meta.json) to entries by matching base name
    unmatched = []
    for p in files:
        try:
            if p.name.endswith('.meta.json'):
                # attach to an entry if base matches
                base_candidate = p.name[:-len('.meta.json')]
                matched = False
                for key, ent in entries.items():
                    if base_candidate.startswith(ent['key']) or ent['key'] in base_candidate:
                        ent['related_files'].append(str(p.resolve()))
                        matched = True
                        break
                if not matched:
                    unmatched.append(p)
                continue
            if p.suffix == '.json':
                # already processed conversion metadata
                continue
            fname = p.name
            matched = False
            for key, ent in entries.items():
                if fname.startswith(ent['key']) or ent['key'] in fname or fname.startswith('patched_' + ent['key']):
                    ent['related_files'].append(str(p.resolve()))
                    matched = True
                    break
            if not matched:
                unmatched.append(p)
        except Exception:
            continue

    # build output entries from grouped entries
    for key, ent in entries.items():
        try:
            item = {
                'id': ent.get('key'),
                'name': ent.get('name'),
                'path': ent.get('normalized') or (ent['related_files'][0] if ent['related_files'] else (ent.get('candidates', [None])[0] if ent.get('candidates') else None)),
                'metadata_paths': ent.get('metadata', []),
                'related_files': ent.get('related_files', []),
                'row_count': ent.get('row_count'),
                'warnings': ent.get('warnings', []),
            }
            # attach owner/uploadedAt from any .meta.json that matches normalized file
            try:
                for rf in ent.get('related_files', []) + (ent.get('metadata', []) or []):
                    try:
                        rp = Path(rf)
                        extra = rp.parent / (rp.name + '.meta.json')
                        if extra.exists():
                            em = json.loads(extra.read_text(encoding='utf-8'))
                            if em.get('owner'):
                                item['owner'] = em.get('owner')
                            if em.get('uploaded_at'):
                                item['uploadedAt'] = em.get('uploaded_at')
                            if em.get('explanations_history'):
                                item['explanations_history'] = em.get('explanations_history')
                    except Exception:
                        continue
            except Exception:
                pass
            # size/modified based on primary path if present
            try:
                if item.get('path'):
                    pth = Path(item['path'])
                    if pth.exists():
                        st = pth.stat()
                        item['size'] = st.st_size
                        item['modified_at'] = datetime.utcfromtimestamp(st.st_mtime).isoformat() + 'Z'
                else:
                    # fallback to first related file
                    if item.get('related_files') and len(item['related_files'])>0:
                        pth = Path(item['related_files'][0])
                        if pth.exists():
                            st = pth.stat()
                            item['size'] = st.st_size
                            item['modified_at'] = datetime.utcfromtimestamp(st.st_mtime).isoformat() + 'Z'
            except Exception:
                pass
            out.append(item)
        except Exception:
            continue

    # include unmatched raw files as separate entries
    for p in unmatched:
        try:
            st = p.stat()
            item = {'name': p.name, 'path': str(p.resolve()), 'size': st.st_size, 'modified_at': datetime.utcfromtimestamp(st.st_mtime).isoformat() + 'Z'}
            # attach .meta.json if present
            try:
                meta = p.parent / (p.name + '.meta.json')
                if meta.exists():
                    mj = json.loads(meta.read_text(encoding='utf-8'))
                    if mj.get('explanations_history'):
                        item['explanations_history'] = mj.get('explanations_history')
                    if mj.get('owner'):
                        item['owner'] = mj.get('owner')
                    if mj.get('uploaded_at'):
                        item['uploadedAt'] = mj.get('uploaded_at')
            except Exception:
                pass
            out.append(item)
        except Exception:
            continue

    return {'uploads': sorted(out, key=lambda r: r.get('modified_at', ''), reverse=True)}


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
        "performed_at": datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
        "rollback_path": str(dest.resolve()),
    }
    rbpath = HISTORY_DIR / f"rollback_{run_id}.json"
    rbpath.write_text(json.dumps(rollback_record, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"rollback": rollback_record}


@app.delete("/history/{run_id}")
def delete_history_entry(run_id: str):
    p = HISTORY_DIR / f"{run_id}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    try:
        p.unlink()
        return {"deleted": run_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/history/dedupe")
def dedupe_history(by: str = "output_path"):
    """Remove duplicate history records. By default de-duplicates by `output_path`.

    Returns list of removed run ids.
    """
    try:
        seen = set()
        removed = []
        for p in sorted(HISTORY_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            key = data.get(by)
            # fallback to full record hash when key is missing
            if not key:
                key = hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()
            if key in seen:
                try:
                    removed.append(data.get("id") or p.stem)
                    p.unlink()
                except Exception:
                    continue
            else:
                seen.add(key)
        return {"removed": removed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/recipes/{rid}/export_sheets")
def export_recipe_to_sheets(rid: str, sheet_name: str = "Sheet1", target_user: Optional[str] = None):
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

    def _owner_opt_out_from_run(record):
        try:
            owner = record.get("owner")
            if owner and owner.get("user_id") and _is_opted_out(user_id=owner.get("user_id")):
                return True
        except Exception:
            pass
        return False

    export_id = f"export_{uuid4().hex[:8]}"
    # if explicit target_user provided, block if they opted out
    if target_user and _is_opted_out(user_id=target_user):
        _privacy_audit("blocked_recipe_export_target_opt_out", {"recipe_id": rid, "target_user": target_user})
        raise HTTPException(status_code=403, detail="Target user has opted out of exports")

    token = _google_drive_access_token()
    record = {
        "id": export_id,
        "recipe_id": rid,
        "requested_at": datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
        "output_path": output_path,
        "sheet_name": sheet_name,
    }

    # If the latest completed run's owner has opted out, block this export
    try:
        if latest and _owner_opt_out_from_run(latest):
            _privacy_audit("blocked_recipe_export_owner_opt_out", {"recipe_id": rid, "run": latest.get("id")})
            raise HTTPException(status_code=403, detail="Recipe run owner has opted out of exports")
    except HTTPException:
        raise
    except Exception:
        pass

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


@app.post("/join-preview")
def join_preview(spec: JoinPreviewSpec):
    try:
        left_res = resolve_source(spec.left_path)
        right_res = resolve_source(spec.right_path)
        if not left_res.exists:
            raise HTTPException(status_code=404, detail=f"Left source not found: {spec.left_path}")
        if not right_res.exists:
            raise HTTPException(status_code=404, detail=f"Right source not found: {spec.right_path}")

        left_df = _read_table(left_res.materialized_path or left_res.path)
        right_df = _read_table(right_res.materialized_path or right_res.path)

        # determine join keys
        left_on = spec.left_on
        right_on = spec.right_on
        if (not left_on or not right_on) or (len(left_on) == 0 or len(right_on) == 0):
            # infer common columns if not provided
            lcols = set(left_df.columns)
            rcols = set(right_df.columns)
            common = list(lcols & rcols)
            if not common:
                raise HTTPException(status_code=400, detail="No join keys provided and no common columns found")
            left_on = common[:1]
            right_on = common[:1]

        how = spec.join_type or "inner"
        # map some common aliases
        how_map = {"outer": "outer", "full": "outer", "left": "left", "right": "right", "inner": "inner", "anti": "anti"}
        how = how_map.get(how.lower(), how.lower())

        # handle conflict resolution options: rename maps and suffix/preference
        conf = spec.conflict_resolution or {}
        suffix_left = conf.get('suffix_left', '_left')
        suffix_right = conf.get('suffix_right', '_right')
        prefer = conf.get('prefer', 'left')
        rename_map = conf.get('rename_map') or []

        # apply rename_map to left/right before join
        try:
            for r in rename_map:
                side = (r.get('side') or '').lower()
                frm = r.get('from')
                to = r.get('to')
                if not frm or not to:
                    continue
                if side == 'left':
                    try:
                        left_df = left_df.rename({frm: to})
                    except Exception:
                        try:
                            import pandas as _pd
                            lpd = left_df.to_pandas() if hasattr(left_df, 'to_pandas') else left_df
                            lpd = lpd.rename(columns={frm: to})
                            left_df = pl.from_pandas(lpd)
                        except Exception:
                            pass
                elif side == 'right':
                    try:
                        right_df = right_df.rename({frm: to})
                    except Exception:
                        try:
                            import pandas as _pd
                            rpd = right_df.to_pandas() if hasattr(right_df, 'to_pandas') else right_df
                            rpd = rpd.rename(columns={frm: to})
                            right_df = pl.from_pandas(rpd)
                        except Exception:
                            pass
        except Exception:
            pass

        # perform join (use polars), but first rename conflicting right-side columns
        joined = None
        try:
            # identify conflict columns (exclude join keys)
            lcols = set(left_df.columns)
            rcols = set(right_df.columns)
            join_keys = set((left_on or []) + (right_on or []))
            common = list((lcols & rcols) - join_keys)
            if common:
                # rename right-side conflicting columns with suffix_right
                rename_map_right = {c: f"{c}{suffix_right}" for c in common}
                try:
                    right_df = right_df.rename(rename_map_right)
                except Exception:
                    try:
                        import pandas as _pd
                        rpd = right_df.to_pandas() if hasattr(right_df, 'to_pandas') else right_df
                        rpd = rpd.rename(columns=rename_map_right)
                        right_df = pl.from_pandas(rpd)
                    except Exception:
                        pass

            joined = left_df.join(right_df, left_on=left_on, right_on=right_on, how=how)
        except Exception:
            # try pandas fallback with explicit suffixes
            try:
                import pandas as _pd
                lpd = left_df.to_pandas() if hasattr(left_df, 'to_pandas') else left_df
                rpd = right_df.to_pandas() if hasattr(right_df, 'to_pandas') else right_df
                joined = _pd.merge(lpd, rpd, left_on=left_on, right_on=right_on, how=how, suffixes=(suffix_left, suffix_right))
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Join failed: {e}")

        # compute stats
        try:
            left_count = len(left_df)
        except Exception:
            try:
                left_count = left_df.shape[0]
            except Exception:
                left_count = None
        try:
            right_count = len(right_df)
        except Exception:
            try:
                right_count = right_df.shape[0]
            except Exception:
                right_count = None
        try:
            joined_count = len(joined)
        except Exception:
            try:
                joined_count = joined.shape[0]
            except Exception:
                joined_count = None

        # unmatched samples
        unmatched_left = None
        unmatched_right = None
        try:
            if how in ("inner", "left", "outer"):
                unmatched_left_df = left_df.join(right_df, left_on=left_on, right_on=right_on, how="anti")
                unmatched_left = _df_head_records(unmatched_left_df, n=spec.sample)
        except Exception:
            unmatched_left = []
        try:
            if how in ("inner", "right", "outer"):
                unmatched_right_df = right_df.join(left_df, left_on=right_on, right_on=left_on, how="anti")
                unmatched_right = _df_head_records(unmatched_right_df, n=spec.sample)
        except Exception:
            unmatched_right = []

        # If prefer == 'right', collapse suffixed right columns into base names
        try:
            if prefer == 'right' and common:
                if isinstance(joined, dict) or not hasattr(joined, 'columns'):
                    pass
                else:
                    if hasattr(joined, 'to_pandas'):
                        # polars
                        try:
                            # build expressions to prefer right non-null over left
                            exprs = []
                            for base in common:
                                rname = f"{base}{suffix_right}"
                                if rname in joined.columns and base in joined.columns:
                                    try:
                                        # polars expression
                                        joined = joined.with_columns(
                                            pl.when(pl.col(rname).is_not_null()).then(pl.col(rname)).otherwise(pl.col(base)).alias(base)
                                        )
                                        joined = joined.drop(rname)
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                    else:
                        # pandas-like
                        try:
                            pd = joined
                            import pandas as _pd
                            for base in common:
                                rname = f"{base}{suffix_right}"
                                if rname in pd.columns and base in pd.columns:
                                    pd[base] = pd[rname].combine_first(pd[base])
                                    pd = pd.drop(columns=[rname])
                            joined = pd
                        except Exception:
                            pass
        except Exception:
            pass

        preview_rows = _df_head_records(joined, n=spec.sample)

        return {
            "stats": {"left_count": left_count, "right_count": right_count, "joined_count": joined_count},
            "preview": preview_rows,
            "unmatched_left_sample": unmatched_left,
            "unmatched_right_sample": unmatched_right,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/join-export")
def join_export(spec: JoinExportSpec):
    try:
        # If explicit target_user provided, block if they opted out
        if spec.target_user and _is_opted_out(user_id=spec.target_user):
            _privacy_audit("blocked_join_export_target_opt_out", {"target_user": spec.target_user})
            raise HTTPException(status_code=403, detail="Target user has opted out of exports")

        left_res = resolve_source(spec.left_path)
        right_res = resolve_source(spec.right_path)
        if not left_res.exists:
            raise HTTPException(status_code=404, detail=f"Left source not found: {spec.left_path}")
        if not right_res.exists:
            raise HTTPException(status_code=404, detail=f"Right source not found: {spec.right_path}")

        left_df = _read_table(left_res.materialized_path or left_res.path)
        right_df = _read_table(right_res.materialized_path or right_res.path)

        # If either source has owner metadata and that owner opted out, block
        try:
            for src in [left_res, right_res]:
                try:
                    meta = Path(src.path).parent / (Path(src.path).name + ".meta.json")
                    if meta.exists():
                        mj = json.loads(meta.read_text(encoding="utf-8"))
                        owner = mj.get("owner")
                        if owner and owner.get("user_id") and _is_opted_out(user_id=owner.get("user_id")):
                            _privacy_audit("blocked_join_export_owner_opt_out", {"owner": owner, "source": src.path})
                            raise HTTPException(status_code=403, detail="One of the source owners has opted out of exports")
                except HTTPException:
                    raise
                except Exception:
                    pass
        except HTTPException:
            raise

        left_on = spec.left_on
        right_on = spec.right_on
        if (not left_on or not right_on) or (len(left_on) == 0 or len(right_on) == 0):
            lcols = set(left_df.columns)
            rcols = set(right_df.columns)
            common = list(lcols & rcols)
            if not common:
                raise HTTPException(status_code=400, detail="No join keys provided and no common columns found")
            left_on = common[:1]
            right_on = common[:1]

        how = spec.join_type or "inner"
        how_map = {"outer": "outer", "full": "outer", "left": "left", "right": "right", "inner": "inner", "anti": "anti"}
        how = how_map.get(how.lower(), how.lower())

        # conflict resolution options
        conf = spec.conflict_resolution or {}
        suffix_left = conf.get('suffix_left', '_left')
        suffix_right = conf.get('suffix_right', '_right')
        prefer = conf.get('prefer', 'left')
        rename_map = conf.get('rename_map') or []

        # apply rename_map to left/right before join
        try:
            for r in rename_map:
                side = (r.get('side') or '').lower()
                frm = r.get('from')
                to = r.get('to')
                if not frm or not to:
                    continue
                if side == 'left':
                    try:
                        left_df = left_df.rename({frm: to})
                    except Exception:
                        try:
                            import pandas as _pd
                            lpd = left_df.to_pandas() if hasattr(left_df, 'to_pandas') else left_df
                            lpd = lpd.rename(columns={frm: to})
                            left_df = pl.from_pandas(lpd)
                        except Exception:
                            pass
                elif side == 'right':
                    try:
                        right_df = right_df.rename({frm: to})
                    except Exception:
                        try:
                            import pandas as _pd
                            rpd = right_df.to_pandas() if hasattr(right_df, 'to_pandas') else right_df
                            rpd = rpd.rename(columns={frm: to})
                            right_df = pl.from_pandas(rpd)
                        except Exception:
                            pass
        except Exception:
            pass

        try:
            # identify conflict columns (exclude join keys)
            lcols = set(left_df.columns)
            rcols = set(right_df.columns)
            join_keys = set((left_on or []) + (right_on or []))
            common = list((lcols & rcols) - join_keys)
            if common:
                rename_map_right = {c: f"{c}{suffix_right}" for c in common}
                try:
                    right_df = right_df.rename(rename_map_right)
                except Exception:
                    try:
                        import pandas as _pd
                        rpd = right_df.to_pandas() if hasattr(right_df, 'to_pandas') else right_df
                        rpd = rpd.rename(columns=rename_map_right)
                        right_df = pl.from_pandas(rpd)
                    except Exception:
                        pass

            joined = left_df.join(right_df, left_on=left_on, right_on=right_on, how=how)
        except Exception:
            try:
                import pandas as _pd
                lpd = left_df.to_pandas() if hasattr(left_df, 'to_pandas') else left_df
                rpd = right_df.to_pandas() if hasattr(right_df, 'to_pandas') else right_df
                joined = _pd.merge(lpd, rpd, left_on=left_on, right_on=right_on, how=how, suffixes=(suffix_left, suffix_right))
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Join failed: {e}")

        # apply prefer==right collapse for common columns if requested
        try:
            if prefer == 'right' and common:
                if hasattr(joined, 'to_pandas'):
                    try:
                        for base in common:
                            rname = f"{base}{suffix_right}"
                            if rname in joined.columns and base in joined.columns:
                                try:
                                    joined = joined.with_columns(
                                        pl.when(pl.col(rname).is_not_null()).then(pl.col(rname)).otherwise(pl.col(base)).alias(base)
                                    )
                                    joined = joined.drop(rname)
                                except Exception:
                                    pass
                    except Exception:
                        pass
                else:
                    try:
                        pd = joined
                        for base in common:
                            rname = f"{base}{suffix_right}"
                            if rname in pd.columns and base in pd.columns:
                                pd[base] = pd[rname].combine_first(pd[base])
                                pd = pd.drop(columns=[rname])
                        joined = pd
                    except Exception:
                        pass
        except Exception:
            pass

        # prepare output filename
        fname = spec.filename or f"join_{uuid4().hex[:8]}"
        fmt = (spec.export_format or "csv").lower()
        out_path = OUTPUTS_DIR / f"{fname}.{ 'csv' if fmt!='pandas' and fmt!='sql' else ('pkl' if fmt=='pandas' else 'sqlite') }"

        # CSV export
        if fmt == "csv":
            try:
                _write_csv(joined, str(out_path))
                return {"export_path": str(out_path)}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        if fmt == "xlsx":
            # write CSV first then convert to xlsx
            try:
                tmp_csv = str(OUTPUTS_DIR / f"{fname}.csv")
                _write_csv(joined, tmp_csv)
                from openpyxl import Workbook
                import csv as _csv

                wb = Workbook()
                ws = wb.active
                with open(tmp_csv, newline='', encoding='utf-8') as fh:
                    reader = _csv.reader(fh)
                    for r in reader:
                        ws.append(r)
                xlsx_path = str(OUTPUTS_DIR / f"{fname}.xlsx")
                wb.save(xlsx_path)
                return {"export_path": xlsx_path}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        if fmt == "pandas":
            try:
                import pandas as _pd
                pd_df = joined.to_pandas() if hasattr(joined, 'to_pandas') else joined
                pkl_path = str(OUTPUTS_DIR / f"{fname}.pkl")
                pd_df.to_pickle(pkl_path)
                return {"export_path": pkl_path}
            except Exception:
                # fallback to parquet using polars
                try:
                    pq_path = str(OUTPUTS_DIR / f"{fname}.parquet")
                    try:
                        joined.write_parquet(pq_path)
                    except Exception:
                        # if joined is pandas
                        pd_df.to_parquet(pq_path)
                    return {"export_path": pq_path}
                except Exception as e:
                    raise HTTPException(status_code=500, detail=str(e))

        if fmt == "sql":
            try:
                import sqlite3
                db_path = str(OUTPUTS_DIR / f"{fname}.sqlite")
                # convert to records and insert
                recs = None
                if hasattr(joined, 'to_dicts'):
                    recs = joined.to_dicts()
                else:
                    try:
                        recs = joined.to_dict('records')
                    except Exception:
                        recs = []
                if recs is None:
                    recs = []
                if recs:
                    cols = list(recs[0].keys())
                else:
                    cols = []
                con = sqlite3.connect(db_path)
                cur = con.cursor()
                if cols:
                    cols_sql = ", ".join([f'"{c}" TEXT' for c in cols])
                    cur.execute(f'CREATE TABLE joined ({cols_sql})')
                    placeholders = ",".join(["?" for _ in cols])
                    to_insert = [[str(r.get(c, None)) for c in cols] for r in recs]
                    cur.executemany(f'INSERT INTO joined VALUES ({placeholders})', to_insert)
                    con.commit()
                con.close()
                return {"export_path": db_path}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        # default: fallback to csv
        try:
            _write_csv(joined, str(out_path))
            return {"export_path": str(out_path)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/apply")
def apply_recipe(recipe: Recipe):
    try:
        # Debug: log incoming recipe payload (truncated) to help diagnose 500s
        try:
            raw = recipe.json() if hasattr(recipe, 'json') else str(recipe)
            import logging
            logging.getLogger(__name__).debug("INCOMING /apply payload: %s", raw[:2000])
        except Exception:
            import logging
            logging.getLogger(__name__).debug("INCOMING /apply payload: <unserializable recipe>")
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
        # notify websocket clients of updated uploads list
        try:
            import asyncio
            asyncio.create_task(_broadcast_shared_update_message({"type": "uploads_changed", "deleted": str(resolved)}))
        except Exception:
            pass
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


@app.post("/uploads/explanations")
def persist_explanations(spec: ExplanationsLogSpec):
    """Persist explanation history for a source upload.

    Body: { source_path, recipe_id, explanations }
    Creates or updates a meta file next to the upload named `<filename>.meta.json` with
    an `explanations_history` array (newest first).
    """
    try:
        src = Path(spec.source_path)
        # prefer exact path, otherwise attempt to find matching upload by name
        if not src.exists():
            candidates = list(UPLOAD_DIR.glob(src.name))
            if candidates:
                src = candidates[0]
            else:
                matches = [p for p in UPLOAD_DIR.iterdir() if p.name == src.name]
                if matches:
                    src = matches[0]
        if not src.exists():
            raise HTTPException(status_code=404, detail="Source upload not found")

        meta = src.parent / (src.name + ".meta.json")
        existing = {}
        if meta.exists():
            try:
                existing = json.loads(meta.read_text(encoding="utf-8"))
            except Exception:
                existing = {}

        hist = existing.get("explanations_history") or []
        entry = {"recipe_id": spec.recipe_id, "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00','Z'), "explanations": spec.explanations}
        hist.insert(0, entry)
        # cap history length
        existing["explanations_history"] = hist[:20]
        meta.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/consent/export-job")
def enqueue_export_job(user_id: Optional[str] = None):
    try:
        # block jobs targeting opted-out users
        if user_id and _is_opted_out(user_id=user_id):
            _consent_audit("blocked_enqueue_export_opt_out", {"user_id": user_id})
            raise HTTPException(status_code=403, detail="Target user has opted out of exports")

        job = {"type": "export_consents", "user_id": user_id, "status": "pending", "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00','Z')}
        jid = _write_job(job)
        _consent_audit("enqueue_export", {"job_id": jid, "user_id": user_id})
        # dispatch celery task
        try:
            celery_process_export_job.delay(jid)
        except Exception:
            try:
                celery_process_export_job(jid)
            except Exception:
                pass
        return {"job_id": jid}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/consent/delete-job")
def enqueue_delete_job(user_id: Optional[str] = None):
    try:
        if not user_id:
            raise HTTPException(status_code=400, detail="Missing user_id")
        job = {"type": "delete_consents", "user_id": user_id, "status": "pending", "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00','Z')}
        jid = _write_job(job)
        _consent_audit("enqueue_delete", {"job_id": jid, "user_id": user_id})
        try:
            celery_process_delete_job.delay(jid)
        except Exception:
            try:
                celery_process_delete_job(jid)
            except Exception:
                pass
        return {"job_id": jid}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/jobs/{job_id}")
def get_job_status(job_id: str, download: bool = False):
    try:
        jf = JOBS_DIR / f"{job_id}.json"
        if not jf.exists():
            raise HTTPException(status_code=404, detail="Job not found")
        job = json.loads(jf.read_text(encoding="utf-8"))
        if download:
            res = job.get("result") or {}
            # if job targeted a specific user, block download if they opted out
            target_user = job.get("user_id")
            if target_user and _is_opted_out(user_id=target_user):
                _consent_audit("blocked_job_download_opt_out", {"job_id": job_id, "user_id": target_user})
                raise HTTPException(status_code=403, detail="Target user has opted out of exports")
            export_path = res.get("export_path")
            if export_path and Path(export_path).exists():
                return FileResponse(export_path, filename=Path(export_path).name, media_type='application/json', headers={"Content-Disposition": f"attachment; filename=\"{Path(export_path).name}\""})
        return job
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@app.post("/consent")
def record_consent(spec: ConsentSpec, request: Request):
    try:
        CONSENT_DIR = Path("data") / "consents"
        CONSENT_DIR.mkdir(parents=True, exist_ok=True)
        cid = f"consent_{uuid4().hex[:8]}"
        # prefer explicitly provided IP, otherwise infer from request headers
        ip = spec.ip or request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)
        ua = spec.user_agent or request.headers.get("user-agent")
        payload = {
            "id": cid,
            "user_id": spec.user_id,
            "consents": spec.consents,
            "user_agent": ua,
            "ip": ip,
            "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
        }
        dest = CONSENT_DIR / f"{cid}.json"
        dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        # audit
        try:
            _consent_audit("record_consent", {"id": cid, "user_id": spec.user_id, "ip": ip, "user_agent": ua})
        except Exception:
            pass
        return {"ok": True, "id": cid}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/consent")
def get_consents(user_id: Optional[str] = None):
    try:
        CONSENT_DIR = Path("data") / "consents"
        out = []
        if not CONSENT_DIR.exists():
            return {"consents": []}
        for p in sorted(CONSENT_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if user_id and data.get("user_id") != user_id:
                    continue
                out.append(data)
            except Exception:
                continue
        return {"consents": out}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/consent/export")
def export_consents(user_id: Optional[str] = None):
    """Export consent receipts as a JSON attachment. If `user_id` is provided,
    only exports receipts for that user, otherwise exports all receipts.
    """
    try:
        CONSENT_DIR = Path("data") / "consents"
        out = []
        if not CONSENT_DIR.exists():
            return {"consents": []}
        # if exporting consents for a specific user, ensure they have not opted out
        if user_id and _is_opted_out(user_id=user_id):
            _consent_audit("blocked_export_opt_out", {"user_id": user_id})
            raise HTTPException(status_code=403, detail="Target user has opted out of exports")

        for p in sorted(CONSENT_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if user_id and data.get("user_id") != user_id:
                    continue
                out.append(data)
            except Exception:
                continue
        # return as inline JSON; clients can save the response
        return {"consents": out}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/consent/{consent_id}")
def delete_consent(consent_id: str):
        """Delete a single consent receipt immediately. Also logs an audit entry."""
        try:
            CONSENT_DIR = Path("data") / "consents"
            p = CONSENT_DIR / f"{consent_id}.json"
            if not p.exists():
                raise HTTPException(status_code=404, detail="Consent receipt not found")
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                data = None
            p.unlink()
            _consent_audit("delete_consent", {"id": consent_id, "user_id": data.get("user_id") if data else None})
            return {"deleted": consent_id}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@app.delete("/consent")
def delete_consents_by_user(user_id: Optional[str] = None):
        """Delete all consent receipts for a given `user_id`. If no user_id provided,
        returns 400. This endpoint can be used to withdraw consent and erase records.
        """
        try:
            if not user_id:
                raise HTTPException(status_code=400, detail="Missing user_id")
            CONSENT_DIR = Path("data") / "consents"
            if not CONSENT_DIR.exists():
                return {"deleted": []}
            deleted = []
            for p in list(CONSENT_DIR.glob("*.json")):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if data.get("user_id") == user_id:
                        deleted.append(data.get("id") or p.stem)
                        p.unlink()
                except Exception:
                    continue
            return {"deleted": deleted}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


def _write_job(job: dict):
    jid = job.get("id") or f"job_{uuid4().hex[:8]}"
    job["id"] = jid
    job_file = JOBS_DIR / f"{jid}.json"
    job_file.write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")
    return jid


def _update_job_status(jid: str, status: str, result: Optional[dict] = None):
    job_file = JOBS_DIR / f"{jid}.json"
    try:
        job = json.loads(job_file.read_text(encoding="utf-8"))
    except Exception:
        job = {"id": jid}
    job["status"] = status
    if result is not None:
        job["result"] = result
    job["updated_at"] = datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    job_file.write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")


def _process_export_job(job: dict):
    jid = job.get("id")
    user_id = job.get("user_id")
    try:
        # If the job targets an opted-out user, abort and audit
        if user_id and _is_opted_out(user_id=user_id):
            _consent_audit("blocked_process_export_opt_out", {"job_id": jid, "user_id": user_id})
            return {"error": "target_user_opted_out"}

        # gather consents matching user_id (or all)
        CONSENT_DIR = Path("data") / "consents"
        out = []
        if CONSENT_DIR.exists():
            for p in sorted(CONSENT_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if user_id and data.get("user_id") != user_id:
                        continue
                    out.append(data)
                except Exception:
                    continue
        export_dir = CONSENT_DIR / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        export_path = export_dir / f"export_{jid}.json"
        export_path.write_text(json.dumps({"consents": out}, indent=2, ensure_ascii=False), encoding="utf-8")
        _consent_audit("export_consents", {"job_id": jid, "user_id": user_id, "export_path": str(export_path)})
        return {"export_path": str(export_path), "count": len(out)}
    except Exception as e:
        return {"error": str(e)}


def _process_delete_job(job: dict):
    jid = job.get("id")
    user_id = job.get("user_id")
    try:
        deleted = []
        CONSENT_DIR = Path("data") / "consents"
        if not CONSENT_DIR.exists():
            return {"deleted": deleted}
        for p in list(CONSENT_DIR.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if data.get("user_id") == user_id:
                    deleted.append(data.get("id") or p.stem)
                    p.unlink()
            except Exception:
                continue
        _consent_audit("delete_consents", {"job_id": jid, "user_id": user_id, "deleted": deleted})
        return {"deleted": deleted}
    except Exception as e:
        return {"error": str(e)}


@celery_app.task(name='fbroom.process_export_job')
def celery_process_export_job(jid: str):
    # load job file
    jf = JOBS_DIR / f"{jid}.json"
    try:
        job = json.loads(jf.read_text(encoding="utf-8"))
    except Exception:
        job = {"id": jid}
    _update_job_status(jid, "processing")
    res = _process_export_job(job)
    _update_job_status(jid, "completed" if not res.get('error') else 'failed', res)


@celery_app.task(name='fbroom.process_delete_job')
def celery_process_delete_job(jid: str):
    jf = JOBS_DIR / f"{jid}.json"
    try:
        job = json.loads(jf.read_text(encoding="utf-8"))
    except Exception:
        job = {"id": jid}
    _update_job_status(jid, "processing")
    res = _process_delete_job(job)
    _update_job_status(jid, "completed" if not res.get('error') else 'failed', res)


# --- Simple user account and Utah-style privacy request flow ---


class UserCreate(BaseModel):
    username: str
    email: str
    password: str
    role: Optional[str] = None


class UserLogin(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    password: str
    persistent: Optional[bool] = False


class DeleteAccountRequest(BaseModel):
    password: str
    confirm_text: str


def _users_dir():
    d = Path("data") / "users"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sessions_dir():
    d = Path("data") / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _hash_password(password: str, salt: Optional[bytes] = None):
    if salt is None:
        salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return salt.hex(), dk.hex()


def _verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    salt = bytes.fromhex(salt_hex)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return dk.hex() == hash_hex


def _find_user_by_username(username: str):
    d = _users_dir()
    for p in d.glob("*.json"):
        try:
            u = json.loads(p.read_text(encoding="utf-8"))
            if u.get("username") == username:
                return u
        except Exception:
            continue
    return None


def _find_user_by_email(email: str):
    d = _users_dir()
    for p in d.glob("*.json"):
        try:
            u = json.loads(p.read_text(encoding="utf-8"))
            if u.get("email") == email:
                return u
        except Exception:
            continue
    return None


def _create_user(username: str, email: str, password: str, role: Optional[str] = None, is_admin: bool = False):
    uid = f"user_{uuid4().hex[:8]}"
    salt, ph = _hash_password(password)
    # normalize role
    allowed_roles = ("member", "admin")
    r = (role or "member")
    if r not in allowed_roles:
        r = "member"
    user = {
        "id": uid,
        "username": username,
        "email": email,
        "pw_salt": salt,
        "pw_hash": ph,
        "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
        "role": r,
        "is_admin": True if (is_admin or r == "admin") else False,
    }
    p = _users_dir() / f"{uid}.json"
    p.write_text(json.dumps(user, indent=2, ensure_ascii=False), encoding="utf-8")
    _consent_audit("create_user", {"user_id": uid, "username": username, "email": email, "role": user.get("role")})
    return user


def _create_session(user_id: str, duration_seconds: Optional[int] = 60 * 60 * 24 * 7):
    # Create a JWT for production-style bearer sessions
    jwt_secret = JWT_SECRET
    jwt_algo = JWT_ALGO
    now = datetime.now(timezone.utc)
    jti = uuid4().hex
    payload = {"sub": user_id, "iat": int(now.timestamp()), "jti": jti}
    if duration_seconds is not None:
        exp = now.timestamp() + float(duration_seconds)
        payload["exp"] = int(exp)
    token = jwt.encode(payload, jwt_secret, algorithm=jwt_algo)
    # persist a lightweight session record for revocation/inspection
    try:
        s = {"token": token, "user_id": user_id, "created_at": now.isoformat() + "Z", "jti": jti}
        p = _sessions_dir() / f"session_{jti}.json"
        p.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return token


def _create_session_tokens(user_id: str, access_seconds: int = 60 * 15, refresh_seconds: Optional[int] = 60 * 60 * 24 * 30, persistent: bool = False, ip: Optional[str] = None, user_agent: Optional[str] = None):
    jwt_secret = JWT_SECRET
    jwt_algo = JWT_ALGO
    now = datetime.now(timezone.utc)
    rjti = uuid4().hex
    ajti = uuid4().hex

    # Refresh token: if persistent, omit exp to allow server-side session file to control lifetime
    refresh_payload = {"sub": user_id, "iat": int(now.timestamp()), "jti": rjti, "purpose": "refresh"}
    if not persistent and refresh_seconds is not None:
        refresh_payload["exp"] = int(now.timestamp() + float(refresh_seconds))
    refresh_token = jwt.encode(refresh_payload, jwt_secret, algorithm=jwt_algo)

    # Access token references the refresh jti so we can validate against the session record
    access_payload = {"sub": user_id, "iat": int(now.timestamp()), "jti": ajti, "rjti": rjti}
    if access_seconds is not None:
        access_payload["exp"] = int(now.timestamp() + float(access_seconds))
    access_token = jwt.encode(access_payload, jwt_secret, algorithm=jwt_algo)

    # persist session record keyed by refresh jti
    try:
        s = {
            "refresh_jti": rjti,
            "access_jti": ajti,
            "user_id": user_id,
            "created_at": now.isoformat() + "Z",
            "last_seen": now.isoformat() + "Z",
            "ip": ip,
            "user_agent": user_agent,
        }
        p = _sessions_dir() / f"session_{rjti}.json"
        # write with optional encryption or restrictive permissions
        try:
            _write_session_file(p, s)
        except Exception:
            p.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
            try:
                os.chmod(p, 0o600)
            except Exception:
                pass
    except Exception:
        pass

    return {"access_token": access_token, "refresh_token": refresh_token, "refresh_jti": rjti, "access_jti": ajti}


def _get_user_by_id(user_id: str):
    p = _users_dir() / f"{user_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _find_team_owner_by_name(team_name: str):
    """Find a canonical owner user object for a given team name.
    Preference: user with explicit 'team_owner' flag; otherwise first user with matching team_name.
    """
    if not team_name:
        return None
    try:
        udir = _users_dir()
        candidates = []
        for p in udir.glob("*.json"):
            try:
                uu = json.loads(p.read_text(encoding="utf-8"))
                if uu.get("team_name") == team_name:
                    if uu.get("team_owner"):
                        return uu
                    candidates.append(uu)
            except Exception:
                continue
        if candidates:
            return candidates[0]
    except Exception:
        pass
    return None


def _get_user_from_token(token: str):
    # Support both pre-existing session files and JWT tokens
    try:
        # if token contains dots assume JWT
        if "." in token:
            jwt_secret = JWT_SECRET
            jwt_algo = JWT_ALGO
            try:
                payload = jwt.decode(token, jwt_secret, algorithms=[jwt_algo], leeway=60)
            except Exception:
                return None
            uid = payload.get("sub")
            # Refresh tokens carry purpose="refresh" and their jti is the session key
            if payload.get("purpose") == "refresh":
                rjti = payload.get("jti")
                if not rjti:
                    return None
                p = _sessions_dir() / f"session_{rjti}.json"
                if not p.exists():
                    return None
                # update last_seen
                try:
                    s = _read_session_file(p)
                    s["last_seen"] = datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
                    try:
                        _write_session_file(p, s)
                    except Exception:
                        p.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
                except Exception:
                    pass
                return _get_user_by_id(uid)
            # Access tokens carry rjti linking to session record
            rjti = payload.get("rjti")
            if not rjti:
                return None
            p = _sessions_dir() / f"session_{rjti}.json"
            if not p.exists():
                return None
            # update last_seen
            try:
                s = _read_session_file(p)
                s["last_seen"] = datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
                try:
                    _write_session_file(p, s)
                except Exception:
                    p.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
            return _get_user_by_id(uid)
    except Exception:
        pass
    # fallback: legacy session file keyed by jti or token
    p = _sessions_dir() / f"session_{token}.json"
    if not p.exists():
        return None
    try:
        s = json.loads(p.read_text(encoding="utf-8"))
        return _get_user_by_id(s.get("user_id"))
    except Exception:
        return None


def _send_email_message(to_email: str, subject: str, body: str):
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    from_addr = os.environ.get("FROM_EMAIL") or "noreply@falconbroom.local"
    msg = EmailMessage()


# Session file helpers: optional encryption using SESSION_ENCRYPTION_KEY env var.
def _get_session_encryption_key():
    k = os.environ.get("SESSION_ENCRYPTION_KEY")
    if not k:
        return None
    try:
        return k.encode('utf-8')
    except Exception:
        return None

def _write_session_file(path: Path, obj: dict):
    key = _get_session_encryption_key()
    data = json.dumps(obj, indent=2, ensure_ascii=False).encode('utf-8')
    if key:
        try:
            # use cryptography.Fernet if available
            from cryptography.fernet import Fernet
            f = Fernet(key)
            enc = f.encrypt(data)
            path.write_bytes(enc)
            try:
                os.chmod(path, 0o600)
            except Exception:
                pass
            return
        except Exception:
            pass
    # fallback: write plaintext and tighten perms when possible
    path.write_text(data.decode('utf-8'), encoding='utf-8')
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass

def _read_session_file(path: Path):
    key = _get_session_encryption_key()
    raw = path.read_bytes()
    if key:
        try:
            from cryptography.fernet import Fernet
            f = Fernet(key)
            dec = f.decrypt(raw)
            return json.loads(dec.decode('utf-8'))
        except Exception:
            pass
    try:
        return json.loads(raw.decode('utf-8'))
    except Exception:
        # fall back to reading as text
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return {}
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_email
    msg.set_content(body)
    # If SMTP configured, send; otherwise persist to data/emails for dev
    if smtp_host and smtp_user and smtp_pass:
        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
                s.starttls()
                s.login(smtp_user, smtp_pass)
                s.send_message(msg)
            return True
        except Exception:
            pass
    # fallback: write to data/emails
    try:
        emdir = Path("data") / "emails"
        emdir.mkdir(parents=True, exist_ok=True)
        fn = emdir / f"email_{uuid4().hex}.txt"
        fn.write_text(f"To: {to_email}\nSubject: {subject}\n\n{body}\n", encoding="utf-8")
        return True
    except Exception:
        return False


def _make_verification_token(user_id: str, email: str, expires_seconds: int = 60 * 60 * 24):
    jwt_secret = JWT_SECRET
    jwt_algo = JWT_ALGO
    now = datetime.now(timezone.utc)
    exp = now.timestamp() + expires_seconds
    payload = {"sub": user_id, "email": email, "purpose": "verify_email", "iat": int(now.timestamp()), "exp": int(exp), "jti": uuid4().hex}
    return jwt.encode(payload, jwt_secret, algorithm=jwt_algo)


def _make_invite_token(inviter_id: str, invitee_email: str, team_name: Optional[str] = None, expires_seconds: int = 60 * 60 * 24 * 7):
    jwt_secret = JWT_SECRET
    jwt_algo = JWT_ALGO
    now = datetime.now(timezone.utc)
    exp = now.timestamp() + expires_seconds
    payload = {"inviter": inviter_id, "email": invitee_email, "team": team_name, "purpose": "invite", "iat": int(now.timestamp()), "exp": int(exp), "jti": uuid4().hex}
    return jwt.encode(payload, jwt_secret, algorithm=jwt_algo)


def _require_auth(request: Request):
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth or not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = auth.split(None, 1)[1].strip()
    user = _get_user_from_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


def _require_admin(request: Request):
    # allow admin by bearer token user role or by ADMIN_TOKEN header
    try:
        user = None
        try:
            user = _require_auth(request)
        except Exception:
            user = None
        if user and (user.get('role') == 'admin' or user.get('is_admin')):
            return user
        # fallback: allow with ADMIN_TOKEN env var or AUDIT_EXPORT_KEY header
        hdr = request.headers.get('X-Admin-Token') or request.headers.get('X-Audit-Export-Key')
        if hdr and (hdr == os.environ.get('ADMIN_TOKEN') or hdr == os.environ.get('AUDIT_EXPORT_KEY')):
            return {'id': 'system', 'username': 'admin-token'}
    except Exception:
        pass
    raise HTTPException(status_code=403, detail='Admin required')


@app.post("/signup")
def signup(spec: UserCreate):
    try:
        if _find_user_by_username(spec.username):
            raise HTTPException(status_code=400, detail="Username already exists")
        if _find_user_by_email(spec.email):
            raise HTTPException(status_code=400, detail="Email already registered")
        # honor requested role, but prevent self-assigning admin unless a valid ADMIN_TOKEN header provided
        requested_role = getattr(spec, 'role', None) or None
        role_to_assign = None
        if requested_role == 'admin':
            # require admin token header to allow creating an admin via signup
            hdr = None
            try:
                hdr = None
            except Exception:
                hdr = None
            # Check explicit ADMIN_TOKEN env or header - prefer header
            try:
                header_token = None
            except Exception:
                header_token = None
            # default: disallow admin signup
            if os.environ.get('ADMIN_TOKEN') and False:
                role_to_assign = 'admin'
            else:
                role_to_assign = 'member'
        else:
            role_to_assign = requested_role or 'member'
        user = _create_user(spec.username, spec.email, spec.password, role=role_to_assign)
        # send verification email if email present
        try:
            if spec.email:
                token = _make_verification_token(user.get("id"), spec.email)
                link = os.environ.get("APP_URL", "http://localhost:3000") + f"/verify-email?token={token}"
                _send_email_message(spec.email, "Verify your FalconBroom email", f"Please verify your email by visiting: {link}")
                _consent_audit("send_verification", {"user_id": user.get("id"), "email": spec.email})
        except Exception:
            pass
        return {"ok": True, "user_id": user["id"]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/login")
def login(spec: UserLogin, request: Request, response: Response):
    try:
        user = None
        if spec.username:
            user = _find_user_by_username(spec.username)
        if not user and spec.email:
            user = _find_user_by_email(spec.email)
        if not user:
            raise HTTPException(status_code=400, detail="Invalid credentials")
        if not _verify_password(spec.password, user.get("pw_salt"), user.get("pw_hash")):
            raise HTTPException(status_code=400, detail="Invalid credentials")
        # Create access + refresh tokens; include request metadata if available
        req = None
        try:
            # FastAPI will inject Request if provided; attempt to get from locals
            req = globals().get('request')
        except Exception:
            req = None
        ip = None
        ua = None
        # If a Request object is available via the framework, capture client info
        try:
            # 'spec' is the body model; the actual Request is not passed here. Use environment heuristics.
            pass
        except Exception:
            pass
        ip = None
        ua = None
        try:
            ip = request.client.host if getattr(request, 'client', None) else None
            ua = request.headers.get('user-agent')
        except Exception:
            pass
        tokens = _create_session_tokens(user.get("id"), persistent=getattr(spec, "persistent", False), ip=ip, user_agent=ua)
        # set httpOnly refresh cookie; access token returned in body only
        cookie_name = os.environ.get("REFRESH_COOKIE_NAME") or "falconbroom_refresh"
        secure_flag = True if os.environ.get("ENV") == "production" else False
        # For production, prefer SameSite=None with Secure; for local dev use Lax so browsers will send cookie
        samesite_flag = "none" if secure_flag else "lax"
        try:
            response.set_cookie(cookie_name, tokens.get("refresh_token"), httponly=True, samesite=samesite_flag, secure=secure_flag)
        except Exception:
            pass
        _consent_audit("login", {"user_id": user.get("id")})
        resp = {"access_token": tokens.get("access_token"), "user_id": user.get("id")}
        # In non-production dev environments, return the refresh token in the body to aid local dev flows
        if os.environ.get("ENV") != "production":
            try:
                resp["refresh_token"] = tokens.get("refresh_token")
            except Exception:
                pass
        return resp
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/logout")
def logout(request: Request, response: Response):
    try:
        auth = request.headers.get("authorization") or request.headers.get("Authorization")
        if not auth or not auth.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Missing Authorization header")
        token = auth.split(None, 1)[1].strip()
        # If token is a JWT, decode to find the session refresh jti and remove the session record
        try:
            if "." in token:
                jwt_secret = JWT_SECRET
                jwt_algo = JWT_ALGO
                payload = jwt.decode(token, jwt_secret, algorithms=[jwt_algo])
                # refresh tokens have purpose="refresh" and jti is the session key
                if payload.get("purpose") == "refresh":
                    rjti = payload.get("jti")
                else:
                    rjti = payload.get("rjti")
                    if rjti:
                        p = _sessions_dir() / f"session_{rjti}.json"
                        if p.exists():
                            p.unlink()
                        # clear cookie
                        cookie_name = os.environ.get("REFRESH_COOKIE_NAME") or "falconbroom_refresh"
                        try:
                            response.delete_cookie(cookie_name)
                        except Exception:
                            pass
                        return {"ok": True}
        except Exception:
            pass
        # Fallback: legacy session file keyed by token
        p = _sessions_dir() / f"session_{token}.json"
        if p.exists():
            p.unlink()
        cookie_name = os.environ.get("REFRESH_COOKIE_NAME") or "falconbroom_refresh"
        try:
            response.delete_cookie(cookie_name)
        except Exception:
            pass
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/verify-email")
def verify_email(payload: dict):
    try:
        token = payload.get("token")
        if not token:
            raise HTTPException(status_code=400, detail="Missing token")
        jwt_secret = JWT_SECRET
        jwt_algo = JWT_ALGO
        try:
            # Allow small clock skew when verifying tokens
            data = jwt.decode(token, jwt_secret, algorithms=[jwt_algo], leeway=60)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid or expired token")
        if data.get("purpose") != "verify_email":
            raise HTTPException(status_code=400, detail="Invalid token purpose")
        uid = data.get("sub")
        email = data.get("email")
        user = _get_user_by_id(uid)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        user["email_verified"] = True
        user["email"] = email
        p = _users_dir() / f"{uid}.json"
        p.write_text(json.dumps(user, indent=2, ensure_ascii=False), encoding="utf-8")
        _consent_audit("verify_email", {"user_id": uid, "email": email})
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/refresh")
async def refresh_token(request: Request, response: Response):
    try:
        # read refresh token from httpOnly cookie
        cookie_name = os.environ.get("REFRESH_COOKIE_NAME") or "falconbroom_refresh"
        token = None
        try:
            token = request.cookies.get(cookie_name)
        except Exception:
            token = None
        # If cookie is missing, allow dev-only fallback to accept a refresh token in the JSON body
        if not token:
            if os.environ.get("ENV") != "production":
                try:
                    body = await request.json()
                    token = body.get("refresh_token")
                except Exception:
                    token = None
            if not token:
                raise HTTPException(status_code=400, detail="Missing refresh_token")
        jwt_secret = JWT_SECRET
        jwt_algo = JWT_ALGO
        try:
            data = jwt.decode(token, jwt_secret, algorithms=[jwt_algo], leeway=60)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid or expired refresh token")
        if data.get("purpose") != "refresh":
            raise HTTPException(status_code=400, detail="Invalid token purpose")
        old_rjti = data.get("jti")
        if not old_rjti:
            raise HTTPException(status_code=400, detail="Invalid token")
        p = _sessions_dir() / f"session_{old_rjti}.json"
        if not p.exists():
            # Possible token reuse or already-rotated token
            raise HTTPException(status_code=401, detail="Session revoked or token reuse detected")

        # Read existing session and create rotated session with new refresh jti
        try:
            s = _read_session_file(p)
        except Exception:
            s = {}

        now = datetime.now(timezone.utc)
        new_rjti = uuid4().hex
        new_ajti = uuid4().hex

        # create new refresh token (rotate)
        refresh_payload = {"sub": data.get("sub"), "iat": int(now.timestamp()), "jti": new_rjti, "purpose": "refresh"}
        # preserve expiration semantics: if previous had exp, include similar TTL
        if data.get("exp"):
            ttl = int(data.get("exp")) - int(data.get("iat") or int(now.timestamp()))
            if ttl > 0:
                refresh_payload["exp"] = int(now.timestamp() + ttl)
        new_refresh_token = jwt.encode(refresh_payload, jwt_secret, algorithm=jwt_algo)

        # create new access token referencing new refresh jti
        access_payload = {"sub": data.get("sub"), "iat": int(now.timestamp()), "jti": new_ajti, "rjti": new_rjti}
        access_payload["exp"] = int(now.timestamp() + float(60 * 15))
        access_token = jwt.encode(access_payload, jwt_secret, algorithm=jwt_algo)

        # write new session file and remove old one
        try:
            new_s = {
                "refresh_jti": new_rjti,
                "access_jti": new_ajti,
                "user_id": s.get("user_id") or data.get("sub"),
                "created_at": s.get("created_at") or now.isoformat() + "Z",
                "last_seen": now.isoformat() + "Z",
                "ip": s.get("ip"),
                "user_agent": s.get("user_agent"),
            }
            p_new = _sessions_dir() / f"session_{new_rjti}.json"
            _write_session_file(p_new, new_s)
            try:
                p.unlink()
            except Exception:
                pass
        except Exception:
            pass

        # set rotated refresh cookie
        secure_flag = True if os.environ.get("ENV") == "production" else False
        samesite_flag = "none" if secure_flag else "lax"
        try:
            response.set_cookie(cookie_name, new_refresh_token, httponly=True, samesite=samesite_flag, secure=secure_flag)
        except Exception:
            pass

        return {"access_token": access_token}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sessions")
def list_sessions(request: Request):
    try:
        user = _require_auth(request)
        sessions = []
        d = _sessions_dir()
        for p in d.glob("session_*.json"):
            try:
                s = json.loads(p.read_text(encoding="utf-8"))
                if s.get("user_id") == user.get("id"):
                    sessions.append({"refresh_jti": s.get("refresh_jti"), "access_jti": s.get("access_jti"), "created_at": s.get("created_at"), "last_seen": s.get("last_seen"), "ip": s.get("ip"), "user_agent": s.get("user_agent")})
            except Exception:
                continue
        return {"sessions": sessions}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sessions/revoke")
def revoke_session(request: Request, payload: dict):
    try:
        user = _require_auth(request)
        jti = payload.get("jti")
        if not jti:
            raise HTTPException(status_code=400, detail="Missing jti")
        p = _sessions_dir() / f"session_{jti}.json"
        if not p.exists():
            return {"ok": True}
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
            if s.get("user_id") != user.get("id"):
                raise HTTPException(status_code=403, detail="Not allowed")
        except HTTPException:
            raise
        except Exception:
            pass
        p.unlink()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sessions/revoke_all")
def revoke_all_sessions(request: Request):
    """Revoke all refresh sessions for the authenticated user."""
    try:
        user = _require_auth(request)
        d = _sessions_dir()
        count = 0
        for p in d.glob("session_*.json"):
            try:
                s = _read_session_file(p)
                if s.get("user_id") == user.get("id"):
                    try:
                        p.unlink()
                        count += 1
                    except Exception:
                        pass
            except Exception:
                continue
        return {"ok": True, "revoked": count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/dsar/request")
def dsar_request(request: Request, payload: dict):
    """Request a Data Subject Access Request (export or delete).
    payload: { action: 'export'|'delete', password?: '<password for verification>' }
    """
    try:
        user = _require_auth(request)
        action = (payload.get("action") or "").lower()
        if action not in ("export", "delete"):
            raise HTTPException(status_code=400, detail="Invalid action")
        dsar_dir = Path("data") / "dsar"
        dsar_dir.mkdir(parents=True, exist_ok=True)
        dsar_id = f"dsar_{uuid4().hex[:8]}"
        record = {"id": dsar_id, "user_id": user.get("id"), "action": action, "requested_at": datetime.now(timezone.utc).isoformat().replace('+00:00','Z'), "status": "requested"}
        # verification: for delete requests, allow password or email verification token
        if action == "delete":
            pwd = payload.get("password")
            if pwd and _verify_password(pwd, user.get("pw_salt"), user.get("pw_hash")):
                record["status"] = "verified"
                record["verification_method"] = "password"
            else:
                # send verification email with short-lived token
                try:
                    token = _make_verification_token(user.get("id"), user.get("email"), expires_seconds=60*30)
                    # decode to extract jti
                    try:
                        dec = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
                        record["verify_jti"] = dec.get("jti")
                        record["verify_expires"] = dec.get("exp")
                    except Exception:
                        record["verify_jti"] = None
                    link = os.environ.get("APP_URL", "http://localhost:3000") + f"/dsar/verify?token={token}"
                    _send_email_message(user.get("email"), "Verify your DSAR deletion request", f"Click to verify deletion: {link}\nThis link expires in 30 minutes.")
                    record["status"] = "verification_sent"
                    record["verification_sent_at"] = datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
                except Exception:
                    # fall back to requiring password if email fails
                    pass
        # write request record and audit
        out = dsar_dir / f"{dsar_id}.json"
        try:
            save_sensitive_json(out, record)
        except Exception:
            out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            _append_audit_event(dsar_dir, "dsar_requested", {"dsar_id": dsar_id, "user_id": user.get("id"), "action": action})
        except Exception:
            pass
        # For exports, create an export artifact synchronously for now
        if action == "export":
            try:
                import zipfile
                export_path = OUTPUTS_DIR / f"{dsar_id}.zip"
                with zipfile.ZipFile(export_path, "w") as zf:
                    # include user profile
                    ufile = _users_dir() / f"{user.get('id')}.json"
                    if ufile.exists():
                        zf.write(str(ufile), arcname=ufile.name)
                    # include session files
                    for p in _sessions_dir().glob("session_*.json"):
                        try:
                            s = _read_session_file(p)
                            if s.get("user_id") == user.get("id"):
                                zf.write(str(p), arcname=p.name)
                        except Exception:
                            continue
                    # include uploads metadata list
                    try:
                        up = UPLOAD_DIR
                        for f in up.iterdir():
                            if f.is_file():
                                zf.write(str(f), arcname=f.name)
                    except Exception:
                        pass
                record["export_path"] = str(export_path)
                record["status"] = "completed"
                out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                record["status"] = "failed"
                record["error"] = str(e)
                out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"dsar_id": dsar_id, "status": record.get("status")}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/dsar/delete")
def dsar_delete(request: Request, payload: dict):
    """Perform deletion of user data after DSAR verification. Requires password in payload."""
    try:
        user = _require_auth(request)
        pwd = payload.get("password")
        # allow deletion if password verified OR a prior DSAR verification was completed
        verified = False
        if pwd and _verify_password(pwd, user.get("pw_salt"), user.get("pw_hash")):
            verified = True
        else:
            # look for a verified DSAR record for this user
            try:
                dsar_dir = Path("data") / "dsar"
                for p in dsar_dir.glob("dsar_*.json"):
                    try:
                        r = json.loads(p.read_text(encoding="utf-8"))
                        if r.get("user_id") == user.get("id") and r.get("action") == "delete" and r.get("status") == "verified":
                            verified = True
                            break
                    except Exception:
                        continue
            except Exception:
                pass
        if not verified:
            raise HTTPException(status_code=401, detail="Identity verification failed")
        # perform deletion: user file, sessions, uploads owned by user, outputs and history (best-effort)
        uid = user.get("id")
        deleted = []
        # delete user file
        try:
            p = _users_dir() / f"{uid}.json"
            if p.exists():
                p.unlink()
                deleted.append(str(p))
        except Exception:
            pass
        # delete sessions
        try:
            for p in _sessions_dir().glob("session_*.json"):
                try:
                    s = _read_session_file(p)
                    if s.get("user_id") == uid:
                        p.unlink()
                        deleted.append(str(p))
                except Exception:
                    continue
        except Exception:
            pass
        # delete uploads where meta.owner matches user
        try:
            for p in UPLOAD_DIR.iterdir():
                try:
                    if not p.is_file():
                        continue
                    meta = p.parent / (p.name + ".meta.json")
                    owner = {}
                    if meta.exists():
                        try:
                            owner = json.loads(meta.read_text(encoding="utf-8"))
                        except Exception:
                            owner = {}
                    o = owner.get("owner") or {}
                    if o.get("user_id") == uid or (o.get("email") and o.get("email").lower() == (user.get("email") or "").lower()):
                        try:
                            p.unlink()
                        except Exception:
                            pass
                        try:
                            if meta.exists():
                                meta.unlink()
                        except Exception:
                            pass
                        deleted.append(str(p))
                except Exception:
                    continue
        except Exception:
            pass
        # delete history/run records and associated outputs owned by user
        try:
            for p in HISTORY_DIR.glob("*.json"):
                try:
                    j = json.loads(p.read_text(encoding="utf-8"))
                    owner = j.get("owner") or {}
                    if owner.get("user_id") == uid:
                        # delete referenced output files if present
                        # common naming: {rid}_{run_id} or run id in filename
                        run_id = j.get("id")
                        rid = j.get("recipe_id")
                        try:
                            for outp in OUTPUTS_DIR.iterdir():
                                try:
                                    if run_id and run_id in outp.name:
                                        outp.unlink()
                                        deleted.append(str(outp))
                                    elif rid and rid in outp.name:
                                        # also try recipe-based outputs
                                        outp.unlink()
                                        deleted.append(str(outp))
                                except Exception:
                                    continue
                        except Exception:
                            pass
                        try:
                            p.unlink()
                            deleted.append(str(p))
                        except Exception:
                            pass
                except Exception:
                    continue
        except Exception:
            pass

        # best-effort: delete any outputs that reference user id in filename
        try:
            for outp in OUTPUTS_DIR.iterdir():
                try:
                    if (user.get("id") and user.get("id") in outp.name) or (user.get("email") and user.get("email") in outp.name):
                        try:
                            outp.unlink()
                            deleted.append(str(outp))
                        except Exception:
                            pass
                except Exception:
                    continue
        except Exception:
            pass

        # write propagation record for third-party/manual actions
        try:
            dsar_dir = Path("data") / "dsar"
            dsar_dir.mkdir(parents=True, exist_ok=True)
            prop = dsar_dir / f"propagation_{uuid4().hex[:8]}.json"
            try:
                save_sensitive_json(prop, {"user_id": uid, "deleted": deleted, "ts": datetime.now(timezone.utc).isoformat().replace('+00:00','Z')})
            except Exception:
                prop.write_text(json.dumps({"user_id": uid, "deleted": deleted, "ts": datetime.now(timezone.utc).isoformat().replace('+00:00','Z')} , indent=2), encoding="utf-8")
            # audit
            try:
                _append_audit_event(dsar_dir, "delete", {"user_id": uid, "deleted_count": len(deleted)})
            except Exception:
                pass
        except Exception:
            pass

        return {"ok": True, "deleted_count": len(deleted)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/dsar/verify")
def dsar_verify(payload: dict):
    """Verify an emailed DSAR token. payload: { token: '<jwt token>' }"""
    try:
        token = payload.get("token")
        if not token:
            raise HTTPException(status_code=400, detail="Missing token")
        try:
            data = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid or expired token")
        if data.get("purpose") != "verify_email":
            raise HTTPException(status_code=400, detail="Invalid token purpose")
        uid = data.get("sub")
        jti = data.get("jti")
        dsar_dir = Path("data") / "dsar"
        found = None
        for p in dsar_dir.glob("dsar_*.json*"):
            try:
                r = load_sensitive_json(p) or json.loads(p.read_text(encoding="utf-8"))
                if r.get("user_id") == uid and r.get("action") == "delete" and r.get("status") == "verification_sent":
                    # match jti if present
                    if not r.get("verify_jti") or r.get("verify_jti") == jti:
                        found = (p, r)
                        break
            except Exception:
                continue
        if not found:
            raise HTTPException(status_code=404, detail="Pending DSAR not found")
        p, r = found
        r["status"] = "verified"
        r["verified_at"] = datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
        r["verification_method"] = r.get("verification_method") or "email"
        try:
            save_sensitive_json(p, r)
        except Exception:
            p.write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
        # audit
        try:
            _append_audit_event(dsar_dir, "dsar_verified", {"dsar_id": r.get("id"), "user_id": uid})
        except Exception:
            pass
        return {"ok": True, "dsar_id": r.get("id")}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _process_propagation_file(path: Path):
    try:
        dsar_dir = path.parent
        processed_dir = dsar_dir / "processed"
        processed_dir.mkdir(parents=True, exist_ok=True)
        data = json.loads(path.read_text(encoding="utf-8"))
        # attempt third-party deletions (Google Sheets, Drive). On failure, enqueue for retry.
        results = {"processed_at": datetime.now(timezone.utc).isoformat().replace('+00:00','Z'), "actions": []}
        sheets = data.get("google_sheets") or []
        drive_ids = data.get("google_drive") or []
        try:
            # Lazy import of google client libs if available
            creds_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
            google_available = False
            if creds_json:
                try:
                    from google.oauth2.service_account import Credentials
                    from googleapiclient.discovery import build
                    google_available = True
                except Exception:
                    google_available = False
            else:
                google_available = False
        except Exception:
            google_available = False

        def _enqueue_propagation_retry(item, reason):
            qdir = Path('data') / 'queue' / 'propagation'
            qdir.mkdir(parents=True, exist_ok=True)
            rec = {'original_file': str(path), 'item': item, 'reason': reason, 'attempts': 0, 'next_run': datetime.now(timezone.utc).isoformat().replace('+00:00','Z')}
            qfile = qdir / f"prop_retry_{uuid4().hex}.json"
            try:
                save_sensitive_json(qfile, rec)
            except Exception:
                qfile.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding='utf-8')

        if sheets:
            for sid in sheets:
                if not google_available:
                    results['actions'].append({'service': 'google_sheets', 'id': sid, 'status': 'queued', 'reason': 'google_client_unavailable'})
                    _enqueue_propagation_retry({'service': 'google_sheets', 'id': sid}, 'google_client_unavailable')
                    continue
                try:
                    # example: delete spreadsheet file by id from Drive
                    creds = Credentials.from_service_account_file(creds_json, scopes=["https://www.googleapis.com/auth/drive"])
                    drive = build('drive', 'v3', credentials=creds)
                    drive.files().delete(fileId=sid).execute()
                    results['actions'].append({'service': 'google_sheets', 'id': sid, 'status': 'deleted'})
                except Exception as e:
                    results['actions'].append({'service': 'google_sheets', 'id': sid, 'status': 'error', 'error': str(e)})
                    _enqueue_propagation_retry({'service': 'google_sheets', 'id': sid}, str(e))

        if drive_ids:
            for did in drive_ids:
                if not google_available:
                    results['actions'].append({'service': 'google_drive', 'id': did, 'status': 'queued', 'reason': 'google_client_unavailable'})
                    _enqueue_propagation_retry({'service': 'google_drive', 'id': did}, 'google_client_unavailable')
                    continue
                try:
                    creds = Credentials.from_service_account_file(creds_json, scopes=["https://www.googleapis.com/auth/drive"])
                    drive = build('drive', 'v3', credentials=creds)
                    drive.files().delete(fileId=did).execute()
                    results['actions'].append({'service': 'google_drive', 'id': did, 'status': 'deleted'})
                except Exception as e:
                    results['actions'].append({'service': 'google_drive', 'id': did, 'status': 'error', 'error': str(e)})
                    _enqueue_propagation_retry({'service': 'google_drive', 'id': did}, str(e))
        # mark as processed and persist a processed artifact
        out = processed_dir / f"{path.stem}_processed.json"
        out.write_text(json.dumps({"original": str(path), "data": data, "results": results}, indent=2, ensure_ascii=False), encoding="utf-8")
        # remove original propagation request
        try:
            path.unlink()
        except Exception:
            pass
        # audit
        try:
            _append_audit_event(dsar_dir, "propagation_processed", {"file": str(out), "results": results})
        except Exception:
            pass
        return True
    except Exception:
        return False


async def _dsar_propagation_worker():
    import asyncio
    while True:
        try:
            dsar_dir = Path("data") / "dsar"
            if not dsar_dir.exists():
                await asyncio.sleep(5)
                continue
            for p in list(dsar_dir.glob("propagation_*.json")):
                try:
                    await _process_propagation_file(p)
                except Exception:
                    continue
        except Exception:
            pass
        await asyncio.sleep(15)


@app.on_event("startup")
async def _startup_event():
    """Essential non-blocking startup: perform quick writable checks then schedule background workers.
    Fail-fast on essential errors so the process won't accept traffic unsafely.
    """
    import asyncio
    logger = logging.getLogger('fbroom.startup')
    logger.info('Startup: running lightweight checks')

    def _check_dirs():
        for d in (UPLOAD_DIR, RECIPES_DIR, HISTORY_DIR, OUTPUTS_DIR, INSPECTIONS_DIR, JOBS_DIR, PRIVACY_DIR):
            d.mkdir(parents=True, exist_ok=True)
            # quick write test
            try:
                tf = d / '.startup_write_test'
                tf.write_text('', encoding='utf-8')
                tf.unlink()
            except Exception as e:
                raise

    try:
        await asyncio.to_thread(_check_dirs)
        # validate secrets and config (may raise in production)
        await asyncio.to_thread(_validate_essential_config)
        # ensure audit subsystem initialized and write a startup audit record
        await asyncio.to_thread(_ensure_audit_initialized)
        # validate consents/policies
        await asyncio.to_thread(_ensure_consents)
        # perform lightweight data migrations or validation
        await asyncio.to_thread(_perform_data_migrations)
    except Exception as e:
        logger.exception('Essential startup checks failed')
        # Fail-fast: re-raise to stop server from being marked ready
        raise

    # schedule background workers (their blocking IO is offloaded inside the worker implementations)
    try:
        asyncio.create_task(_dsar_propagation_worker())
        asyncio.create_task(_audit_export_worker())
        asyncio.create_task(_retry_queue_worker())
    except Exception:
        logger.exception('Failed to schedule background workers')

    app.state.ready = True
    logger.info('Startup complete; app is ready')


def _collect_audit_logs():
    """Collect all audit.log JSON-lines from data subdirectories into a list of records."""
    out = []
    try:
        data_root = Path("data")
        if not data_root.exists():
            return out
        for sub in data_root.iterdir():
            try:
                audit_file = sub / "audit.log"
                if audit_file.exists():
                    for line in audit_file.read_text(encoding='utf-8').splitlines():
                        try:
                            j = json.loads(line)
                            out.append({"source": str(audit_file), "record": j})
                        except Exception:
                            # try to treat legacy plain entries
                            try:
                                out.append({"source": str(audit_file), "record": json.loads(line)})
                            except Exception:
                                out.append({"source": str(audit_file), "raw": line})
            except Exception:
                continue
    except Exception:
        pass
    return out


def _sign_payload(payload_bytes: bytes) -> str:
    key = os.environ.get("AUDIT_HMAC_KEY")
    if not key:
        return ""
    try:
        import hmac, hashlib
        mac = hmac.new(key.encode('utf-8'), payload_bytes, hashlib.sha256).hexdigest()
        return mac
    except Exception:
        return ""


def _sync_urlopen_post(url: str, data: bytes, headers: dict = None, timeout: int = 30):
    """Synchronous helper to POST bytes to a URL and return status code.
    Intended to be called via `asyncio.to_thread` from async workers so blocking IO
    does not block the event loop.
    """
    from urllib.request import Request as URLRequest, urlopen
    req = URLRequest(url, data=data, method='POST')
    if headers:
        for k, v in (headers.items() if isinstance(headers, dict) else []):
            req.add_header(k, v)
    with urlopen(req, timeout=timeout) as resp:
        status = getattr(resp, 'status', None)
        if status is None:
            try:
                status = resp.getcode()
            except Exception:
                status = 200
        return status


def _validate_essential_config():
    """Validate that essential secrets and configuration are present.
    In `production` mode this will raise on missing or invalid secrets.
    Returns True on success.
    """
    env = os.environ.get('ENV')
    prod = env == 'production'
    logger = logging.getLogger('fbroom.startup.validate')

    # DATA_ENC_KEY required in production if any encrypted files will be used
    enc_key = os.environ.get('DATA_ENC_KEY') or os.environ.get('DATA_ENC_KEY_FILE')
    if prod and not enc_key:
        raise Exception('Missing DATA_ENC_KEY or DATA_ENC_KEY_FILE in production')
    # if provided, ensure fernet can be constructed
    if enc_key:
        f = _get_fernet()
        if not f:
            raise Exception('DATA_ENC_KEY provided but failed to initialize Fernet')

    # AUDIT_HMAC_KEY is required in production for tamper-evident audits
    if prod and not os.environ.get('AUDIT_HMAC_KEY'):
        raise Exception('Missing AUDIT_HMAC_KEY in production')

    # JWT secret should be explicitly set in production
    if prod and not os.environ.get('JWT_SECRET'):
        raise Exception('Missing JWT_SECRET in production')

    # Key rotation hint: if old key file present, log informational note
    old_key = os.environ.get('DATA_ENC_KEY_FILE_OLD')
    if old_key:
        logger.info('Detected legacy/rotation key file: DATA_ENC_KEY_FILE_OLD present')

    return True


def _ensure_audit_initialized():
    """Ensure audit directory exists and append a startup audit record.
    Returns True on success.
    """
    try:
        ad = Path('data') / 'audit'
        ad.mkdir(parents=True, exist_ok=True)
        # append a small startup event; _append_audit_event handles HMAC fallback
        try:
            _append_audit_event(ad, 'startup', {'ts': datetime.now(timezone.utc).isoformat().replace('+00:00','Z'), 'env': os.environ.get('ENV')})
        except Exception:
            # if appending fails, make this visible
            raise
        return True
    except Exception as e:
        raise


def _ensure_consents():
    """Validate that consent templates/policies are present or warn.
    In production, missing consents are a hard failure.
    """
    try:
        cons = Path('data') / 'consents'
        if not cons.exists() or not any(cons.glob('consent_*.json')):
            if os.environ.get('ENV') == 'production':
                raise Exception('No consent templates found in data/consents for production')
            else:
                logging.getLogger('fbroom.startup').warning('No consent templates found (dev only)')
        return True
    except Exception:
        raise


def _perform_data_migrations():
    """Perform or validate lightweight data migrations.
    Creates a marker file `data/.migrated_v1` after checks. If heavy migrations are required,
    this function should raise so ops can run separate maintenance.
    """
    try:
        marker = Path('data') / '.migrated_v1'
        if marker.exists():
            return True
        # Ensure queue and dsar directories exist
        for d in ('queue', 'dsar', 'audit'):
            p = Path('data') / d
            p.mkdir(parents=True, exist_ok=True)
        # write marker to indicate migrations validated
        try:
            marker.write_text(datetime.now(timezone.utc).isoformat().replace('+00:00','Z'), encoding='utf-8')
        except Exception:
            pass
        return True
    except Exception:
        raise


def _export_audit_once():
    """Collect audits, sign, POST to AUDIT_EXPORT_URL and optionally write to AUDIT_EXPORT_DIR.
    Returns dict with result metadata.
    """
    try:
        audits = _collect_audit_logs()
        payload = {"host": os.environ.get("HOSTNAME") or os.uname().nodename if hasattr(os, 'uname') else os.environ.get("HOSTNAME") or "local", "ts": datetime.now(timezone.utc).isoformat().replace('+00:00','Z'), "count": len(audits), "audits": audits}
        b = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        sig = _sign_payload(b)
        export_url = os.environ.get("AUDIT_EXPORT_URL")
        export_dir = os.environ.get("AUDIT_EXPORT_DIR")
        result = {"posted": False, "written": False, "url": export_url, "dir": export_dir}
        # write to local export dir if configured
        if export_dir:
            try:
                ed = Path(export_dir)
                ed.mkdir(parents=True, exist_ok=True)
                fname = ed / f"audit_export_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
                try:
                    saved = save_sensitive_bytes(fname, b)
                    result["written_path"] = str(saved)
                except Exception:
                    fname.write_bytes(b)
                    result["written_path"] = str(fname)
                # write signature file next to export
                try:
                    sig = _sign_payload(b)
                    sig_path = Path(str(fname) + '.sig')
                    try:
                        save_sensitive_bytes(sig_path, (sig or '').encode('utf-8'))
                        result['sig_path'] = str(sig_path) + ('.enc' if _get_fernet() else '')
                    except Exception:
                        sig_path.write_text(sig or '', encoding='utf-8')
                        result['sig_path'] = str(sig_path)
                except Exception:
                    pass
                result["written"] = True
            except Exception:
                pass
        # post to external endpoint if configured
        if export_url:
            try:
                headers = {'Content-Type': 'application/json'}
                if sig:
                    headers['X-Audit-Signature'] = sig
                # optional API key header
                exp_key = os.environ.get('AUDIT_EXPORT_KEY')
                if exp_key:
                    headers['X-Audit-Export-Key'] = exp_key
                try:
                    status = _sync_urlopen_post(export_url, b, headers, timeout=30)
                    result['posted'] = (200 <= status < 300)
                    result['status'] = status
                except Exception as e:
                    result['error'] = str(e)
                    # enqueue audit export for retry
            except Exception as e:
                result['error'] = str(e)
                # enqueue audit export for retry
                try:
                    qdir = Path('data') / 'queue' / 'audit_exports'
                    qdir.mkdir(parents=True, exist_ok=True)
                    qfile = qdir / f"audit_export_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex}.json"
                    qrec = {"payload": json.loads(b.decode('utf-8')), "attempts": 0, "last_error": str(e), "next_run": datetime.now(timezone.utc).isoformat().replace('+00:00','Z')}
                    try:
                        save_sensitive_json(qfile, qrec)
                    except Exception:
                        qfile.write_text(json.dumps(qrec, indent=2, ensure_ascii=False), encoding='utf-8')
                    result['queued'] = str(qfile)
                except Exception:
                    pass
        return result
    except Exception as e:
        return {"error": str(e)}


async def _audit_export_worker():
    import asyncio
    interval = int(os.environ.get('AUDIT_EXPORT_INTERVAL', '300'))
    while True:
        try:
            # Offload blocking export work to a thread so the event loop is not blocked
            await asyncio.to_thread(_export_audit_once)
        except Exception:
            pass
        await asyncio.sleep(interval)


async def _retry_queue_worker():
    import asyncio
    while True:
        try:
            qroot = Path('data') / 'queue'
            # process audit export retries
            adir = qroot / 'audit_exports'
            if adir.exists():
                for p in sorted(adir.iterdir()):
                    try:
                        j = load_sensitive_json(p) or json.loads(p.read_text(encoding='utf-8'))
                        # respect next_run
                        nr = j.get('next_run')
                        if nr:
                            try:
                                nr_dt = datetime.fromisoformat(nr.replace('Z', '+00:00'))
                            except Exception:
                                nr_dt = None
                            if nr_dt and nr_dt > datetime.now(timezone.utc):
                                continue
                        # attempt POST again
                        targ = os.environ.get('AUDIT_EXPORT_URL')
                        if not targ:
                            # nothing to do
                            continue
                        b = json.dumps(j.get('payload')).encode('utf-8')
                        sig = _sign_payload(b)
                        headers = {'Content-Type': 'application/json'}
                        if sig:
                            headers['X-Audit-Signature'] = sig
                        exp_key = os.environ.get('AUDIT_EXPORT_KEY')
                        if exp_key:
                            headers['X-Audit-Export-Key'] = exp_key
                        try:
                            status = await asyncio.to_thread(_sync_urlopen_post, targ, b, headers, 30)
                            if 200 <= status < 300:
                                try:
                                    p.unlink()
                                except Exception:
                                    pass
                                continue
                            else:
                                raise Exception(f'status:{status}')
                        except Exception as e:
                            # exponential backoff
                            at = j.get('attempts', 0) + 1
                            delay = min(3600, (2 ** at) * 10)
                            j['attempts'] = at
                            j['last_error'] = str(e)
                            j['next_run'] = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat().replace('+00:00','Z')
                            p.write_text(json.dumps(j, indent=2, ensure_ascii=False), encoding='utf-8')
                            continue
                    except Exception:
                        continue

            # process propagation retries
            pdir = qroot / 'propagation'
            if pdir.exists():
                for p in sorted(pdir.iterdir()):
                    try:
                        j = load_sensitive_json(p) or json.loads(p.read_text(encoding='utf-8'))
                        nr = j.get('next_run')
                        if nr:
                            try:
                                nr_dt = datetime.fromisoformat(nr.replace('Z', '+00:00'))
                            except Exception:
                                nr_dt = None
                            if nr_dt and nr_dt > datetime.now(timezone.utc):
                                continue
                        # attempt to process the item
                        item = j.get('item')
                        # create a temporary propagation file and run process
                        tmpdir = Path('data') / 'dsar' / 'retry_tmp'
                        tmpdir.mkdir(parents=True, exist_ok=True)
                        tmpf = tmpdir / f"prop_{uuid4().hex}.json"
                        tmpf.write_text(json.dumps({'google_sheets': [item.get('id')]} if item.get('service')=='google_sheets' else {'google_drive': [item.get('id')]}, ensure_ascii=False), encoding='utf-8')
                        # call processor
                        await _process_propagation_file(tmpf)
                        # on success, remove queue file and tmp file
                        try:
                            p.unlink()
                        except Exception:
                            pass
                        try:
                            tmpf.unlink()
                        except Exception:
                            pass
                    except Exception:
                        continue
        except Exception:
            pass
        await asyncio.sleep(20)



@app.get("/dsar/propagation")
def dsar_propagation_status(request: Request):
    try:
        # require admin
        user = _require_admin(request)
        try:
            _append_audit_event(Path('data') / 'dsar', 'admin_action', {'action': 'dsar_propagation_status', 'by': user.get('id') if user else None})
        except Exception:
            pass
        dsar_dir = Path("data") / "dsar"
        dsar_dir.mkdir(parents=True, exist_ok=True)
        pending = []
        for p in sorted(dsar_dir.glob("propagation_*.json*")):
            try:
                j = load_sensitive_json(p) or json.loads(p.read_text(encoding="utf-8"))
                pending.append({"file": p.name, "payload": j})
            except Exception:
                pending.append({"file": p.name, "payload": None})
        processed = []
        procdir = dsar_dir / "processed"
        if procdir.exists():
            for p in sorted(procdir.iterdir()):
                try:
                    j = load_sensitive_json(p) or json.loads(p.read_text(encoding="utf-8"))
                    processed.append({"file": p.name, "summary": j.get("results")})
                except Exception:
                    processed.append({"file": p.name, "summary": None})
        return {"pending": pending, "processed": processed}
    except HTTPException:
        # preserve explicit HTTP errors (e.g., permission denied)
        raise
    except Exception as e:
        try:
            log_dir = Path('data') / 'logs'
            log_dir.mkdir(parents=True, exist_ok=True)
            errfile = log_dir / 'dsar_propagation_error.log'
            with errfile.open('a', encoding='utf-8') as fh:
                fh.write(f"[{datetime.now(timezone.utc).isoformat()}] {repr(e)}\n")
                import traceback as _tb
                fh.write(_tb.format_exc())
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Failed to list propagation status: {str(e)}")


@app.post("/audit/export")
def audit_export(request: Request):
    """Trigger an immediate audit export. Requires admin role or valid export key header."""
    try:
        user = None
        try:
            user = _require_admin(request)
        except HTTPException:
            # allow export via AUDIT_EXPORT_KEY header as fallback
            hdr = request.headers.get('X-Audit-Export-Key')
            if not hdr or hdr != os.environ.get('AUDIT_EXPORT_KEY'):
                raise
            user = {'id': 'system'}
        try:
            _append_audit_event(Path('data') / 'audit', 'admin_action', {'action': 'audit_export', 'by': user.get('id') if user else None})
        except Exception:
            pass
        res = _export_audit_once()
        return {"ok": True, "result": res}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/queue")
def admin_queue_list(request: Request, page: Optional[int] = None, page_size: Optional[int] = None, filter: Optional[str] = None, service: Optional[str] = 'all'):
    """List queued retry items. Supports optional server-side pagination and filtering:
    - `page` (1-based) and `page_size` for pagination
    - `filter` substring to match name/path/meta
    - `service` one of `all`, `audit`, `prop` to filter item types
    If pagination params are omitted, returns full lists in `audit_exports` and `propagation` for compatibility.
    """
    try:
        user = _require_admin(request)
        qroot = Path('data') / 'queue'
        audit_list = []
        prop_list = []
        adir = qroot / 'audit_exports'
        if adir.exists():
            for p in sorted(adir.iterdir()):
                try:
                    j = load_sensitive_json(p) or json.loads(p.read_text(encoding='utf-8'))
                except Exception:
                    j = None
                audit_list.append({"name": p.name, "path": str(p), "meta": j, "_type": 'audit'})
        pdir = qroot / 'propagation'
        if pdir.exists():
            for p in sorted(pdir.iterdir()):
                try:
                    j = load_sensitive_json(p) or json.loads(p.read_text(encoding='utf-8'))
                except Exception:
                    j = None
                prop_list.append({"name": p.name, "path": str(p), "meta": j, "_type": 'propagation'})

        # Combined list for filtering/pagination
        combined = audit_list + prop_list
        # apply simple filter if requested
        ftxt = (filter or '').lower().strip()
        if ftxt:
            def match_item(it):
                if ftxt in (it.get('name') or '').lower():
                    return True
                if ftxt in (it.get('path') or '').lower():
                    return True
                try:
                    if ftxt in json.dumps(it.get('meta') or {}).lower():
                        return True
                except Exception:
                    pass
                return False
            combined = [it for it in combined if match_item(it)]

        # apply service filter if requested
        if service and service != 'all':
            if service == 'audit':
                combined = [it for it in combined if it.get('_type') == 'audit']
            elif service == 'prop':
                combined = [it for it in combined if it.get('_type') == 'propagation']

        # if pagination requested, slice and return a paginated response
        if page is not None and page_size is not None:
            try:
                page_i = max(1, int(page))
                psize = max(1, int(page_size))
            except Exception:
                raise HTTPException(status_code=400, detail='Invalid page or page_size')
            total = len(combined)
            pages = max(1, (total + psize - 1) // psize)
            page_i = min(page_i, pages)
            start = (page_i - 1) * psize
            end = start + psize
            slice_items = combined[start:end]
            try:
                _append_audit_event(Path('data') / 'audit', 'admin_action', {'action': 'admin_queue_list', 'by': user.get('id') if user else None, 'page': page_i, 'page_size': psize, 'filter': filter, 'service': service})
            except Exception:
                pass
            return {
                'queue': {
                    'items': slice_items,
                    'total': total,
                    'page': page_i,
                    'pages': pages,
                    'page_size': psize,
                }
            }

        # default behavior: return full lists for compatibility
        try:
            _append_audit_event(Path('data') / 'audit', 'admin_action', {'action': 'admin_queue_list', 'by': user.get('id') if user else None})
        except Exception:
            pass
        return {"audit_exports": audit_list, "propagation": prop_list}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post('/admin/queue/run')
def admin_queue_run(request: Request, payload: dict):
    try:
        user = _require_admin(request)
        path_str = payload.get('path')
        if not path_str:
            raise HTTPException(status_code=400, detail='Missing path')
        # ensure path is under data/queue
        qroot = Path('data') / 'queue'
        target = Path(path_str)
        try:
            target = target if target.is_absolute() else Path(str(target))
            # canonicalize relative to workspace
            resolved = (Path.cwd() / target).resolve()
        except Exception:
            resolved = target
        if str(resolved).find(str((Path.cwd() / qroot).resolve())) != 0:
            raise HTTPException(status_code=400, detail='Invalid path')
        if not resolved.exists():
            raise HTTPException(status_code=404, detail='Queue item not found')
        # read item (supports encrypted queue files)
        try:
            j = load_sensitive_json(resolved) or json.loads(resolved.read_text(encoding='utf-8'))
        except Exception as e:
            raise HTTPException(status_code=400, detail='Invalid queue item')
        # audit
        try:
            _append_audit_event(Path('data') / 'audit', 'admin_action', {'action': 'admin_queue_run', 'file': str(resolved), 'by': user.get('id') if user else None})
        except Exception:
            pass
        # dispatch based on content
        if j.get('payload') is not None:
            # audit export retry
            targ = os.environ.get('AUDIT_EXPORT_URL')
            if not targ:
                raise HTTPException(status_code=400, detail='No AUDIT_EXPORT_URL configured')
            b = json.dumps(j.get('payload')).encode('utf-8')
            sig = _sign_payload(b)
            headers = {'Content-Type': 'application/json'}
            if sig:
                headers['X-Audit-Signature'] = sig
            exp_key = os.environ.get('AUDIT_EXPORT_KEY')
            if exp_key:
                headers['X-Audit-Export-Key'] = exp_key
            try:
                status = _sync_urlopen_post(targ, b, headers, timeout=30)
                ok = (200 <= status < 300)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f'Post failed: {e}')
            if ok:
                try:
                    resolved.unlink()
                except Exception:
                    pass
                return {'ok': True, 'status': 'posted'}
            raise HTTPException(status_code=500, detail='Failed to post')
        elif j.get('item') is not None:
            # propagation retry
            item = j.get('item')
            tmpdir = Path('data') / 'dsar' / 'retry_tmp'
            tmpdir.mkdir(parents=True, exist_ok=True)
            tmpf = tmpdir / f"prop_admin_{uuid4().hex}.json"
            try:
                if item.get('service') == 'google_sheets':
                    tmpf.write_text(json.dumps({'google_sheets': [item.get('id')]}, ensure_ascii=False), encoding='utf-8')
                else:
                    tmpf.write_text(json.dumps({'google_drive': [item.get('id')]}, ensure_ascii=False), encoding='utf-8')
                import asyncio
                ok = asyncio.run(_process_propagation_file(tmpf))
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
            try:
                if ok:
                    resolved.unlink()
                    try:
                        tmpf.unlink()
                    except Exception:
                        pass
                    return {'ok': True, 'status': 'processed'}
            except Exception:
                pass
            raise HTTPException(status_code=500, detail='Processing failed')
        else:
            raise HTTPException(status_code=400, detail='Unknown queue item')
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete('/admin/queue')
def admin_queue_delete(request: Request, payload: dict):
    try:
        user = _require_admin(request)
        path_str = payload.get('path')
        if not path_str:
            raise HTTPException(status_code=400, detail='Missing path')
        target = Path(path_str)
        try:
            resolved = (Path.cwd() / target).resolve()
        except Exception:
            resolved = target
        qroot = Path('data') / 'queue'
        if str(resolved).find(str((Path.cwd() / qroot).resolve())) != 0:
            raise HTTPException(status_code=400, detail='Invalid path')
        if resolved.exists():
            try:
                resolved.unlink()
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        try:
            _append_audit_event(Path('data') / 'audit', 'admin_action', {'action': 'admin_queue_delete', 'file': str(resolved), 'by': user.get('id') if user else None})
        except Exception:
            pass
        return {'ok': True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/team/invite")
def team_invite(payload: dict, request: Request):
    try:
        user = _require_auth(request)
        email = payload.get("email")
        role = payload.get("role") or "member"
        if not email:
            raise HTTPException(status_code=400, detail="Missing invite email")
        inviter = user
        team_name = inviter.get("team_name") or payload.get("team_name") or inviter.get("username") + "'s team"
        token = _make_invite_token(inviter.get("id"), email, team_name)
        # persist invite record
        invdir = Path("data") / "invites"
        invdir.mkdir(parents=True, exist_ok=True)
        invfile = invdir / f"invite_{uuid4().hex}.json"
        invfile.write_text(json.dumps({"inviter": inviter.get("id"), "email": email, "role": role, "team": team_name, "token": token, "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00','Z')}, indent=2), encoding="utf-8")
        # send email
        try:
            link = os.environ.get("APP_URL", "http://localhost:3000") + f"/accept-invite?token={token}"
            _send_email_message(email, "You are invited to FalconBroom", f"You were invited to join {team_name}. Accept: {link}")
        except Exception:
            pass
        _consent_audit("team_invite", {"inviter": inviter.get("id"), "email": email, "team": team_name})
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/team/accept")
def team_accept(payload: dict):
    try:
        token = payload.get("token")
        username = payload.get("username")
        password = payload.get("password")
        if not token:
            raise HTTPException(status_code=400, detail="Missing token")
        jwt_secret = JWT_SECRET
        jwt_algo = JWT_ALGO
        try:
            data = jwt.decode(token, jwt_secret, algorithms=[jwt_algo])
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid or expired token")
        if data.get("purpose") != "invite":
            raise HTTPException(status_code=400, detail="Invalid token purpose")
        email = data.get("email")
        inviter_id = data.get("inviter")
        team_name = data.get("team")
        # find or create user for invitee
        existing = _find_user_by_email(email)
        if existing:
            # add to inviter's team
            inviter = _get_user_by_id(inviter_id)
            if not inviter:
                raise HTTPException(status_code=404, detail="Inviter not found")
            inviter.setdefault("team_name", team_name)
            members = inviter.setdefault("team_members", [])
            if email not in members:
                members.append(email)
            p = _users_dir() / f"{inviter.get('id')}.json"
            p.write_text(json.dumps(inviter, indent=2, ensure_ascii=False), encoding="utf-8")
            _consent_audit("team_accept", {"inviter": inviter.get("id"), "added": email})
            return {"ok": True}
        # create new user with supplied username/password
        if not username or not password:
            raise HTTPException(status_code=400, detail="Missing username/password for account creation")
        new_user = _create_user(username, email, password)
        # add to inviter's team
        inviter = _get_user_by_id(inviter_id)
        if inviter:
            inviter.setdefault("team_name", team_name)
            members = inviter.setdefault("team_members", [])
            if email not in members:
                members.append(email)
            p = _users_dir() / f"{inviter.get('id')}.json"
            p.write_text(json.dumps(inviter, indent=2, ensure_ascii=False), encoding="utf-8")
        _consent_audit("team_accept", {"inviter": inviter_id, "new_user": new_user.get('id')})
        tokens = _create_session_tokens(new_user.get("id"), persistent=True)
        # set cookie via response semantics not available here; return access token and client should call /login
        return {"access_token": tokens.get("access_token"), "user_id": new_user.get("id")}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/uploads/{name}/share")
def share_upload(name: str, payload: dict, request: Request):
    """Mark an uploaded file as shared with the team (or unshare).
    Persists a small .meta.json file beside the upload to record sharing metadata.
    """
    try:
        user = _require_auth(request)
        shared = bool(payload.get("shared"))
        upath = UPLOAD_DIR / name
        if not upath.exists():
            raise HTTPException(status_code=404, detail="Upload not found")
        # enforce opt-out: do not allow sharing if user opted-out of sale/targeting
        if _is_opted_out(user.get('id'), user.get('email')) and shared:
            _privacy_audit("prevent_share_opt_out", {"user": user.get('id'), "email": user.get('email'), "file": str(upath)})
            raise HTTPException(status_code=403, detail="User has opted out of sharing/sale/targeting")

        meta = {
            "shared_with_team": shared,
            "shared_by": user.get("id"),
            "shared_at": datetime.now(timezone.utc).isoformat().replace('+00:00','Z') if shared else None,
        }
        try:
            meta_path = upath.parent / (upath.name + ".meta.json")
            existing = {}
            if meta_path.exists():
                try:
                    existing = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    existing = {}
            existing.update(meta)
            meta_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        _consent_audit("upload_share", {"user": user.get("id"), "file": str(upath), "shared": shared})
        _privacy_audit("upload_share", {"user": user.get("id"), "file": str(upath), "shared": shared})
        try:
            import asyncio
            asyncio.create_task(_broadcast_shared_update_message({"type": "shared_changed", "file": str(upath), "shared": shared}))
        except Exception:
            pass
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/uploads/shared")
def list_shared_uploads():
    out = []
    try:
        for p in UPLOAD_DIR.iterdir():
            try:
                if not p.is_file():
                    continue
                meta = p.parent / (p.name + ".meta.json")
                if not meta.exists():
                    continue
                mj = json.loads(meta.read_text(encoding="utf-8"))
                if mj.get("shared_with_team"):
                    st = p.stat()
                    out.append({"name": p.name, "path": str(p), "size": st.st_size, "modified_at": datetime.utcfromtimestamp(st.st_mtime).isoformat() + "Z", "shared_by": mj.get("shared_by"), "shared_at": mj.get("shared_at")})
            except Exception:
                continue
    except Exception:
        pass
    return {"uploads": sorted(out, key=lambda r: r.get("shared_at") or "", reverse=True)}


@app.post("/privacy/optout")
def privacy_optout(payload: dict, request: Request):
    try:
        # allow anonymous opt-out by email or authenticated user
        user = None
        try:
            user = _require_auth(request)
        except Exception:
            user = None
        email = payload.get("email") or (user and user.get("email"))
        user_id = payload.get("user_id") or (user and user.get("id"))
        reason = payload.get("reason") or payload.get("note")
        if not email and not user_id:
            raise HTTPException(status_code=400, detail="Provide email or user_id to opt out")
        outs = _load_privacy_json("opt_outs.json", []) or []
        rec = {"id": f"opt_{uuid4().hex[:8]}", "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00','Z'), "user_id": user_id, "email": email, "reason": reason}
        outs.insert(0, rec)
        _save_privacy_json("opt_outs.json", outs[:1000])
        _privacy_audit("opt_out", rec)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/privacy/optin")
def privacy_optin(payload: dict, request: Request):
    try:
        user = None
        try:
            user = _require_auth(request)
        except Exception:
            user = None
        email = payload.get("email") or (user and user.get("email"))
        user_id = payload.get("user_id") or (user and user.get("id"))
        if not email and not user_id:
            raise HTTPException(status_code=400, detail="Provide email or user_id to opt in")
        outs = _load_privacy_json("opt_outs.json", []) or []
        next_outs = [o for o in outs if not ((user_id and str(o.get("user_id")) == str(user_id)) or (email and o.get("email") and str(o.get("email")).lower() == str(email).lower()))]
        _save_privacy_json("opt_outs.json", next_outs[:1000])
        _privacy_audit("opt_in", {"user_id": user_id, "email": email})
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/privacy/inventory")
def privacy_inventory_list():
    inv = _load_privacy_json("inventory.json", []) or []
    return {"inventory": inv}


@app.post("/privacy/inventory")
def privacy_inventory_add(payload: dict):
    try:
        inv = _load_privacy_json("inventory.json", []) or []
        entry = payload or {}
        entry.setdefault("id", f"inv_{uuid4().hex[:8]}")
        entry.setdefault("created_at", datetime.now(timezone.utc).isoformat().replace('+00:00','Z'))
        inv.insert(0, entry)
        _save_privacy_json("inventory.json", inv[:2000])
        _privacy_audit("inventory_add", entry)
        return {"ok": True, "entry": entry}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/privacy/inventory/{inv_id}")
def privacy_inventory_delete(inv_id: str):
    try:
        inv = _load_privacy_json("inventory.json", []) or []
        next_inv = [i for i in inv if str(i.get("id")) != str(inv_id)]
        _save_privacy_json("inventory.json", next_inv[:2000])
        _privacy_audit("inventory_delete", {"id": inv_id})
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/privacy/inventory/{inv_id}")
def privacy_inventory_update(inv_id: str, payload: dict):
    try:
        inv = _load_privacy_json("inventory.json", []) or []
        updated = False
        for i, it in enumerate(inv):
            if str(it.get("id")) == str(inv_id):
                new = {**it, **(payload or {})}
                new.setdefault("id", it.get("id"))
                inv[i] = new
                updated = True
                break
        if not updated:
            raise HTTPException(status_code=404, detail="Inventory item not found")
        _save_privacy_json("inventory.json", inv[:2000])
        _privacy_audit("inventory_update", {"id": inv_id, "payload": payload})
        return {"ok": True, "entry": new}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/privacy/dpias")
def privacy_dpias():
    items = _load_privacy_json("dpias.json", []) or []
    return {"dpias": items}


@app.post("/privacy/dpias")
def privacy_add_dpia(payload: dict):
    try:
        items = _load_privacy_json("dpias.json", []) or []
        item = payload or {}
        item.setdefault("id", f"dpia_{uuid4().hex[:8]}")
        item.setdefault("created_at", datetime.now(timezone.utc).isoformat().replace('+00:00','Z'))
        items.insert(0, item)
        _save_privacy_json("dpias.json", items[:500])
        _privacy_audit("dpia_add", item)
        return {"ok": True, "dpia": item}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/privacy/dpias/{dpia_id}")
def privacy_delete_dpia(dpia_id: str):
    try:
        items = _load_privacy_json("dpias.json", []) or []
        next_items = [i for i in items if str(i.get("id")) != str(dpia_id)]
        _save_privacy_json("dpias.json", next_items[:500])
        _privacy_audit("dpia_delete", {"id": dpia_id})
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/privacy/dpias/{dpia_id}")
def privacy_update_dpia(dpia_id: str, payload: dict):
    try:
        items = _load_privacy_json("dpias.json", []) or []
        updated = False
        for idx, it in enumerate(items):
            if str(it.get("id")) == str(dpia_id):
                new = {**it, **(payload or {})}
                new.setdefault("id", it.get("id"))
                items[idx] = new
                updated = True
                break
        if not updated:
            raise HTTPException(status_code=404, detail="DPIA not found")
        _save_privacy_json("dpias.json", items[:500])
        _privacy_audit("dpia_update", {"id": dpia_id, "payload": payload})
        return {"ok": True, "dpia": new}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/privacy/record")
def privacy_record(payload: dict):
    try:
        _privacy_audit("record", payload or {})
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/shared")
async def ws_shared(websocket: WebSocket):
    await websocket.accept()
    _connected_ws.add(websocket)
    try:
        # send initial state
        try:
            init = list_shared_uploads()
            await websocket.send_text(json.dumps({"type": "init", "uploads": init.get("uploads", [])}, default=str))
        except Exception:
            pass
        while True:
            # keep connection alive; accept pings from client
            await websocket.receive_text()
    except WebSocketDisconnect:
        _connected_ws.discard(websocket)
    except Exception:
        _connected_ws.discard(websocket)


@app.get("/me")
def me(request: Request):
    try:
        user = _require_auth(request)
        # don't expose password data
        safe = {k: v for k, v in user.items() if k not in ("pw_hash", "pw_salt")}
        return safe
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/team/invites")
def list_team_invites(request: Request):
    try:
        user = _require_auth(request)
        invdir = Path("data") / "invites"
        out = []
        if not invdir.exists():
            return {"invites": []}
        for p in invdir.iterdir():
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
                # include file id so clients can act on invites
                j["id"] = p.name
                # decode token for expiry and normalize metadata
                try:
                    token = j.get("token")
                    if token and ".." not in token:
                        jwt_secret = JWT_SECRET
                        jwt_algo = JWT_ALGO
                        try:
                            tk = jwt.decode(token, jwt_secret, algorithms=[jwt_algo], options={"verify_exp": False})
                            j["token_payload"] = {"exp": tk.get("exp"), "iat": tk.get("iat"), "jti": tk.get("jti")}
                        except Exception:
                            j["token_payload"] = None
                except Exception:
                    j["token_payload"] = None

                # attach inviter info when available
                try:
                    inv = _get_user_by_id(j.get("inviter"))
                    if inv:
                        j["inviter_username"] = inv.get("username")
                        j["inviter_email"] = inv.get("email")
                except Exception:
                    pass

                # determine canonical owner for the team and whether current user can manage this invite
                owner = _find_team_owner_by_name(j.get("team"))
                can_manage = False
                if owner and owner.get("id") == user.get("id"):
                    can_manage = True
                if j.get("inviter") == user.get("id"):
                    can_manage = True

                j["can_manage"] = can_manage

                # include invites created by this user, invites for this user's team, or invites targeting this user's email
                if j.get("inviter") == user.get("id"):
                    out.append(j)
                    continue
                if user.get("team_name") and j.get("team") == user.get("team_name"):
                    out.append(j)
                    continue
                if user.get("email") and j.get("email") == user.get("email"):
                    out.append(j)
                    continue
            except Exception:
                continue
        return {"invites": sorted(out, key=lambda x: x.get("created_at") or "", reverse=True)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/team/members")
def list_team_members(request: Request):
    try:
        user = _require_auth(request)
        # determine owner of the team
        owner = None
        if user.get("team_name"):
            owner = user
        else:
            # find any user that lists this user's email in team_members
            try:
                udir = _users_dir()
                for p in udir.glob("*.json"):
                    try:
                        uu = json.loads(p.read_text(encoding="utf-8"))
                        members = uu.get("team_members") or []
                        if user.get("email") and user.get("email") in members:
                            owner = uu
                            break
                    except Exception:
                        continue
            except Exception:
                owner = None

        if not owner:
            # no team found
            return {"team_name": None, "members": []}

        team_name = owner.get("team_name")
        members = []
        # owner entry
        members.append({"id": owner.get("id"), "username": owner.get("username"), "email": owner.get("email"), "role": "owner"})
        # include explicit team_members (emails)
        for email in (owner.get("team_members") or []):
            try:
                u = _find_user_by_email(email)
                if u:
                    members.append({"id": u.get("id"), "username": u.get("username"), "email": u.get("email"), "role": "member"})
                else:
                    members.append({"id": None, "username": None, "email": email, "role": "member"})
            except Exception:
                members.append({"id": None, "username": None, "email": email, "role": "member"})

        return {"team_name": team_name, "members": members}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/team/invites/{invite_id}")
def revoke_invite(invite_id: str, request: Request):
    try:
        user = _require_auth(request)
        invdir = Path("data") / "invites"
        p = invdir / invite_id
        if not p.exists():
            raise HTTPException(status_code=404, detail="Invite not found")
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            j = {}
        # allow revocation if requester is the inviter or the canonical team owner
        allowed = False
        if j.get("inviter") == user.get("id"):
            allowed = True
        owner = _find_team_owner_by_name(j.get("team"))
        if owner and owner.get("id") == user.get("id"):
            allowed = True
        if not allowed:
            raise HTTPException(status_code=403, detail="Not allowed to revoke invite")
        try:
            p.unlink()
        except Exception:
            pass
        _consent_audit("team_invite_revoked", {"by": user.get("id"), "invite": j})
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/team/invite/decline")
def decline_invite(payload: dict):
    try:
        token = payload.get("token")
        if not token:
            raise HTTPException(status_code=400, detail="Missing token")
        jwt_secret = JWT_SECRET
        jwt_algo = JWT_ALGO
        try:
            data = jwt.decode(token, jwt_secret, algorithms=[jwt_algo])
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid or expired token")
        if data.get("purpose") != "invite":
            raise HTTPException(status_code=400, detail="Invalid token purpose")
        # find matching invite file and remove it
        invdir = Path("data") / "invites"
        removed = False
        if invdir.exists():
            for p in invdir.iterdir():
                try:
                    j = json.loads(p.read_text(encoding="utf-8"))
                    if j.get("token") == token:
                        try:
                            p.unlink()
                        except Exception:
                            pass
                        _consent_audit("team_invite_declined", {"token": token, "email": data.get("email")})
                        removed = True
                        break
                except Exception:
                    continue
        if not removed:
            raise HTTPException(status_code=404, detail="Invite not found")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/team/members")
def update_team_member(payload: dict, request: Request):
    try:
        user = _require_auth(request)
        # restrict to canonical team owner for this user's team
        if not user.get("team_name"):
            raise HTTPException(status_code=403, detail="Only team owners can modify members")
        owner = _find_team_owner_by_name(user.get("team_name"))
        if not owner or owner.get("id") != user.get("id"):
            raise HTTPException(status_code=403, detail="Only team owners can modify members")
        action = payload.get("action")
        target_email = payload.get("email")
        if not target_email:
            raise HTTPException(status_code=400, detail="Missing target email")
        owner = user
        # load owner record, modify team_members and team_roles stored on owner
        changed = False
        if action == "remove":
            members = owner.setdefault("team_members", [])
            if target_email in members:
                members.remove(target_email)
                changed = True
            # also remove any stored role
            roles = owner.setdefault("team_roles", {})
            if roles.pop(target_email, None) is not None:
                changed = True
            if changed:
                p = _users_dir() / f"{owner.get('id')}.json"
                p.write_text(json.dumps(owner, indent=2, ensure_ascii=False), encoding="utf-8")
                _consent_audit("team_member_removed", {"by": owner.get("id"), "removed": target_email})
            return {"ok": True}
        elif action == "update_role":
            role = payload.get("role")
            if not role:
                raise HTTPException(status_code=400, detail="Missing role")
            roles = owner.setdefault("team_roles", {})
            roles[target_email] = role
            p = _users_dir() / f"{owner.get('id')}.json"
            p.write_text(json.dumps(owner, indent=2, ensure_ascii=False), encoding="utf-8")
            _consent_audit("team_member_role_updated", {"by": owner.get('id'), "email": target_email, "role": role})
            return {"ok": True}
        else:
            raise HTTPException(status_code=400, detail="Unknown action")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/team/owners")
def manage_team_owner(payload: dict, request: Request):
    try:
        user = _require_auth(request)
        # only canonical owner may manage owners
        if not user.get("team_name"):
            raise HTTPException(status_code=403, detail="Only team owners can manage owners")
        owner = _find_team_owner_by_name(user.get("team_name"))
        if not owner or owner.get("id") != user.get("id"):
            raise HTTPException(status_code=403, detail="Only team owners can manage owners")

        action = (payload.get("action") or "").lower()
        email = payload.get("email")
        if not action or not email:
            raise HTTPException(status_code=400, detail="Missing action or email")

        target = _find_user_by_email(email)
        if not target:
            raise HTTPException(status_code=404, detail="Target user not found")

        # Promote: mark target as team_owner and ensure team_name set
        if action == "promote":
            target["team_name"] = owner.get("team_name")
            target["team_owner"] = True
            p = _users_dir() / f"{target.get('id')}.json"
            p.write_text(json.dumps(target, indent=2, ensure_ascii=False), encoding="utf-8")
            # ensure target is listed in owner's team_members
            members = owner.setdefault("team_members", [])
            if target.get("email") and target.get("email") not in members:
                members.append(target.get("email"))
                po = _users_dir() / f"{owner.get('id')}.json"
                po.write_text(json.dumps(owner, indent=2, ensure_ascii=False), encoding="utf-8")
            _consent_audit("team_owner_promoted", {"by": owner.get("id"), "promoted": target.get("email")})
            return {"ok": True}

        # Demote: unset team_owner flag (cannot demote self)
        if action == "demote":
            if target.get("id") == owner.get("id"):
                raise HTTPException(status_code=400, detail="Owner cannot demote themselves")
            # ensure at least one owner remains for the team
            owners = []
            try:
                udir = _users_dir()
                for pth in udir.glob("*.json"):
                    try:
                        uu = json.loads(pth.read_text(encoding="utf-8"))
                        if uu.get("team_name") == owner.get("team_name") and uu.get("team_owner"):
                            owners.append(uu)
                    except Exception:
                        continue
            except Exception:
                owners = [owner]
            if len(owners) <= 1:
                raise HTTPException(status_code=400, detail="Cannot demote the last owner; transfer ownership first")
            target["team_owner"] = False
            p = _users_dir() / f"{target.get('id')}.json"
            p.write_text(json.dumps(target, indent=2, ensure_ascii=False), encoding="utf-8")
            try:
                _send_email_message(target.get("email"), "Your owner role was removed", f"{owner.get('username')} has removed your owner role for team {owner.get('team_name')}")
            except Exception:
                pass
            _consent_audit("team_owner_demoted", {"by": owner.get("id"), "demoted": target.get("email")})
            return {"ok": True}

        # Transfer ownership: atomic swap owner -> target
        if action == "transfer":
            if target.get("id") == owner.get("id"):
                raise HTTPException(status_code=400, detail="Cannot transfer ownership to yourself")
            # set target as owner and unset current owner atomically
            try:
                # assign team_name and owner flag to target
                target["team_name"] = owner.get("team_name")
                target["team_owner"] = True
                p_target = _users_dir() / f"{target.get('id')}.json"
                p_target.write_text(json.dumps(target, indent=2, ensure_ascii=False), encoding="utf-8")
                # unset owner flag on current owner
                owner["team_owner"] = False
                p_owner = _users_dir() / f"{owner.get('id')}.json"
                p_owner.write_text(json.dumps(owner, indent=2, ensure_ascii=False), encoding="utf-8")
                # notify both parties
                try:
                    _send_email_message(target.get("email"), "You are now a team owner", f"You have been made an owner of team {owner.get('team_name')} by {owner.get('username')}")
                except Exception:
                    pass
                try:
                    _send_email_message(owner.get("email"), "Ownership transferred", f"You transferred ownership of team {owner.get('team_name')} to {target.get('username')}")
                except Exception:
                    pass
                _consent_audit("team_owner_transferred", {"from": owner.get("id"), "to": target.get("id")})
                return {"ok": True}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        raise HTTPException(status_code=400, detail="Unknown action")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/users/{user_id}/role")
def admin_set_user_role(user_id: str, payload: dict, request: Request):
    """Admin-only endpoint to set a user's role or is_admin flag.

    Body: { role: 'member'|'admin' }
    """
    try:
        _require_admin(request)
        role = (payload.get('role') or '').strip() if payload else ''
        if not role:
            raise HTTPException(status_code=400, detail='Missing role')
        if role not in ('member', 'admin'):
            raise HTTPException(status_code=400, detail='Invalid role')
        user = _get_user_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail='User not found')
        user['role'] = role
        user['is_admin'] = True if role == 'admin' else False
        p = _users_dir() / f"{user_id}.json"
        p.write_text(json.dumps(user, indent=2, ensure_ascii=False), encoding='utf-8')
        _consent_audit('admin_set_user_role', {'by': (request and getattr(request, 'client', None) and getattr(request.client, 'host', None)) or None, 'user_id': user_id, 'role': role})
        safe = {k: v for k, v in user.items() if k not in ('pw_hash', 'pw_salt')}
        return {'ok': True, 'user': safe}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/users")
def admin_list_users(q: Optional[str] = None, limit: int = 100, request: Request = None):
    """Admin-only: list users.

    Query params:
    - q: optional substring to search in id, username, or email
    - limit: max number of users to return
    """
    try:
        _require_admin(request)
        users = []
        udir = _users_dir()
        for p in sorted(udir.glob('*.json'), reverse=True):
            try:
                u = json.loads(p.read_text(encoding='utf-8'))
            except Exception:
                continue
            if q:
                qq = q.lower()
                if not (qq in (u.get('id') or '').lower() or qq in (u.get('username') or '').lower() or qq in (u.get('email') or '').lower()):
                    continue
            safe = {k: v for k, v in u.items() if k not in ('pw_hash', 'pw_salt')}
            users.append(safe)
            if len(users) >= int(limit or 100):
                break
        return {'ok': True, 'users': users}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/account/export")
def account_export(request: Request):
    try:
        user = _require_auth(request)
        # enqueue export job scoped to this user
        return enqueue_export_job(user_id=user.get("id"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/account/delete")
def account_delete(spec: DeleteAccountRequest, request: Request):
    try:
        # Utah-style verifiable request: require password and explicit confirm text
        user = _require_auth(request)
        if spec.confirm_text.strip() != "DELETE MY ACCOUNT":
            raise HTTPException(status_code=400, detail="Confirmation text mismatch; type exactly: DELETE MY ACCOUNT")
        if not _verify_password(spec.password, user.get("pw_salt"), user.get("pw_hash")):
            raise HTTPException(status_code=401, detail="Invalid password")
        # enqueue deletion of consents for this user
        resp = enqueue_delete_job(user_id=user.get("id"))
        # remove user record and sessions
        try:
            p = _users_dir() / f"{user.get('id')}.json"
            if p.exists():
                p.unlink()
        except Exception:
            pass
        # remove all sessions for user
        for s in _sessions_dir().glob("session_*.json"):
            try:
                data = json.loads(s.read_text(encoding="utf-8"))
                if data.get("user_id") == user.get("id"):
                    s.unlink()
            except Exception:
                continue
        _consent_audit("account_delete_requested", {"user_id": user.get("id")})
        return resp
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/account")
def account_update(payload: dict, request: Request):
    try:
        user = _require_auth(request)
        # allow updating email and team info
        allowed = {"email", "team_name", "team_members"}
        changed = False
        for k, v in payload.items():
            if k in allowed:
                user[k] = v
                changed = True
        if changed:
            p = _users_dir() / f"{user.get('id')}.json"
            p.write_text(json.dumps(user, indent=2, ensure_ascii=False), encoding="utf-8")
            _consent_audit("account_update", {"user_id": user.get("id"), "changes": {k: payload.get(k) for k in payload if k in allowed}})
        safe = {kk: vv for kk, vv in user.items() if kk not in ("pw_hash", "pw_salt")}
        return safe
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/preview")
def preview_recipe(recipe: Recipe, recipe_id: Optional[str] = None, n: Optional[int] = None):
    try:
        # Debug: log incoming recipe payload (truncated) and recipe_id
        try:
            raw = recipe.json() if hasattr(recipe, 'json') else str(recipe)
            logger.debug("INCOMING /preview payload (recipe_id=%s): %s", recipe_id, raw)
        except Exception:
            logger.debug("INCOMING /preview payload (recipe_id=%s): <unserializable recipe>", recipe_id)
        # validate source path early to provide clearer errors to the UI
        src = recipe.sources[0]["path"] if recipe.sources else None
        if not src:
            raise HTTPException(status_code=400, detail="Recipe missing source path")
        if not Path(src).exists():
            raise HTTPException(status_code=400, detail=f"Source path not found: {src}")
        # if UI sent empty cleaning_steps, attempt to fallback to last generated recipe for this source
        try:
            cs = None
            try:
                cs = recipe.cleaning_steps
            except Exception:
                try:
                    cs = recipe.get('cleaning_steps')
                except Exception:
                    cs = None
            if (cs is None) or (isinstance(cs, (list, tuple)) and len(cs) == 0):
                cached = app.state.generated_recipes_cache.get(src)
                if cached and isinstance(cached, dict) and cached.get('cleaning_steps'):
                    # convert cached dict to Recipe model if needed
                    try:
                        new_recipe = Recipe.model_validate(cached)
                    except Exception:
                        try:
                            new_recipe = Recipe.parse_obj(cached)
                        except Exception:
                            new_recipe = None
                    if new_recipe is not None:
                        recipe = new_recipe
        except Exception:
            pass
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
