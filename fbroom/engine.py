import json
import csv
import io

from .connectors import resolve_source

try:
    import polars as pl
except Exception:
    pl = None


def _read_table(path: str):
    if pl is None:
        raise RuntimeError("Polars not installed. See requirements.txt to install dependencies.")

    resolved = resolve_source(path)
    materialized_path = getattr(resolved, "materialized_path", None) or resolved.path

    read_kwargs = {
        "infer_schema_length": 1000,
        "ignore_errors": True,
        "truncate_ragged_lines": True,
        "null_values": ["", "NA", "N/A", "null", "None"],
        "try_parse_dates": True,
    }
    suffix = str(materialized_path).lower()
    if suffix.endswith(".parquet"):
        return pl.read_parquet(materialized_path)
    for separator in (",", "\t", ";"):
        try:
            return pl.read_csv(materialized_path, separator=separator, **read_kwargs)
        except Exception:
            continue
    return pl.read_csv(materialized_path, has_header=False, new_columns=["value"], **read_kwargs)


class Cleaner:
    def profile(self, path: str):
        """Produce a minimal profile for a CSV path using Polars if available."""
        df = _read_table(path)
        profile = {}
        for col in df.columns:
            s = df[col]
            dtype = str(s.dtype)
            nulls = int(s.null_count())
            unique = int(s.n_unique())
            profile[col] = {"dtype": dtype, "nulls": nulls, "unique": unique}
        return profile

    def inspect_source(self, path: str, offset: int = 0, limit: int = 100):
        """Return paged row-level data for Source-tab inspection.

        If the ingestion produced long-form rows (with `unit_kind` and
        `text` columns), attempt to reconstruct a spreadsheet-like table by
        parsing each `text` line into columns so the frontend can render a
        familiar grid view.
        """
        df = _read_table(path)

        def _is_number(x: str) -> bool:
            try:
                float(str(x).replace(',', ''))
                return True
            except Exception:
                return False

        def compute_diagnostics_from_rows(rows_list: list[dict], cols: list[str]):
            total = len(rows_list)
            diag = {}
            for c in cols:
                missing_positions = []
                values = []
                for i, r in enumerate(rows_list):
                    v = r.get(c)
                    values.append(v)
                    if v is None or (isinstance(v, str) and v.strip() == ""):
                        if len(missing_positions) < 20:
                            missing_positions.append(i + 1)
                num_missing = sum(1 for v in values if v is None or (isinstance(v, str) and v.strip() == ""))
                unique = len(set([v for v in values if v is not None and v != ""]))
                # type mix detection
                num_numeric = sum(1 for v in values if v is not None and v != "" and _is_number(str(v)))
                mixed_type = (num_numeric > 0 and num_numeric < (total - num_missing))
                const = (unique <= 1)
                diag[c] = {
                    "missing_count": num_missing,
                    "missing_positions_sample": missing_positions,
                    "missing_pct": round(100 * num_missing / total, 2) if total else 0,
                    "unique_count": unique,
                    "mixed_type": mixed_type,
                    "constant": const,
                }
            return diag

        # Best-effort table reconstruction from long-form ingestion
        try:
            if pl is not None and {"unit_kind", "text"}.issubset(set(df.columns)):
                try:
                    lines = df.filter(pl.col("unit_kind") == "line").sort("row_index").select(["row_index", "text"]).to_dicts()
                except Exception:
                    lines = df.filter(pl.col("unit_kind") == "line").select(["row_index", "text"]).to_dicts()

                if lines:
                    sample = "\n".join(str(r.get("text", "")) for r in lines[:8])
                    counts = {",": sample.count(","), "\t": sample.count("\t"), ";": sample.count(";"), "|": sample.count("|")}
                    delim = max(counts.items(), key=lambda kv: kv[1])[0]
                    parsed_rows = []
                    for r in lines:
                        text = r.get("text") or ""
                        try:
                            reader = csv.reader(io.StringIO(text), delimiter=delim)
                            parsed = next(reader)
                        except Exception:
                            parsed = text.split(delim) if delim else [text]
                        parsed_rows.append([c for c in parsed])

                    header = None
                    if parsed_rows:
                        first_vals = parsed_rows[0]
                        non_numeric = sum(1 for v in first_vals if not (v is None or v == "" or _is_number(v)))
                        if non_numeric >= 1:
                            header = [h or f"col_{i+1}" for i, h in enumerate(first_vals)]

                    rows_out = []
                    cols = []
                    for i, vals in enumerate(parsed_rows):
                        if header and i == 0:
                            continue
                        rowdict = {}
                        for j, v in enumerate(vals):
                            colname = header[j] if header and j < len(header) else f"col_{j+1}"
                            rowdict[colname] = v
                            if colname not in cols:
                                cols.append(colname)
                        rows_out.append(rowdict)

                    if rows_out:
                        total_rows = len(rows_out)
                        safe_offset = max(0, int(offset))
                        safe_limit = max(1, min(int(limit), 500))
                        slice_rows = rows_out[safe_offset : safe_offset + safe_limit]
                        diagnostics = compute_diagnostics_from_rows(rows_out, cols)
                        # include a small raw preview of original extracted text lines
                        raw_preview = lines[safe_offset : safe_offset + safe_limit]
                        return {
                            "path": path,
                            "row_count": total_rows,
                            "column_count": len(cols),
                            "columns": cols,
                            "rows": slice_rows,
                            "offset": safe_offset,
                            "limit": safe_limit,
                            "returned_rows": len(slice_rows),
                            "has_prev": safe_offset > 0,
                            "has_next": (safe_offset + safe_limit) < total_rows,
                            "diagnostics": diagnostics,
                            "raw_preview": raw_preview,
                        }
        except Exception:
            pass

        # Default: use polars dataframe columns and compute lightweight diagnostics
        safe_offset = max(0, int(offset))
        safe_limit = max(1, min(int(limit), 500))
        rows = df.slice(safe_offset, safe_limit).to_dicts()
        total_rows = int(df.height)
        end = safe_offset + len(rows)

        diagnostics = {}
        try:
            df_small = df.with_row_count("_row_index")
            for col in df.columns:
                try:
                    # find up to 20 missing positions
                    nulls = df_small.filter((pl.col(col).is_null()) | (pl.col(col) == "")).select("_row_index").to_series().to_list()
                    missing_positions = [i + 1 for i in nulls[:20]]
                    num_missing = len(nulls)
                    # unique count
                    unique = int(df.select(pl.col(col).n_unique()).to_series()[0]) if hasattr(df, 'select') else 0
                    # mixed type detection (sample)
                    sample_vals = df_small.select(pl.col(col)).head(200).to_series().to_list()
                    num_numeric = sum(1 for v in sample_vals if v is not None and v != "" and _is_number(str(v)))
                    mixed_type = (num_numeric > 0 and num_numeric < (len(sample_vals) - num_missing))
                    const = (unique <= 1)
                    diagnostics[col] = {
                        "missing_count": num_missing,
                        "missing_positions_sample": missing_positions,
                        "missing_pct": round(100 * num_missing / total_rows, 2) if total_rows else 0,
                        "unique_count": unique,
                        "mixed_type": mixed_type,
                        "constant": const,
                    }
                except Exception:
                    diagnostics[col] = {"missing_count": None}
        except Exception:
            diagnostics = {}

        return {
            "path": path,
            "row_count": total_rows,
            "column_count": int(len(df.columns)),
            "columns": list(df.columns),
            "rows": rows,
            "offset": safe_offset,
            "limit": safe_limit,
            "returned_rows": int(len(rows)),
            "has_prev": safe_offset > 0,
            "has_next": end < total_rows,
            "diagnostics": diagnostics,
        }

    def suggest_fixes(self, profile: dict):
        suggestions = []
        for col, meta in profile.items():
            if "int" in meta["dtype"] and meta["nulls"] > 0:
                suggestions.append({"column": col, "action": "impute", "strategy": "median"})
            if meta["unique"] == 1:
                suggestions.append({"column": col, "action": "drop_constant"})
            if "str" in meta["dtype"] and meta["nulls"] > 0:
                suggestions.append({"column": col, "action": "impute", "strategy": "empty_string"})
        return suggestions

    def apply_recipe_from_spec(self, recipe):
        """Apply a Recipe (pydantic model) to a CSV source and write output.

        This is a minimal, deterministic runner meant for local development and
        demonstration. Production runners should add transactionality, dataset
        snapshotting, lineage, and more robust transform plumbing.
        """
        if not recipe.sources:
            raise ValueError("Recipe must contain at least one source")
        src = recipe.sources[0]["path"]
        df = _read_table(src)
        for step in recipe.cleaning_steps:
            if step.action == "drop_column" and step.column:
                df = df.drop(step.column)
            elif step.action == "impute" and step.column:
                strat = step.params.get("strategy") or step.params.get("strategy", None) or getattr(step, "strategy", None)
                if strat == "median":
                    try:
                        med = df[step.column].median()
                        df = df.with_column(df[step.column].fill_null(med).alias(step.column))
                    except Exception:
                        df = df.with_column(df[step.column].fill_null(0).alias(step.column))
                elif strat == "empty_string":
                    df = df.with_column(df[step.column].fill_null("").alias(step.column))
                elif strat == "forward_fill":
                    df = df.with_column(df[step.column].fill_null(strategy="forward").alias(step.column))
            elif step.action == "normalize" and step.column:
                try:
                    case = (step.params or {}).get("case", "lower")
                    if case == "upper":
                        df = df.with_column(df[step.column].str.to_uppercase().alias(step.column))
                    elif case == "trim":
                        df = df.with_column(df[step.column].str.strip_chars().alias(step.column))
                    else:
                        df = df.with_column(df[step.column].str.to_lowercase().alias(step.column))
                except Exception:
                    pass
            elif step.action == "deduplicate":
                subset = [step.column] if step.column else None
                df = df.unique(subset=subset, keep="first")
            elif step.action == "rename" and step.column:
                new_name = (step.params or {}).get("new_name")
                if new_name:
                    df = df.rename({step.column: new_name})
        out_path = recipe.outputs[0]["path"] if recipe.outputs else "output.csv"
        df.write_csv(out_path)
        return {"written": out_path}

    def preview_recipe(self, recipe, n=5):
        """Run recipe transforms in-memory and return first `n` rows before and after as dicts."""
        if not recipe.sources:
            raise ValueError("Recipe must contain at least one source")
        src = recipe.sources[0]["path"]
        df = _read_table(src)
        before = df.head(n).to_dicts()
        df_after = df
        for step in recipe.cleaning_steps:
            if step.action == "drop_column" and step.column:
                df_after = df_after.drop(step.column)
            elif step.action == "impute" and step.column:
                strat = step.params.get("strategy") or step.params.get("strategy", None) or getattr(step, "strategy", None)
                if strat == "median":
                    try:
                        med = df_after[step.column].median()
                        df_after = df_after.with_column(df_after[step.column].fill_null(med).alias(step.column))
                    except Exception:
                        df_after = df_after.with_column(df_after[step.column].fill_null(0).alias(step.column))
                elif strat == "empty_string":
                    df_after = df_after.with_column(df_after[step.column].fill_null("").alias(step.column))
                elif strat == "forward_fill":
                    df_after = df_after.with_column(df_after[step.column].fill_null(strategy="forward").alias(step.column))
            elif step.action == "normalize" and step.column:
                try:
                    case = (step.params or {}).get("case", "lower")
                    if case == "upper":
                        df_after = df_after.with_column(df_after[step.column].str.to_uppercase().alias(step.column))
                    elif case == "trim":
                        df_after = df_after.with_column(df_after[step.column].str.strip_chars().alias(step.column))
                    else:
                        df_after = df_after.with_column(df_after[step.column].str.to_lowercase().alias(step.column))
                except Exception:
                    pass
            elif step.action == "deduplicate":
                subset = [step.column] if step.column else None
                df_after = df_after.unique(subset=subset, keep="first")
            elif step.action == "rename" and step.column:
                new_name = (step.params or {}).get("new_name")
                if new_name:
                    df_after = df_after.rename({step.column: new_name})
        after = df_after.head(n).to_dicts()
        return {"before": before, "after": after}
