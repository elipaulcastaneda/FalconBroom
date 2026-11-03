"""Build the FalconBroom backend into a single executable Tauri sidecar.

    python scripts/build_sidecar.py

Writes src-tauri/binaries/falconbroom-backend-<target-triple>[.exe].
Requires: pip install -r requirements.txt -r requirements-build.txt
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "src-tauri" / "binaries"
NAME = "falconbroom-backend"


def host_triple() -> str:
    out = subprocess.check_output(["rustc", "-vV"], text=True)
    return re.search(r"^host:\s*(\S+)", out, re.M).group(1)


def main() -> int:
    triple = os.environ.get("TAURI_TARGET_TRIPLE") or host_triple()
    ext = ".exe" if "windows" in triple else ""
    build_dir = ROOT / "build" / "sidecar"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile",
        "--name", NAME,
        "--distpath", str(build_dir / "dist"),
        "--workpath", str(build_dir / "work"),
        "--specpath", str(build_dir),
        "--paths", str(ROOT),
        "--collect-submodules", "uvicorn",
        "--collect-submodules", "fbroom",
        "--collect-submodules", "celery",
        "--collect-all", "polars",
        "--collect-all", "duckdb",
        "--add-data", f"{ROOT / 'fbroom' / 'data'}{os.pathsep}fbroom/data",
        "--hidden-import", "multipart",
        "--exclude-module", "tkinter",
        "--exclude-module", "pytest",
        # Present in some dev environments but never imported by FalconBroom; they bloat the sidecar by 100+ MB.
        *[arg for module in ("numba", "llvmlite", "IPython", "ipywidgets", "ipykernel", "jedi", "matplotlib",
                             "boto3", "botocore", "s3transfer", "notebook", "jupyter_client", "jupyter_core", "sympy",
                             "tensorflow", "torch", "bokeh", "plotly", "dask", "distributed", "pytest")
          for arg in ("--exclude-module", module)],
        str(ROOT / "fbroom" / "sidecar.py"),
    ]
    print(" ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / f"{NAME}-{triple}{ext}"
    shutil.copy2(build_dir / "dist" / f"{NAME}{ext}", target)
    print(f"Wrote {target} ({target.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
