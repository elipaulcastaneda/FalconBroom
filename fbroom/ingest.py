from __future__ import annotations

import csv
import io
import json
import os
import re
import unicodedata
from collections import Counter
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
from .semantics import (
    column_kinds as _column_kinds, fits as _kind_fits, same_meaning as _same_meaning, swap_is_safe as _swap_is_safe,
    header_kinds as _header_kinds, _header_words, is_email as _is_email, is_formatted_phone as _is_formatted_phone,
    _MISSING as _SEMANTIC_MISSING,
)
from .formats import EXTRA_DATE_FORMATS, numeric_format_entry as _numeric_format_entry
from .standardize import mixed_date_entries as _mixed_date_entries, mixed_phone_entries as _mixed_phone_entries, boolean_choice_entries as _boolean_choice_entries
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


_CSV_FIELDNAMES = [
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


class _CsvRowSink:
    def __init__(self, writer: csv.DictWriter):
        self.writer = writer
        self.count = 0

    def append(self, row: dict[str, Any]) -> None:
        self.writer.writerow(row)
        self.count += 1


def _append_row(rows: _CsvRowSink, **row: Any) -> None:
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
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_FIELDNAMES, quoting=csv.QUOTE_MINIMAL)
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


def _extract_google_drive_export(source_path: Path, output_dir: Path, rows: _CsvRowSink, warnings: list[str]) -> bool:
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


def _extract_text_rows(source_path: Path, source_kind: str, rows: _CsvRowSink) -> None:
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


def _extract_workbook_rows(source_path: Path, rows: _CsvRowSink) -> None:
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


def _extract_docx_rows(source_path: Path, rows: _CsvRowSink) -> None:
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


def _extract_pptx_rows(source_path: Path, rows: _CsvRowSink) -> None:
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


def _extract_google_shortcut_rows(source_path: Path, output_dir: Path, rows: _CsvRowSink, warnings: list[str]) -> None:
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
_ID_KINDS = frozenset({'id', 'barcode', 'card', 'routing', 'ssn', 'financial_id', 'icd', 'uuid', 'hex'})
_VALUE_KINDS = frozenset({'money', 'percent', 'date', 'quantity', 'temperature'})
_BARE_CODE_WORDS = frozenset("code codes number numbers num no nbr".split())


def _is_identifier_name(col: str) -> bool:
    """Columns whose digits are labels, not amounts (leading zeros matter, nothing should be summed or cast)."""
    if _ID_NAME_RE.search(col):
        return True
    if all(word in _BARE_CODE_WORDS for word in _header_words(col)):
        return False
    kinds = _header_kinds(col)
    return bool(kinds and kinds & _ID_KINDS and not kinds & _VALUE_KINDS)


_UNIQUE_KEY_NAME_RE = re.compile(r"email|(^|_)(id|key|sku|upc|isbn|account|acct|ssn|order|customer)(_|$)", re.I)
_NUM_RE = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)$")
_AFFIX_RE = re.compile(r"^(?P<pre>[^\d\-+.]*?)\s*(?P<num>[-+]?\d[\d,]*(?:\.\d+)?)\s*(?P<suf>[^\d]*)$")
_KNOWN_SUFFIXES = {
    'yr', 'yrs', 'year', 'years', 'kg', 'g', 'mg', 'lb', 'lbs', 'oz', 'km', 'm', 'cm', 'mm', 'mi', 'ft', 'in', '°c', '°f',
    'c', 'f', '%', 'sqft', 'sq ft', 'sq.ft', 'sf', 'ft2', 'sqm', 'm2', 'sq m', 'acre', 'acres', 'ac', 'ha', 'gal', 'l',
    'ml', 'mph', 'kph', 'km/h', 'kmh', 'mmhg', 'bpm', 'hz', 'kwh', 'kw', 'w', 'v', 'psi', 'ppm', 'mol', 'mg/dl', 'day',
    'days', 'hr', 'hrs', 'hour', 'hours', 'min', 'mins', 'sec', 'secs', 'month', 'months', 'mo', 'wk', 'wks', 'week',
    'weeks', 'pcs', 'units', 'items', 'bd', 'ba', 'beds', 'baths',
}
_MISSING_MARKERS = {'n/a', 'na', 'null', 'none', 'unknown', 'unk', 'tbd', 'not available', 'not provided', '--'} | set(_SEMANTIC_MISSING)
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
) + tuple((fmt, pattern) for fmt, pattern, _ in EXTRA_DATE_FORMATS)
_DATE_FORMAT_NOTES = {fmt: note for fmt, _, note in EXTRA_DATE_FORMATS if note}
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


def _value_shape(value: str) -> str:
    """Digits -> 9, upper -> A, lower -> a; runs of the same class collapse ('68113-3892' -> '9-9')."""
    out: list[str] = []
    for ch in value.strip():
        token = '9' if ch.isdigit() else ('A' if ch.isupper() else ('a' if ch.islower() else ch))
        if not (out and out[-1] == token and token in '9Aa'):
            out.append(token)
    return ''.join(out)


def _shape_regex(shape: str) -> str:
    return ''.join({'9': r'\d+', 'A': r'[A-Z]+', 'a': r'[A-Za-z]+'}.get(ch) or re.escape(ch) for ch in shape)


def _swap_shape(value: str) -> str:
    """Case-insensitive shape for swap detection: 'ORLANDO' and 'Orlando' are the same shape."""
    return _value_shape(re.sub(r'[A-Za-z]', 'a', value))


def _column_shapes(raw_values: list) -> tuple[list, set, set] | None:
    """Per-row shapes, the core shapes covering 75% of values, and every recurring shape."""
    shapes = [_swap_shape(v) if v else None for v in raw_values]
    counts: dict[str, int] = {}
    for shape in shapes:
        if shape is not None:
            counts[shape] = counts.get(shape, 0) + 1
    total = sum(counts.values())
    if total < 20:
        return None
    home: set = set()
    covered = 0
    for shape, count in sorted(counts.items(), key=lambda item: -item[1]):
        home.add(shape)
        covered += count
        if covered / total >= 0.75:
            break
    recurring = {shape for shape, count in counts.items() if count >= 5 and count / total >= 0.02}
    return shapes, home, home | recurring


def _shape_class(shape: str) -> str:
    if re.fullmatch(r"9(?:[-/. ]9)*[Aa]?", shape):
        return 'digit'
    if re.fullmatch(r"[Aa]+(?:[ '.-][Aa]+)*", shape):
        return 'word'
    return 'mixed'


def _belongs(shape: str, accepted: set, home: set) -> bool:
    """A shape belongs to a column when it recurs there or is the same plain class as the column's usual values."""
    if shape in accepted:
        return True
    kind = _shape_class(shape)
    return kind != 'mixed' and any(_shape_class(h) == kind for h in home)


_MIN_VOCAB_SUPPORT = 5
_VOCAB_DOMINANCE = 3
_MAX_EXACT_SWAP_VALUES = 200


def _lives_in(key: str, here: Counter, there: Counter) -> bool:
    """A value 'lives in' a column when it recurs there and clearly dominates its count in the other column."""
    return here[key] >= _MIN_VOCAB_SUPPORT and here[key] >= _VOCAB_DOMINANCE * there[key]


def _vocabulary_swap(left: str, right: str, left_raw: list, right_raw: list, left_seen: Counter, right_seen: Counter) -> dict[str, Any] | None:
    """Text-vs-text swaps can't be told apart by shape, so judge each row by which column its values live in."""
    crossed: list[int] = []
    home_rows = 0
    for index in range(min(len(left_raw), len(right_raw))):
        if not left_raw[index] or not right_raw[index]:
            continue
        a, b = left_raw[index].casefold(), right_raw[index].casefold()
        if _lives_in(a, left_seen, right_seen) and _lives_in(b, right_seen, left_seen):
            home_rows += 1
        elif _lives_in(a, right_seen, left_seen) and _lives_in(b, left_seen, right_seen):
            crossed.append(index)
    if len(crossed) < 5 or home_rows < 10 or home_rows < len(crossed) * 2:
        return None
    exact_left = {left_raw[i] for i in crossed}
    exact_right = {right_raw[i] for i in crossed}
    if len(exact_left) > _MAX_EXACT_SWAP_VALUES or len(exact_right) > _MAX_EXACT_SWAP_VALUES:
        return None
    moves = [
        {'source': left, 'target': right, 'type': 'pattern', 'pattern': '(?i:' + '|'.join(re.escape(v) for v in sorted(exact_left)) + ')', 'exceptions': []},
        {'source': right, 'target': left, 'type': 'pattern', 'pattern': '(?i:' + '|'.join(re.escape(v) for v in sorted(exact_right)) + ')', 'exceptions': []},
    ]
    return {
        'left': left, 'right': right, 'rows': len(crossed), 'home_rows': home_rows, 'crossed': crossed,
        'left_examples': _uniq([left_raw[i] for i in crossed], 3),
        'right_examples': _uniq([right_raw[i] for i in crossed], 3),
        'step': {'action': 'swap_by_types', 'column': None, 'params': {'moves': moves, 'replacement': ''}},
    }


def _shape_swaps(col_stats: dict[str, dict[str, Any]], skip_pairs: set) -> list[dict[str, Any]]:
    """Find column pairs whose values sit in each other's place on the same rows."""
    profiles = {}
    vocabularies = {}
    meanings = {}
    for col, stats in col_stats.items():
        profile = _column_shapes(stats.get('raw_values') or [])
        if profile:
            profiles[col] = profile
            vocabularies[col] = Counter(v.casefold() for v in stats['raw_values'] if v)
            meanings[col] = _column_kinds(stats['raw_values'])
    found: list[dict[str, Any]] = []
    names = list(profiles)
    for left_index, left in enumerate(names):
        for right in names[left_index + 1:]:
            if (left, right) in skip_pairs:
                continue
            left_kinds, right_kinds = meanings[left][1], meanings[right][1]
            if _same_meaning(meanings[left][0], meanings[right][0]):
                continue
            left_shapes, left_home, left_accepted = profiles[left]
            right_shapes, right_home, right_accepted = profiles[right]
            left_raw, right_raw = col_stats[left]['raw_values'], col_stats[right]['raw_values']
            if all(_shape_class(shape) != 'digit' for shape in left_home | right_home):
                match = _vocabulary_swap(left, right, left_raw, right_raw, vocabularies[left], vocabularies[right])
                if match and _swap_is_safe(left, right, left_raw, right_raw, match['crossed'], match['home_rows'], meanings[left], meanings[right]):
                    found.append(match)
                continue
            if left_home & right_home:
                continue
            crossed: list[int] = []
            home_rows = 0
            for index in range(min(len(left_shapes), len(right_shapes))):
                a, b = left_shapes[index], right_shapes[index]
                if a is None or b is None:
                    continue
                if a in left_home and b in right_home:
                    home_rows += 1
                elif (
                    a not in left_home and _belongs(a, right_accepted, right_home)
                    and b not in right_home and _belongs(b, left_accepted, left_home)
                    and not _kind_fits(left_raw[index], left_kinds)
                    and not _kind_fits(right_raw[index], right_kinds)
                ):
                    crossed.append(index)
            if len(crossed) < 5 or home_rows < 10 or home_rows < len(crossed) * 2:
                continue
            left_set = sorted({left_shapes[i] for i in crossed})
            right_set = sorted({right_shapes[i] for i in crossed})
            if len(left_set) > 12 or len(right_set) > 12:
                continue
            if not _swap_is_safe(left, right, left_raw, right_raw, crossed, home_rows, meanings[left], meanings[right]):
                continue
            moves = [
                {'source': left, 'target': right, 'type': 'pattern', 'pattern': '|'.join(_shape_regex(s) for s in left_set), 'exceptions': []},
                {'source': right, 'target': left, 'type': 'pattern', 'pattern': '|'.join(_shape_regex(s) for s in right_set), 'exceptions': []},
            ]
            found.append({
                'left': left, 'right': right, 'rows': len(crossed), 'home_rows': home_rows,
                'left_examples': _uniq([left_raw[i] for i in crossed], 3),
                'right_examples': _uniq([right_raw[i] for i in crossed], 3),
                'step': {'action': 'swap_by_types', 'column': None, 'params': {'moves': moves, 'replacement': ''}},
            })
    return found

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


def _parses_with(value: str, fmt: str) -> bool:
    try:
        datetime.strptime(value, fmt)
        return True
    except ValueError:
        return False


def _drop_rows_entry(col: str, count: int, total: int, description: str, params: dict[str, Any], examples: list) -> dict[str, Any]:
    shown = f" (e.g. {', '.join(repr(e) for e in examples)})" if examples else ''
    return _entry(
        f"Or drop the {count} row(s) where '{col}' {description}{shown} among {total} analyzed rows; this removes the whole row from the output instead of fixing the value",
        None,
        0.4,
        [{'action': 'drop_rows', 'column': col, 'params': params}],
    )


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
    if n < 1:
        return {'kind': 'text', 'entries': []}
    total_rows = n + missing_count
    blank_count = missing_count - len(markers)

    def drop_entries(issues: list[tuple[str, dict[str, Any], int, list]]) -> list[dict[str, Any]]:
        """Row-removal alternatives as (description, params, count, examples); skipped if over half the rows would go."""
        found = list(issues)
        if blank_count:
            found.append(('is blank', {'issue': 'blank'}, blank_count, []))
        if markers:
            distinct = _uniq(markers, 12)
            found.append((f"is {', '.join(distinct)}", {'issue': 'values', 'values': distinct}, len(markers), distinct))
        return [
            _drop_rows_entry(col, count, total_rows, description, params, _uniq(examples, 3))
            for description, params, count, examples in found
            if 0 < count and count / total_rows <= 0.5
        ]

    pending_drops: list[dict[str, Any]] = []
    european_numbers = [s for s in vals if _LOCALE_NUMBER_RE.fullmatch(re.sub(r"[€£$¥₹\s]", "", s))]
    if european_numbers:
        entries = [_entry(
            f"'{col}' contains comma-decimal values such as {', '.join(_uniq(european_numbers))}. Review the number locale before converting; automatic comma removal could change their magnitude",
            None, 0.45)]
        converted = _numeric_format_entry(
            col, vals,
            lambda s: not _LOCALE_NUMBER_RE.fullmatch(re.sub(r"[€£$¥₹\s]", "", s)) and _classify_cell(s)[0] in ('numeric', 'formatted'))
        if converted:
            entries.append(_entry(converted['text'], None, 0.5, converted['steps']))
        return {'kind': 'locale_number_review', 'entries': entries}
    header_kinds_here = _header_kinds(col) or frozenset()
    date_format_counts = []
    for date_fmt, pattern in _DATE_FORMATS:
        if date_fmt == '%Y%m%d' and 'date' not in header_kinds_here:
            continue
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
    if not _is_identifier_name(col) and date_format_counts:
        ranked_date_formats = sorted(date_format_counts, key=lambda item: item[1], reverse=True)
        fmt, matched = ranked_date_formats[0]
        runner_up = ranked_date_formats[1][1] if len(ranked_date_formats) > 1 else 0
        if matched / n >= 0.9 and matched > runner_up:
            invalid_count = n - matched
            text = f"Parse consistently formatted dates in '{col}' using {fmt}"
            if _DATE_FORMAT_NOTES.get(fmt):
                text += f" ({_DATE_FORMAT_NOTES[fmt]})"
            if invalid_count:
                text += f"; {invalid_count} other value(s) will become empty"
            return {'kind': 'date', 'entries': [_entry(
                text,
                None,
                0.85 if invalid_count == 0 else 0.7,
                [{'action': 'cast', 'column': col, 'params': {'to': 'datetime', 'fmt': fmt}}],
            )] + drop_entries([(
                f"is not a valid {fmt} date", {'issue': 'not_date', 'fmt': fmt}, invalid_count,
                [s for s in vals if not _parses_with(s, fmt)],
            )] if invalid_count else [])}
    date_matches = sum(1 for s in vals if _DATE_LIKE_RE.match(s))
    if not _is_identifier_name(col):
        mixed_dates = _mixed_date_entries(col, vals, _DATE_FORMATS, _entry)
        if mixed_dates:
            return {'kind': 'date', 'entries': mixed_dates}
    if date_matches / n >= 0.75:
        iso = sum(1 for s in vals if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s))
        if iso / n >= 0.9 and not _is_identifier_name(col):
            bad_dates = [s for s in vals if not _parses_with(s, '%Y-%m-%d')]
            return {'kind': 'date', 'entries': [_entry(
                f"Parse ISO dates in '{col}' as dates; invalid values will become empty",
                None, 0.85, [{'action': 'cast', 'column': col, 'params': {'to': 'datetime', 'fmt': '%Y-%m-%d'}}]) ] + drop_entries(
                [("is not a valid %Y-%m-%d date", {'issue': 'not_date', 'fmt': '%Y-%m-%d'}, len(bad_dates), bad_dates)] if bad_dates else [])}
        return {'kind': 'date_review', 'entries': [_entry(
            f"'{col}' contains date-like values, but the format or day/month order is ambiguous. Review examples before converting: {', '.join(_uniq(vals))}",
            None, 0.45)]}
    kinds = [_classify_cell(s) for s in vals]
    cnt = {'numeric': 0, 'formatted': 0, 'affix': 0, 'zip4': 0, 'text': 0}
    for k, _, _ in kinds:
        cnt[k] += 1
    digits5 = sum(1 for s in vals if re.fullmatch(r"\d{5}", s))
    zip_like = bool(re.search(r"zip|postal|post_?code", col, re.I)) or (cnt['zip4'] > 0 and (cnt['zip4'] + digits5) / n >= 0.5)
    email_like = bool(re.search(r'email|e_mail', col, re.I)) or sum(_is_email(s) for s in vals) / n >= 0.6
    phone_like = bool(re.search(r'phone|mobile|telephone|\btel\b', col, re.I)) or sum(_is_formatted_phone(s) for s in vals) / n >= 0.6
    leading_zero_ids = n >= 3 and sum(1 for s in vals if re.fullmatch(r"0\d{2,}", s)) / n >= 0.3
    if zip_like:
        result = {'kind': 'identifier', 'entries': _zip_entries(col, vals)}
        words = [s for s in vals if not re.search(r"\d", s)]
        if words:
            pending_drops.extend(drop_entries([("is text with no digits", {'issue': 'matches', 'pattern': r"\D+"}, len(words), words)]))
    elif email_like:
        spaced = [s for s in raw_values if s != s.strip()]
        invalid = [s.strip() for s in vals if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", s.strip())]
        ents = []
        if spaced:
            ents.append(_entry(f"Trim surrounding whitespace from {len(spaced)} email values in '{col}'", f'normalize column={_q(col)} case=trim', 0.9,
                               [{'action': 'normalize', 'column': col, 'params': {'case': 'trim'}}]))
        if invalid:
            ents.append(_entry(f"Review {len(invalid)} email-shaped values in '{col}' that do not match a basic email format; examples: {', '.join(_uniq(invalid))}", None, 0.5))
            pending_drops.extend(drop_entries([("is not a valid email address", {'issue': 'not_matches', 'pattern': r"[^\s@]+@[^\s@]+\.[^\s@]+"}, len(invalid), invalid)]))
        result = {'kind': 'identifier', 'entries': ents}
    elif phone_like:
        compact = [re.sub(r'[^\d+]', '', s) for s in vals]
        malformed = [s for s, v in zip(vals, compact) if not re.fullmatch(r"\+?\d{7,15}", v)]
        punctuation = any(re.search(r'[\s().-]', s) for s in vals)
        if not malformed and punctuation:
            ents = _mixed_phone_entries(col, vals, _entry)
            text = f"Remove display punctuation and spaces from phone numbers in '{col}' while preserving the leading country-code plus sign"
            ents.append(_entry(text, None, 0.8, [{'action': 'regex_replace', 'column': col, 'params': {'pattern': r'[\s().-]', 'replace': ''}}]))
        else:
            ents = [_entry(f"Review {len(malformed)} phone values in '{col}' with extensions or unusual formats before normalizing; examples: {', '.join(_uniq(malformed))}", None, 0.5)] if malformed else []
        result = {'kind': 'identifier', 'entries': ents}
    elif _is_identifier_name(col) or leading_zero_ids:
        entries = []
        result = {'kind': 'identifier', 'entries': entries}
    else:
        formatted = _numeric_format_entry(col, vals, lambda s: _classify_cell(s)[0] in ('numeric', 'formatted'))
        if formatted:
            result = {'kind': 'numeric', 'entries': [_entry(formatted['text'], None, formatted['confidence'], formatted['steps'])]}
        else:
            result = _numeric_entries(col, vals, kinds, cnt)
        bad_numbers = [s for s, (k, _, _) in zip(vals, kinds) if k in ('text', 'zip4')]
        if result.get('kind') == 'numeric' and not cnt['affix'] and bad_numbers:
            pending_drops.extend(drop_entries([("is not a number", {'issue': 'not_numeric'}, len(bad_numbers), bad_numbers)]))

    if not any(e['json_steps'][0]['params'].get('issue') in ('blank', 'values') for e in pending_drops):
        pending_drops.extend(drop_entries([]))

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
            steps = [{'action': 'regex_replace', 'column': col, 'params': {'pattern': marker_pattern, 'replace': ''}}]
            steps += [s for s in (first.get('json_steps') or []) if s.get('action') == 'regex_replace']
            steps.append({'action': 'cast', 'column': col, 'params': {'to': target}})
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

    compatibility_values = [value for value in vals if unicodedata.normalize('NFKC', value) != value]
    if compatibility_values:
        result['entries'].append(_entry(
            f"Normalize compatibility characters in '{col}' using NFKC ({len(compatibility_values)} sampled value(s) change; examples: {', '.join(_uniq(compatibility_values))})",
            None,
            0.8,
            [{'action': 'unicode_normalize', 'column': col, 'params': {'form': 'NFKC'}}],
        ))

    # Suggest normalization only for low-cardinality labels with case/space variants.
    preferred_case = _preferred_abbreviation_case(col, vals)
    proper_name_field = bool(_GEOGRAPHIC_FIELD_RE.search(col) or _ORGANIZATION_FIELD_RE.search(col)) and preferred_case is None
    if result.get('kind') not in ('identifier', 'date', 'date_review') and n >= 2:
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
        boolean_field = bool(re.search(r'active|enabled|verified|consent|is_|^has_|^on_|passed|failed|approved|paid|subscribed|accepted|confirmed|opted|flag|bool', col, re.I))
        word_labels = {'yes', 'no', 'true', 'false', 'pass', 'fail', 'passed', 'failed', 'approved', 'rejected', 'paid', 'unpaid'}
        true_words = {'yes', 'true', 'pass', 'passed', 'approved', 'paid'}
        value_driven = bool(lowered) and lowered <= word_labels and bool(lowered & true_words) and bool(lowered - true_words)
        bool_labels.update({'pass': 'true', 'passed': 'true', 'approved': 'true', 'paid': 'true', 'verified': 'true', 'confirmed': 'true',
                            'fail': 'false', 'failed': 'false', 'rejected': 'false', 'unpaid': 'false', 'denied': 'false'})
        if (boolean_field or value_driven) and lowered and lowered <= set(bool_labels):
            mapping = {
                raw: bool_labels[raw.strip().lower()]
                for raw in raw_values
            }
            if mapping:
                result['entries'].append(_entry(
                    f"Standardize yes/no variants in '{col}' to true/false",
                    None, 0.8, [{'action': 'map', 'column': col, 'params': {'mapping': mapping}}]))
                result['entries'].extend(_boolean_choice_entries(col, mapping, _entry))

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
    result['entries'].extend(pending_drops)
    return result


def _analyze_columns(path: str | None, df=None) -> dict[str, dict[str, Any]]:
    """Analyze every column of the normalized table so suggestions reflect the real value patterns."""
    out: dict[str, dict[str, Any]] = {}
    if df is None:
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
            row_count = len(df)
            if row_count > 5000:
                indices = [i * (row_count - 1) // 4999 for i in range(5000)]
                if _is_polars_df(df):
                    vals = df.get_column(c).gather(indices).to_list()
                else:
                    vals = df[c].iloc[indices].tolist()
            else:
                vals = df[c].to_list() if _is_polars_df(df) else df[c].tolist()
        except Exception:
            continue
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


def _label_suggestion(entry: dict[str, Any]) -> None:
    """Tag a suggestion with a display category and the columns it concerns, so the UI can group it."""
    steps = entry.get('json_steps') or []
    actions = {step.get('action') for step in steps}
    if not steps:
        category = 'review'
    elif 'drop_rows' in actions:
        category = 'drop_rows'
    elif actions & {'move_by_type', 'swap_by_types'}:
        category = 'move'
    elif actions & {'drop_column', 'deduplicate', 'remove_by_type'}:
        category = 'remove'
    else:
        category = 'fix'

    columns: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value and value not in columns:
            columns.append(value)
        elif isinstance(value, list):
            for item in value:
                add(item)

    for step in steps:
        add(step.get('column'))
        params = step.get('params') or {}
        add(params.get('source'))
        add(params.get('target'))
        for move in params.get('moves') or []:
            if isinstance(move, dict):
                add(move.get('source'))
                add(move.get('target'))
    if not columns:
        quoted = re.search(r"'([^']+)'", str(entry.get('text') or ''))
        if quoted:
            columns.append(quoted.group(1))
    entry['category'] = category
    entry['column'] = columns[0] if columns else None
    entry['columns'] = columns


def generate_plain_language_suggestions(metadata: dict[str, Any], df=None) -> list[dict[str, Any]]:
    """Create short, editable plain-English suggestions from metadata/diagnostics.

    Returns a list of {text: str, source: str} entries.
    """
    out: list[dict[str, Any]] = []
    try:
        col_info = _analyze_columns(metadata.get("normalized_path") or metadata.get("path"), df=df)
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
                if df is None:
                    df = _read_table(norm)
                cols = list(df.columns if _is_polars_df(df) else list(df.columns))
                # sample up to 5000 rows to estimate types
                sample = None
                try:
                    sample = df.head(5000) if _is_polars_df(df) else (df.head(5000) if hasattr(df, 'head') else df)
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
                            ser = list(sample[c].tolist()[:5000]) if c in sample.columns else []
                        numeric_count = 0
                        date_count = 0
                        nonempty = 0
                        non_numeric_count = 0
                        vals = []
                        raw_values = []
                        type_values = []
                        type_counts = {'numeric': 0, 'string': 0, 'date': 0}
                        type_examples = {'numeric': [], 'string': [], 'date': []}
                        for v in ser[:5000]:
                            try:
                                missing = v is None or (isinstance(v, float) and v != v)
                            except Exception:
                                missing = v is None
                            s = '' if missing else str(v).strip()
                            raw_values.append(s or None)
                            if s == '':
                                type_values.append(None)
                                continue
                            nonempty += 1
                            if _DATE_LIKE_RE.match(s):
                                kind = 'date'
                            else:
                                cell_kind, _, _ = _classify_cell(s)
                                kind = 'numeric' if cell_kind in ('numeric', 'formatted') else 'string'
                            type_values.append(kind)
                            type_counts[kind] += 1
                            if len(type_examples[kind]) < 5:
                                type_examples[kind].append(s)
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
                            'examples': vals[:10],
                            'type_values': type_values,
                            'type_counts': type_counts,
                            'type_examples': type_examples,
                            'raw_values': raw_values,
                        }
                    except Exception:
                        col_stats[c] = {'numeric_count': 0, 'date_count': 0, 'non_numeric_count': 0, 'nonempty': 0, 'examples': [], 'type_values': [], 'type_counts': {'numeric': 0, 'string': 0, 'date': 0}, 'type_examples': {'numeric': [], 'string': [], 'date': []}}

                # Suggest type removal only for a small, non-date minority in ordinary data columns.
                for col, stats in col_stats.items():
                    counts = stats.get('type_counts') or {}
                    numeric_count = counts.get('numeric', 0)
                    string_count = counts.get('string', 0)
                    typed_count = numeric_count + string_count
                    if (
                        _is_identifier_name(col)
                        or counts.get('date', 0)
                        or typed_count < 20
                        or not numeric_count
                        or not string_count
                    ):
                        continue
                    minority_type = 'numeric' if numeric_count < string_count else 'string'
                    minority_count = min(numeric_count, string_count)
                    minority_share = minority_count / typed_count
                    if minority_count < 5 or minority_share > 0.2:
                        continue
                    examples = ', '.join(repr(value) for value in (stats.get('type_examples') or {}).get(minority_type, []))
                    readable_type = 'numeric' if minority_type == 'numeric' else 'text'
                    step = {'action': 'remove_by_type', 'column': col, 'params': {'target_type': minority_type, 'replacement': ''}}
                    out.append({
                        'text': f"'{col}' has {minority_count} {readable_type} value(s) ({minority_share:.0%} of the typed sample), versus {typed_count - minority_count} of the other type. Review whether to blank the minority values{f' such as {examples}' if examples else ''}.",
                        'source': 'mixed_type_minority',
                        'confidence': 0.65,
                        'json_steps': [step],
                    })

                # Recommend reciprocal swaps only when off-type values align across rows
                # and each column has a strong, opposite dominant type.
                columns = list(col_stats)
                meanings = {col: _column_kinds(stats['raw_values']) for col, stats in col_stats.items() if stats.get('raw_values')}
                swap_columns: set[str] = set()
                swap_pairs: set[tuple[str, str]] = set()
                shape_swap_columns: set[str] = set()
                for left_index, left in enumerate(columns):
                    left_stats = col_stats[left]
                    left_counts = left_stats.get('type_counts') or {}
                    left_total = left_counts.get('numeric', 0) + left_counts.get('string', 0)
                    if _is_identifier_name(left) or left_counts.get('date') or left_total < 20:
                        continue
                    for right in columns[left_index + 1:]:
                        right_stats = col_stats[right]
                        right_counts = right_stats.get('type_counts') or {}
                        right_total = right_counts.get('numeric', 0) + right_counts.get('string', 0)
                        if _is_identifier_name(right) or right_counts.get('date') or right_total < 20:
                            continue
                        orientations = (
                            ('string', 'numeric', 'numeric', 'string'),
                            ('numeric', 'string', 'string', 'numeric'),
                        )
                        for left_expected, right_expected, left_wrong, right_wrong in orientations:
                            if left_counts.get(left_expected, 0) / left_total < 0.65 or right_counts.get(right_expected, 0) / right_total < 0.65:
                                continue
                            comparable = min(len(left_stats['type_values']), len(right_stats['type_values']))
                            expected_rows = sum(
                                left_stats['type_values'][i] == left_expected and right_stats['type_values'][i] == right_expected
                                for i in range(comparable)
                            )
                            wrong_rows = sum(
                                left_stats['type_values'][i] == left_wrong and right_stats['type_values'][i] == right_wrong
                                for i in range(comparable)
                            )
                            if wrong_rows < 5 or expected_rows < 10 or expected_rows < wrong_rows * 2:
                                continue
                            if meanings.get(left) and meanings.get(right):
                                if _same_meaning(meanings[left][0], meanings[right][0]):
                                    continue
                                left_values, right_values = left_stats['raw_values'], right_stats['raw_values']
                                crossed_rows = [
                                    i for i in range(min(comparable, len(left_values), len(right_values)))
                                    if left_stats['type_values'][i] == left_wrong and right_stats['type_values'][i] == right_wrong
                                    and not _kind_fits(left_values[i] or '', meanings[left][1])
                                    and not _kind_fits(right_values[i] or '', meanings[right][1])
                                ]
                                foreign_rows = len(crossed_rows)
                                if foreign_rows < 5 or foreign_rows < wrong_rows * 0.9:
                                    continue
                                if not _swap_is_safe(left, right, left_values, right_values, crossed_rows, expected_rows, meanings[left], meanings[right]):
                                    continue
                            moves = [
                                {'source': left, 'target': right, 'type': left_wrong, 'exceptions': []},
                                {'source': right, 'target': left, 'type': right_wrong, 'exceptions': []},
                            ]
                            step = {'action': 'swap_by_types', 'column': None, 'params': {'moves': moves, 'replacement': ''}}
                            out.append({
                                'text': f"'{left}' is mostly {left_expected} and '{right}' is mostly {right_expected}, but {wrong_rows} sampled row(s) have {left_wrong} values in '{left}' alongside {right_wrong} values in '{right}' (the dominant pairing appears in {expected_rows} rows). Review swapping those off-type values.",
                                'source': 'cross_column_type_swap',
                                'confidence': 0.7,
                                'json_steps': [step],
                            })
                            swap_columns.update((left, right))
                            swap_pairs.add((left, right))
                            break

                # Any two columns whose values sit in each other's place on the same rows (zip<->city, phone<->name, ...).
                for found in _shape_swaps(col_stats, swap_pairs):
                    left, right = found['left'], found['right']
                    out.append({
                        'text': f"On {found['rows']} sampled row(s), '{left}' holds values that look like '{right}' (e.g. {', '.join(map(repr, found['left_examples']))}) while '{right}' holds values that look like '{left}' (e.g. {', '.join(map(repr, found['right_examples']))}); the usual pairing appears on {found['home_rows']} rows. Swap them on those rows.",
                        'source': 'cross_column_shape_swap',
                        'confidence': 0.85,
                        'json_steps': [found['step']],
                    })
                    swap_columns.update((left, right))
                    shape_swap_columns.update((left, right))
                if shape_swap_columns:
                    out[:] = [
                        entry for entry in out
                        if not (entry.get('source') == 'diagnostics' and any(f"in '{col}' are text, not ZIP codes" in entry.get('text', '') for col in shape_swap_columns))
                    ]

                if swap_columns:
                    out[:] = [
                        entry for entry in out
                        if entry.get('source') != 'mixed_type_minority'
                        or not any(step.get('column') in swap_columns for step in entry.get('json_steps') or [])
                    ]

                # A one-way move is executable only when misplaced values align with blanks
                # in a destination that otherwise contains the same value type.
                move_sources: set[str] = set()
                for source, source_stats in col_stats.items():
                    source_counts = source_stats.get('type_counts') or {}
                    source_total = source_counts.get('numeric', 0) + source_counts.get('string', 0)
                    if _is_identifier_name(source) or source_counts.get('date') or source_total < 20:
                        continue
                    for target_type in ('numeric', 'string'):
                        minority_count = source_counts.get(target_type, 0)
                        if minority_count < 5 or minority_count / source_total > 0.2:
                            continue
                        for target, target_stats in col_stats.items():
                            if target == source or target in swap_columns or _is_identifier_name(target):
                                continue
                            target_counts = target_stats.get('type_counts') or {}
                            target_total = target_counts.get('numeric', 0) + target_counts.get('string', 0)
                            if target_counts.get('date') or target_total < 20:
                                continue
                            if target_counts.get(target_type, 0) / target_total < 0.75:
                                continue
                            row_count = min(len(source_stats['type_values']), len(target_stats['type_values']))
                            aligned_blanks = sum(
                                source_stats['type_values'][index] == target_type
                                and target_stats['type_values'][index] is None
                                for index in range(row_count)
                            )
                            target_has_value = sum(
                                source_stats['type_values'][index] != target_type
                                and target_stats['type_values'][index] == target_type
                                for index in range(row_count)
                            )
                            if aligned_blanks < 5 or aligned_blanks / minority_count < 0.9 or target_has_value < 10:
                                continue
                            if source in move_sources:
                                continue
                            step = {'action': 'move_by_type', 'column': None, 'params': {
                                'source': source, 'target': target, 'type': target_type,
                                'exceptions': [], 'replacement': '',
                            }}
                            out.append({
                                'text': f"'{source}' contains {aligned_blanks} {target_type} value(s) on rows where '{target}' is blank; '{target}' contains {target_type} values on {target_has_value} other sampled rows. Review moving those values into '{target}'.",
                                'source': 'row_aligned_type_transfer',
                                'confidence': 0.8,
                                'json_steps': [step],
                            })
                            move_sources.add(source)
                            break
                if move_sources:
                    out[:] = [
                        entry for entry in out
                        if entry.get('source') != 'mixed_type_minority'
                        or not any(step.get('column') in move_sources for step in entry.get('json_steps') or [])
                    ]

                # Text in a ZIP column belongs in the column holding similar text (e.g. city names).
                zip_name_re = re.compile(r'zip|postal|post_?code', re.I)
                place_name_re = re.compile(r'city|town|place|locality|municipality', re.I)
                text_shape = re.compile(r"[A-Za-z][A-Za-z .'-]*")
                for zip_col, zip_stats in col_stats.items():
                    if not zip_name_re.search(zip_col) or zip_col in shape_swap_columns:
                        continue
                    zip_raw = zip_stats.get('raw_values') or []
                    zip_text_rows = [i for i, v in enumerate(zip_raw) if v and not re.search(r'\d', v)]
                    if len(zip_text_rows) < 5:
                        continue
                    # Mixed digit/letter values would be swept up by a type-based step, so they must be cleaned first.
                    mixed_digit = {v for v in zip_raw if v and re.search(r'\d', v) and not re.fullmatch(r'\d+', v)}
                    zip4_values = {v for v in mixed_digit if re.fullmatch(r'\d{5}-\d{4}', v)}
                    trailing_values = {v for v in mixed_digit if re.fullmatch(r'\d{4,5}[A-Za-z]+', v)}
                    unknown_values = mixed_digit - zip4_values - trailing_values
                    cleanup_steps = []
                    if zip4_values:
                        cleanup_steps.append({'action': 'regex_replace', 'column': zip_col, 'params': {'pattern': r'^(\d{5})-\d{4}$', 'replace': r'\1'}})
                    if trailing_values:
                        cleanup_steps.append({'action': 'regex_replace', 'column': zip_col, 'params': {'pattern': r'^(\d{4,5})[A-Za-z]+$', 'replace': r'\1'}})
                    zip_text = {zip_raw[i].casefold() for i in zip_text_rows}
                    best = None
                    for target, target_stats in col_stats.items():
                        if target == zip_col or zip_name_re.search(target):
                            continue
                        target_raw = target_stats.get('raw_values') or []
                        row_count = min(len(zip_raw), len(target_raw))
                        target_text_rows = {i for i in range(row_count) if target_raw[i] and not re.search(r'\d', target_raw[i])}
                        elsewhere = [i for i in target_text_rows if i not in set(zip_text_rows)]
                        if len(elsewhere) < 10:
                            continue
                        target_text = {target_raw[i].casefold() for i in target_text_rows}
                        overlap = len(zip_text & target_text) / len(zip_text)
                        shape = sum(bool(text_shape.fullmatch(zip_raw[i])) for i in zip_text_rows) / len(zip_text_rows)
                        name_match = bool(place_name_re.search(target))
                        if overlap < 0.3 and not (name_match and shape >= 0.8):
                            continue
                        score = overlap + (0.5 if name_match else 0) + shape * 0.1
                        if best is None or score > best[0]:
                            best = (score, target, target_raw, row_count)
                    if best is None:
                        continue
                    _, target, target_raw, row_count = best
                    both_text = sum(1 for i in zip_text_rows if i < row_count and target_raw[i] and not re.search(r'\d', target_raw[i]))
                    target_blank = sum(1 for i in zip_text_rows if i >= row_count or not target_raw[i])
                    target_digits = [i for i in range(row_count) if target_raw[i] and re.fullmatch(r'\d+', target_raw[i])]
                    digits_on_text_rows = sum(1 for i in target_digits if zip_raw[i] and not re.search(r'\d', zip_raw[i]))
                    examples = ', '.join(_uniq([zip_raw[i] for i in zip_text_rows]))
                    count = len(zip_text_rows)
                    blocked_reason = None
                    if unknown_values:
                        blocked_reason = f"'{zip_col}' also contains values such as {', '.join(_uniq(sorted(unknown_values)))} that mix digits and letters in an unrecognized way"
                    elif both_text:
                        blocked_reason = f"'{target}' already holds text on {both_text} of those rows (e.g. {', '.join(_uniq([target_raw[i] for i in zip_text_rows if i < row_count and target_raw[i]]))}), so a move would overwrite it"
                    transfer = None
                    if blocked_reason:
                        pass
                    elif target_blank == count:
                        transfer = {'action': 'move_by_type', 'column': None, 'params': {'source': zip_col, 'target': target, 'type': 'string', 'exceptions': [], 'replacement': ''}}
                        text = f"Move the {count} text value(s) in '{zip_col}' (e.g. {examples}) into '{target}', where they match the other values and the cell is blank."
                    elif target_digits and digits_on_text_rows == len(target_digits) and digits_on_text_rows >= max(5, int(count * 0.8)):
                        moves = [
                            {'source': zip_col, 'target': target, 'type': 'string', 'exceptions': []},
                            {'source': target, 'target': zip_col, 'type': 'numeric', 'exceptions': []},
                        ]
                        transfer = {'action': 'swap_by_types', 'column': None, 'params': {'moves': moves, 'replacement': ''}}
                        text = f"Swap the {count} text value(s) in '{zip_col}' (e.g. {examples}) with the ZIP-like numbers in '{target}' on the same rows; the text matches the other values in '{target}'."
                    else:
                        blocked_reason = f"'{target}' is neither blank nor ZIP-like on those rows, so the rows do not line up for a swap or move"
                    if transfer is None:
                        out.append({
                            'text': f"{count} text value(s) in '{zip_col}' (e.g. {examples}) look like they belong in '{target}', but no automatic transfer is offered: {blocked_reason}. Review those rows.",
                            'source': 'zip_text_transfer_blocked',
                            'confidence': 0.6,
                        })
                    else:
                        out.append({'text': text, 'source': 'zip_text_transfer', 'confidence': 0.8, 'json_steps': cleanup_steps + [transfer]})
                    out[:] = [
                        entry for entry in out
                        if not (entry.get('source') == 'diagnostics' and f"in '{zip_col}' are text, not ZIP codes" in entry.get('text', ''))
                    ]
            except Exception:
                pass
    except Exception:
        pass
    # Date normalization suggestions: detect non-ISO date formats
    try:
        norm = metadata.get("normalized_path")
        if norm and Path(norm).exists():
            try:
                if df is None:
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
                            elif re.search(r'\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b', s, re.I) or re.search(r'\d{1,4}[\-/.]\d{1,2}[\-/.]\d{2,4}', s):
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
        _label_suggestion(e)
        uniq.append(e)
    return uniq


def convert_uploaded_file(source_path: str | Path, output_dir: str | Path) -> dict[str, Any]:
    source_path = Path(source_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    suffix = source_path.suffix.lower()
    target = output_dir / f"{_safe_stem(source_path.name)}_{source_path.stat().st_size}_{source_path.stat().st_mtime_ns & 0xFFFF:x}.csv"
    warnings: list[str] = []
    temporary_target = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        with temporary_target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=_CSV_FIELDNAMES, quoting=csv.QUOTE_MINIMAL)
            writer.writeheader()
            rows = _CsvRowSink(writer)

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

            if rows.count == 0:
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
            row_count = rows.count

        temporary_target.replace(target)
    except BaseException:
        temporary_target.unlink(missing_ok=True)
        raise

    metadata = {
        "source_path": str(source_path.resolve()),
        "normalized_path": str(target.resolve()),
        "source_kind": suffix.lstrip(".") or "unknown",
        "source_name": source_path.name,
        "row_count": row_count,
        "warnings": warnings,
    }
    # Attempt to compute lightweight profile/inspect/suggestions synchronously.
    # If any step fails, record a warning but proceed with the original metadata.
    try:
        analysis_frame = _read_table(str(target))
    except Exception:
        analysis_frame = None
    try:
        cleaner = Cleaner()
    except Exception as e:
        cleaner = None
        warnings.append(f"cleaner_init_failed: {e}")

    if cleaner:
        try:
            prof = cleaner.profile(str(target), df=analysis_frame)
            metadata["profile"] = prof
        except Exception as e:
            warnings.append(f"profile_failed: {e}")
        try:
            insp = cleaner.inspect_source(str(target), offset=0, limit=20, df=analysis_frame)
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
                metadata["plain_language_suggestions"] = generate_plain_language_suggestions(metadata, df=analysis_frame)
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

    del analysis_frame
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
