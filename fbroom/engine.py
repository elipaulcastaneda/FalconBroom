import json
import csv
import io
import re
import difflib
import os
import tempfile
import hashlib
import shutil
from pathlib import Path

from .connectors import resolve_source

try:
    import polars as pl
except Exception:
    pl = None

from datetime import datetime
try:
    from dateutil import parser as _dateutil_parser  # type: ignore
except Exception:
    _dateutil_parser = None


def _is_date_str(val: str) -> bool:
    """Best-effort date detection: use dateutil if available, else try common formats and iso regex."""
    if val is None:
        return False
    s = str(val).strip()
    if s == "":
        return False
    # quick ISO-like regex
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s) or re.match(r"^\d{2}/\d{2}/\d{4}$", s) or re.match(r"^\d{1,2} [A-Za-z]{3,9} \d{4}$", s):
        return True
    if _dateutil_parser:
        try:
            _dateutil_parser.parse(s, dayfirst=False)
            return True
        except Exception:
            try:
                _dateutil_parser.parse(s, dayfirst=True)
                return True
            except Exception:
                return False
    # fallback: try several common strptime formats
    fmts = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%b %d, %Y", "%d %b %Y", "%B %d, %Y", "%d %B %Y"]
    for f in fmts:
        try:
            datetime.strptime(s, f)
            return True
        except Exception:
            continue
    return False


def _is_polars_df(df):
    try:
        return pl is not None and isinstance(df, pl.DataFrame)
    except Exception:
        return False


def _df_head_records(df, n=None):
    """Return up to `n` records from dataframe as list of dicts.
    If `n` is None or n <= 0, return all records.
    """
    # treat None or non-positive as request for all rows
    try:
        if n is None or (isinstance(n, int) and n <= 0):
            if _is_polars_df(df):
                return df.to_dicts()
            try:
                return df.to_dict("records")
            except Exception:
                return []
        # otherwise return head(n)
        if _is_polars_df(df):
            return df.head(n).to_dicts()
        try:
            return df.head(n).to_dict("records")
        except Exception:
            return []
    except Exception:
        return []


def _fill_null_column(df, col, value, strategy=None):
    # strategy is used for forward-fill in polars
    if _is_polars_df(df):
        try:
            if strategy == "forward":
                return df.with_columns(df[col].fill_null(strategy="forward").alias(col))
            return df.with_columns(df[col].fill_null(value).alias(col))
        except Exception:
            try:
                if strategy == "forward":
                    return df.with_column(df[col].fill_null(strategy="forward").alias(col))
                return df.with_column(df[col].fill_null(value).alias(col))
            except Exception:
                return df
    else:
        import pandas as _pd
        df2 = df.copy()
        if strategy == "forward":
            df2[col] = df2[col].fillna(method="ffill")
        else:
            df2[col] = df2[col].fillna(value)
        return df2


def _string_transform_column(df, col, case="lower"):
    if _is_polars_df(df):
        try:
            if case == "upper":
                return df.with_columns(df[col].str.to_uppercase().alias(col))
            if case == "trim":
                return df.with_columns(df[col].str.strip_chars().alias(col))
            return df.with_columns(df[col].str.to_lowercase().alias(col))
        except Exception:
            try:
                if case == "upper":
                    return df.with_column(df[col].str.to_uppercase().alias(col))
                if case == "trim":
                    return df.with_column(df[col].str.strip_chars().alias(col))
                return df.with_column(df[col].str.to_lowercase().alias(col))
            except Exception:
                return df
    else:
        df2 = df.copy()
        if case == "upper":
            df2[col] = df2[col].astype(str).str.upper()
        elif case == "trim":
            df2[col] = df2[col].astype(str).str.strip()
        else:
            df2[col] = df2[col].astype(str).str.lower()
        return df2


def _unicode_normalize_column(df, col, form: str = "NFKC", remove_diacritics: bool = False):
    import unicodedata as _ud

    def _normalize_val(v):
        if v is None:
            return None
        s = str(v)
        try:
            s = _ud.normalize(form, s)
        except Exception:
            try:
                s = _ud.normalize("NFKC", s)
            except Exception:
                pass
        if remove_diacritics:
            try:
                s = ''.join(ch for ch in _ud.normalize('NFKD', s) if not _ud.combining(ch))
            except Exception:
                pass
        return s

    if _is_polars_df(df):
        try:
            return df.with_columns(pl.col(col).apply(lambda v: _normalize_val(v)).alias(col))
        except Exception:
            try:
                return df.with_column(pl.col(col).apply(lambda v: _normalize_val(v)).alias(col))
            except Exception:
                return df
    else:
        import pandas as _pd
        df2 = df.copy()
        try:
            df2[col] = df2[col].astype(str).apply(lambda v: _normalize_val(v))
        except Exception:
            try:
                df2[col] = df2[col].apply(lambda v: _normalize_val(v))
            except Exception:
                pass
        return df2


def _unique_df(df, subset=None):
    if _is_polars_df(df):
        return df.unique(subset=subset, keep="first")
    else:
        try:
            return df.drop_duplicates(subset=subset, keep="first")
        except Exception:
            return df


def _fuzzy_dedupe(df, subset=None, threshold: float = 0.85, method: str = 'difflib'):
    """Perform fuzzy deduplication on `subset` columns (list or single column name).
    Returns (df_new, info) where info contains cluster report and rows removed.
    """
    try:
        import pandas as _pd
    except Exception:
        _pd = None

    cols = None
    if subset is None:
        cols = None
    elif isinstance(subset, (list, tuple)):
        cols = list(subset)
    else:
        cols = [subset]

    # operate in pandas for flexible string ops
    try:
        if _is_polars_df(df):
            pd_df = df.to_pandas()
        else:
            pd_df = df.copy()
    except Exception:
        return df, None

    if cols is None:
        # use all columns joined as key
        key_ser = pd_df.astype(str).agg('||'.join, axis=1)
    else:
        for c in cols:
            if c not in pd_df.columns:
                pd_df[c] = ''
        key_ser = pd_df[cols].astype(str).agg('||'.join, axis=1)

    # normalize keys for matching
    try:
        import unicodedata as _ud
        norm_keys = key_ser.fillna('').apply(lambda s: _ud.normalize('NFKD', s).casefold())
        norm_keys = norm_keys.str.replace(r'\s+', ' ', regex=True).str.strip()
    except Exception:
        norm_keys = key_ser.fillna('').astype(str).str.lower()

    unique_keys = norm_keys.unique().tolist()
    clusters = []
    used = set()
    for k in unique_keys:
        if k in used:
            continue
        if method == 'minhash':
            # fallback to difflib if datasketch not available
            try:
                from datasketch import MinHash
                # build mh for k and compare to others (simple but OK for small sets)
            except Exception:
                method_local = 'difflib'
            else:
                method_local = 'minhash'
        else:
            method_local = 'difflib'

        group = [k]
        used.add(k)
        if method_local == 'difflib':
            matches = difflib.get_close_matches(k, unique_keys, n=len(unique_keys), cutoff=threshold)
            for m in matches:
                if m not in used:
                    used.add(m)
                    group.append(m)
        clusters.append(group)

    # map keys back to row indices and pick keepers
    rows_removed = 0
    dup_indices = set()
    cluster_report = []
    for grp in clusters:
        # find all row indices for this cluster
        idxs = [i for i, v in enumerate(norm_keys.tolist()) if v in grp]
        if not idxs:
            continue
        keeper = idxs[0]
        removed = idxs[1:]
        dup_indices.update(removed)
        rows_removed += len(removed)
        cluster_report.append({"size": len(idxs), "keeper_index": keeper + 1, "removed_count": len(removed), "sample_indices": [i + 1 for i in idxs[:5]]})

    if dup_indices:
        try:
            pd_new = pd_df.drop(pd_df.index[list(sorted(dup_indices))])
        except Exception:
            pd_new = pd_df
    else:
        pd_new = pd_df

    try:
        if _is_polars_df(df):
            df_new = pl.from_pandas(pd_new)
        else:
            df_new = pd_new
    except Exception:
        df_new = pd_new

    info = {"step": "fuzzy_dedupe", "clusters": len(cluster_report), "rows_removed": rows_removed, "cluster_sample": cluster_report[:10]}
    return df_new, info


def _drop_column(df, col):
    if _is_polars_df(df):
        try:
            return df.drop(col)
        except Exception:
            return df
    else:
        try:
            return df.drop(columns=[col])
        except Exception:
            return df


def _rename_column(df, old, new):
    if _is_polars_df(df):
        try:
            return df.rename({old: new})
        except Exception:
            return df
    else:
        try:
            return df.rename(columns={old: new})
        except Exception:
            return df


def _write_csv(df, out_path):
    if _is_polars_df(df):
        return df.write_csv(out_path)
    else:
        try:
            return df.to_csv(out_path, index=False)
        except Exception:
            return None


def _resolve_conflict_name(existing_names, desired_name):
    """Return a non-conflicting column name based on `desired_name`.
    Appends a numeric suffix if needed: name, name_1, name_2, ...
    """
    if desired_name not in existing_names:
        return desired_name
    base = desired_name
    i = 1
    while True:
        cand = f"{base}_{i}"
        if cand not in existing_names:
            return cand
        i += 1


def _safe_rename_columns(df, rename_map: dict):
    """Rename columns in `df` safely avoiding conflicts by using `_resolve_conflict_name`.
    `rename_map` is {old_name: desired_new_name}.
    Returns (df_new, applied_map) where applied_map maps old->actual_new.
    """
    if not rename_map:
        return df, {}
    cols = list(df.columns if _is_polars_df(df) else list(df.columns))
    applied = {}
    taken = set(cols)
    # compute actual new names without collisions
    for old, desired in rename_map.items():
        if old not in cols:
            continue
        new_name = desired or old
        if new_name == old:
            applied[old] = old
            continue
        actual = _resolve_conflict_name(taken - {old}, new_name)
        applied[old] = actual
        # reserve the name
        taken.add(actual)

    # apply renames
    try:
        if _is_polars_df(df):
            mapping = {old: new for old, new in applied.items() if old in cols and new != old}
            if mapping:
                return df.rename(mapping), applied
            return df, applied
        else:
            import pandas as _pd
            pd_df = df.copy()
            mapping = {old: new for old, new in applied.items() if old in pd_df.columns and new != old}
            if mapping:
                pd_df = pd_df.rename(columns=mapping)
            return pd_df, applied
    except Exception:
        return df, applied


def _write_parquet(df, out_path, compression: str = None, atomic: bool = True):
    """Write dataframe to parquet with optional `compression` (snappy,gzip,zstd,brotli)
    and atomic write (write to temp then move).
    Returns the final path on success, else None.
    """
    try:
        tmp = None
        dirp = os.path.dirname(out_path) or '.'
        # atomic write via temp file in same dir
        if atomic:
            fd, tmp = tempfile.mkstemp(dir=dirp, prefix="tmp_parquet_")
            os.close(fd)
            target = tmp
        else:
            target = out_path

        if _is_polars_df(df):
            kwargs = {}
            if compression:
                kwargs['compression'] = compression
            try:
                df.write_parquet(target, **kwargs)
            except Exception:
                # try via pyarrow table
                try:
                    import pyarrow as pa
                    import pyarrow.parquet as pq
                    tbl = df.to_arrow()
                    pq.write_table(tbl, target, compression=compression)
                except Exception:
                    raise
        else:
            # pandas path using pyarrow
            try:
                import pyarrow as pa
                import pyarrow.parquet as pq
                tbl = pa.Table.from_pandas(df)
                pq.write_table(tbl, target, compression=compression)
            except Exception:
                return None

        if atomic and tmp:
            shutil.move(tmp, out_path)
        return out_path
    except Exception:
        try:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return None


def _replace_values(df, col, old, new):
    # perform literal replacements (avoid regex metacharacters for literal match)
    old_raw = str(old)
    literal_old = re.escape(old_raw)
    if _is_polars_df(df):
        try:
            # prefer expression API with literal=True when available; pass raw old string
            try:
                return df.with_columns(pl.col(col).str.replace(old_raw, str(new), literal=True).alias(col))
            except Exception:
                # fallback to Series API using raw old (may treat as regex)
                return df.with_columns(df[col].str.replace(old_raw, str(new)).alias(col))
        except Exception:
            try:
                return df.with_column(pl.col(col).str.replace(literal_old, str(new)).alias(col))
            except Exception:
                return df
    else:
        df2 = df.copy()
        try:
            df2[col] = df2[col].astype(str).str.replace(old, new, regex=False)
        except Exception:
            try:
                # fallback: escape regex in pandas replace
                df2[col] = df2[col].astype(str).str.replace(literal_old, str(new), regex=True)
            except Exception:
                pass
        return df2


def _map_values(df, col, mapping: dict):
    if _is_polars_df(df):
        try:
            # Prefer building a polars expression chain for deterministic mapping
            # This avoids Python-level apply for common mapping sizes.
            if mapping:
                try:
                    # build a chained when/then expression
                    expr = pl.col(col)
                    for k, v in mapping.items():
                        # compare as string to be robust across types
                        expr = pl.when(pl.col(col).cast(pl.Utf8) == str(k)).then(v).otherwise(expr)
                    return df.with_columns(expr.alias(col))
                except Exception:
                    # fallback to per-key replace
                    df2 = df
                    for k, v in mapping.items():
                        df2 = _replace_values(df2, col, k, v)
                    return df2
        except Exception:
            try:
                # fallback: convert to pandas if available
                pd = df.to_pandas()
                pd[col] = pd[col].astype(str).map(mapping).fillna(pd[col])
                return pl.from_pandas(pd)
            except Exception:
                try:
                    return df.with_columns(pl.col(col).apply(lambda v: mapping.get(str(v), v)).alias(col))
                except Exception:
                    return df
    else:
        import pandas as _pd
        df2 = df.copy()
        try:
            df2[col] = df2[col].map(mapping).fillna(df2[col])
        except Exception:
            try:
                df2[col] = df2[col].astype(str).map(mapping).fillna(df2[col])
            except Exception:
                pass
        return df2


def _parse_regex_flags(flags):
    if not flags:
        return 0
    if isinstance(flags, int):
        return int(flags)
    if isinstance(flags, (list, tuple)):
        toks = flags
    else:
        toks = [t.strip().lower() for t in str(flags).split(',') if t.strip()]
    f = 0
    for t in toks:
        if t in ('i', 'ignorecase', 'ignore_case', 'ic'):
            f |= re.IGNORECASE
        elif t in ('m', 'multiline'):
            f |= re.MULTILINE
        elif t in ('s', 'dotall'):
            f |= re.DOTALL
        elif t in ('x', 'verbose'):
            f |= re.VERBOSE
        elif t in ('a', 'ascii'):
            f |= re.ASCII
    return f


def _regex_replace(df, col, pattern, repl, flags=None):
    rf = _parse_regex_flags(flags)
    if _is_polars_df(df):
        try:
            if rf == 0:
                return df.with_columns(pl.col(col).str.replace_all(pattern, repl).alias(col))
            # if flags set, fall back to Python-level substitution to respect flags
            prog = re.compile(pattern, flags=rf)
            return df.with_columns(pl.col(col).apply(lambda v: prog.sub(repl, str(v) if v is not None else "")).alias(col))
        except Exception:
            try:
                prog = re.compile(pattern, flags=rf)
                return df.with_columns(df[col].apply(lambda v: prog.sub(repl, str(v) if v is not None else "")).alias(col))
            except Exception:
                return df
    else:
        import pandas as _pd
        df2 = df.copy()
        try:
            # pandas.Series.str.replace accepts `flags` kwarg
            df2[col] = df2[col].astype(str).str.replace(pattern, repl, regex=True, flags=rf)
        except Exception:
            try:
                prog = re.compile(pattern, flags=rf)
                df2[col] = df2[col].astype(str).apply(lambda v: prog.sub(repl, str(v)))
            except Exception:
                pass
        return df2


def _remove_by_type(df, col, params=None):
    """Remove (replace) values in `col` that match a data type criterion.
    params: {target_type: 'string'|'numeric', replacement: any}
    Returns (df_new, info) similar to _impute_missing diagnostics.
    """
    params = params or {}
    target_type = params.get("target_type", "string")
    replacement = params.get("replacement", "")
    exceptions = params.get("exceptions") or []

    if col not in (df.columns if _is_polars_df(df) else list(df.columns)):
        return df, None

    try:
        if _is_polars_df(df):
            # numeric detection: full-string numeric match
            try:
                # Normalize common currency symbols and thousands separators,
                # allow parentheses for negative values, then cast to float.
                cleaned = pl.col(col).str.replace_all(r"^\s*\(|\)\s*$", "")
                cleaned = cleaned.str.replace_all(r"[\$\£\€\¥\₹]", "")
                cleaned = cleaned.str.replace_all(r"[,\s]", "")
                # attempt to cast cleaned values to float; non-numeric become null
                num_mask = cleaned.cast(pl.Float64).is_not_null()
            except Exception:
                try:
                    # fallback: best-effort cast on original column
                    num_mask = pl.col(col).cast(pl.Float64).is_not_null()
                except Exception:
                    num_mask = pl.lit(False)

            if target_type == "numeric":
                mask = num_mask
            else:
                mask = (~num_mask) & (~pl.col(col).is_null()) & (pl.col(col) != "")

            # If exceptions include date, build a date-detection expression and exclude those rows from mask
            try:
                if 'date' in exceptions:
                    # build polars expression to detect iso-like patterns and fallback to parsing attempt
                    date_expr = pl.col(col).is_not_null() & pl.col(col).str.strip() != ""
                    # basic ISO pattern
                    iso_mask = pl.col(col).str.contains(r"^\d{4}-\d{2}-\d{2}$")
                    mdm = None
                    try:
                        mdm = pl.col(col).str.contains(r"^\d{1,2}/\d{1,2}/\d{4}$")
                    except Exception:
                        mdm = None
                    if mdm is not None:
                        date_mask = iso_mask | mdm
                    else:
                        date_mask = iso_mask
                    # exclude date-like strings from mask if present
                    mask = mask & (~date_mask)
            except Exception:
                pass

            # If expression-based detection found nothing or is unsupported in this polars build,
            # fall back to a Python-list based heuristic that is more permissive.
            try:
                # evaluate mask to python list if num_mask is an expression
                if isinstance(mask, pl.Expr):
                    try:
                        evaluated = df.select(mask.alias("__mask_eval__")).to_series("__mask_eval__").to_list()
                    except Exception:
                        # try alternative access
                        try:
                            evaluated = mask.to_frame().to_series().to_list()
                        except Exception:
                            evaluated = None
                else:
                    evaluated = None
            except Exception:
                evaluated = None

            if not evaluated:
                # Python-level heuristic
                try:
                    vals = None
                    try:
                        vals = df.select(pl.col(col)).to_series().to_list()
                    except Exception:
                        try:
                            vals = df[col].to_list()
                        except Exception:
                            vals = []

                    import re as _re
                    num_re = _re.compile(r'^[-+]?\d+(?:\.\d+)?$')
                    def is_num_val(x):
                        if x is None:
                            return False
                        s = str(x).strip()
                        if s == '':
                            return False
                        # parentheses as negative
                        if s.startswith('(') and s.endswith(')'):
                            s = s[1:-1]
                        # remove currency symbols
                        s = _re.sub(r'[\$\£\€\¥\₹]', '', s)
                        # remove commas/spaces used as thousands separators
                        s = s.replace(',', '').replace(' ', '')
                        return bool(num_re.match(s))

                    def is_date_val(x):
                        try:
                            return _is_date_str(x)
                        except Exception:
                            return False

                    mask_list = [is_num_val(v) for v in vals]
                    # construct new column values; respect exceptions (e.g., keep dates)
                    new_vals = []
                    for v, m in zip(vals, mask_list):
                        if 'date' in exceptions and is_date_val(v):
                            new_vals.append(v)
                        else:
                            if (m and target_type == 'numeric') or (not m and target_type != 'numeric'):
                                new_vals.append(replacement)
                            else:
                                new_vals.append(v)
                    # create polars Series and assign
                    try:
                        df_new = df.with_columns(pl.Series(col, new_vals).alias(col))
                    except Exception:
                        # fallback: build a new DataFrame from dicts
                        dicts = df.to_dicts()
                        for i, d in enumerate(dicts):
                            d[col] = new_vals[i]
                        df_new = pl.from_dicts(dicts)

                    affected = []
                    for i, (v, m) in enumerate(zip(vals, mask_list)):
                        if 'date' in exceptions and is_date_val(v):
                            continue
                        if (m and target_type == 'numeric') or (not m and target_type != 'numeric'):
                            affected.append(i)
                    info = {"step": "remove_by_type", "column": col, "method": "remove_by_type", "target_type": target_type, "rows_changed": len(affected), "sample_positions": [i + 1 for i in (sorted(affected)[:20])]} 
                    return df_new, info
                except Exception:
                    pass

            try:
                df_with_idx = df.with_row_index("__row_idx__")
                affected = df_with_idx.filter(mask).select("__row_idx__").to_series().to_list()
            except Exception:
                affected = []

            try:
                df_new = df.with_columns(pl.when(mask).then(pl.lit(replacement)).otherwise(pl.col(col)).alias(col))
            except Exception:
                # try alternative column assignment
                try:
                    df_new = df.with_column(pl.when(mask).then(pl.lit(replacement)).otherwise(pl.col(col)).alias(col))
                except Exception:
                    return df, None

            info = {"step": "remove_by_type", "column": col, "method": "remove_by_type", "target_type": target_type, "rows_changed": len(affected), "sample_positions": [i + 1 for i in (sorted(affected)[:20])]} 
            return df_new, info
        else:
            import pandas as _pd
            df2 = df.copy()
            try:
                ser = df2[col].astype(str)
            except Exception:
                ser = df2[col].map(lambda x: "" if x is None else str(x))

            import re as _re
            # Clean strings: remove surrounding parentheses, currency symbols, and thousands separators
            clean = ser.str.strip()
            # remove surrounding parentheses that denote negatives
            try:
                has_paren = clean.str.startswith('(') & clean.str.endswith(')')
                clean = clean.str.replace(r'^\(|\)$', '', regex=True)
            except Exception:
                pass
            try:
                clean = clean.str.replace(r'[\$\£\€\¥\₹]', '', regex=True)
                clean = clean.str.replace(r'[,\s]', '', regex=True)
            except Exception:
                # best-effort non-regex replacements
                clean = clean.str.replace('$', '').str.replace('£', '').str.replace('€', '').str.replace('¥', '').str.replace('₹', '')
                clean = clean.str.replace(',', '').str.replace(' ', '')

            mask = clean.str.match(r'^[-+]?\d+(?:\.\d+)?$') if target_type == 'numeric' else (~clean.str.match(r'^[-+]?\d+(?:\.\d+)?$')) & (clean.str.strip() != '')

            affected_idx = mask[mask].index.tolist()
            df2.loc[mask, col] = replacement
            info = {"step": "remove_by_type", "column": col, "method": "remove_by_type", "target_type": target_type, "rows_changed": len(affected_idx), "sample_positions": [i + 1 for i in (sorted(affected_idx)[:20])]} 
            return df2, info
    except Exception:
        return df, None


def _swap_by_types(df, moves, replacement=""):
    """Perform bidirectional swaps/moves between columns based on type matches.
    `moves` is a list of dicts: {source, target, type, exceptions: []}
    Returns (df_new, info)
    """
    if not moves:
        return df, None

    # collect involved columns
    cols = []
    for m in moves:
        cols.append(m.get('source'))
        cols.append(m.get('target'))
    cols = [c for c in dict.fromkeys(cols) if c in (df.columns if _is_polars_df(df) else list(df.columns))]
    if not cols:
        return df, None

    # helper detectors
    import re as _re
    def is_num_val(x):
        if x is None or x == "":
            return False
        s = str(x).strip()
        if s == "":
            return False
        if s.startswith('(') and s.endswith(')'):
            s = s[1:-1]
        s = _re.sub(r'[\$\£\€\¥\₹]', '', s)
        s = s.replace(',', '').replace(' ', '')
        return bool(_re.match(r'^[-+]?\d+(?:\.\d+)?$', s))

    def is_date_val(x):
        try:
            return _is_date_str(x)
        except Exception:
            return False

    def is_string_val(x):
        if x is None or x == "":
            return False
        if is_date_val(x):
            return False
        if is_num_val(x):
            return False
        return True

    # Try a vectorized pandas-backed implementation when Polars DF available
    try:
        import pandas as _pd
        if _is_polars_df(df):
            pd_df = df.to_pandas()
        else:
            pd_df = df.copy()

        rows_changed = 0

        # helper vectorized detectors
        def is_num_series(s):
            ser = s.astype(str).str.strip().fillna("")
            ser = ser.str.replace(r"[\$\£\€\¥\₹]", "", regex=True).str.replace(r"[\,\s]","", regex=True)
            return ser.str.match(r'^[-+]?\d+(?:\.\d+)?$')

        def is_date_series(s):
            # try to parse, treat non-parsable as NaT
            try:
                parsed = _pd.to_datetime(s, errors='coerce', infer_datetime_format=True)
                return ~parsed.isna()
            except Exception:
                return _pd.Series([False] * len(s), index=s.index)

        def is_string_series(s):
            ser = s.astype(str).fillna("")
            return ~(is_num_series(ser) | is_date_series(ser) | (ser == ""))

        # perform reciprocal swaps first
        for a in moves:
            for b in moves:
                if a is b:
                    continue
                if a.get('source') == b.get('target') and a.get('target') == b.get('source'):
                    src = a.get('source')
                    tgt = a.get('target')
                    if src not in pd_df.columns or tgt not in pd_df.columns:
                        continue
                    # build masks
                    mask_a = None
                    mask_b = None
                    typ_a = a.get('type')
                    typ_b = b.get('type')
                    if typ_a == 'numeric':
                        mask_a = is_num_series(pd_df[src])
                    elif typ_a == 'date':
                        mask_a = is_date_series(pd_df[src])
                    else:
                        mask_a = is_string_series(pd_df[src])

                    if typ_b == 'numeric':
                        mask_b = is_num_series(pd_df[tgt])
                    elif typ_b == 'date':
                        mask_b = is_date_series(pd_df[tgt])
                    else:
                        mask_b = is_string_series(pd_df[tgt])

                    mask = mask_a & mask_b
                    if mask.any():
                        # swap columns where mask True
                        tmp_src = pd_df.loc[mask, src].copy()
                        pd_df.loc[mask, src] = pd_df.loc[mask, tgt].values
                        pd_df.loc[mask, tgt] = tmp_src.values
                        rows_changed += int(mask.sum())

        # single-direction moves
        for mov in moves:
            src = mov.get('source')
            tgt = mov.get('target')
            if src not in pd_df.columns or tgt not in pd_df.columns:
                continue
            typ = mov.get('type')
            if typ == 'numeric':
                mask = is_num_series(pd_df[src])
            elif typ == 'date':
                mask = is_date_series(pd_df[src])
            else:
                mask = is_string_series(pd_df[src])

            if mask.any():
                pd_df.loc[mask, tgt] = pd_df.loc[mask, src].values
                pd_df.loc[mask, src] = replacement
                rows_changed += int(mask.sum())

        try:
            df_new = pl.from_pandas(pd_df)
        except Exception:
            df_new = pd_df

        info = {"step": "swap_by_types", "moves": moves, "rows_changed": rows_changed}
        return df_new, info
    except Exception:
        # fallback to original row-wise implementation
        pass

    # original row-wise fallback
    try:
        if _is_polars_df(df):
            data = {c: df.select(pl.col(c)).to_series().to_list() for c in cols}
        else:
            data = {c: df[c].tolist() for c in cols}
    except Exception:
        try:
            pd = df.to_pandas() if _is_polars_df(df) else df
            data = {c: pd[c].tolist() for c in cols}
        except Exception:
            return df, None

    n = len(next(iter(data.values()))) if data else 0
    rows_changed = 0
    new_data = {c: list(data[c]) for c in cols}

    for i in range(n):
        applied_swap = False
        for a in moves:
            for b in moves:
                if a is b:
                    continue
                if a.get('source') == b.get('target') and a.get('target') == b.get('source'):
                    src_col = a.get('source')
                    tgt_col = a.get('target')
                    va = data.get(src_col)[i]
                    vb = data.get(tgt_col)[i]
                    def matches(mov, val):
                        typ = mov.get('type')
                        exc = mov.get('exceptions') or []
                        if typ == 'numeric':
                            if 'date' in exc and is_date_val(val):
                                return False
                            return is_num_val(val)
                        elif typ == 'string':
                            if 'date' in exc and is_date_val(val):
                                return False
                            return is_string_val(val)
                        elif typ == 'date':
                            return is_date_val(val)
                        return False

                    if matches(a, va) and matches(b, vb):
                        new_data[src_col][i] = vb
                        new_data[tgt_col][i] = va
                        rows_changed += 1
                        applied_swap = True
                        break
            if applied_swap:
                break
        if applied_swap:
            continue

        for mov in moves:
            src = mov.get('source')
            tgt = mov.get('target')
            if src not in data or tgt not in data:
                continue
            val = data[src][i]
            typ = mov.get('type')
            exc = mov.get('exceptions') or []
            ok = False
            if typ == 'numeric':
                if 'date' in exc and is_date_val(val):
                    ok = False
                else:
                    ok = is_num_val(val)
            elif typ == 'string':
                if 'date' in exc and is_date_val(val):
                    ok = False
                else:
                    ok = is_string_val(val)
            elif typ == 'date':
                ok = is_date_val(val)

            if ok:
                new_data[tgt][i] = val
                new_data[src][i] = replacement
                rows_changed += 1

    try:
        if _is_polars_df(df):
            df_new = df
            for c in cols:
                df_new = df_new.with_columns(pl.Series(c, new_data[c]).alias(c))
        else:
            import pandas as _pd
            pd = df if isinstance(df, _pd.DataFrame) else df.to_pandas()
            for c in cols:
                pd[c] = new_data[c]
            try:
                df_new = pl.from_pandas(pd)
            except Exception:
                df_new = pd
    except Exception:
        return df, None

    info = {"step": "swap_by_types", "moves": moves, "rows_changed": rows_changed}
    return df_new, info


def _bucketize(df, col, buckets):
    # buckets: list of {min, max, label}
    def label_for_value(v):
        try:
            # try to extract numeric portion (handle values like '3+')
            s = str(v)
            m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
            if not m:
                return v
            fv = float(m.group(0))
        except Exception:
            return v
        for b in buckets:
            lo = b.get("min")
            hi = b.get("max")
            if lo is None and hi is not None:
                if fv < hi:
                    return b.get("label")
            elif lo is not None and hi is None:
                if fv > lo:
                    return b.get("label")
            elif lo is not None and hi is not None:
                if fv >= lo and fv <= hi:
                    return b.get("label")
        return v

    if _is_polars_df(df):
        try:
            # coerce numeric portion by stripping non-numeric chars then cast
            try:
                num = pl.col(col).str.replace_all(r"[^0-9\.\-]+", "").cast(pl.Float64)
            except Exception:
                num = pl.col(col)
            expr = pl.col(col)
            for b in buckets:
                lo = b.get('min')
                hi = b.get('max')
                label = b.get('label')
                cond = None
                if lo is None and hi is not None:
                    cond = num < hi
                elif lo is not None and hi is None:
                    cond = num > lo
                elif lo is not None and hi is not None:
                    cond = (num >= lo) & (num <= hi)
                if cond is not None:
                    expr = pl.when(cond).then(pl.lit(label)).otherwise(expr)
            return df.with_columns(expr.alias(col))
        except Exception:
            try:
                pd = df.to_pandas()
                pd[col] = pd[col].apply(label_for_value)
                return pl.from_pandas(pd)
            except Exception:
                return df
    else:
        df2 = df.copy()
        try:
            df2[col] = df2[col].apply(label_for_value)
        except Exception:
            try:
                df2[col] = df2[col].astype(float).apply(label_for_value)
            except Exception:
                pass
        return df2


def _suggest_buckets(df, col, strategy: str = "quantile", n_buckets: int = 5):
    """Suggest bucket definitions for `col`.
    strategy: 'quantile'|'equal'|'kmeans'
    Returns list of buckets: {min, max, label, pmin?, pmax?}
    """
    if col not in (df.columns if _is_polars_df(df) else list(df.columns)):
        return []
    try:
        # extract numeric series
        if _is_polars_df(df):
            ser = df.select(pl.col(col)).to_series().cast(pl.Float64)
            nums = [float(x) for x in ser.to_list() if x is not None]
        else:
            ser = df[col]
            nums = [float(x) for x in ser.tolist() if x is not None]
    except Exception:
        return []

    if not nums:
        return []

    nums_sorted = sorted(nums)
    buckets = []
    try:
        if strategy == "quantile":
            # build equal-probability buckets
            import math
            for i in range(n_buckets):
                pmin = (i / n_buckets) * 100
                pmax = ((i + 1) / n_buckets) * 100
                lo = nums_sorted[int(math.floor((pmin / 100.0) * (len(nums_sorted) - 1)))]
                hi = nums_sorted[int(math.floor((pmax / 100.0) * (len(nums_sorted) - 1)))]
                buckets.append({"min": float(lo), "max": float(hi), "label": f"b{i+1}", "pmin": pmin, "pmax": pmax})
        elif strategy == "equal":
            lo = nums_sorted[0]
            hi = nums_sorted[-1]
            width = (hi - lo) / float(n_buckets)
            for i in range(n_buckets):
                bmin = None if i == 0 else lo + i * width
                bmax = None if i == n_buckets - 1 else lo + (i + 1) * width
                buckets.append({"min": None if bmin is None else float(bmin), "max": None if bmax is None else float(bmax), "label": f"b{i+1}"})
        elif strategy == "kmeans":
            try:
                import numpy as _np
                from sklearn.cluster import KMeans
                arr = _np.array(nums_sorted).reshape(-1, 1)
                km = KMeans(n_clusters=min(n_buckets, len(arr))).fit(arr)
                centers = sorted([float(c[0]) for c in km.cluster_centers_.tolist()])
                # build buckets between midpoints
                for i, c in enumerate(centers):
                    if i == 0:
                        lo = min(nums_sorted)
                        hi = (c + centers[i + 1]) / 2.0 if len(centers) > 1 else max(nums_sorted)
                    elif i == len(centers) - 1:
                        lo = (centers[i - 1] + c) / 2.0
                        hi = max(nums_sorted)
                    else:
                        lo = (centers[i - 1] + c) / 2.0
                        hi = (c + centers[i + 1]) / 2.0
                    buckets.append({"min": float(lo), "max": float(hi), "label": f"b{i+1}"})
            except Exception:
                # fallback to quantile
                return _suggest_buckets(df, col, strategy="quantile", n_buckets=n_buckets)
        else:
            return []
    except Exception:
        return []

    return buckets


def _build_condition_expr(condition, for_polars=True):
    """Build a polars expression or pandas boolean mask function from a condition.
    Condition can be:
      - simple: {'column': c, 'op': '>', 'value': v}
      - compound: {'op': 'and'|'or'|'not', 'conds': [cond,...]}
    Returns: for_polars True -> pl.Expr, else -> function(pd_df)->pd.Series mask
    """
    if condition is None:
        return None

    op = condition.get('op') if isinstance(condition, dict) else None

    if for_polars:
        try:
            if op and op.lower() in ('and', 'or'):
                sub = condition.get('conds', [])
                exprs = [_build_condition_expr(c, for_polars=True) for c in sub]
                exprs = [e for e in exprs if e is not None]
                if not exprs:
                    return None
                if op.lower() == 'and':
                    e = exprs[0]
                    for ex in exprs[1:]:
                        e = e & ex
                    return e
                else:
                    e = exprs[0]
                    for ex in exprs[1:]:
                        e = e | ex
                    return e
            if op and op.lower() == 'not':
                sub = condition.get('cond') or (condition.get('conds') or [None])[0]
                e = _build_condition_expr(sub, for_polars=True)
                if e is None:
                    return None
                return ~e

            # simple condition
            col = condition.get('column')
            cmp = condition.get('op')
            val = condition.get('value')
            if col is None or cmp is None:
                return None
            if cmp == '>':
                return pl.col(col) > val
            if cmp == '<':
                return pl.col(col) < val
            if cmp == '>=':
                return pl.col(col) >= val
            if cmp == '<=':
                return pl.col(col) <= val
            if cmp in ('==', '='):
                return pl.col(col) == val
            if cmp in ('!=', '<>'):
                return pl.col(col) != val
            if cmp == 'in':
                return pl.col(col).is_in(val if isinstance(val, (list, tuple)) else [val])
            if cmp == 'contains':
                return pl.col(col).str.contains(str(val))
            return None
        except Exception:
            return None
    else:
        # return a function that when given a pandas DataFrame returns boolean mask
        def mask_func(pd_df):
            import pandas as _pd
            try:
                if op and op.lower() in ('and', 'or'):
                    subs = condition.get('conds', [])
                    masks = [(_build_condition_expr(c, for_polars=False))(pd_df) for c in subs]
                    if not masks:
                        return _pd.Series([True] * len(pd_df), index=pd_df.index)
                    res = masks[0]
                    for m in masks[1:]:
                        if op.lower() == 'and':
                            res = res & m
                        else:
                            res = res | m
                    return res
                if op and op.lower() == 'not':
                    sub = condition.get('cond') or (condition.get('conds') or [None])[0]
                    return ~((_build_condition_expr(sub, for_polars=False))(pd_df))

                col = condition.get('column')
                cmp = condition.get('op')
                val = condition.get('value')
                if col is None or cmp is None:
                    return _pd.Series([True] * len(pd_df), index=pd_df.index)
                ser = pd_df[col]
                if cmp == '>':
                    return ser > val
                if cmp == '<':
                    return ser < val
                if cmp == '>=':
                    return ser >= val
                if cmp == '<=':
                    return ser <= val
                if cmp in ('==', '='):
                    return ser == val
                if cmp in ('!=', '<>'):
                    return ser != val
                if cmp == 'in':
                    return ser.isin(val if isinstance(val, (list, tuple)) else [val])
                if cmp == 'contains':
                    return ser.astype(str).str.contains(str(val))
                return _pd.Series([False] * len(pd_df), index=pd_df.index)
            except Exception:
                return _pd.Series([False] * len(pd_df), index=pd_df.index)

        return mask_func


def _conditional_transform(df, col, value, condition):
    # condition: simple or compound dict
    if condition is None:
        return df
    # build expression/mask depending on df type
    if _is_polars_df(df):
        try:
            expr = _build_condition_expr(condition, for_polars=True)
            if expr is None:
                return df
            try:
                lit_val = pl.lit(value)
            except Exception:
                lit_val = value
            return df.with_columns(pl.when(expr).then(lit_val).otherwise(pl.col(col)).alias(col))
        except Exception:
            return df
    else:
        try:
            pd = df.copy()
            mask_func = _build_condition_expr(condition, for_polars=False)
            if mask_func is None:
                return df
            mask = mask_func(pd)
            pd.loc[mask, col] = value
            return pd
        except Exception:
            return df


def _cast_column(df, col, to_type: str, fmt: str = None, errors: str = "coerce"):
    """Cast column `col` to `to_type`.
    to_type: 'int','float','str','datetime','bool','category'
    fmt: optional datetime format
    errors: 'coerce'|'ignore'
    Returns (df_new, info) where info contains rows changed/failed conversions when available.
    """
    to_type = (to_type or "").lower()
    if col not in (df.columns if _is_polars_df(df) else list(df.columns)):
        return df, None

    try:
        if _is_polars_df(df):
            try:
                # Polars-first expression-based casting
                if to_type in ("int", "integer"):
                    clean = pl.col(col).cast(pl.Utf8).str.replace_all(r"[\$\,\s\(\)]", "")
                    num = clean.cast(pl.Float64)
                    expr = pl.when(clean.str.lengths() > 0).then(num.cast(pl.Int64)).otherwise(pl.lit(None)).alias(col)
                    df_new = df.with_columns(expr)
                elif to_type in ("float", "double"):
                    clean = pl.col(col).cast(pl.Utf8).str.replace_all(r"[\$\,\s\(\)]", "")
                    expr = pl.when(clean.str.lengths() > 0).then(clean.cast(pl.Float64)).otherwise(pl.lit(None)).alias(col)
                    df_new = df.with_columns(expr)
                elif to_type in ("str", "string"):
                    df_new = df.with_columns(pl.col(col).cast(pl.Utf8).alias(col))
                elif to_type in ("datetime", "date", "ts"):
                    # Prefer pandas-style loose parsing for datetimes (handles many free-form inputs)
                    raise Exception("use_pandas_datetime")
                elif to_type in ("bool", "boolean"):
                    # Prefer pandas-backed boolean parsing (accepts yes/no/1/0/true/false)
                    raise Exception("use_pandas_bool")
                elif to_type in ("category", "cat"):
                    try:
                        df_new = df.with_columns(pl.col(col).cast(pl.Categorical).alias(col))
                    except Exception:
                        df_new = df.with_columns(pl.col(col).cast(pl.Utf8).alias(col))
                else:
                    return df, None

                # normalize results for numeric targets (ensure Python numeric types)
                try:
                    before_vals = df.select(pl.col(col)).to_series().to_list()
                    after_vals = df_new.select(pl.col(col)).to_series().to_list()
                    if to_type in ("int", "integer"):
                        normalized = []
                        for v in after_vals:
                            if v is None:
                                normalized.append(None)
                                continue
                            try:
                                # handle numpy types and numeric strings
                                nv = int(float(v))
                                normalized.append(nv)
                            except Exception:
                                normalized.append(None)
                        try:
                            df_new = df_new.with_columns(pl.Series(col, normalized).alias(col))
                            after_vals = normalized
                        except Exception:
                            pass
                    elif to_type in ("float", "double"):
                        normalized = []
                        for v in after_vals:
                            if v is None:
                                normalized.append(None)
                                continue
                            try:
                                nv = float(v)
                                normalized.append(nv)
                            except Exception:
                                normalized.append(None)
                        try:
                            df_new = df_new.with_columns(pl.Series(col, normalized).alias(col))
                            after_vals = normalized
                        except Exception:
                            pass
                    before_not_null = sum(1 for v in before_vals if v is not None and v != "")
                    after_not_null = sum(1 for v in after_vals if v is not None and v != "")
                    rows_changed = int(max(0, before_not_null - after_not_null))
                except Exception:
                    rows_changed = None

                info = {"step": "cast", "column": col, "to_type": to_type, "rows_changed": rows_changed}
                return df_new, info
            except Exception as exc:
                    # For certain types prefer pandas-backed loose parsing
                    try:
                        import pandas as _pd
                        pd_df = df.to_pandas()
                        df2 = pd_df.copy()
                        before_non_null = df2[col].notnull().sum() if col in df2.columns else None
                        if to_type in ("int", "integer"):
                            df2[col] = _pd.to_numeric(df2[col], errors=errors).astype('Int64')
                        elif to_type in ("float", "double"):
                            df2[col] = _pd.to_numeric(df2[col], errors=errors).astype(float)
                        elif to_type in ("str", "string"):
                            df2[col] = df2[col].astype(str)
                        elif to_type in ("datetime", "date", "ts"):
                            # loose datetime parsing: allow infer_formats and dayfirst try
                            try:
                                df2[col] = _pd.to_datetime(df2[col], format=fmt if fmt else None, errors=errors, infer_datetime_format=True)
                            except Exception:
                                df2[col] = _pd.to_datetime(df2[col], errors=errors, infer_datetime_format=True, dayfirst=False)
                        elif to_type in ("bool", "boolean"):
                            # loose boolean parsing accepting many textual variants
                            def parse_bool(v):
                                if v is None:
                                    return None
                                if isinstance(v, bool):
                                    return v
                                s = str(v).strip().lower()
                                if s in ("true", "t", "yes", "y", "1"):
                                    return True
                                if s in ("false", "f", "no", "n", "0"):
                                    return False
                                try:
                                    iv = int(float(s))
                                    return bool(iv)
                                except Exception:
                                    return None
                            df2[col] = df2[col].apply(parse_bool)
                        elif to_type in ("category", "cat"):
                            df2[col] = df2[col].astype('category')
                        else:
                            return df, None
                        after_non_null = df2[col].notnull().sum() if col in df2.columns else None
                        rows_changed = None
                        if before_non_null is not None and after_non_null is not None:
                            rows_changed = int(max(0, int(before_non_null) - int(after_non_null)))
                        info = {"step": "cast", "column": col, "to_type": to_type, "rows_changed": rows_changed}
                        try:
                            new_vals = df2[col].tolist()
                            df_new = df.with_columns(pl.Series(col, new_vals).alias(col))
                            return df_new, info
                        except Exception:
                            return df, info
                    except Exception:
                        info = {"step": "cast", "column": col, "to_type": to_type, "rows_changed": None}
                        return df, info
        else:
            import pandas as _pd
            df2 = df.copy()
            before_non_null = None
            try:
                before_non_null = df2[col].notnull().sum()
            except Exception:
                before_non_null = None

            try:
                if to_type in ("int", "integer"):
                    df2[col] = _pd.to_numeric(df2[col], errors=errors).astype('Int64')
                elif to_type in ("float", "double"):
                    df2[col] = _pd.to_numeric(df2[col], errors=errors).astype(float)
                elif to_type in ("str", "string"):
                    df2[col] = df2[col].astype(str)
                elif to_type in ("datetime", "date", "ts"):
                    df2[col] = _pd.to_datetime(df2[col], format=fmt if fmt else None, errors=errors)
                elif to_type in ("bool", "boolean"):
                    df2[col] = df2[col].astype('boolean')
                elif to_type in ("category", "cat"):
                    df2[col] = df2[col].astype('category')
                else:
                    return df2, None
            except Exception:
                return df2, None

            after_non_null = None
            try:
                after_non_null = df2[col].notnull().sum()
            except Exception:
                after_non_null = None

            rows_changed = None
            if before_non_null is not None and after_non_null is not None:
                rows_changed = int(max(0, int(before_non_null) - int(after_non_null)))
            info = {"step": "cast", "column": col, "to_type": to_type, "rows_changed": rows_changed}
            return df2, info
    except Exception:
        return df, None


def _percentile_bucketize(df, col, buckets):
    # buckets: list of {pmin, pmax, label} where pmin/pmax are percentiles 0-100
    if col not in (df.columns if _is_polars_df(df) else list(df.columns)):
        return df
    try:
        if _is_polars_df(df):
            ser = df.select(pl.col(col)).to_series()
            nums = ser.cast(pl.Float64)
            quantiles = {p: nums.quantile(p / 100.0) for b in buckets for p in (b.get('pmin', 0), b.get('pmax', 100))}
            # build expression
            num_expr = pl.col(col).cast(pl.Float64)
            expr = pl.col(col)
            for b in buckets:
                pmin = b.get('pmin', 0)
                pmax = b.get('pmax', 100)
                lo = quantiles.get(pmin)
                hi = quantiles.get(pmax)
                cond = (num_expr >= lo) & (num_expr <= hi) if lo is not None and hi is not None else None
                if cond is not None:
                    expr = pl.when(cond).then(b.get('label')).otherwise(expr)
            return df.with_columns(expr.alias(col))
        else:
            import pandas as _pd
            pdser = df[col].astype(float)
            quant = {}
            for b in buckets:
                for p in (b.get('pmin', 0), b.get('pmax', 100)):
                    quant[p] = pdser.quantile(p / 100.0)
            df2 = df.copy()
            def label_val(v):
                for b in buckets:
                    lo = quant.get(b.get('pmin', 0))
                    hi = quant.get(b.get('pmax', 100))
                    if lo is not None and hi is not None and lo <= float(v) <= hi:
                        return b.get('label')
                return v
            df2[col] = df2[col].apply(label_val)
            return df2
    except Exception:
        return df


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


def _impute_missing(df, col, params=None):
    params = params or {}
    strategy = params.get("strategy")
    source = params.get("source")
    sources = params.get("sources") or params.get("source_columns")
    group_by = params.get("group_by")
    sentinels = params.get("treat_as_missing") or []

    if col not in (df.columns if _is_polars_df(df) else list(df.columns)):
        return df

    try:
        if _is_polars_df(df):
            # do not operate on metadata/internal columns
            if col.startswith("_"):
                return df, None
            # compute missing positions before
            try:
                df_with_idx = df.with_row_index("__row_idx__")
                mask = (pl.col(col).is_null()) | (pl.col(col) == "")
                # include configured sentinels (both numeric and string forms)
                for s in sentinels:
                    try:
                        mask = mask | (pl.col(col) == s)
                    except Exception:
                        pass
                # default heuristic: do not treat 0 as missing unless sentinel present
                before_missing = df_with_idx.filter(mask).select("__row_idx__").to_series().to_list()
            except Exception:
                before_missing = []
            # from another column
            if strategy == "from_column" and source and source in df.columns:
                try:
                    # treat empty strings and configured sentinels as missing when copying from another column
                    try:
                        missing_expr = (pl.col(col).is_null()) | (pl.col(col) == "")
                        for s in sentinels:
                            try:
                                missing_expr = missing_expr | (pl.col(col) == s)
                            except Exception:
                                pass
                        df_new = df.with_columns(pl.when(missing_expr).then(pl.col(source)).otherwise(pl.col(col)).alias(col))
                    except Exception:
                        # fallback to simple fill_null if the above fails
                        df_new = df.with_columns(pl.col(col).fill_null(pl.col(source)).alias(col))
                    # compute after
                    try:
                        df2_with_idx = df_new.with_row_index("__row_idx__")
                        mask2 = (pl.col(col).is_null()) | (pl.col(col) == "")
                        for s in sentinels:
                            try:
                                mask2 = mask2 | (pl.col(col) == s)
                            except Exception:
                                pass
                        after_missing = df2_with_idx.filter(mask2).select("__row_idx__").to_series().to_list()
                    except Exception:
                        after_missing = []
                    filled = len(set(before_missing) - set(after_missing))
                    sample = sorted(list(set(before_missing) - set(after_missing)))[:20]
                    info = {"step": "impute", "column": col, "method": "from_column", "rows_changed": filled, "sample_positions": [i + 1 for i in sample], "source": source}
                    return df_new, info
                except Exception:
                    return df, None

            # forward/backward fill
            if strategy in ("ffill", "forward_fill"):
                try:
                    df_new = df.with_columns(df[col].fill_null(strategy="forward").alias(col))
                    # compute diagnostics
                    try:
                        df2_with_idx = df_new.with_row_index("__row_idx__")
                        after_missing = df2_with_idx.filter((pl.col(col).is_null()) | (pl.col(col) == "") | (pl.col(col) == 0)).select("__row_idx__").to_series().to_list()
                    except Exception:
                        after_missing = []
                    filled = len(set(before_missing) - set(after_missing))
                    info = {"step": "impute", "column": col, "method": "ffill", "rows_changed": filled}
                    return df_new, info
                except Exception:
                    return df, None

            if strategy in ("bfill", "backfill"):
                try:
                    df_new = df.with_columns(df[col].fill_null(strategy="backward").alias(col))
                    try:
                        df2_with_idx = df_new.with_row_index("__row_idx__")
                        after_missing = df2_with_idx.filter((pl.col(col).is_null()) | (pl.col(col) == "") | (pl.col(col) == 0)).select("__row_idx__").to_series().to_list()
                    except Exception:
                        after_missing = []
                    filled = len(set(before_missing) - set(after_missing))
                    info = {"step": "impute", "column": col, "method": "bfill", "rows_changed": filled}
                    return df_new, info
                except Exception:
                    return df, None

            # constant fill
            if strategy == "constant":
                value = params.get("value")
                try:
                    df_new = df.with_columns(pl.col(col).fill_null(value).alias(col))
                    try:
                        df2_with_idx = df_new.with_row_index("__row_idx__")
                        after_missing = df2_with_idx.filter((pl.col(col).is_null()) | (pl.col(col) == "") | (pl.col(col) == 0)).select("__row_idx__").to_series().to_list()
                    except Exception:
                        after_missing = []
                    filled = len(set(before_missing) - set(after_missing))
                    info = {"step": "impute", "column": col, "method": "constant", "rows_changed": filled, "value": value}
                    return df_new, info
                except Exception:
                    return df, None

                # fill missing strings with empty string
                if strategy == "empty_string":
                    try:
                        # replace nulls, empty strings, and sentinels with empty string
                        try:
                            missing_expr = (pl.col(col).is_null()) | (pl.col(col) == "")
                            for s in sentinels:
                                try:
                                    missing_expr = missing_expr | (pl.col(col) == s)
                                except Exception:
                                    pass
                        except Exception:
                            missing_expr = (pl.col(col).is_null()) | (pl.col(col) == "")

                        df_new = df.with_columns(pl.when(missing_expr).then(pl.lit("")).otherwise(pl.col(col)).alias(col))

                        # compute rows changed by comparing old vs new values
                        try:
                            old_rows = df.with_row_count("__row_idx__").select(["__row_idx__", col]).to_dicts()
                            new_rows = df_new.with_row_count("__row_idx__").select(["__row_idx__", col]).to_dicts()
                            diffs = [r["__row_idx__"] for r, n in zip(old_rows, new_rows) if (r.get(col) != n.get(col))]
                            filled = len(diffs)
                            sample = sorted(diffs)[:20]
                        except Exception:
                            filled = 0
                            sample = []

                        info = {"step": "impute", "column": col, "method": "empty_string", "rows_changed": filled, "sample_positions": [i + 1 for i in sample]}
                        return df_new, info
                    except Exception:
                        return df, None

            # mode (most frequent)
            if strategy in ("mode", "most_common"):
                try:
                    try:
                        mode_val = df.select(pl.col(source if source in df.columns else col).mode()).to_series()[0]
                    except Exception:
                        # fallback to pandas
                        pd = df.to_pandas()
                        mode_val = pd[source if source in pd.columns else col].mode().iloc[0]
                    df_new = df.with_columns(pl.col(col).fill_null(mode_val).alias(col))
                    try:
                        df2_with_idx = df_new.with_row_index("__row_idx__")
                        after_missing = df2_with_idx.filter((pl.col(col).is_null()) | (pl.col(col) == "") | (pl.col(col) == 0)).select("__row_idx__").to_series().to_list()
                    except Exception:
                        after_missing = []
                    filled = len(set(before_missing) - set(after_missing))
                    info = {"step": "impute", "column": col, "method": "mode", "rows_changed": filled, "mode_value": mode_val}
                    return df_new, info
                except Exception:
                    return df, None

            # mean/median/group mean
            if strategy in ("mean", "average", "median"):
                try:
                    # support median vs mean
                    if strategy == 'median':
                        agg = 'median'
                    else:
                        agg = 'mean'
                    if source and source in df.columns:
                        if group_by and group_by in df.columns:
                            if agg == 'mean':
                                df_new = df.with_columns(pl.col(col).fill_null(pl.col(source).mean().over(group_by)).alias(col))
                            else:
                                df_new = df.with_columns(pl.col(col).fill_null(pl.col(source).median().over(group_by)).alias(col))
                        else:
                            if agg == 'mean':
                                m = df.select(pl.col(source).mean()).to_series()[0]
                            else:
                                m = df.select(pl.col(source).median()).to_series()[0]
                            df_new = df.with_columns(pl.col(col).fill_null(m).alias(col))
                    else:
                        # fall back to target column mean/median
                        if strategy == 'median':
                            m = df.select(pl.col(col).median()).to_series()[0]
                        else:
                            m = df.select(pl.col(col).mean()).to_series()[0]
                        df_new = df.with_columns(pl.col(col).fill_null(m).alias(col))
                    # compute after missing and report
                    try:
                        df2_with_idx = df_new.with_row_index("__row_idx__")
                        after_missing = df2_with_idx.filter((pl.col(col).is_null()) | (pl.col(col) == "")).select("__row_idx__").to_series().to_list()
                    except Exception:
                        after_missing = []
                    filled = len(set(before_missing) - set(after_missing))
                    sample = sorted(list(set(before_missing) - set(after_missing)))[:20]
                    info = {"step": "impute", "column": col, "method": "mean", "rows_changed": filled, "sample_positions": [i + 1 for i in sample], "source": source, "group_by": group_by}
                    return df_new, info
                except Exception:
                    return df, None

            # row-wise mean across multiple source columns
            if strategy == "row_mean" and sources:
                try:
                    exprs = [pl.col(s).cast(pl.Float64) for s in sources if s in df.columns]
                    if not exprs:
                        return df, None
                    sum_expr = exprs[0]
                    for e in exprs[1:]:
                        sum_expr = sum_expr + e
                    mean_expr = (sum_expr / len(exprs)).alias("__row_mean__")
                    df2 = df.with_columns(mean_expr)
                    df_new = df2.with_columns(pl.col(col).fill_null(pl.col("__row_mean__")).alias(col)).drop("__row_mean__")
                    try:
                        df_old_idx = df.with_row_count("__row_idx__")
                        before_missing = df_old_idx.filter((pl.col(col).is_null()) | (pl.col(col) == "")).select("__row_idx__").to_series().to_list()
                        df_new_idx = df_new.with_row_count("__row_idx__")
                        after_missing = df_new_idx.filter((pl.col(col).is_null()) | (pl.col(col) == "")).select("__row_idx__").to_series().to_list()
                        filled = len(set(before_missing) - set(after_missing))
                        sample = sorted(list(set(before_missing) - set(after_missing)))[:20]
                    except Exception:
                        filled = 0
                        sample = []
                    info = {"step": "impute", "column": col, "method": "row_mean", "rows_changed": filled, "sample_positions": [i + 1 for i in sample], "source": sources}
                    return df_new, info
                except Exception:
                    return df, None

            # regression imputation: convert to pandas and reuse pandas path for robustness
            if strategy == 'regression' and sources:
                try:
                    # convert to pandas and delegate to pandas regression handling above
                    import pandas as _pd
                    pd_df = _pd.DataFrame(df.to_dicts())
                    import numpy as _np
                    valid_srcs = [s for s in sources if s in pd_df.columns]
                    if not valid_srcs:
                        return df, None
                    train = pd_df.dropna(subset=valid_srcs + [col])
                    if train.shape[0] == 0:
                        return df, None
                    X_train = train[valid_srcs].astype(float).to_numpy()
                    y_train = train[col].astype(float).to_numpy()
                    X_train_aug = _np.hstack([_np.ones((X_train.shape[0],1)), X_train])
                    coef, *_ = _np.linalg.lstsq(X_train_aug, y_train, rcond=None)
                    missing_idx = pd_df[pd_df[col].isnull()].index.tolist()
                    if not missing_idx:
                        return df, None
                    X_missing = pd_df.loc[missing_idx, valid_srcs].astype(float).to_numpy()
                    X_missing_aug = _np.hstack([_np.ones((X_missing.shape[0],1)), X_missing])
                    preds = X_missing_aug.dot(coef)
                    for idx, p in zip(missing_idx, preds):
                        pd_df.at[idx, col] = float(p)
                    df_new = pl.from_pandas(pd_df)
                    info = {"step": "impute", "column": col, "method": "regression", "rows_changed": len(preds), "model": "ols", "sources": valid_srcs}
                    return df_new, info
                except Exception:
                    return df, None

            return df, None
        else:
            import pandas as _pd
            df2 = df.copy()
            # do not operate on metadata/internal columns
            if str(col).startswith("_"):
                return df2, None
            # compute before missing
            try:
                mask = df2[col].isnull() | (df2[col].astype(str).str.strip() == "")
                try:
                    if sentinels:
                        mask = mask | df2[col].isin(sentinels)
                except Exception:
                    # if isin fails due to types, try string comparison
                    try:
                        mask = mask | df2[col].astype(str).isin([str(s) for s in sentinels])
                    except Exception:
                        pass
                before_missing = df2[mask].index.tolist()
            except Exception:
                before_missing = []

            if strategy == "from_column" and source and source in df2.columns:
                # treat empty strings and configured sentinels as missing when copying from another column
                try:
                    mask2 = df2[col].isnull() | (df2[col].astype(str).str.strip() == "")
                    try:
                        if sentinels:
                            mask2 = mask2 | df2[col].isin(sentinels)
                    except Exception:
                        try:
                            mask2 = mask2 | df2[col].astype(str).isin([str(s) for s in sentinels])
                        except Exception:
                            pass
                    df2.loc[mask2, col] = df2.loc[mask2, source]
                except Exception:
                    df2[col] = df2[col].fillna(df2[source])

                try:
                    mask2 = df2[col].isnull() | (df2[col].astype(str).str.strip() == "")
                    try:
                        if sentinels:
                            mask2 = mask2 | df2[col].isin(sentinels)
                    except Exception:
                        try:
                            mask2 = mask2 | df2[col].astype(str).isin([str(s) for s in sentinels])
                        except Exception:
                            pass
                    after_missing = df2[mask2].index.tolist()
                    filled = len(set(before_missing) - set(after_missing))
                    sample = sorted(list(set(before_missing) - set(after_missing)))[:20]
                except Exception:
                    filled = 0
                    sample = []
                info = {"step": "impute", "column": col, "method": "from_column", "rows_changed": filled, "sample_positions": [i + 1 for i in sample], "source": source}
                return df2, info
            # forward/backward fill
            if strategy in ("ffill", "forward_fill"):
                try:
                    df2[col] = df2[col].fillna(method='ffill')
                    try:
                        mask2 = df2[col].isnull() | (df2[col].astype(str).str.strip() == "")
                        try:
                            if sentinels:
                                mask2 = mask2 | df2[col].isin(sentinels)
                        except Exception:
                            try:
                                mask2 = mask2 | df2[col].astype(str).isin([str(s) for s in sentinels])
                            except Exception:
                                pass
                        after_missing = df2[mask2].index.tolist()
                        filled = len(set(before_missing) - set(after_missing))
                        sample = sorted(list(set(before_missing) - set(after_missing)))[:20]
                    except Exception:
                        filled = 0
                        sample = []
                except Exception:
                    filled = 0
                    sample = []
                info = {"step": "impute", "column": col, "method": "ffill", "rows_changed": filled}
                return df2, info
            if strategy in ("bfill", "backfill"):
                try:
                    df2[col] = df2[col].fillna(method='bfill')
                    try:
                        mask2 = df2[col].isnull() | (df2[col].astype(str).str.strip() == "")
                        try:
                            if sentinels:
                                mask2 = mask2 | df2[col].isin(sentinels)
                        except Exception:
                            try:
                                mask2 = mask2 | df2[col].astype(str).isin([str(s) for s in sentinels])
                            except Exception:
                                pass
                        after_missing = df2[mask2].index.tolist()
                        filled = len(set(before_missing) - set(after_missing))
                        sample = sorted(list(set(before_missing) - set(after_missing)))[:20]
                    except Exception:
                        filled = 0
                        sample = []
                except Exception:
                    filled = 0
                    sample = []
                info = {"step": "impute", "column": col, "method": "bfill", "rows_changed": filled}
                return df2, info
            # constant fill
            if strategy == "constant":
                value = params.get("value")
                try:
                    df2[col] = df2[col].fillna(value)
                    try:
                        mask2 = df2[col].isnull() | (df2[col].astype(str).str.strip() == "")
                        try:
                            if sentinels:
                                mask2 = mask2 | df2[col].isin(sentinels)
                        except Exception:
                            try:
                                mask2 = mask2 | df2[col].astype(str).isin([str(s) for s in sentinels])
                            except Exception:
                                pass
                        after_missing = df2[mask2].index.tolist()
                        filled = len(set(before_missing) - set(after_missing))
                        sample = sorted(list(set(before_missing) - set(after_missing)))[:20]
                    except Exception:
                        filled = 0
                        sample = []
                except Exception:
                    filled = 0
                    sample = []
                info = {"step": "impute", "column": col, "method": "constant", "rows_changed": filled, "value": value}
                return df2, info
            # mode
            # fill missing strings with empty string (pandas path)
            if strategy == "empty_string":
                try:
                    # detect nulls/empty/sentinels
                    try:
                        mask_null = df2[col].isnull()
                        mask_empty = df2[col].astype(str).str.strip() == ""
                        mask = mask_null | mask_empty
                        try:
                            if sentinels:
                                mask = mask | df2[col].isin(sentinels)
                        except Exception:
                            try:
                                mask = mask | df2[col].astype(str).isin([str(s) for s in sentinels])
                            except Exception:
                                pass
                    except Exception:
                        mask = df2[col].isnull()

                    df_old = df2.copy()
                    df2.loc[mask, col] = ""

                    try:
                        # compare old vs new to count changed rows
                        changed = (df_old[col].fillna(object()) != df2[col].fillna(object()))
                        diffs_idx = df_old[changed].index.tolist()
                        filled = len(diffs_idx)
                        sample = sorted(diffs_idx)[:20]
                    except Exception:
                        filled = 0
                        sample = []

                    info = {"step": "impute", "column": col, "method": "empty_string", "rows_changed": filled, "sample_positions": [i + 1 for i in sample]}
                    return df2, info
                except Exception:
                    return df2, None

            if strategy in ("mode", "most_common"):
                try:
                    mode_series = df2[source if source in df2.columns else col].mode()
                    if len(mode_series) > 0:
                        mode_val = mode_series.iloc[0]
                    else:
                        mode_val = None
                    if mode_val is not None:
                        df2[col] = df2[col].fillna(mode_val)
                    try:
                        mask2 = df2[col].isnull() | (df2[col].astype(str).str.strip() == "")
                        try:
                            if sentinels:
                                mask2 = mask2 | df2[col].isin(sentinels)
                        except Exception:
                            try:
                                mask2 = mask2 | df2[col].astype(str).isin([str(s) for s in sentinels])
                            except Exception:
                                pass
                        after_missing = df2[mask2].index.tolist()
                        filled = len(set(before_missing) - set(after_missing))
                        sample = sorted(list(set(before_missing) - set(after_missing)))[:20]
                    except Exception:
                        filled = 0
                        sample = []
                except Exception:
                    filled = 0
                    sample = []
                info = {"step": "impute", "column": col, "method": "mode", "rows_changed": filled, "mode_value": mode_val if 'mode_val' in locals() else None}
                return df2, info

            if strategy in ("mean", "average", "median"):
                if source and source in df2.columns:
                    # support median vs mean
                    if strategy == 'median':
                        agg = 'median'
                    else:
                        agg = 'mean'
                    if group_by and group_by in df2.columns:
                        if agg == 'mean':
                            grp = df2.groupby(group_by)[source].transform("mean")
                        else:
                            grp = df2.groupby(group_by)[source].transform("median")
                        df2[col] = df2[col].fillna(grp)
                        try:
                            after_missing = df2[df2[col].isnull() | (df2[col].astype(str).str.strip() == "")].index.tolist()
                            filled = len(set(before_missing) - set(after_missing))
                            sample = sorted(list(set(before_missing) - set(after_missing)))[:20]
                        except Exception:
                            filled = 0
                            sample = []
                        info = {"step": "impute", "column": col, "method": agg, "rows_changed": filled, "sample_positions": [i + 1 for i in sample], "source": source, "group_by": group_by}
                        return df2, info
                    else:
                        if agg == 'mean':
                            m = df2[source].astype(float).mean()
                        else:
                            m = df2[source].astype(float).median()
                        df2[col] = df2[col].fillna(m)
                        try:
                            after_missing = df2[df2[col].isnull() | (df2[col].astype(str).str.strip() == "")].index.tolist()
                            filled = len(set(before_missing) - set(after_missing))
                            sample = sorted(list(set(before_missing) - set(after_missing)))[:20]
                        except Exception:
                            filled = 0
                            sample = []
                        info = {"step": "impute", "column": col, "method": agg, "rows_changed": filled, "sample_positions": [i + 1 for i in sample], "source": source}
                        return df2, info
                else:
                    m = df2[col].astype(float).mean()
                    df2[col] = df2[col].fillna(m)
                    try:
                        after_missing = df2[df2[col].isnull() | (df2[col].astype(str).str.strip() == "")].index.tolist()
                        filled = len(set(before_missing) - set(after_missing))
                        sample = sorted(list(set(before_missing) - set(after_missing)))[:20]
                    except Exception:
                        filled = 0
                        sample = []
                    info = {"step": "impute", "column": col, "method": "mean", "rows_changed": filled, "sample_positions": [i + 1 for i in sample]}
                    return df2, info
            if strategy == "row_mean" and sources:
                valid = [s for s in sources if s in df2.columns]
                if not valid:
                    return df2
                rowmean = df2[valid].astype(float).mean(axis=1)
                df2[col] = df2[col].fillna(rowmean)
                try:
                    after_missing = df2[df2[col].isnull() | (df2[col].astype(str).str.strip() == "")].index.tolist()
                    filled = len(set(before_missing) - set(after_missing))
                    sample = sorted(list(set(before_missing) - set(after_missing)))[:20]
                except Exception:
                    filled = 0
                    sample = []
                info = {"step": "impute", "column": col, "method": "row_mean", "rows_changed": filled, "sample_positions": [i + 1 for i in sample], "source": valid}
                return df2, info
            # regression imputation for pandas
            if strategy == 'regression' and sources:
                try:
                    import numpy as _np
                    valid_srcs = [s for s in sources if s in df2.columns]
                    if not valid_srcs:
                        return df2, None
                    train = df2.dropna(subset=valid_srcs + [col])
                    if train.shape[0] == 0:
                        return df2, None
                    X_train = train[valid_srcs].astype(float).to_numpy()
                    y_train = train[col].astype(float).to_numpy()
                    X_train_aug = _np.hstack([_np.ones((X_train.shape[0],1)), X_train])
                    coef, *_ = _np.linalg.lstsq(X_train_aug, y_train, rcond=None)
                    missing_idx = df2[df2[col].isnull()].index.tolist()
                    if not missing_idx:
                        return df2, None
                    X_missing = df2.loc[missing_idx, valid_srcs].astype(float).to_numpy()
                    X_missing_aug = _np.hstack([_np.ones((X_missing.shape[0],1)), X_missing])
                    preds = X_missing_aug.dot(coef)
                    for idx, p in zip(missing_idx, preds):
                        df2.at[idx, col] = float(p)
                    info = {"step": "impute", "column": col, "method": "regression", "rows_changed": len(preds), "model": "ols", "sources": valid_srcs}
                    return df2, info
                except Exception:
                    return df2, None
            return df2, None
    except Exception:
        return df, None


class Cleaner:
    # Columns considered metadata and not part of the user's data table
    META_KEYS = {"container_name", "sheet_name", "slide_number", "paragraph_index", "table_index", "column_index", "cell_label", "source_kind", "source_name", "source_path", "unit_kind", "row_index"}
    def profile(self, path: str):
        """Produce a minimal profile for a CSV path using Polars if available."""
        df = _read_table(path)
        profile = {}
        for col in df.columns:
            # ignore metadata columns
            if col in self.META_KEYS or (isinstance(col, str) and col.startswith("_")):
                continue
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
                    s = str(x).strip()
                    if s == "":
                        return False
                    # handle parentheses as negative numbers
                    if s.startswith('(') and s.endswith(')'):
                        s = '-' + s[1:-1]
                    # remove common currency symbols
                    s = re.sub(r'[\$\£\€\¥\₹]', '', s)
                    # remove thousands separators (commas and spaces)
                    s = s.replace(',', '').replace(' ', '')
                    float(s)
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
                        # filter out metadata columns from output
                        out_cols = [c for c in cols if c not in self.META_KEYS and not (isinstance(c, str) and c.startswith("_"))]
                        slice_rows = [ {k:v for k,v in r.items() if k in out_cols} for r in slice_rows ]
                        diagnostics = compute_diagnostics_from_rows(rows_out, out_cols)
                        # include a small raw preview of original extracted text lines
                        raw_preview = lines[safe_offset : safe_offset + safe_limit]
                        return {
                            "path": path,
                            "row_count": total_rows,
                            "column_count": len(out_cols),
                            "columns": out_cols,
                            "rows": slice_rows,
                            "offset": safe_offset,
                            "limit": safe_limit,
                            "returned_rows": len(slice_rows),
                            "has_prev": safe_offset > 0,
                            "has_next": (safe_offset + safe_limit) < total_rows,
                            "diagnostics": diagnostics,
                            "raw_preview": raw_preview,
                        }
                    # If cell-level extraction exists (from .xlsx, .docx tables, .pptx tables), assemble a 2D grid
                    try:
                        if pl is not None and "unit_kind" in df.columns and any(k in ("cell", "table_cell") for k in df.select(pl.col("unit_kind")).to_series().to_list()):
                            # prefer sheet-level if present; choose the first sheet by occurrence
                            try:
                                cells_df = df.filter(pl.col("unit_kind").is_in(["cell", "table_cell"]))
                            except Exception:
                                cells_df = df.filter((pl.col("unit_kind") == "cell") | (pl.col("unit_kind") == "table_cell"))

                            cells = cells_df.select([c for c in ["sheet_name", "row_index", "column_index", "cell_label", "text"] if c in cells_df.columns]).to_dicts()
                            if cells:
                                # group by sheet_name and pick the first non-empty sheet
                                sheets = {}
                                for c in cells:
                                    sheet = c.get("sheet_name") or "sheet1"
                                    sheets.setdefault(sheet, []).append(c)
                                first_sheet = list(sheets.keys())[0]
                                sheet_cells = sheets[first_sheet]

                                # coerce indices to ints and find extents
                                max_row = 0
                                max_col = 0
                                cell_map = {}
                                for cc in sheet_cells:
                                    try:
                                        r = int(cc.get("row_index") or 0)
                                        col = int(cc.get("column_index") or 0)
                                    except Exception:
                                        continue
                                    if r < 1 or col < 1:
                                        continue
                                    max_row = max(max_row, r)
                                    max_col = max(max_col, col)
                                    cell_map[(r, col)] = cc.get("text")

                                # build rows matrix (1-based rows). We'll detect header from first row.
                                matrix = []
                                for r in range(1, max_row + 1):
                                    row_vals = [cell_map.get((r, c), None) for c in range(1, max_col + 1)]
                                    matrix.append(row_vals)

                                # header detection: if first row has at least one non-numeric and not all empty, treat as header
                                def _is_number_val(x):
                                    try:
                                        if x is None or x == "":
                                            return False
                                        s = str(x).strip()
                                        if s == "":
                                            return False
                                        if s.startswith('(') and s.endswith(')'):
                                            s = '-' + s[1:-1]
                                        s = re.sub(r'[\$\£\€\¥\₹]', '', s)
                                        s = s.replace(',', '').replace(' ', '')
                                        float(s)
                                        return True
                                    except Exception:
                                        return False

                                header = None
                                if matrix and any(v not in (None, "") for v in matrix[0]):
                                    first_row = matrix[0]
                                    non_numeric = sum(1 for v in first_row if not _is_number_val(v))
                                    if non_numeric >= 1:
                                        header = [h if h not in (None, "") else f"col_{i+1}" for i, h in enumerate(first_row)]

                                cols = []
                                rows_out = []
                                start_row = 1
                                if header:
                                    cols = header
                                    start_row = 2
                                else:
                                    cols = [f"col_{i+1}" for i in range(max_col)]

                                for ridx in range(start_row - 1, len(matrix)):
                                    row = matrix[ridx]
                                    rowdict = {}
                                    for j, v in enumerate(row):
                                        colname = cols[j] if j < len(cols) else f"col_{j+1}"
                                        rowdict[colname] = v
                                    rows_out.append(rowdict)

                                if rows_out:
                                    total_rows = len(rows_out)
                                    safe_offset = max(0, int(offset))
                                    safe_limit = max(1, min(int(limit), 500))
                                    slice_rows = rows_out[safe_offset : safe_offset + safe_limit]
                                    # filter out metadata columns from output
                                    out_cols = [c for c in cols if c not in self.META_KEYS and not (isinstance(c, str) and c.startswith("_"))]
                                    slice_rows = [ {k:v for k,v in r.items() if k in out_cols} for r in slice_rows ]
                                    diagnostics = compute_diagnostics_from_rows(rows_out, out_cols)
                                    raw_preview = [ {"r": r, "cells": matrix[r] } for r in range(safe_offset, min(safe_offset + safe_limit, len(matrix))) ]
                                    return {
                                        "path": path,
                                        "row_count": total_rows,
                                        "column_count": len(out_cols),
                                        "columns": out_cols,
                                        "rows": slice_rows,
                                        "offset": safe_offset,
                                        "limit": safe_limit,
                                        "returned_rows": len(slice_rows),
                                        "has_prev": safe_offset > 0,
                                        "has_next": (safe_offset + safe_limit) < total_rows,
                                        "diagnostics": diagnostics,
                                        "raw_preview": raw_preview,
                                        "sheet_name": first_sheet,
                                    }
                    except Exception:
                        pass
        except Exception:
            # best-effort reconstruction failed; fall back to dataframe inspection
            pass

        # Default: use polars dataframe columns and compute lightweight diagnostics
        safe_offset = max(0, int(offset))
        safe_limit = max(1, min(int(limit), 500))
        rows = df.slice(safe_offset, safe_limit).to_dicts()
        total_rows = int(df.height)
        end = safe_offset + len(rows)

        # filter out metadata columns from rows and diagnostics
        out_cols = [c for c in list(df.columns) if c not in self.META_KEYS and not (isinstance(c, str) and c.startswith("_"))]
        rows = [ {k:v for k,v in r.items() if k in out_cols} for r in rows ]

        diagnostics = {}
        try:
            df_small = df.with_row_count("_row_index")
            for col in out_cols:
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
            "column_count": int(len(out_cols)),
            "columns": out_cols,
            "rows": rows,
            "offset": safe_offset,
            "limit": safe_limit,
            "returned_rows": int(len(rows)),
            "has_prev": safe_offset > 0,
            "has_next": end < total_rows,
            "diagnostics": diagnostics,
        }

    def _reconstruct_table_from_df(self, df, offset: int = 0, limit: int = None):
        """Attempt to reconstruct a wide table from long-form ingestion DataFrame.
        Returns a list of row dicts (filtered of meta columns). If limit is None or <=0, return all rows.
        """
        try:
            if pl is None or not {"unit_kind", "text"}.issubset(set(df.columns if hasattr(df, 'columns') else [])):
                # Not long-form; fall back to head records
                return _df_head_records(df, limit if limit is not None else None)

            # gather line-level rows
            try:
                lines = df.filter(pl.col("unit_kind") == "line").sort("row_index").select(["row_index", "text"]).to_dicts()
            except Exception:
                try:
                    lines = df.filter(pl.col("unit_kind") == "line").select(["row_index", "text"]).to_dicts()
                except Exception:
                    lines = []

            if lines:
                # detect delimiter from sample
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

                # detect header from first row
                header = None
                if parsed_rows:
                    first_vals = parsed_rows[0]
                    non_numeric = sum(1 for v in first_vals if not (v is None or v == "" or (isinstance(v, str) and v.replace(',', '').replace('.', '').isdigit())))
                    if non_numeric >= 1:
                        header = [h or f"col_{i+1}" for i, h in enumerate(first_vals)]

                cols = []
                rows_out = []
                start_row = 1
                if header:
                    cols = header
                    start_row = 2
                else:
                    cols = [f"col_{i+1}" for i in range(max(len(r) for r in parsed_rows))]

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
                    # handle limit/offset
                    safe_offset = max(0, int(offset or 0))
                    if limit is None or (isinstance(limit, int) and limit <= 0):
                        slice_rows = rows_out[safe_offset:]
                    else:
                        safe_limit = max(1, min(int(limit), 10000))
                        slice_rows = rows_out[safe_offset : safe_offset + safe_limit]

                    out_cols = [c for c in cols if c not in self.META_KEYS and not (isinstance(c, str) and c.startswith("_"))]
                    slice_rows = [{k: v for k, v in r.items() if k in out_cols} for r in slice_rows]
                    return slice_rows

            # cell-level table assembly
            try:
                if pl is not None and "unit_kind" in df.columns and any(k in ("cell", "table_cell") for k in df.select(pl.col("unit_kind")).to_series().to_list()):
                    try:
                        cells_df = df.filter(pl.col("unit_kind").is_in(["cell", "table_cell"]))
                    except Exception:
                        cells_df = df.filter((pl.col("unit_kind") == "cell") | (pl.col("unit_kind") == "table_cell"))

                    cells = cells_df.select([c for c in ["sheet_name", "row_index", "column_index", "cell_label", "text"] if c in cells_df.columns]).to_dicts()
                    if cells:
                        sheets = {}
                        for c in cells:
                            sheet = c.get("sheet_name") or "sheet1"
                            sheets.setdefault(sheet, []).append(c)
                        first_sheet = list(sheets.keys())[0]
                        sheet_cells = sheets[first_sheet]

                        max_row = 0
                        max_col = 0
                        cell_map = {}
                        for cc in sheet_cells:
                            try:
                                r = int(cc.get("row_index") or 0)
                                col = int(cc.get("column_index") or 0)
                            except Exception:
                                continue
                            if r < 1 or col < 1:
                                continue
                            max_row = max(max_row, r)
                            max_col = max(max_col, col)
                            cell_map[(r, col)] = cc.get("text")

                        matrix = []
                        for r in range(1, max_row + 1):
                            row_vals = [cell_map.get((r, c), None) for c in range(1, max_col + 1)]
                            matrix.append(row_vals)

                        def _is_number_val(x):
                            try:
                                if x is None or x == "":
                                    return False
                                float(str(x).replace(',', ''))
                                return True
                            except Exception:
                                return False

                        header = None
                        if matrix and any(v not in (None, "") for v in matrix[0]):
                            first_row = matrix[0]
                            non_numeric = sum(1 for v in first_row if not _is_number_val(v))
                            if non_numeric >= 1:
                                header = [h if h not in (None, "") else f"col_{i+1}" for i, h in enumerate(first_row)]

                        cols = header if header else [f"col_{i+1}" for i in range(max_col)]
                        rows_out = []
                        start_row = 2 if header else 1
                        for ridx in range(start_row - 1, len(matrix)):
                            row = matrix[ridx]
                            rowdict = {}
                            for j, v in enumerate(row):
                                colname = cols[j] if j < len(cols) else f"col_{j+1}"
                                rowdict[colname] = v
                            rows_out.append(rowdict)

                        if rows_out:
                            if limit is None or (isinstance(limit, int) and limit <= 0):
                                slice_rows = rows_out
                            else:
                                safe_limit = max(1, min(int(limit), 10000))
                                slice_rows = rows_out[0:safe_limit]
                            out_cols = [c for c in cols if c not in self.META_KEYS and not (isinstance(c, str) and c.startswith("_"))]
                            slice_rows = [{k: v for k, v in r.items() if k in out_cols} for r in slice_rows]
                            return slice_rows
            except Exception:
                pass
        except Exception:
            pass
        # fallback: return head records
        return _df_head_records(df, limit if limit is not None else None)

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

    def cleanup_snapshots(self, retention_days: int = 30):
        """Delete snapshot files older than `retention_days` from data/history/snapshots.
        Returns a summary dict with counts.
        """
        snaps_dir = Path("data") / "history" / "snapshots"
        if not snaps_dir.exists():
            return {"deleted": 0, "remaining": 0}
        now = datetime.utcnow()
        deleted = 0
        remaining = 0
        for p in snaps_dir.iterdir():
            try:
                if not p.is_file():
                    continue
                mtime = datetime.utcfromtimestamp(p.stat().st_mtime)
                age_days = (now - mtime).total_seconds() / 86400.0
                if age_days > float(retention_days):
                    try:
                        p.unlink()
                        deleted += 1
                    except Exception:
                        continue
                else:
                    remaining += 1
            except Exception:
                continue
        return {"deleted": deleted, "remaining": remaining}

    def validate_schema(self, path: str, expected_columns: list):
        """Compare current profile of `path` to `expected_columns` and return drift info.

        Returns: {missing: [...], extra: [...], ok: bool, expected_count: int, actual_count: int}
        """
        try:
            profile = self.profile(path)
        except Exception:
            # if we can't profile, report as not ok
            return {"ok": False, "error": "cannot_profile_path", "expected_count": len(expected_columns), "actual_count": 0}
        actual_cols = set(profile.keys())
        expected = set(expected_columns or [])
        missing = sorted(list(expected - actual_cols))
        extra = sorted(list(actual_cols - expected))
        ok = (len(missing) == 0)
        return {"ok": ok, "missing": missing, "extra": extra, "expected_count": len(expected), "actual_count": len(actual_cols)}

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
        # If source is long-form extraction, reconstruct a wide table first
        try:
            if pl is not None and {"unit_kind", "text"}.issubset(set(df.columns if hasattr(df, 'columns') else [])):
                rows = self._reconstruct_table_from_df(df, offset=0, limit=None)
                if rows:
                    try:
                        df = pl.from_dicts(rows)
                    except Exception:
                        try:
                            import pandas as _pd
                            df = _pd.DataFrame(rows)
                        except Exception:
                            # fall back to original polars df if conversion fails
                            df = _read_table(src)
        except Exception:
            pass
        diagnostics = []
        for step in recipe.cleaning_steps:
            # Skip steps that reference missing columns to avoid runtime errors
            col = getattr(step, "column", None)
            # Never apply transformations to metadata columns
            if col and col in self.META_KEYS:
                continue
            if col and col not in df.columns:
                # skip silently in runner; caller may validate schema separately
                continue

            if step.action == "drop_column" and col:
                df = _drop_column(df, col)
            elif step.action == "impute" and col:
                params = step.params or {}
                res = _impute_missing(df, col, params)
                if isinstance(res, tuple):
                    df = res[0]
                    info = res[1]
                    if info:
                        diagnostics.append(info)
                else:
                    df = res
            elif step.action == "normalize" and col:
                try:
                    case = (step.params or {}).get("case", "lower")
                    # support unicode normalization via params
                    if (step.params or {}).get("unicode"):
                        form = (step.params or {}).get("form", "NFKC")
                        remove_diacritics = bool((step.params or {}).get("remove_diacritics", False))
                        df = _unicode_normalize_column(df, col, form=form, remove_diacritics=remove_diacritics)
                    df = _string_transform_column(df, col, case=case)
                except Exception:
                    pass
            elif step.action == "deduplicate":
                subset = [col] if col else None
                df = _unique_df(df, subset=subset)
            elif step.action == "unicode_normalize" and col:
                params = step.params or {}
                form = params.get("form", "NFKC")
                remove_diacritics = bool(params.get("remove_diacritics", False))
                df = _unicode_normalize_column(df, col, form=form, remove_diacritics=remove_diacritics)
            elif step.action == "map" and col:
                mapping = (step.params or {}).get("mapping") or {}
                if mapping:
                    df = _map_values(df, col, mapping)
            elif step.action == "regex_replace" and col:
                pat = (step.params or {}).get("pattern")
                repl = (step.params or {}).get("replace")
                if pat is not None:
                    flags = (step.params or {}).get("flags")
                    df = _regex_replace(df, col, pat, repl or "", flags=flags)
            elif step.action == "bucketize" and col:
                buckets = (step.params or {}).get("buckets", [])
                if buckets:
                    df = _bucketize(df, col, buckets)
            elif step.action == "replace" and col:
                old = (step.params or {}).get("old")
                new = (step.params or {}).get("new")
                if old is not None:
                    df = _replace_values(df, col, old, new)
            elif step.action == "remove_by_type" and col:
                params = step.params or {}
                res = _remove_by_type(df, col, params)
                if isinstance(res, tuple):
                    df = res[0]
                    info = res[1]
                    if info:
                        diagnostics.append(info)
                else:
                    df = res
            elif step.action == "move_by_type" or step.action == "swap_by_types":
                params = step.params or {}
                if step.action == "move_by_type":
                    moves = [params]
                else:
                    moves = params.get("moves") or []
                res = _swap_by_types(df, moves, replacement=params.get("replacement", ""))
                if isinstance(res, tuple):
                    df = res[0]
                    info = res[1]
                    if info:
                        diagnostics.append(info)
                else:
                    df = res
            elif step.action == "rename" and col:
                new_name = (step.params or {}).get("new_name")
                if new_name:
                    df = _rename_column(df, col, new_name)
            elif step.action == "fuzzy_dedupe":
                params = step.params or {}
                subset = params.get("subset")
                threshold = float(params.get("threshold", 0.85))
                method = params.get("method", 'difflib')
                res = _fuzzy_dedupe(df, subset=subset, threshold=threshold, method=method)
                if isinstance(res, tuple):
                    df = res[0]
                    info = res[1]
                    if info:
                        diagnostics.append(info)
                else:
                    df = res
            elif step.action == "cast" and col:
                params = step.params or {}
                to_type = params.get("to_type") or params.get("type")
                fmt = params.get("format")
                res = _cast_column(df, col, to_type, fmt)
                if isinstance(res, tuple):
                    df = res[0]
                    info = res[1]
                    if info:
                        diagnostics.append(info)
                else:
                    df = res
            elif step.action == "conditional" and col:
                params = step.params or {}
                val = params.get("value")
                cond = params.get("condition")
                if cond is not None:
                    df = _conditional_transform(df, col, val, cond)
            elif step.action == "join":
                params = step.params or {}
                left_path = params.get("left")
                right_path = params.get("right")
                keys = params.get("keys") or []
                fuzzy = params.get("fuzzy", False)
                # only support single-key joins for fuzzy matching
                if right_path and keys:
                    try:
                        right_df = _read_table(right_path)
                        join_key = keys[0]
                        if not fuzzy:
                            # standard left join
                            if _is_polars_df(df) and _is_polars_df(right_df):
                                df = df.join(right_df, on=join_key, how="left")
                            else:
                                # pandas fallback
                                import pandas as _pd
                                left_pd = df.to_pandas() if _is_polars_df(df) else df
                                right_pd = right_df.to_pandas() if _is_polars_df(right_df) else right_df
                                df = left_pd.merge(right_pd, left_on=join_key, right_on=join_key, how="left")
                        else:
                            # fuzzy join: match closest right key for each left key value
                            # build list of candidate keys from right
                            if _is_polars_df(right_df):
                                try:
                                    right_keys = [str(x) for x in right_df.select(pl.col(join_key)).to_series().to_list()]
                                except Exception:
                                    # fallback: try direct column access on polars DataFrame
                                    try:
                                        right_keys = [str(x) for x in right_df[join_key].to_list()] if join_key in right_df.columns else []
                                    except Exception:
                                        right_keys = []
                            else:
                                right_keys = [str(x) for x in right_df[join_key].astype(str).unique().tolist()]

                            # threshold for matching and algorithm choice
                            cutoff = float(params.get("threshold", 0.8))
                            algorithm = (params.get("algorithm") or params.get("method") or "difflib").lower()
                            for rk in right_keys:
                                pass

                            # build matching function: support 'difflib' and optional 'minhash' (if datasketch installed)
                            find_best = None
                            if algorithm == 'minhash':
                                try:
                                    from datasketch import MinHash, MinHashLSH
                                    num_perm = int(params.get('num_perm', 128))
                                    lsh = MinHashLSH(threshold=cutoff, num_perm=num_perm)
                                    for idx, rk in enumerate(right_keys):
                                        mh = MinHash(num_perm=num_perm)
                                        for sh in str(rk).split():
                                            mh.update(sh.encode('utf8'))
                                        lsh.insert(str(idx), mh)

                                    def _find_best_minhash(v):
                                        if v is None:
                                            return None
                                        mh = MinHash(num_perm=num_perm)
                                        for sh in str(v).split():
                                            mh.update(sh.encode('utf8'))
                                        res = lsh.query(mh)
                                        if res:
                                            try:
                                                return right_keys[int(res[0])]
                                            except Exception:
                                                return right_keys[0] if right_keys else None
                                        return None

                                    find_best = _find_best_minhash
                                except Exception:
                                    # fall back to difflib below
                                    find_best = None

                            if find_best is None:
                                def find_best(v):
                                    if v is None:
                                        return None
                                    s = str(v)
                                    matches = difflib.get_close_matches(s, right_keys, n=1, cutoff=cutoff)
                                    return matches[0] if matches else None

                            if _is_polars_df(df):
                                try:
                                    # create helper mapped column
                                    mapped = df.select(pl.col(join_key)).to_series().to_list()
                                    mapped_vals = [find_best(x) for x in mapped]
                                    df = df.with_columns(pl.Series("__fuzzy_key__", mapped_vals))
                                    # rename right side columns to avoid collisions
                                    right_pref = right_df.rename({c: f"right__{c}" for c in right_df.columns})
                                    df = df.join(right_pref, left_on="__fuzzy_key__", right_on=f"right__{join_key}", how="left")
                                    # drop helper key
                                    try:
                                        df = df.drop("__fuzzy_key__")
                                    except Exception:
                                        pass
                                except Exception:
                                    pass
                            else:
                                import pandas as _pd
                                left_pd = df.to_pandas() if _is_polars_df(df) else df.copy()
                                right_pd = right_df.to_pandas() if _is_polars_df(right_df) else right_df.copy()
                                left_pd["__fuzzy_key__"] = left_pd[join_key].apply(find_best)
                                right_pd = right_pd.rename(columns={join_key: "__right_key__"})
                                df = left_pd.merge(right_pd, left_on="__fuzzy_key__", right_on="__right_key__", how="left")
                    except Exception:
                        pass
        out_path = recipe.outputs[0]["path"] if recipe.outputs else "output.csv"

        # Ensure target directory exists
        try:
            out_p = Path(out_path)
            if not out_p.parent.exists():
                out_p.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            out_p = Path(out_path)

        snapshot_info = None
        try:
            # Create a snapshot of the source for audit/rollback purposes
            try:
                src_p = Path(src).resolve()
                if src_p.exists():
                    snaps_dir = Path("data") / "history" / "snapshots"
                    snaps_dir.mkdir(parents=True, exist_ok=True)
                    # compute a short hash of the source for uniqueness
                    h = hashlib.sha256()
                    with src_p.open("rb") as fh:
                        for chunk in iter(lambda: fh.read(8192), b""):
                            h.update(chunk)
                    short = h.hexdigest()[:10]
                    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
                    snap_name = f"{src_p.stem}_snapshot_{ts}_{short}{src_p.suffix}"
                    snap_path = snaps_dir / snap_name
                    shutil.copyfile(str(src_p), str(snap_path))
                    snapshot_info = {"path": str(snap_path), "created_at": ts, "hash": short}
            except Exception:
                snapshot_info = None

            # Write to a temp file in the destination directory then atomically replace
            tmp = None
            try:
                tmp_f = tempfile.NamedTemporaryFile(delete=False, dir=str(out_p.parent) if out_p.parent.exists() else None, prefix=f".tmp_{out_p.name}_", suffix=out_p.suffix)
                tmp = Path(tmp_f.name)
                tmp_f.close()
                _write_csv(df, str(tmp))
                # atomic replace
                try:
                    os.replace(str(tmp), str(out_p))
                except Exception:
                    # fallback to copy then unlink
                    shutil.copyfile(str(tmp), str(out_p))
                    try:
                        tmp.unlink()
                    except Exception:
                        pass
            except Exception:
                # ensure we don't leave a dangling tmp
                if tmp and tmp.exists():
                    try:
                        tmp.unlink()
                    except Exception:
                        pass
                raise

            result = {"written": str(out_p), "diagnostics": diagnostics}
            if snapshot_info:
                result["snapshot"] = snapshot_info
            return result
        except Exception:
            # If any file operations failed, surface write failure but do not crash silently
            raise

    def preview_recipe(self, recipe, n=None):
        """Run recipe transforms in-memory and return first `n` rows before and after as dicts.

        If `n` is None or <= 0, the full dataset is returned (use with care for large files).
        """
        if not recipe.sources:
            raise ValueError("Recipe must contain at least one source")
        src = recipe.sources[0]["path"]
        df = _read_table(src)
        wide_df = None
        try:
            if pl is not None and {"unit_kind", "text"}.issubset(set(df.columns if hasattr(df, 'columns') else [])):
                rows_all = self._reconstruct_table_from_df(df, offset=0, limit=None)
                if rows_all:
                    try:
                        wide_df = pl.from_dicts(rows_all)
                    except Exception:
                        import pandas as _pd
                        wide_df = _pd.DataFrame(rows_all)
        except Exception:
            wide_df = None

        # choose df to present/operate on: prefer reconstructed wide table when available
        df_for_ops = wide_df if wide_df is not None else df
        before = _df_head_records(df_for_ops, n)
        # strip metadata keys from preview rows
        def _strip_meta_rows(rows):
            try:
                return [ {k:v for k,v in (r or {}).items() if k not in self.META_KEYS and not (isinstance(k, str) and k.startswith('_'))} for r in (rows or []) ]
            except Exception:
                return rows
        before = _strip_meta_rows(before)
        df_after = df_for_ops
        warnings = []
        for step in recipe.cleaning_steps:
            col = getattr(step, "column", None)
            if col and col not in (df_after.columns if _is_polars_df(df_after) else list(df_after.columns)):
                warnings.append({"step": step.action, "column": col, "reason": "column_missing"})
                continue

            if step.action == "drop_column" and col:
                df_after = _drop_column(df_after, col)
            elif step.action == "impute" and col:
                params = step.params or {}
                res = _impute_missing(df_after, col, params)
                info = None
                if isinstance(res, tuple):
                    df_after = res[0]
                    info = res[1]
                else:
                    df_after = res
                if info:
                    # add human-readable warning for preview
                    try:
                        method = info.get('method') or info.get('strategy') or ''
                        rows_changed = int(info.get('rows_changed') or 0)
                        colname = info.get('column')
                        source = info.get('source')
                        group_by = info.get('group_by')
                        msg = f"Imputed {colname}: {rows_changed} rows filled"
                        details = []
                        if method:
                            details.append(method)
                        if source:
                            details.append('from ' + (source if isinstance(source, str) else json.dumps(source)))
                        if group_by:
                            details.append('grouped by ' + group_by)
                        if details:
                            msg = msg + ' (' + ', '.join(details) + ')'
                        warnings.append({"step": "impute", "column": colname, "message": msg, "rows_changed": rows_changed, "sample_positions": info.get('sample_positions', [])})
                    except Exception:
                        warnings.append({"step": "impute", "column": col, "message": "Imputation applied", "rows_changed": 0})
            elif step.action == "normalize" and col:
                try:
                    case = (step.params or {}).get("case", "lower")
                    df_after = _string_transform_column(df_after, col, case=case)
                except Exception:
                    pass
            elif step.action == "deduplicate":
                subset = [col] if col else None
                df_after = _unique_df(df_after, subset=subset)
            elif step.action == "map" and col:
                mapping = (step.params or {}).get("mapping") or {}
                if mapping:
                    df_after = _map_values(df_after, col, mapping)
            elif step.action == "regex_replace" and col:
                pat = (step.params or {}).get("pattern")
                repl = (step.params or {}).get("replace")
                if pat is not None:
                    flags = (step.params or {}).get("flags")
                    df_after = _regex_replace(df_after, col, pat, repl or "", flags=flags)
            elif step.action == "bucketize" and col:
                buckets = (step.params or {}).get("buckets", [])
                if buckets:
                    df_after = _bucketize(df_after, col, buckets)
            elif step.action == "replace" and col:
                old = (step.params or {}).get("old")
                new = (step.params or {}).get("new")
                if old is not None:
                    df_after = _replace_values(df_after, col, old, new)
            elif step.action == "remove_by_type" and col:
                params = step.params or {}
                res = _remove_by_type(df_after, col, params)
                info = None
                if isinstance(res, tuple):
                    df_after = res[0]
                    info = res[1]
                else:
                    df_after = res
                if info:
                    # add human-readable warning for preview
                    try:
                        rows_changed = int(info.get('rows_changed') or 0)
                        colname = info.get('column')
                        ttype = info.get('target_type')
                        msg = f"Removed {ttype} entries from {colname}: {rows_changed} rows changed"
                        warnings.append({"step": "remove_by_type", "column": colname, "message": msg, "rows_changed": rows_changed, "sample_positions": info.get('sample_positions', [])})
                    except Exception:
                        warnings.append({"step": "remove_by_type", "column": col, "message": "Removed values by type", "rows_changed": 0})
            elif step.action == "rename" and col:
                new_name = (step.params or {}).get("new_name")
                if new_name:
                    df_after = _rename_column(df_after, col, new_name)
            elif step.action == "fuzzy_dedupe":
                try:
                    params = step.params or {}
                    subset = params.get("subset")
                    threshold = float(params.get("threshold", 0.85))
                    method = params.get("method", 'difflib')
                    res = _fuzzy_dedupe(df_after, subset=subset, threshold=threshold, method=method)
                    if isinstance(res, tuple):
                        df_after = res[0]
                        info = res[1]
                        if info:
                            warnings.append({"step": "fuzzy_dedupe", "message": f"Removed {info.get('rows_removed',0)} rows via fuzzy dedupe", "details": info})
                    else:
                        df_after = res
                except Exception:
                    pass
            elif step.action == "cast" and col:
                params = step.params or {}
                to_type = params.get("to_type") or params.get("type")
                fmt = params.get("format")
                res = _cast_column(df_after, col, to_type, fmt)
                info = None
                if isinstance(res, tuple):
                    df_after = res[0]
                    info = res[1]
                else:
                    df_after = res
                if info:
                    warnings.append({"step": "cast", "column": info.get('column'), "message": f"Cast to {info.get('to_type')}", "rows_changed": info.get('rows_changed')})
            elif step.action == "move_by_type" or step.action == "swap_by_types":
                params = step.params or {}
                if step.action == "move_by_type":
                    moves = [params]
                else:
                    moves = params.get("moves") or []
                res = _swap_by_types(df_after, moves, replacement=params.get("replacement", ""))
                info = None
                if isinstance(res, tuple):
                    df_after = res[0]
                    info = res[1]
                else:
                    df_after = res
                if info:
                    warnings.append({"step": step.action, "message": f"Moved/swapped values: {info.get('rows_changed', 0)} rows changed", "details": info})
            elif step.action == "conditional" and col:
                params = step.params or {}
                val = params.get("value")
                cond = params.get("condition")
                if cond is not None:
                    df_after = _conditional_transform(df_after, col, val, cond)
            elif step.action == "join":
                params = step.params or {}
                left_path = params.get("left")
                right_path = params.get("right")
                keys = params.get("keys") or []
                fuzzy = params.get("fuzzy", False)
                if right_path and keys:
                    try:
                        right_df = _read_table(right_path)
                        join_key = keys[0]
                        if not fuzzy:
                            if _is_polars_df(df_after) and _is_polars_df(right_df):
                                df_after = df_after.join(right_df, on=join_key, how="left")
                            else:
                                import pandas as _pd
                                left_pd = df_after.to_pandas() if _is_polars_df(df_after) else df_after
                                right_pd = right_df.to_pandas() if _is_polars_df(right_df) else right_df
                                df_after = left_pd.merge(right_pd, left_on=join_key, right_on=join_key, how="left")
                        else:
                            if _is_polars_df(right_df):
                                try:
                                    right_keys = [str(x) for x in right_df.select(pl.col(join_key)).to_series().to_list()]
                                except Exception:
                                    right_keys = [str(x) for x in right_df[join_key].to_list()] if join_key in right_df.columns else []
                            else:
                                right_keys = [str(x) for x in right_df[join_key].astype(str).unique().tolist()]

                            cutoff = float(params.get("threshold", 0.8))
                            algorithm = (params.get("algorithm") or params.get("method") or "difflib").lower()

                            find_best = None
                            if algorithm == 'minhash':
                                try:
                                    from datasketch import MinHash, MinHashLSH
                                    num_perm = int(params.get('num_perm', 128))
                                    lsh = MinHashLSH(threshold=cutoff, num_perm=num_perm)
                                    for idx, rk in enumerate(right_keys):
                                        mh = MinHash(num_perm=num_perm)
                                        for sh in str(rk).split():
                                            mh.update(sh.encode('utf8'))
                                        lsh.insert(str(idx), mh)

                                    def _find_best_minhash(v):
                                        if v is None:
                                            return None
                                        mh = MinHash(num_perm=num_perm)
                                        for sh in str(v).split():
                                            mh.update(sh.encode('utf8'))
                                        res = lsh.query(mh)
                                        if res:
                                            try:
                                                return right_keys[int(res[0])]
                                            except Exception:
                                                return right_keys[0] if right_keys else None
                                        return None

                                    find_best = _find_best_minhash
                                except Exception:
                                    find_best = None

                            if find_best is None:
                                def find_best(v):
                                    if v is None:
                                        return None
                                    s = str(v)
                                    matches = difflib.get_close_matches(s, right_keys, n=1, cutoff=cutoff)
                                    return matches[0] if matches else None

                            if _is_polars_df(df_after):
                                try:
                                    mapped = df_after.select(pl.col(join_key)).to_series().to_list()
                                    mapped_vals = [find_best(x) for x in mapped]
                                    df_after = df_after.with_columns(pl.Series("__fuzzy_key__", mapped_vals))
                                    right_pref = right_df.rename({c: f"right__{c}" for c in right_df.columns})
                                    df_after = df_after.join(right_pref, left_on="__fuzzy_key__", right_on=f"right__{join_key}", how="left")
                                    try:
                                        df_after = df_after.drop("__fuzzy_key__")
                                    except Exception:
                                        pass
                                except Exception:
                                    pass
                            else:
                                import pandas as _pd
                                left_pd = df_after.to_pandas() if _is_polars_df(df_after) else df_after.copy()
                                right_pd = right_df.to_pandas() if _is_polars_df(right_df) else right_df.copy()
                                left_pd["__fuzzy_key__"] = left_pd[join_key].apply(find_best)
                                right_pd = right_pd.rename(columns={join_key: "__right_key__"})
                                df_after = left_pd.merge(right_pd, left_on="__fuzzy_key__", right_on="__right_key__", how="left")
                    except Exception:
                        pass
        after = _df_head_records(df_after, n)
        return {"before": before, "after": after, "warnings": warnings}
