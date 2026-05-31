# FalconBroom

## Prototype scaffold

This repository now contains a minimal Python prototype for data profiling and cleaning.

- Backend API: `fbroom/main.py` (FastAPI)
- Core engine: `fbroom/engine.py`
- Recipe models: `fbroom/recipe_schema.py`
- CLI: `fbroom/cli.py`
- Design notes: `design.md`
- Dependencies: `requirements.txt`

Development notes for Tauri:

- The prototype backend listens on a HTTP port. For Tauri development use a dedicated dev port (3005) to avoid conflicts with other apps. The backend can be started with:

```bash
python -m uvicorn fbroom.main:app --reload --port 3005
```

- The Tauri frontend should be configured during dev to call `http://127.0.0.1:3005` for API requests.

Next steps: scaffold a small Tauri frontend that calls the `/profile`, `/suggest`, and `/apply` endpoints, and add dataset connectors (S3/DB) as needed.

Demo and file picking
- A small demo dataset is available at `data/demo/customers.csv`.
- A sample recipe is at `samples/demo_recipe.json` which runs imputations and normalization and writes to `data/output/customers_cleaned.csv`.
- The frontend now uploads files directly from the Source tab. Local exports of Google Workspace files work when you upload the exported `.docx`, `.xlsx`, or `.pptx` file.

Google Drive authenticated fetches
- To fetch Google Docs, Sheets, and Slides shortcuts directly from Drive, set `FALCONBROOM_GOOGLE_DRIVE_ACCESS_TOKEN` to a valid OAuth access token.
- The backend will export Google Workspace files through the Drive API when a shortcut JSON contains a Drive URL or file ID.
- If no token is configured, Google shortcut uploads still persist as metadata, and local exports continue to work.

Source connector support
- The API accepts local files, `file://` URIs, `http://` and `https://` downloads, `s3://bucket/key` objects, and warehouse-style URIs for SQLite or DuckDB.
- Warehouse URIs materialize query results to a cached CSV before profiling or previewing them, for example `sqlite:///data/demo/sample.db?table=people` or `duckdb:///data/demo/sample.duckdb?query=SELECT%20*%20FROM%20people`.
- Use `POST /resolve-source` to inspect how a source was resolved and where the materialized file lives.

Example quick test:
1. Start backend:

```bash
python -m uvicorn fbroom.main:app --reload --port 3008
```

2. Start frontend (in another terminal):

```bash
cd frontend
npm install
npm run dev
```

3. Open the Vite UI at `http://127.0.0.1:5173` (or run the Tauri app which points to that dev path). Use the file picker or enter `data/demo/customers.csv`, click `Suggest` to populate the recipe editor, then `Preview` and `Apply`.

Running the packaged Tauri app (native)
--------------------------------------

Prerequisites:
- Rust toolchain (`rustup` + `cargo`) installed
- Node.js + npm installed
- Python 3.11 recommended (install from https://python.org). Newer Python versions such as 3.14 may not yet have prebuilt wheels for some native dependencies used by this project (e.g., `duckdb`, `polars`). The build and packaging scripts assume Python 3.11 or a compatible system Python is available in PATH.

Note: if you already have Python 3.11 installed you can create a venv and install dependencies:

Windows (PowerShell):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

macOS / Linux:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

To run the Tauri app in dev mode (it will spawn the Python backend automatically):

```powershell
# from repo root
cd frontend
npm install
npm run build   # build the frontend assets

# run the Tauri app from the correct project folder
cd ..\src-tauri
cargo tauri dev
```

Or from `frontend`, use the helper script:

```powershell
npm run tauri:dev
```

Notes:
 - The Tauri Rust binary spawns the Python backend using the bundled venv if present, or falls back to a system Python: it runs `python -m uvicorn fbroom.main:app --port 3008` in the bundled `python_app` working directory.

Packaging a self-contained installer
----------------------------------

The repository includes helper scripts that prepare a bundled Python virtual environment and copy the Python app into `src-tauri` resources so the generated installer can run without a system Python.

Steps (recommended):

1. Prepare the bundled Python environment (creates `src-tauri/py` and `src-tauri/python_app`):

Windows (PowerShell):

```powershell
cd src-tauri/scripts
./prepare_python_env.ps1
```

macOS / Linux:

```bash
cd src-tauri/scripts
./prepare_python_env.sh
```

2. Build the Tauri installer (this will include the prepared `py` venv and `python_app` directory in the app bundle):

Windows:

```powershell
cd scripts
./package_windows.ps1
```

macOS / Linux:

```bash
cd scripts
./package_unix.sh
```

3. The built artifacts and installers appear under `src-tauri/target` (platform-specific subfolders).

Notes and caveats:
 - The packaging scripts create a virtualenv and install Python dependencies into it. The resulting installer includes that venv and the `fbroom` Python package directory so the app can run without a system Python.
 - This approach increases installer size (bundling a Python venv). Test installers on target platforms to ensure paths and file permissions are correct.
 - For maximum portability you can consider shipping a small native runtime (reimplementing the runtime in Rust) instead of bundling Python.



A tool to join data sources and automate data preparation. 
