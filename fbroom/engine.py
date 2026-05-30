import json
try:
    import polars as pl
except Exception:
    pl = None


class Cleaner:
    def profile(self, path: str):
        """Produce a minimal profile for a CSV path using Polars if available."""
        if pl is None:
            raise RuntimeError("Polars not installed. See requirements.txt to install dependencies.")
        df = pl.read_csv(path, infer_schema_length=1000)
        profile = {}
        for col in df.columns:
            s = df[col]
            dtype = str(s.dtype)
            nulls = int(s.null_count())
            unique = int(s.n_unique())
            profile[col] = {"dtype": dtype, "nulls": nulls, "unique": unique}
        return profile

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
        if pl is None:
            raise RuntimeError("Polars not installed. See requirements.txt to install dependencies.")
        if not recipe.sources:
            raise ValueError("Recipe must contain at least one source")
        src = recipe.sources[0]["path"]
        df = pl.read_csv(src)
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
            elif step.action == "normalize" and step.column:
                try:
                    df = df.with_column(df[step.column].str.to_lowercase().alias(step.column))
                except Exception:
                    pass
        out_path = recipe.outputs[0]["path"] if recipe.outputs else "output.csv"
        df.write_csv(out_path)
        return {"written": out_path}

    def preview_recipe(self, recipe, n=5):
        """Run recipe transforms in-memory and return first `n` rows before and after as dicts."""
        if pl is None:
            raise RuntimeError("Polars not installed. See requirements.txt to install dependencies.")
        if not recipe.sources:
            raise ValueError("Recipe must contain at least one source")
        src = recipe.sources[0]["path"]
        df = pl.read_csv(src)
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
            elif step.action == "normalize" and step.column:
                try:
                    df_after = df_after.with_column(df_after[step.column].str.to_lowercase().alias(step.column))
                except Exception:
                    pass
        after = df_after.head(n).to_dicts()
        return {"before": before, "after": after}
