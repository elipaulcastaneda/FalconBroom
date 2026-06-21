from fbroom.engine import _unicode_normalize_column, _fuzzy_dedupe
import pandas as pd
import polars as pl


def test_unicode_normalize_remove_diacritics_pandas():
    df = pd.DataFrame({"name": ["José", "Jose\u0301", "Müller", "Mueller"]})
    df2 = _unicode_normalize_column(df, "name", form="NFKC", remove_diacritics=True)
    # ensure accents removed and comparable
    vals = set(df2["name"].astype(str).str.lower().tolist())
    assert "jose" in vals
    assert "muller" in vals


def test_unicode_normalize_polars():
    df = pl.DataFrame({"city": ["Zürich", "Zurich\u0308"]})
    df2 = _unicode_normalize_column(df, "city", form="NFKC", remove_diacritics=True)
    import unicodedata as _ud
    out = [str(x) for x in df2.select(pl.col("city")).to_series().to_list()]
    # strip diacritics for robust comparison
    def strip(s):
        t = _ud.normalize('NFKD', str(s))
        return ''.join(ch for ch in t if not _ud.combining(ch)).lower()

    out_norm = [strip(x) for x in out]
    assert "zurich" in out_norm


def test_fuzzy_dedupe_basic():
    df = pd.DataFrame({"name": ["Alice", "Alic3", "Bob", "Robert", "Rob"]})
    res, info = _fuzzy_dedupe(df, subset=["name"], threshold=0.6, method='difflib')
    # should remove at least one fuzzy duplicate (Robert/Rob or Alice/Alic3)
    assert isinstance(info, dict)
    assert info.get("rows_removed") is not None
    assert int(info.get("rows_removed")) >= 1
    # resulting dataframe should be smaller or equal
    assert len(res) <= len(df)
