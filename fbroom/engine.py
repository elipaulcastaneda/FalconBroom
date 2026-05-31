import json
try:
    import polars as pl
except Exception:
    pl = None


def _read_table(path: str):
    if pl is None:
        raise RuntimeError("Polars not installed. See requirements.txt to install dependencies.")

    read_kwargs = {
        "infer_schema_length": 1000,
        "ignore_errors": True,
        "truncate_ragged_lines": True,
        "null_values": ["", "NA", "N/A", "null", "None"],
        "try_parse_dates": True,
    }
    for separator in (",", "\t", ";"):
        try:
            return pl.read_csv(path, separator=separator, **read_kwargs)
        except Exception:
            continue
    return pl.read_csv(path, has_header=False, new_columns=["value"], **read_kwargs)


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
        """Return paged row-level data for Source-tab inspection."""
        df = _read_table(path)
        safe_offset = max(0, int(offset))
        safe_limit = max(1, min(int(limit), 500))
        rows = df.slice(safe_offset, safe_limit).to_dicts()
        total_rows = int(df.height)
        end = safe_offset + len(rows)
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
