import os
import tempfile
import shutil
from pathlib import Path

from fbroom.engine import Cleaner
from fbroom.recipe_schema import Recipe


def test_transactional_apply_creates_snapshot_and_writes_output(tmp_path):
    # prepare a small CSV source
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src_file = src_dir / "data.csv"
    src_content = "a,b\n1,foo\n2,bar\n"
    src_file.write_text(src_content, encoding="utf-8")

    # destination output inside tmp dir
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    out_file = out_dir / "out.csv"

    # build minimal recipe
    recipe = Recipe(sources=[{"path": str(src_file)}], cleaning_steps=[], outputs=[{"path": str(out_file)}])

    cleaner = Cleaner()

    # run apply
    result = cleaner.apply_recipe_from_spec(recipe)

    # check written path present and content matches source
    written = result.get("written")
    assert written is not None
    assert Path(written).exists()
    assert Path(written).read_text(encoding="utf-8") == src_content

    # snapshot should be created under data/history/snapshots and reference the source stem
    snaps_dir = Path("data") / "history" / "snapshots"
    assert snaps_dir.exists()
    snaps = list(snaps_dir.glob(f"{src_file.stem}_snapshot_*{src_file.suffix}"))
    assert len(snaps) >= 1

    # ensure no lingering tmp files in the output directory
    tmp_files = [p for p in out_dir.iterdir() if p.name.startswith(".tmp_")]
    assert len(tmp_files) == 0

    # cleanup artifacts created under data/history/snapshots
    for s in snaps:
        try:
            s.unlink()
        except Exception:
            pass
