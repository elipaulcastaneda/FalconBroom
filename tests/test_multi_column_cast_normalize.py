import re
import json
import pytest
import os
from pathlib import Path
try:
    import polars as pl
except Exception:
    pl = None

from fbroom.recipe_schema import Recipe, CleaningStep
from fbroom.engine import Cleaner


@pytest.mark.skipif(pl is None, reason="polars not available")
def test_multi_column_cast_and_normalize(tmp_path):
    # prepare a small CSV
    csv_path = tmp_path / "multi_dates.csv"
    rows = [
        {"order_id": "O1", "product": "Widget", "order_date": "09/10/2025", "delivery_date": "2025-09-17"},
        {"order_id": "O2", "product": "GADGET", "order_date": "2025-12-06", "delivery_date": "12/14/2025"},
        {"order_id": "O3", "product": "Thingamajig", "order_date": "11-Sep-2025", "delivery_date": "09/23/2025"},
    ]
    # write CSV
    hdr = list(rows[0].keys())
    with csv_path.open("w", encoding="utf-8") as fh:
        fh.write(",".join(hdr) + "\n")
        for r in rows:
            fh.write(",".join([str(r[h]) for h in hdr]) + "\n")

    # build recipe with multi-column cast and normalize
    recipe = Recipe(
        sources=[{"path": str(csv_path)}],
        cleaning_steps=[
            CleaningStep(action="cast", column=["order_date", "delivery_date"], params={"to_type": "datetime", "format": "%Y-%m-%d"}),
            CleaningStep(action="normalize", column=["product"], params={"case": "lower"}),
        ],
        outputs=[{"path": str(tmp_path / "out.csv")}],
    )

    cleaner = Cleaner()
    preview = cleaner.preview_recipe(recipe, n=10)
    after = preview.get("after")
    assert after and len(after) == 3

    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    for row in after:
        assert date_re.match(str(row.get("order_date", ""))), f"order_date not ISO: {row.get('order_date')}"
        assert date_re.match(str(row.get("delivery_date", ""))), f"delivery_date not ISO: {row.get('delivery_date')}"
        # product should be lowercased
        assert str(row.get("product", "")).islower()
