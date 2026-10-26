from __future__ import annotations

import csv
import io
import json
import os
import re
from pathlib import Path
from uuid import uuid4
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

try:
    from docx import Document
except Exception:  # pragma: no cover - optional dependency
    Document = None

from .engine import Cleaner, _read_table, _is_polars_df, _fuzzy_dedupe, _spell_correct_column
from .workflow_rules import suggest_columns_to_clean
from .step_catalog import parse_machine_template, steps_to_template, steps_to_text
import threading
import traceback
from datetime import datetime

try:
    from openpyxl import load_workbook
except Exception:  # pragma: no cover - optional dependency
    load_workbook = None

try:
    from pptx import Presentation
except Exception:  # pragma: no cover - optional dependency
    Presentation = None


TEXT_EXTENSIONS = {".csv", ".tsv", ".txt", ".md"}
SPREADSHEET_EXTENSIONS = {".xlsx"}
DOCUMENT_EXTENSIONS = {".docx"}
PRESENTATION_EXTENSIONS = {".pptx"}
GOOGLE_SHORTCUT_EXTENSIONS = {".gdoc", ".gsheet", ".gslides"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | SPREADSHEET_EXTENSIONS | DOCUMENT_EXTENSIONS | PRESENTATION_EXTENSIONS | GOOGLE_SHORTCUT_EXTENSIONS

GOOGLE_DRIVE_EXPORTS = {
    ".gdoc": {
        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "suffix": ".docx",
    },
    ".gsheet": {
        "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "suffix": ".xlsx",
    },
    ".gslides": {
        "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "suffix": ".pptx",
    },
}

GOOGLE_DRIVE_ACCESS_TOKEN_ENV_VARS = (
    "FALCONBROOM_GOOGLE_DRIVE_ACCESS_TOKEN",
    "GOOGLE_DRIVE_ACCESS_TOKEN",
    "GOOGLE_OAUTH_ACCESS_TOKEN",
    "GOOGLE_OAUTH2_ACCESS_TOKEN",
)


def _safe_stem(name: str) -> str:
    base = Path(name).stem.strip() or "upload"
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in base)
    return cleaned or "upload"


def _decode_bytes(raw_bytes: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
                try:
                        return raw_bytes.decode(encoding)
                except Exception:
                        continue
        return raw_bytes.decode("utf-8", errors="replace")


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _append_row(rows: list[dict[str, Any]], **row: Any) -> None:
    rows.append(
        {
            "source_kind": row.get("source_kind", "unknown"),
            "source_name": row.get("source_name", ""),
            "source_path": row.get("source_path", ""),
            "unit_kind": row.get("unit_kind", ""),
            "container_name": row.get("container_name", ""),
            "sheet_name": row.get("sheet_name", ""),
            "slide_number": row.get("slide_number", ""),
            "paragraph_index": row.get("paragraph_index", ""),
            "table_index": row.get("table_index", ""),
            "row_index": row.get("row_index", ""),
            "column_index": row.get("column_index", ""),
            "cell_label": row.get("cell_label", ""),
            "text": row.get("text", ""),
            "style_json": row.get("style_json", ""),
            "notes": row.get("notes", ""),
        }
    )


def _write_rows_to_csv(rows: list[dict[str, Any]], output_path: Path) -> None:
    fieldnames = [
        "source_kind",
        "source_name",
        "source_path",
        "unit_kind",
        "container_name",
        "sheet_name",
        "slide_number",
        "paragraph_index",
        "table_index",
        "row_index",
        "column_index",
        "cell_label",
        "text",
        "style_json",
        "notes",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _sniff_delimiter(sample: str) -> str:
    if "\t" in sample and sample.count("\t") >= sample.count(","):
        return "\t"
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|:")
        return dialect.delimiter
    except Exception:
        return "," if sample.count(",") >= sample.count(";") else ";"


def _first_nonempty(values: list[str | None]) -> str:
    for value in values:
        if value:
            return value
    return ""


def _google_drive_access_token() -> str:
    return _first_nonempty([os.environ.get(name, "").strip() for name in GOOGLE_DRIVE_ACCESS_TOKEN_ENV_VARS])


def _google_drive_file_id(candidate: str) -> str:
    if not candidate:
        return ""

    text = candidate.strip()
    parsed = urlparse(text)
    if parsed.query:
        query_params = parse_qs(parsed.query)
        for key in ("id", "fileId"):
            values = query_params.get(key)
            if values and values[0]:
                return values[0]

    patterns = [
        r"/d/([A-Za-z0-9_-]{8,})",
        r"[?&]id=([A-Za-z0-9_-]{8,})",
        r"/folders/([A-Za-z0-9_-]{8,})",
        r"^([A-Za-z0-9_-]{8,})$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def _google_shortcut_payload(source_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(_decode_bytes(source_path.read_bytes()))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _download_google_drive_export(file_id: str, mime_type: str) -> bytes:
    token = _google_drive_access_token()
    if not token:
        raise RuntimeError(
            "Google Drive access token not configured. Set FALCONBROOM_GOOGLE_DRIVE_ACCESS_TOKEN to enable authenticated fetches."
        )

    export_url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export?mimeType={mime_type}"
    request = Request(
        export_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/octet-stream",
        },
    )
    try:
        with urlopen(request, timeout=60) as response:
            return response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        raise RuntimeError(f"Drive export failed: {detail or exc}") from exc
    except URLError as exc:
        raise RuntimeError(f"Drive export request failed: {exc}") from exc


def _extract_google_drive_export(source_path: Path, output_dir: Path, rows: list[dict[str, Any]], warnings: list[str]) -> bool:
    payload = _google_shortcut_payload(source_path)
    url = _first_nonempty(
        [
            _stringify(payload.get("url")),
            _stringify(payload.get("doc_url")),
            _stringify(payload.get("sheet_url")),
            _stringify(payload.get("presentation_url")),
        ]
    )
    resource_id = _first_nonempty(
        [
            _stringify(payload.get("resource_id")),
            _stringify(payload.get("doc_id")),
            _stringify(payload.get("sheet_id")),
            _stringify(payload.get("presentation_id")),
            _stringify(payload.get("id")),
        ]
    )
    file_id = _google_drive_file_id(resource_id or url)
    export_info = GOOGLE_DRIVE_EXPORTS.get(source_path.suffix.lower())

    if not export_info or not file_id:
        return False

    try:
        export_bytes = _download_google_drive_export(file_id, export_info["mime_type"])
    except Exception as exc:
        warnings.append(str(exc))
        return False

    exported_path = output_dir / f"{_safe_stem(source_path.name)}_{uuid4().hex[:8]}{export_info['suffix']}"
    exported_path.write_bytes(export_bytes)
    warnings.append(f"Fetched Google Drive export for {source_path.name}.")

    if export_info["suffix"] == ".docx":
        _extract_docx_rows(exported_path, rows)
    elif export_info["suffix"] == ".xlsx":
        _extract_workbook_rows(exported_path, rows)
    elif export_info["suffix"] == ".pptx":
        _extract_pptx_rows(exported_path, rows)
    else:
        _extract_text_rows(exported_path, "google_drive", rows)

    return True


def _extract_text_rows(source_path: Path, source_kind: str, rows: list[dict[str, Any]]) -> None:
    raw_text = _decode_bytes(source_path.read_bytes())
    stripped = raw_text.strip()
    if not stripped:
        _append_row(
            rows,
            source_kind=source_kind,
            source_name=source_path.name,
            source_path=str(source_path),
            unit_kind="line",
            row_index=1,
            text="",
            notes="empty text file",
        )
        return

    sample = "\n".join(line for line in raw_text.splitlines() if line.strip())[:4096]
    delimiter = _sniff_delimiter(sample) if sample else ","
    reader = csv.reader(io.StringIO(raw_text), delimiter=delimiter, quotechar='"', escapechar="\\", strict=False)

    any_rows = False
    for row_index, row in enumerate(reader, start=1):
        any_rows = True
        if not row:
            continue
        row_text = delimiter.join(cell for cell in row if cell is not None)
        _append_row(
            rows,
            source_kind=source_kind,
            source_name=source_path.name,
            source_path=str(source_path),
            unit_kind="line",
            row_index=row_index,
            text=row_text,
            style_json=json.dumps({"delimiter": delimiter, "fields": len(row)}, ensure_ascii=False),
        )

    if not any_rows:
        for row_index, line in enumerate(raw_text.splitlines(), start=1):
            if not line.strip():
                continue
            _append_row(
                rows,
                source_kind=source_kind,
                source_name=source_path.name,
                source_path=str(source_path),
                unit_kind="line",
                row_index=row_index,
                text=line,
                notes="line fallback",
            )


def _extract_workbook_rows(source_path: Path, rows: list[dict[str, Any]]) -> None:
    if load_workbook is None:
        raise RuntimeError("openpyxl is not installed. Install requirements to enable spreadsheet extraction.")

    workbook = load_workbook(source_path, data_only=False, read_only=True)
    for sheet_name in workbook.sheetnames:
        sheet = workbook[sheet_name]
        for row_index, row in enumerate(sheet.iter_rows(), start=1):
            for column_index, cell in enumerate(row, start=1):
                try:
                    value = cell.value
                    if value is None or value == "":
                        continue

                    style = {
                        "data_type": cell.data_type,
                        "bold": bool(getattr(cell.font, "bold", False)),
                        "italic": bool(getattr(cell.font, "italic", False)),
                        "font_name": getattr(cell.font, "name", None),
                        "font_size": getattr(cell.font, "sz", None),
                        "number_format": getattr(cell, "number_format", None),
                        "alignment": getattr(cell.alignment, "horizontal", None),
                        "fill": getattr(getattr(cell.fill, "fgColor", None), "rgb", None),
                    }
                    if isinstance(value, str) and value.startswith("="):
                        style["formula"] = value

                    _append_row(
                        rows,
                        source_kind="xlsx",
                        source_name=source_path.name,
                        source_path=str(source_path),
                        unit_kind="cell",
                        sheet_name=sheet_name,
                        row_index=row_index,
                        column_index=column_index,
                        cell_label=cell.coordinate,
                        text=_stringify(value),
                        style_json=json.dumps(style, ensure_ascii=False, default=_stringify),
                    )
                except Exception as exc:
                    _append_row(
                        rows,
                        source_kind="xlsx",
                        source_name=source_path.name,
                        source_path=str(source_path),
                        unit_kind="cell_error",
                        sheet_name=sheet_name,
                        row_index=row_index,
                        column_index=column_index,
                        cell_label=getattr(cell, "coordinate", ""),
                        text=_stringify(getattr(cell, "value", "")),
                        notes=str(exc),
                    )


def _extract_docx_rows(source_path: Path, rows: list[dict[str, Any]]) -> None:
    if Document is None:
        raise RuntimeError("python-docx is not installed. Install requirements to enable Word extraction.")

    document = Document(source_path)
    for paragraph_index, paragraph in enumerate(document.paragraphs, start=1):
        text = paragraph.text.strip()
        if not text:
            continue
        run_bold_count = sum(1 for run in paragraph.runs if getattr(run, "bold", False))
        run_italic_count = sum(1 for run in paragraph.runs if getattr(run, "italic", False))
        style = {
            "style_name": getattr(getattr(paragraph, "style", None), "name", None),
            "alignment": getattr(paragraph.paragraph_format.alignment, "value", None) if paragraph.paragraph_format else None,
            "run_count": len(paragraph.runs),
            "bold_runs": run_bold_count,
            "italic_runs": run_italic_count,
        }
        _append_row(
            rows,
            source_kind="docx",
            source_name=source_path.name,
            source_path=str(source_path),
            unit_kind="paragraph",
            paragraph_index=paragraph_index,
            text=text,
            style_json=json.dumps(style, ensure_ascii=False, default=_stringify),
        )

    for table_index, table in enumerate(document.tables, start=1):
        for row_index, table_row in enumerate(table.rows, start=1):
            for column_index, cell in enumerate(table_row.cells, start=1):
                try:
                    text = cell.text.strip()
                    if not text:
                        continue
                    style = {
                        "paragraphs": len(cell.paragraphs),
                        "tables": len(cell.tables),
                    }
                    _append_row(
                        rows,
                        source_kind="docx",
                        source_name=source_path.name,
                        source_path=str(source_path),
                        unit_kind="table_cell",
                        container_name=f"table_{table_index}",
                        table_index=table_index,
                        row_index=row_index,
                        column_index=column_index,
                        cell_label=f"r{row_index}c{column_index}",
                        text=text,
                        style_json=json.dumps(style, ensure_ascii=False, default=_stringify),
                    )
                except Exception as exc:
                    _append_row(
                        rows,
                        source_kind="docx",
                        source_name=source_path.name,
                        source_path=str(source_path),
                        unit_kind="table_cell_error",
                        container_name=f"table_{table_index}",
                        table_index=table_index,
                        row_index=row_index,
                        column_index=column_index,
                        cell_label=f"r{row_index}c{column_index}",
                        notes=str(exc),
                    )


def _extract_pptx_rows(source_path: Path, rows: list[dict[str, Any]]) -> None:
    if Presentation is None:
        raise RuntimeError("python-pptx is not installed. Install requirements to enable PowerPoint extraction.")

    presentation = Presentation(source_path)
    for slide_number, slide in enumerate(presentation.slides, start=1):
        for shape_index, shape in enumerate(slide.shapes, start=1):
            try:
                shape_name = getattr(shape, "name", f"shape_{shape_index}")
                if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
                    for paragraph_index, paragraph in enumerate(shape.text_frame.paragraphs, start=1):
                        text = paragraph.text.strip()
                        if not text:
                            continue
                        style = {
                            "shape_name": shape_name,
                            "shape_type": getattr(shape, "shape_type", None),
                            "paragraph_level": getattr(paragraph, "level", None),
                            "alignment": getattr(paragraph.alignment, "value", None),
                            "run_count": len(paragraph.runs),
                        }
                        _append_row(
                            rows,
                            source_kind="pptx",
                            source_name=source_path.name,
                            source_path=str(source_path),
                            unit_kind="paragraph",
                            slide_number=slide_number,
                            container_name=shape_name,
                            paragraph_index=paragraph_index,
                            text=text,
                            style_json=json.dumps(style, ensure_ascii=False, default=_stringify),
                        )

                if getattr(shape, "has_table", False):
                    table = shape.table
                    for row_index, table_row in enumerate(table.rows, start=1):
                        for column_index, cell in enumerate(table_row.cells, start=1):
                            text = cell.text.strip()
                            if not text:
                                continue
                            _append_row(
                                rows,
                                source_kind="pptx",
                                source_name=source_path.name,
                                source_path=str(source_path),
                                unit_kind="table_cell",
                                slide_number=slide_number,
                                container_name=shape_name,
                                row_index=row_index,
                                column_index=column_index,
                                cell_label=f"r{row_index}c{column_index}",
                                text=text,
                                notes="slide table",
                            )
            except Exception as exc:
                _append_row(
                    rows,
                    source_kind="pptx",
                    source_name=source_path.name,
                    source_path=str(source_path),
                    unit_kind="shape_error",
                    slide_number=slide_number,
                    container_name=getattr(shape, "name", f"shape_{shape_index}"),
                    notes=str(exc),
                )


def _extract_google_shortcut_rows(source_path: Path, output_dir: Path, rows: list[dict[str, Any]], warnings: list[str]) -> None:
    payload = _google_shortcut_payload(source_path)

    if isinstance(payload, dict):
        url = payload.get("url") or payload.get("doc_url") or payload.get("sheet_url") or payload.get("presentation_url")
        title = payload.get("title") or payload.get("name") or source_path.stem
        resource_id = payload.get("resource_id") or payload.get("doc_id") or payload.get("id") or ""
        _append_row(
            rows,
            source_kind="google_shortcut",
            source_name=source_path.name,
            source_path=str(source_path),
            unit_kind="shortcut_metadata",
            text=_stringify(title),
            style_json=json.dumps(payload, ensure_ascii=False, default=_stringify),
            notes=f"{url or 'Google Drive shortcut'}; content extraction will use Drive export when authenticated",
        )
        if resource_id:
            _append_row(
                rows,
                source_kind="google_shortcut",
                source_name=source_path.name,
                source_path=str(source_path),
                unit_kind="shortcut_reference",
                text=_stringify(resource_id),
                notes=url or "",
            )
        if _extract_google_drive_export(source_path, output_dir, rows, warnings):
            return
        return

    _extract_text_rows(source_path, "google_shortcut", rows)


def compute_extended_diags(norm_path: str, meta_path: str) -> None:
    try:
        diag = {"path": norm_path, "computed_at": None, "columns": {}}
        df = None
        try:
            df = _read_table(norm_path)
        except Exception:
            return
        # choose candidate text columns
        cols = list(df.columns if _is_polars_df(df) else list(df.columns))
        text_cols: list[str] = []
        for c in cols:
            try:
                if _is_polars_df(df):
                    ser = df.select(c).to_series().to_list()
                else:
                    ser = list(df[c].astype(str).tolist()) if hasattr(df, 'astype') else []
                non_num = 0
                total = 0
                for v in ser[:500]:
                    total += 1
                    s = '' if v is None else str(v)
                    s2 = s.strip()
                    if s2 == '':
                        continue
                    if not re.match(r'^[-+]?\d+(?:[\.,]\d+)?$', s2):
                        non_num += 1
                if total > 0 and (non_num / max(1, total)) > 0.5:
                    text_cols.append(c)
            except Exception:
                continue

        diag['computed_at'] = datetime.now().isoformat()
        for c in text_cols:
            try:
                info: dict[str, Any] = {}
                try:
                    sample_df = df.head(1000) if _is_polars_df(df) else (df.head(1000) if hasattr(df, 'head') else df)
                except Exception:
                    sample_df = df
                try:
                    _, finfo = _fuzzy_dedupe(sample_df, subset=c, threshold=0.85)
                    info['fuzzy_dedupe'] = finfo
                except Exception:
                    info['fuzzy_dedupe'] = None
                try:
                    if _is_polars_df(df):
                        uvals = list({str(x) for x in df.select(c).to_series().to_list()[:500]})
                    else:
                        uvals = list({str(x) for x in (df[c].astype(str).tolist()[:500] if c in df.columns else [])})
                except Exception:
                    uvals = []
                try:
                    corrected: dict[str, str] = {}
                    if uvals:
                        for v in uvals[:200]:
                            if v is None:
                                continue
                            lv = v.strip()
                            if not lv:
                                continue
                            canon = lv.strip().title() if lv.lower() != lv else lv
                            if canon != lv:
                                corrected[lv] = canon
                    info['spell_suggestions'] = corrected
                except Exception:
                    info['spell_suggestions'] = None
                diag['columns'][c] = info
            except Exception:
                diag['columns'][c] = {'error': 'failed_column_diag'}
        try:
            # include plain language suggestions derived from extended diags
            try:
                diag['plain_language_suggestions'] = []
                for col, info in diag.get('columns', {}).items():
                    try:
                        # fuzzy clusters
                        if info.get('fuzzy_dedupe'):
                            diag['plain_language_suggestions'].append({
                                'text': f"Values in '{col}' contain near-duplicates (clusters detected). Suggest merging similar variants and normalizing whitespace/case.",
                                'source': 'extended_fuzzy'
                            })
                        # spell suggestions
                        if info.get('spell_suggestions'):
                            examples = list(info.get('spell_suggestions').items())[:3]
                            ex_txt = ', '.join(f"{k}→{v}" for k, v in examples)
                            diag['plain_language_suggestions'].append({
                                'text': f"Column '{col}' has spelling/variant suggestions ({ex_txt}). Suggest applying canonical mappings.",
                                'source': 'extended_spell'
                            })
                    except Exception:
                        continue
            except Exception:
                diag['plain_language_suggestions'] = []
            diag_path = Path(meta_path).with_suffix('.diag.json')
            diag_path.write_text(json.dumps(diag, indent=2, ensure_ascii=False), encoding='utf-8')
        except Exception:
            try:
                dpath = Path(norm_path).with_suffix('.diag.json')
                dpath.write_text(json.dumps(diag, indent=2, ensure_ascii=False), encoding='utf-8')
            except Exception:
                pass
    except Exception:
        traceback.print_exc()


_ID_NAME_RE = re.compile(r"zip_?code|post(al)?_?code|(^|_)(zip|postal|phone|tel|ssn|sku|upc|isbn|account|acct|ein|npi)(_|$)|(^|_)id$", re.I)
_UNIQUE_KEY_NAME_RE = re.compile(r"email|(^|_)(id|key|sku|upc|isbn|account|acct|ssn|order|customer)(_|$)", re.I)
_NUM_RE = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)$")
_AFFIX_RE = re.compile(r"^(?P<pre>[^\d\-+.]*?)\s*(?P<num>[-+]?\d[\d,]*(?:\.\d+)?)\s*(?P<suf>[^\d]*)$")
_KNOWN_SUFFIXES = {'yr', 'yrs', 'year', 'years', 'kg', 'g', 'mg', 'lb', 'lbs', 'oz', 'km', 'm', 'cm', 'mm', 'mi', 'ft', 'in', '°c', '°f', 'c', 'f', '%'}
_MISSING_MARKERS = {'n/a', 'na', 'null', 'none', 'unknown', 'unk', 'tbd', 'not available', 'not provided', '--'}
_DATE_LIKE_RE = re.compile(r"^(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}|\d{1,2}[- ][A-Za-z]{3,9}[- ,]\d{2,4})", re.I)
_DATE_FORMATS = (
    ("%Y-%m-%d", re.compile(r"^\d{4}-\d{2}-\d{2}$")),
    ("%Y/%m/%d", re.compile(r"^\d{4}/\d{2}/\d{2}$")),
    ("%m/%d/%Y", re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")),
    ("%d/%m/%Y", re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")),
    ("%m-%d-%Y", re.compile(r"^\d{1,2}-\d{1,2}-\d{4}$")),
    ("%d-%m-%Y", re.compile(r"^\d{1,2}-\d{1,2}-\d{4}$")),
    ("%d-%b-%Y", re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{4}$", re.I)),
    ("%Y-%m-%d %H:%M:%S", re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")),
    ("%Y-%m-%dT%H:%M:%S", re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")),
    ("%Y-%m-%dT%H:%M:%S%z", re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:?\d{2})$", re.I)),
    ("%b %d, %Y", re.compile(r"^[A-Za-z]{3} \d{1,2}, \d{4}$", re.I)),
    ("%B %d, %Y", re.compile(r"^[A-Za-z]{3,9} \d{1,2}, \d{4}$", re.I)),
)
_LOCALE_NUMBER_RE = re.compile(r"^[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+),\d+$")
_META_COLS = {'container_name', 'sheet_name', 'slide_number', 'paragraph_index', 'table_index', 'column_index', 'cell_label', 'source_kind', 'source_name', 'source_path', 'unit_kind', 'row_index'}
_GEOGRAPHIC_FIELD_RE = re.compile(r"(?:^|_)(?:state|province|country)(?:_|$)", re.I)
_EXPLICIT_CODE_FIELD_RE = re.compile(r"(?:^|_)(?:state|province|country|currency|region|iso)(?:_?(?:code|abbr|abbrev|abbreviation|alpha2|alpha3))$", re.I)
_ABBREVIATION_FIELD_RE = re.compile(r"(?:^|_)(?:abbr|abbrev|abbreviation|acronym|initials|ticker|symbol|code)(?:_|$)", re.I)
_ORGANIZATION_FIELD_RE = re.compile(r"(?:^|_)(?:organization|organisation|org|company|business|brand)(?:_?(?:name|label|code|abbr|abbreviation))?(?:_|$)", re.I)


def _q(v: str) -> str:
    return f'"{v}"' if re.search(r"\s", v) else v


def _uniq(vals, k: int = 3) -> list:
    seen: list = []
    for v in vals:
        if v not in seen:
            seen.append(v)
        if len(seen) >= k:
            break
    return seen


def _preferred_abbreviation_case(col: str, vals: list[str]) -> str | None:
    if not vals or not all(re.fullmatch(r"(?=.*[A-Za-z])[A-Za-z0-9]{2,6}", value) for value in vals):
        return None
    state_or_province = bool(re.search(r"(?:^|_)(?:state|province)(?:_|$)", col, re.I))
    explicit_code = bool(_EXPLICIT_CODE_FIELD_RE.search(col))
    explicit_abbreviation = bool(_ABBREVIATION_FIELD_RE.search(col))
    organization_acronyms = bool(_ORGANIZATION_FIELD_RE.search(col)) and not any(value.istitle() for value in vals)
    if state_or_province and all(re.fullmatch(r"[A-Za-z]{2}", value) for value in vals):
        return "upper"
    if explicit_code or explicit_abbreviation or organization_acronyms:
        return "upper"
    return None


def _classify_cell(s: str) -> tuple[str, str, str]:
    """Classify a cell as numeric, formatted numeric, known-unit numeric, ZIP+4, or text."""
    if re.fullmatch(r"\d{5}-\d{4}", s):
        return 'zip4', '', ''
    c = re.sub(r"[\$£€¥₹,\s]", "", s)
    if c.startswith('(') and c.endswith(')'):
        c = '-' + c[1:-1]
    if _NUM_RE.match(c):
        return ('formatted' if c != s else 'numeric'), '', ''
    m = _AFFIX_RE.match(s)
    if m:
        pre = m.group('pre').strip()
        suf = m.group('suf').strip()
        if not pre and suf.lower() in _KNOWN_SUFFIXES:
            return 'affix', '', suf.lower()
        if pre in {'$', '£', '€', '¥', '₹'} and not suf:
            return 'formatted', '', ''
    return 'text', '', ''


def _complete_entries(entries: list[dict[str, Any]]) -> None:
    """Give every executable suggestion the same step expressed as json, a template line and an instruction."""
    for e in entries:
        if not isinstance(e, dict):
            continue
        steps = e.get('json_steps')
        if not steps:
            mt = e.get('machine_template') or e.get('machine')
            steps = [s for s in (parse_machine_template(line) for line in str(mt or '').splitlines()) if s]
        if not steps:
            continue
        e['json_steps'] = steps
        e['json_step'] = steps[0]
        template = steps_to_template(steps)
        if template:
            e['machine_template'] = template
        instruction = steps_to_text(steps)
        if instruction:
            e['instruction'] = instruction


def _entry(text: str, machine: str | None, confidence: float, steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    e: dict[str, Any] = {"text": text, "source": "diagnostics", "confidence": confidence}
    if machine:
        e["machine_template"] = machine
    if steps:
        e["json_steps"] = steps
        e["json_step"] = steps[0]
    return e


def _zip_entries(col: str, vals: list[str]) -> list[dict[str, Any]]:
    c = _q(col)
    ents: list[dict[str, Any]] = []
    zip4 = [s for s in vals if re.fullmatch(r"\d{5}-\d{4}", s)]
    trail = [s for s in vals if re.fullmatch(r"\d{5}[A-Za-z]+", s)]
    short = [s for s in vals if re.fullmatch(r"\d{4}", s)]
    words = [s for s in vals if not re.search(r"\d", s)]
    if zip4:
        pattern = r'^(\d{5})-\d{4}$'
        step = {'action': 'regex_replace', 'column': col, 'params': {'pattern': pattern, 'replace': r'\1'}}
        ents.append(_entry(
            f"Standardize ZIP+4 values in '{col}' to 5-digit ZIP codes ({len(zip4)} values like {', '.join(_uniq(zip4))}); the column stays text so leading zeros are kept",
            'regex_replace column=' + c + r' pattern="^(\d{5})-\d{4}$" replace="\1"', 0.8, [step]))
    if trail:
        pattern = r'^(\d{5})[A-Za-z]+$'
        step = {'action': 'regex_replace', 'column': col, 'params': {'pattern': pattern, 'replace': r'\1'}}
        ents.append(_entry(
            f"Remove stray trailing letters from ZIP codes in '{col}' ({len(trail)} values like {', '.join(_uniq(trail))})",
            'regex_replace column=' + c + r' pattern="^(\d{5})[A-Za-z]+$" replace="\1"', 0.7, [step]))
    if short:
        pattern = r'^(\d{4})$'
        step = {'action': 'regex_replace', 'column': col, 'params': {'pattern': pattern, 'replace': r'0\1'}}
        ents.append(_entry(
            f"Pad 4-digit ZIP codes in '{col}' with a leading zero ({len(short)} values like {', '.join(_uniq(short))}); assumes US ZIP codes whose leading zero was lost",
            'regex_replace column=' + c + r' pattern="^(\d{4})$" replace="0\1"', 0.6, [step]))
    if words:
        ents.append(_entry(
            f"{len(words)} values in '{col}' are text, not ZIP codes (e.g. {', '.join(_uniq(words))}). They probably belong in another column, so review them; '{col}' is kept as text and is not cast to a number",
            None, 0.6))
    return ents


def _numeric_entries(col: str, vals: list[str], kinds: list[tuple[str, str, str]], cnt: dict[str, int]) -> dict[str, Any]:
    n = len(vals)
    c = _q(col)
    affixed = [(s, p, x) for s, (k, p, x) in zip(vals, kinds) if k == 'affix']
    suf_tokens = _uniq([x for _, _, x in affixed if x], 50)
    units = {x for _, _, x in affixed}
    if len(units) > 1:
        examples = _uniq([s for s, _, _ in affixed])
        return {'kind': 'mixed', 'entries': [_entry(
            f"Column '{col}' contains numbers with different unit suffixes ({', '.join(sorted(units))}); review before converting values such as {', '.join(examples)}",
            None, 0.4)]}
    numericish = cnt['numeric'] + cnt['formatted'] + cnt['affix']
    if numericish / n < 0.25:
        return {'kind': 'text', 'entries': []}
    bad = [s for s, (k, _, _) in zip(vals, kinds) if k in ('text', 'zip4')]
    share_now = (cnt['numeric'] + cnt['formatted']) / n
    share_after = numericish / n
    if affixed and share_after >= 0.75:
        unit = next(iter(units))
        pattern = r'\s*' + re.escape(unit) + r'$'
        nums = [re.sub(r"[\$£€¥₹,\s]", "", _AFFIX_RE.match(s).group('num')) if k == 'affix' else re.sub(r"[\$£€¥₹,\s()]", "", s)
                for s, (k, _, _) in zip(vals, kinds) if k in ('numeric', 'formatted', 'affix')]
        to = 'int' if nums and all(re.fullmatch(r"[-+]?\d+", x) for x in nums) and unit not in {'%', '°c', '°f'} else 'float'
        text = f"Strip the consistent '{unit}' suffix from '{col}' ({len(affixed)} values like {', '.join(_uniq([s for s, _, _ in affixed]))}), then convert it to {to}; this removes the label but does not convert units"
        if unit == '%':
            text += " and keeps percentage points (10% becomes 10, not 0.10)"
        if bad:
            text += f". {len(bad)} other values (e.g. {', '.join(_uniq(bad))}) are not numbers and would become empty"
        steps = [
            {'action': 'regex_replace', 'column': col, 'params': {'pattern': pattern, 'replace': ''}},
            {'action': 'cast', 'column': col, 'params': {'to': to}},
        ]
        mt = 'regex_replace column=' + c + ' pattern="' + pattern + '" replace=""\n' + f'cast column={c} to={to}'
        entries = [_entry(text, mt, 0.85, steps)]
        if unit == '%':
            entries.append(_entry(
                f"Convert percentage points in '{col}' to decimal fractions (10% becomes 0.1)",
                None,
                0.8,
                steps + [{'action': 'numeric_transform', 'column': col, 'params': {'operation': 'divide', 'factor': 100}}],
            ))
        return {'kind': 'numeric', 'entries': entries}
    if share_now >= 0.75 and not affixed and bad:
        text = (f"Column '{col}' is {share_now:.0%} numeric. Convert it to float; {len(bad)} values that are not numbers "
                f"(e.g. {', '.join(_uniq(bad))}) would become empty for review")
        return {'kind': 'numeric', 'entries': [_entry(text, f'cast column={c} to=float', 0.9, [
            {'action': 'cast', 'column': col, 'params': {'to': 'float'}},
        ])]}
    if share_now >= 0.75 and not affixed and cnt['formatted']:
        to = 'int' if all(re.fullmatch(r"[-+]?\d+", re.sub(r"[\$£€¥₹,\s()]", "", s)) for s in vals) else 'float'
        text = f"Convert consistently formatted numeric values in '{col}' to {to}; currency symbols, grouping commas, and parentheses are parsed, but no unit or currency conversion is performed"
        return {'kind': 'numeric', 'entries': [_entry(text, f'cast column={c} to={to}', 0.85, [
            {'action': 'cast', 'column': col, 'params': {'to': to}},
        ])]}
    if share_now >= 0.25 and (share_after < 0.75 or affixed):
        odd = [s for s, (k, _, _) in zip(vals, kinds) if k != 'numeric']
        text = (f"Column '{col}' mixes numbers and text ({share_now:.0%} plain numbers), so it was not converted automatically. "
                f"Review the {len(odd)} non-numeric values (e.g. {', '.join(_uniq(odd))})")
        return {'kind': 'mixed', 'entries': [_entry(text, None, 0.4)]}
    return {'kind': 'numeric', 'entries': []}


def _analyze_column(col: str, values: list) -> dict[str, Any] | None:
    vals: list[str] = []
    raw_values: list[str] = []
    markers: list[str] = []
    missing_count = 0
    for v in values:
        if v is None:
            missing_count += 1
            continue
        raw = str(v)
        s = raw.strip()
        if s == '':
            missing_count += 1
            continue
        if s.lower() in _MISSING_MARKERS:
            markers.append(s)
            missing_count += 1
            continue
        raw_values.append(raw)
        vals.append(s)
    n = len(vals)
    if n < 10:
        return {'kind': 'text', 'entries': []}
    european_numbers = [s for s in vals if _LOCALE_NUMBER_RE.fullmatch(re.sub(r"[€£$¥₹\s]", "", s))]
    if european_numbers:
        return {'kind': 'locale_number_review', 'entries': [_entry(
            f"'{col}' contains comma-decimal values such as {', '.join(_uniq(european_numbers))}. Review the number locale before converting; automatic comma removal could change their magnitude",
            None, 0.45)]}
    date_format_counts = []
    for date_fmt, pattern in _DATE_FORMATS:
        valid_count = 0
        for value in vals:
            if not pattern.fullmatch(value):
                continue
            try:
                datetime.strptime(value, date_fmt)
                valid_count += 1
            except ValueError:
                continue
        date_format_counts.append((date_fmt, valid_count))
    if not _ID_NAME_RE.search(col) and date_format_counts:
        ranked_date_formats = sorted(date_format_counts, key=lambda item: item[1], reverse=True)
        fmt, matched = ranked_date_formats[0]
        runner_up = ranked_date_formats[1][1] if len(ranked_date_formats) > 1 else 0
        if matched / n >= 0.9 and matched > runner_up:
            invalid_count = n - matched
            text = f"Parse consistently formatted dates in '{col}' using {fmt}"
            if invalid_count:
                text += f"; {invalid_count} other value(s) will become empty"
            return {'kind': 'date', 'entries': [_entry(
                text,
                None,
                0.85 if invalid_count == 0 else 0.7,
                [{'action': 'cast', 'column': col, 'params': {'to': 'datetime', 'fmt': fmt}}],
            )]}
    date_matches = sum(1 for s in vals if _DATE_LIKE_RE.match(s))
    if date_matches / n >= 0.75:
        iso = sum(1 for s in vals if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s))
        if iso / n >= 0.9 and not _ID_NAME_RE.search(col):
            return {'kind': 'date', 'entries': [_entry(
                f"Parse ISO dates in '{col}' as dates; invalid values will become empty",
                None, 0.85, [{'action': 'cast', 'column': col, 'params': {'to': 'datetime', 'fmt': '%Y-%m-%d'}}]) ]}
        return {'kind': 'date_review', 'entries': [_entry(
            f"'{col}' contains date-like values, but the format or day/month order is ambiguous. Review examples before converting: {', '.join(_uniq(vals))}",
            None, 0.45)]}
    kinds = [_classify_cell(s) for s in vals]
    cnt = {'numeric': 0, 'formatted': 0, 'affix': 0, 'zip4': 0, 'text': 0}
    for k, _, _ in kinds:
        cnt[k] += 1
    digits5 = sum(1 for s in vals if re.fullmatch(r"\d{5}", s))
    zip_like = bool(re.search(r"zip|postal|post_?code", col, re.I)) or (cnt['zip4'] > 0 and (cnt['zip4'] + digits5) / n >= 0.5)
    if zip_like:
        result = {'kind': 'identifier', 'entries': _zip_entries(col, vals)}
    elif re.search(r'email|e_mail', col, re.I):
        spaced = [s for s in raw_values if s != s.strip()]
        invalid = [s.strip() for s in vals if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", s.strip())]
        ents = []
        if spaced:
            ents.append(_entry(f"Trim surrounding whitespace from {len(spaced)} email values in '{col}'", f'normalize column={_q(col)} case=trim', 0.9,
                               [{'action': 'normalize', 'column': col, 'params': {'case': 'trim'}}]))
        if invalid:
            ents.append(_entry(f"Review {len(invalid)} email-shaped values in '{col}' that do not match a basic email format; examples: {', '.join(_uniq(invalid))}", None, 0.5))
        result = {'kind': 'identifier', 'entries': ents}
    elif re.search(r'phone|mobile|telephone|\btel\b', col, re.I):
        compact = [re.sub(r'[^\d+]', '', s) for s in vals]
        malformed = [s for s, v in zip(vals, compact) if not re.fullmatch(r"\+?\d{7,15}", v)]
        punctuation = any(re.search(r'[\s().-]', s) for s in vals)
        if not malformed and punctuation:
            text = f"Remove display punctuation and spaces from phone numbers in '{col}' while preserving the leading country-code plus sign"
            ents = [_entry(text, None, 0.8, [{'action': 'regex_replace', 'column': col, 'params': {'pattern': r'[\s().-]', 'replace': ''}}])]
        else:
            ents = [_entry(f"Review {len(malformed)} phone values in '{col}' with extensions or unusual formats before normalizing; examples: {', '.join(_uniq(malformed))}", None, 0.5)] if malformed else []
        result = {'kind': 'identifier', 'entries': ents}
    elif _ID_NAME_RE.search(col):
        entries = []
        result = {'kind': 'identifier', 'entries': entries}
    else:
        result = _numeric_entries(col, vals, kinds, cnt)

    is_potential_key = bool(_UNIQUE_KEY_NAME_RE.search(col) or re.search(r'email|e_mail', col, re.I))
    if is_potential_key:
        value_counts: dict[str, int] = {}
        display_values: dict[str, str] = {}
        for value in vals:
            key = value.strip().casefold() if re.search(r'email|e_mail', col, re.I) else value.strip()
            value_counts[key] = value_counts.get(key, 0) + 1
            display_values.setdefault(key, value)
        duplicate_keys = [key for key, count in value_counts.items() if count > 1]
        duplicate_rows = sum(value_counts[key] - 1 for key in duplicate_keys)
        if duplicate_rows:
            result['entries'].append(_entry(
                f"Potential key field '{col}' has {duplicate_rows} repeated value(s) among analyzed rows (up to 5,000 sampled); examples: {', '.join(_uniq([display_values[key] for key in duplicate_keys]))}. Review whether these are valid duplicates; no rows will be removed",
                None,
                0.6,
            ))
        if missing_count:
            result['entries'].append(_entry(
                f"Potential key field '{col}' has {missing_count} blank or recognized missing value(s) among analyzed rows (up to 5,000 sampled); review before using it as an identifier",
                None,
                0.7,
            ))

    if re.search(r'url|website|web_?site|uri', col, re.I):
        missing_scheme = [s for s in vals if re.match(r'^[\w.-]+\.[A-Za-z]{2,}(?:/|$)', s) and not re.match(r'^https?://', s, re.I)]
        if missing_scheme:
            result['entries'].append(_entry(
                f"{len(missing_scheme)} values in '{col}' look like web addresses without a scheme. Review before adding https://; examples: {', '.join(_uniq(missing_scheme))}", None, 0.55))

    if markers and result.get('kind') == 'numeric' and result.get('entries'):
        marker_pattern = r'(?i)^(?:' + '|'.join(re.escape(x) for x in _uniq(markers, 12)) + r')$'
        first = result['entries'][0]
        cast_step = next((s for s in (first.get('json_steps') or []) if s.get('action') == 'cast'), None)
        if cast_step:
            target = cast_step['params'].get('to', 'float')
            text = f"Treat {', '.join(_uniq(markers, 12))} in '{col}' as missing, then cast to {target}; review any other values that cannot be parsed"
            steps = [
                {'action': 'regex_replace', 'column': col, 'params': {'pattern': marker_pattern, 'replace': ''}},
                {'action': 'cast', 'column': col, 'params': {'to': target}},
            ]
            result['entries'].insert(0, _entry(text, None, 0.8, steps))
    elif markers and result.get('kind') == 'numeric':
        marker_pattern = r'(?i)^(?:' + '|'.join(re.escape(x) for x in _uniq(markers, 12)) + r')$'
        result['entries'].append(_entry(
            f"Treat {', '.join(_uniq(markers, 12))} in '{col}' as missing before numeric conversion",
            None, 0.8, [
                {'action': 'regex_replace', 'column': col, 'params': {'pattern': marker_pattern, 'replace': ''}},
                {'action': 'cast', 'column': col, 'params': {'to': 'float'}},
            ]))
    elif markers and result.get('kind') in ('text', 'mixed'):
        marker_pattern = r'(?i)^(?:' + '|'.join(re.escape(x) for x in _uniq(markers, 12)) + r')$'
        result['entries'].insert(0, _entry(
            f"Mark {', '.join(_uniq(markers, 12))} in '{col}' as blank for review; this does not impute or delete rows",
            None,
            0.65,
            [{'action': 'regex_replace', 'column': col, 'params': {'pattern': marker_pattern, 'replace': ''}}],
        ))

    # Suggest normalization only for low-cardinality labels with case/space variants.
    preferred_case = _preferred_abbreviation_case(col, vals)
    proper_name_field = bool(_GEOGRAPHIC_FIELD_RE.search(col) or _ORGANIZATION_FIELD_RE.search(col)) and preferred_case is None
    if result.get('kind') not in ('identifier', 'date', 'date_review') and n >= 10:
        groups: dict[str, set[str]] = {}
        for s in raw_values:
            groups.setdefault(s.strip().casefold(), set()).add(s)
        variants = [group for group in groups.values() if len(group) > 1]
        unique_count = len(groups)
        needs_upper = preferred_case == 'upper' and any(s != s.upper() for s in raw_values)
        if (needs_upper or (variants and not proper_name_field)) and unique_count <= 50 and unique_count / n <= 0.5 and not re.search(r'name|address|description|comment|notes|email', col, re.I):
            examples = _uniq([value for group in variants for value in sorted(group)])
            case = preferred_case or 'lower'
            if case == 'upper':
                text = f"Standardize '{col}' abbreviations to uppercase (e.g. {', '.join(repr(v) for v in _uniq(raw_values))})"
            else:
                text = f"Standardize casing and surrounding spaces in '{col}'; labels that differ only by case or spaces include {', '.join(repr(v) for v in examples)}"
            result['entries'].append(_entry(
                text,
                f'normalize column={_q(col)} case={case}', 0.75,
                [{'action': 'normalize', 'column': col, 'params': {'case': case}}]))
        elif variants and proper_name_field:
            result['entries'].append(_entry(
                f"Values in '{col}' differ by case, but appear to be proper names. Review the intended display casing before normalizing them",
                None, 0.5))
        lowered = {s.strip().lower() for s in vals}
        bool_labels = {
            'yes': 'true', 'y': 'true', 'true': 'true', 't': 'true', '1': 'true',
            'enabled': 'true', 'active': 'true', 'on': 'true', 'accepted': 'true', 'present': 'true',
            'no': 'false', 'n': 'false', 'false': 'false', 'f': 'false', '0': 'false',
            'disabled': 'false', 'inactive': 'false', 'off': 'false', 'declined': 'false', 'absent': 'false',
        }
        boolean_field = bool(re.search(r'active|enabled|verified|consent|is_|^has_|^on_', col, re.I))
        if boolean_field and lowered and lowered <= set(bool_labels):
            mapping = {
                raw: bool_labels[raw.strip().lower()]
                for raw in raw_values
            }
            if mapping:
                result['entries'].append(_entry(
                    f"Standardize yes/no variants in '{col}' to true/false",
                    None, 0.8, [{'action': 'map', 'column': col, 'params': {'mapping': mapping}}]))

    metric_field = bool(re.search(r"age|amount|price|cost|total|quantity|count|score|rate|weight|height|balance|duration", col, re.I))
    if metric_field and result.get('kind') == 'numeric' and n >= 20:
        numeric_values = []
        for value, (kind, _, _) in zip(vals, kinds):
            if kind not in ('numeric', 'formatted'):
                continue
            cleaned = re.sub(r"[\$£€¥₹,\s()]", "", value)
            try:
                numeric_values.append((float(cleaned), value))
            except ValueError:
                continue
        if len(numeric_values) >= 20 and len(numeric_values) / n >= 0.9:
            ordered = sorted(number for number, _ in numeric_values)
            q1 = ordered[int(0.25 * (len(ordered) - 1))]
            q3 = ordered[int(0.75 * (len(ordered) - 1))]
            spread = q3 - q1
            if spread > 0:
                low, high = q1 - 1.5 * spread, q3 + 1.5 * spread
                outliers = [value for number, value in numeric_values if number < low or number > high]
                if outliers:
                    result['entries'].append(_entry(
                        f"Review {len(outliers)} statistical outlier(s) in the analyzed sample for '{col}' beyond the 1.5×IQR range; examples: {', '.join(_uniq(outliers))}. No values will be changed automatically",
                        None,
                        0.55,
                    ))
    return result


def _analyze_columns(path: str | None) -> dict[str, dict[str, Any]]:
    """Analyze every column of the normalized table so suggestions reflect the real value patterns."""
    out: dict[str, dict[str, Any]] = {}
    if not path or not Path(path).exists():
        return out
    df = _read_table(path)
    try:
        row_columns = [c for c in df.columns if c not in _META_COLS and not (isinstance(c, str) and c.startswith('_'))]
        if row_columns:
            duplicate_count = int(df.select(row_columns).is_duplicated().sum())
            if duplicate_count:
                out['__dataset__'] = {
                    'kind': 'duplicates',
                    'entries': [_entry(
                        f"Found {duplicate_count} exact duplicate row(s) in the dataset. Review before removing them; duplicate rows may be valid repeated events",
                        None,
                        0.8,
                        [{'action': 'deduplicate', 'column': None, 'params': {}}],
                    )],
                }
    except Exception:
        pass
    for c in list(df.columns):
        if not isinstance(c, str) or c.startswith('_') or c in _META_COLS:
            continue
        try:
            vals = df[c].to_list() if _is_polars_df(df) else df[c].tolist()
        except Exception:
            continue
        if len(vals) > 5000:
            indices = [i * (len(vals) - 1) // 4999 for i in range(5000)]
            vals = [vals[i] for i in indices]
        res = _analyze_column(c, vals)
        if res:
            out[c] = res

    names = {str(c).lower(): c for c in df.columns if isinstance(c, str) and c not in _META_COLS and not c.startswith('_')}
    pairs: set[tuple[str, str]] = set()
    for lower, original in names.items():
        candidates = []
        if lower.startswith('start_'):
            candidates.append('end_' + lower[len('start_'):])
        if lower.startswith('min_'):
            candidates.append('max_' + lower[len('min_'):])
        for left, right in (('_start_date', '_end_date'), ('_start_time', '_end_time'), ('_start', '_end'), ('_min', '_max'), ('_minimum', '_maximum')):
            if lower.endswith(left):
                candidates.append(lower[:-len(left)] + right)
        for candidate in candidates:
            other = names.get(candidate)
            if other and original != other:
                pairs.add((original, other))

    pair_notes = []
    for left, right in pairs:
        try:
            sample = df.select([left, right]).head(5000).to_dicts() if _is_polars_df(df) else df[[left, right]].head(5000).to_dict('records')
            is_temporal = bool(re.search(r'date|time', left + ' ' + right, re.I))
            violations = []
            compared = 0
            for row in sample:
                a, b = row.get(left), row.get(right)
                if a is None or b is None or not str(a).strip() or not str(b).strip():
                    continue
                try:
                    if is_temporal:
                        av = datetime.fromisoformat(str(a).replace('Z', '+00:00'))
                        bv = datetime.fromisoformat(str(b).replace('Z', '+00:00'))
                    else:
                        av, bv = float(str(a).replace(',', '')), float(str(b).replace(',', ''))
                    compared += 1
                    if av > bv:
                        violations.append((str(a), str(b)))
                except (TypeError, ValueError):
                    continue
            if compared >= 10 and violations and len(violations) / compared >= 0.05:
                examples = _uniq([f"{a} > {b}" for a, b in violations])
                relation = 'start is after end' if is_temporal else 'minimum/start is greater than maximum/end'
                pair_notes.append(_entry(
                    f"Review {len(violations)} of {compared} sampled row(s) where '{left}' {relation} '{right}' (examples: {', '.join(examples)}); no values will be changed automatically",
                    None,
                    0.55,
                ))
        except Exception:
            continue
    if pair_notes:
        out.setdefault('__dataset__', {'kind': 'integrity', 'entries': []})['entries'].extend(pair_notes)
    return out


def generate_plain_language_suggestions(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """Create short, editable plain-English suggestions from metadata/diagnostics.

    Returns a list of {text: str, source: str} entries.
    """
    out: list[dict[str, Any]] = []
    try:
        col_info = _analyze_columns(metadata.get("normalized_path") or metadata.get("path"))
    except Exception:
        col_info = {}
    try:
        for s in (metadata.get("suggestions") or []):
            try:
                col = s.get("column") if isinstance(s, dict) else None
                reason = s.get("reason") if isinstance(s, dict) else None
                # Prefer explicit actionable templates that map to backend actions
                machine = None
                confidence = 0.45
                if col and reason:
                    text = f"Review column '{col}' ({reason}); no automatic change is suggested without evidence of a specific value issue."
                elif col:
                    text = f"Review column '{col}' for a specific correction before generating a recipe."
                else:
                    text = f"Data cleansing suggested: {s}. Try explicit actions like: normalize <column> to lowercase, cast <column> to integer, impute <column> with mean."
                entry = {"text": text, "source": "suggestions", "confidence": confidence}
                if machine:
                    entry["machine_template"] = machine
                out.append(entry)
            except Exception:
                continue

        # Diagnostics-based prompts (profile/inspect)
        inspect_preview = metadata.get("inspect_preview") if isinstance(metadata.get("inspect_preview"), dict) else {}
        diag = metadata.get("diagnostics") or inspect_preview.get("diagnostics")
        if isinstance(diag, dict):
            for col, info in diag.items():
                try:
                    if not isinstance(info, dict):
                        continue
                    # Missing values -> give explicit impute templates
                    if (
                        info.get("missing_count")
                        and col_info.get(col, {}).get("kind") == "numeric"
                        and not any("would become empty" in entry.get("text", "") for entry in col_info.get(col, {}).get("entries", []))
                    ):
                        cnt = int(info.get("missing_count") or 0)
                        analysis_entries = col_info.get(col, {}).get("entries") or []
                        base_steps = []
                        for candidate in analysis_entries:
                            candidate_steps = candidate.get("json_steps") or []
                            if any(step.get("action") == "cast" for step in candidate_steps):
                                base_steps = [step for step in candidate_steps if step.get("action") in ("regex_replace", "cast")]
                                break
                        if not base_steps:
                            base_steps = [{"action": "cast", "column": col, "params": {"to": "float"}}]
                        impute_step = {"action": "impute", "column": col, "params": {"strategy": "median"}}
                        steps = base_steps + [impute_step]
                        out.append({
                            "text": f"Convert '{col}' to numeric, then fill its {cnt} missing values with the median",
                            "source": "diagnostics",
                            "confidence": 0.75,
                            "json_steps": steps,
                            "json_step": steps[0],
                        })
                    # Mixed types -> decide assertive vs tentative based on numeric parse rate
                    if info.get("mixed_type") and col not in col_info:
                        try:
                            numeric_rate = None
                            insp = metadata.get('inspect_preview') if isinstance(metadata.get('inspect_preview'), dict) else None
                            if insp and isinstance(insp.get('rows'), list):
                                rows = insp.get('rows') or []
                                nonempty = 0
                                num_numeric = 0
                                for r in rows[:500]:
                                    v = r.get(col)
                                    if v is None:
                                        continue
                                    s = str(v).strip()
                                    if s == '':
                                        continue
                                    nonempty += 1
                                    try:
                                        # reuse engine's numeric check heuristics: allow floats/ints with optional separators
                                        ss = s.replace(',', '').replace(' ', '')
                                        # parentheses negative
                                        if ss.startswith('(') and ss.endswith(')'):
                                            ss = '-' + ss[1:-1]
                                        float(ss)
                                        num_numeric += 1
                                    except Exception:
                                        pass
                                if nonempty > 0:
                                    numeric_rate = float(num_numeric) / float(nonempty)
                            # default messaging
                            if numeric_rate is not None and numeric_rate >= 0.75:
                                out.append({
                                    "text": f"Column '{col}' contains mixed types. Coerce to float and coerce unparsable values to null for review (action: cast, params: to=float)",
                                    "source": "diagnostics",
                                    "confidence": 0.9,
                                    "machine_template": f"cast column={col} to=float",
                                    "json_steps": [{"action": "cast", "column": col, "params": {"to": "float"}}],
                                    "json_step": {"action": "cast", "column": col, "params": {"to": "float"}},
                                })
                            else:
                                out.append({
                                    "text": f"Column '{col}' contains mixed types, but the sample does not support a safe numeric conversion. Review its values and intended type before changing it.",
                                    "source": "diagnostics",
                                    "confidence": 0.5,
                                })
                        except Exception:
                            out.append({
                                "text": f"Column '{col}' contains mixed types. Review its values and intended type before changing it.",
                                "source": "diagnostics"
                            })
                    # Nearly constant -> explicit suggestion
                    dataset_rows = info.get("row_count") or inspect_preview.get("row_count") or metadata.get("row_count") or 0
                    if info.get("unique_count") is not None and info.get("unique_count") <= 3 and dataset_rows > 10:
                        out.append({"text": f"Column '{col}' appears nearly-constant (unique={info.get('unique_count')}). Review whether it carries information; dropping it removes the column from the output", "source": "diagnostics", "confidence": 0.5, "json_steps": [{"action": "drop_column", "column": col, "params": {}}]})
                except Exception:
                    continue

        for ci in col_info.values():
            out.extend(ci.get("entries") or [])

        # Fallback: short summary from profile
        prof = metadata.get("profile")
        if isinstance(prof, dict):
            # e.g., detect many nulls across dataset
            try:
                total_rows = prof.get("row_count") or metadata.get("row_count") or 0
                for col, cinfo in (prof.get("columns") or {}).items():
                    try:
                        if cinfo.get("nulls", 0) and cinfo.get("nulls", 0) / max(1, total_rows) > 0.25:
                            out.append({"text": f"Column '{col}' has >25% missing values. Suggestion: consider dropping the column or imputing after discussion.", "source": "profile"})
                    except Exception:
                        continue
            except Exception:
                pass
    except Exception:
        traceback.print_exc()
    _complete_entries(out)
    # Detect cross-column transfer opportunities: numeric-like values embedded in text columns
    try:
        norm = metadata.get("normalized_path")
        if norm and Path(norm).exists():
            try:
                df = _read_table(norm)
                cols = list(df.columns if _is_polars_df(df) else list(df.columns))
                # sample up to 1000 rows to estimate types
                sample = None
                try:
                    sample = df.head(1000) if _is_polars_df(df) else (df.head(1000) if hasattr(df, 'head') else df)
                except Exception:
                    sample = df

                transfer_texts: list[str] = []
                # first pass: compute per-column stats
                col_stats: dict[str, dict[str, Any]] = {}
                for c in cols:
                    try:
                        if _is_polars_df(sample):
                            ser = sample.select(c).to_series().to_list()
                        else:
                            ser = list(sample[c].astype(str).tolist()[:1000]) if c in sample.columns else []
                        numeric_count = 0
                        date_count = 0
                        nonempty = 0
                        non_numeric_count = 0
                        vals = []
                        for v in ser[:500]:
                            s = '' if v is None else str(v).strip()
                            if s == '':
                                continue
                            nonempty += 1
                            if re.match(r'^[-+]?\d+(?:[\.,]\d+)?$', s):
                                numeric_count += 1
                                vals.append(s)
                            elif re.match(r'^\d{4}-\d{2}-\d{2}$', s):
                                date_count += 1
                            elif re.search(r'[\./]|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b', s, re.I) or re.search(r'\d{1,2}[\-/]\d{1,2}[\-/]\d{2,4}', s):
                                # treat as non-ISO date-like
                                date_count += 1
                            else:
                                non_numeric_count += 1
                        col_stats[c] = {
                            'numeric_count': numeric_count,
                            'date_count': date_count,
                            'non_numeric_count': non_numeric_count,
                            'nonempty': nonempty,
                            'examples': vals[:10]
                        }
                    except Exception:
                        col_stats[c] = {'numeric_count': 0, 'date_count': 0, 'non_numeric_count': 0, 'nonempty': 0, 'examples': []}

                # generate transfer suggestions based on col_stats
                for c, stats in col_stats.items():
                    try:
                        nonempty = stats.get('nonempty', 0)
                        numeric_count = stats.get('numeric_count', 0)
                        date_count = stats.get('date_count', 0)
                        non_numeric_count = stats.get('non_numeric_count', 0)
                        numeric_vals = stats.get('numeric_vals', [])
                        # compute simple numeric summary
                        src_median = None
                        if numeric_vals:
                            try:
                                svals = sorted(numeric_vals)
                                src_median = svals[len(svals)//2]
                            except Exception:
                                src_median = None
                        # detect year-like source column
                        src_year_like = False
                        try:
                            if numeric_vals:
                                year_like_count = sum(1 for v in numeric_vals if 1800 <= v <= 2100)
                                src_year_like = (year_like_count / max(1, len(numeric_vals))) >= 0.8
                        except Exception:
                            src_year_like = False
                        # numeric-heavy sources
                        if nonempty >= 10 and numeric_count >= 5 and (numeric_count / nonempty) > 0.15:
                            # find destination columns
                            for d, dst in col_stats.items():
                                if d == c:
                                    continue
                                # destination empty or date-like or numeric-like
                                dst_nonempty = dst.get('nonempty', 0)
                                dst_numeric = dst.get('numeric_count', 0)
                                dst_numeric_vals = dst.get('numeric_vals', [])
                                dst_median = None
                                if dst_numeric_vals:
                                    try:
                                        dvals = sorted(dst_numeric_vals)
                                        dst_median = dvals[len(dvals)//2]
                                    except Exception:
                                        dst_median = None

                                candidate_ok = False
                                # allow if destination empty
                                if dst_nonempty == 0:
                                    candidate_ok = True
                                # allow if both numeric and medians are compatible (avoid years->bedrooms)
                                elif dst_numeric > dst_nonempty * 0.5 and src_median is not None and dst_median is not None:
                                    # avoid moving large-year-like numbers into small-integer columns
                                    if src_year_like and not (1800 <= (dst_median or 0) <= 2100):
                                        candidate_ok = False
                                    else:
                                        # allow if medians are of same order magnitude
                                        try:
                                            if dst_median == 0:
                                                candidate_ok = False
                                            else:
                                                ratio = abs(src_median) / max(1, abs(dst_median))
                                                candidate_ok = 0.2 <= ratio <= 5
                                        except Exception:
                                            candidate_ok = False
                                # allow if destination is date-like and source looks like year
                                if not candidate_ok and dst.get('date_count', 0) > dst_nonempty * 0.5 and src_year_like:
                                    candidate_ok = True

                                # when source is year-like, prefer destinations with 'year' in name or empty
                                if src_year_like and not (candidate_ok and (dst_nonempty == 0 or 'year' in d.lower())):
                                    candidate_ok = False

                                if not candidate_ok:
                                    continue

                                # build suggestion text
                                if dst.get('date_count', 0) > 0 and dst.get('non_numeric_count', 0) > 0:
                                    text = (
                                        f"Put all numerical values of the '{c}' column in the '{d}' column, and move all string values of the '{d}' column except for dates in the '{c}' column."
                                    )
                                else:
                                    text = (
                                        f"Put all numerical values of the '{c}' column in the '{d}' column. Suggest coercing '{d}' to numeric and keeping remaining text in '{c}'."
                                    )
                                examples = ', '.join(sorted(set(stats.get('examples', []) or []))[:5])
                                if examples:
                                    text = text + f" Examples: {examples}"
                                existing_texts = {e.get('text') for e in out if e.get('text')}
                                if text not in existing_texts:
                                    out.append({'text': text, 'source': 'cross_column_numeric_transfer', 'confidence': 0.5, 'json_steps': [{'action': 'move_by_type', 'column': None, 'params': {
                                        'source': c, 'target': d, 'type': 'numeric', 'exceptions': [], 'replacement': '',
                                    }}]})
                                    transfer_texts.append(text)
                        # non-numeric-heavy sources -> move to mostly-empty dest
                        if nonempty >= 10 and non_numeric_count >= 5 and (non_numeric_count / nonempty) > 0.15:
                            for d, dst in col_stats.items():
                                if d == c:
                                    continue
                                if dst.get('nonempty', 0) == 0:
                                    text = f"Move non-numeric values from '{c}' into '{d}'."
                                    examples = ''
                                    existing_texts = {e.get('text') for e in out if e.get('text')}
                                    if text not in existing_texts:
                                        out.append({'text': text, 'source': 'cross_column_text_transfer', 'confidence': 0.5, 'json_steps': [{'action': 'move_by_type', 'column': None, 'params': {
                                            'source': c, 'target': d, 'type': 'string', 'exceptions': [], 'replacement': '',
                                        }}]})
                                        transfer_texts.append(text)
                    except Exception:
                        continue
            except Exception:
                pass
            # aggregate multiple transfer suggestions into one compound suggestion
            try:
                if len(transfer_texts) > 1:
                    # join compactly using commas and 'then'
                    combined = transfer_texts[0]
                    for t in transfer_texts[1:]:
                        combined = combined.rstrip('. ') + ", then " + t[0].lower() + t[1:]
                    # avoid duplicates
                    existing_texts = {e.get('text') for e in out if e.get('text')}
                    if combined and combined not in existing_texts:
                        out.append({'text': combined, 'source': 'cross_column_transfer_combined'})
            except Exception:
                pass
    except Exception:
        pass
    # Date normalization suggestions: detect non-ISO date formats
    try:
        norm = metadata.get("normalized_path")
        if norm and Path(norm).exists():
            try:
                df = _read_table(norm)
                cols = list(df.columns if _is_polars_df(df) else list(df.columns))
                sample = df.head(1000) if _is_polars_df(df) else (df.head(1000) if hasattr(df, 'head') else df)
                for c in cols:
                    try:
                        if _is_polars_df(sample):
                            ser = sample.select(c).to_series().to_list()
                        else:
                            ser = list(sample[c].astype(str).tolist()[:1000]) if c in sample.columns else []
                        date_like = 0
                        iso_like = 0
                        total = 0
                        for v in ser[:500]:
                            s = '' if v is None else str(v).strip()
                            if not s:
                                continue
                            total += 1
                            if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
                                iso_like += 1
                            # common non-ISO date patterns: slashes, month names, dots, or month name words
                            elif re.search(r'[\./]|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b', s, re.I) or re.search(r'\d{1,2}[\-/]\d{1,2}[\-/]\d{2,4}', s):
                                date_like += 1
                        if total >= 10 and date_like >= max(5, 0.2 * total) and iso_like < date_like:
                            text = f"Normalize the values of '{c}' to the ISO format YYYY-MM-DD. The day/month order is ambiguous in these values, so confirm it before converting"
                            existing_texts = {e.get('text') for e in out if e.get('text')}
                            if text not in existing_texts and not any(c in e.get('text', '') and 'date' in e.get('text', '').lower() for e in out):
                                out.append({'text': text, 'source': 'date_normalization', 'confidence': 0.4})
                    except Exception:
                        continue
            except Exception:
                pass
    except Exception:
        pass
    # dedupe identical texts and preserve order
    _complete_entries(out)
    seen = set()
    uniq: list[dict[str, Any]] = []
    for e in out:
        t = e.get("text")
        if not t or t in seen:
            continue
        seen.add(t)
        uniq.append(e)
    return uniq


def convert_uploaded_file(source_path: str | Path, output_dir: str | Path) -> dict[str, Any]:
    source_path = Path(source_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    suffix = source_path.suffix.lower()
    target = output_dir / f"{_safe_stem(source_path.name)}_{source_path.stat().st_size}_{source_path.stat().st_mtime_ns & 0xFFFF:x}.csv"
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []

    if suffix in TEXT_EXTENSIONS:
        _extract_text_rows(source_path, suffix.lstrip("."), rows)
    elif suffix in SPREADSHEET_EXTENSIONS:
        _extract_workbook_rows(source_path, rows)
    elif suffix in DOCUMENT_EXTENSIONS:
        _extract_docx_rows(source_path, rows)
    elif suffix in PRESENTATION_EXTENSIONS:
        _extract_pptx_rows(source_path, rows)
    elif suffix in GOOGLE_SHORTCUT_EXTENSIONS:
        _extract_google_shortcut_rows(source_path, output_dir, rows, warnings)
    else:
        warnings.append(f"Unsupported extension {suffix or '(none)'}; stored as line-based text fallback.")
        _extract_text_rows(source_path, suffix.lstrip(".") or "binary", rows)

    if not rows:
        warnings.append("No structured content was extracted; added a placeholder row.")
        _append_row(
            rows,
            source_kind=suffix.lstrip(".") or "unknown",
            source_name=source_path.name,
            source_path=str(source_path),
            unit_kind="placeholder",
            text="",
            notes="no structured content extracted",
        )

    _write_rows_to_csv(rows, target)

    metadata = {
        "source_path": str(source_path.resolve()),
        "normalized_path": str(target.resolve()),
        "source_kind": suffix.lstrip(".") or "unknown",
        "source_name": source_path.name,
        "row_count": len(rows),
        "warnings": warnings,
    }
    # Attempt to compute lightweight profile/inspect/suggestions synchronously.
    # If any step fails, record a warning but proceed with the original metadata.
    try:
        cleaner = Cleaner()
    except Exception as e:
        cleaner = None
        warnings.append(f"cleaner_init_failed: {e}")

    if cleaner:
        try:
            prof = cleaner.profile(str(target))
            metadata["profile"] = prof
        except Exception as e:
            warnings.append(f"profile_failed: {e}")
        try:
            insp = cleaner.inspect_source(str(target), offset=0, limit=20)
            # attach diagnostics summary and a short preview
            metadata["inspect_preview"] = insp
            try:
                metadata["diagnostics"] = insp.get("diagnostics") if isinstance(insp, dict) else None
            except Exception:
                pass
        except Exception as e:
            warnings.append(f"inspect_failed: {e}")
        try:
            # suggestions operate on profile; guard if profile missing
            try:
                prof_for_sugg = metadata.get("profile") or prof
            except Exception:
                prof_for_sugg = None
            if prof_for_sugg:
                suggs = [
                    {"column": col, "score": score, "reason": reason}
                    for col, score, reason in suggest_columns_to_clean(prof_for_sugg)
                ]
                metadata["suggestions"] = suggs
        except Exception as e:
            warnings.append(f"suggestions_failed: {e}")

    metadata_path = target.with_suffix(".json")
    # persist metadata (including any added profile/inspect/suggestions)
    try:
        # generate plain-language suggestions for immediate UI use
        try:
            metadata["plain_language_suggestions"] = generate_plain_language_suggestions(metadata)
        except Exception:
            metadata["plain_language_suggestions"] = []
        metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        # fallback: attempt to write minimal metadata
        try:
            fallback = {k: metadata[k] for k in ("source_path", "normalized_path", "source_kind", "source_name", "row_count", "warnings") if k in metadata}
            metadata_path.write_text(json.dumps(fallback, indent=2, ensure_ascii=False), encoding="utf-8")
            warnings.append("metadata_write_fallback_used")
        except Exception:
            pass

    metadata["metadata_path"] = str(metadata_path.resolve())
    metadata["path"] = metadata["normalized_path"]
    # spawn background worker to compute expensive diagnostics (fuzzy clusters, spell analysis)
    try:
        try:
            t = threading.Thread(target=compute_extended_diags, args=(metadata['normalized_path'], metadata_path), daemon=True)
            t.start()
        except Exception:
            pass
    except Exception:
        pass
    return metadata