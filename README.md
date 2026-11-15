# FalconBroom

License: proprietary beta license. See [LICENSE](LICENSE).

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

Prompt guidance
---------------

Short, clear phrases work best. When asking the parser to move or copy values between columns, include a clarifying type or quantifier when possible. Supported clarifying terms include (but are not limited to):

- quantity/selection words: `all`, `values`, `entries`, `items`, `rows`, `records`
- type hints: `numeric`, `number`, `digits`, `string`, `text`, `letters`, `alpha`, `alphabetic`, `alphanumeric`, `chars`, `characters`, `date`, `postal`, `zip`, `city`
- negation forms: `non-numeric`, `non-numerical`, `not numeric`

Examples:

- "Move all non-numerical values from postal_code to city, then drop col_6."
- "Move values from col_6 to postal_code, then drop col_6."
- "Move digits from zip_code to postal_code."

If your instruction is very terse (e.g., "Move A to B"), the parser may need more context; adding a clarifying token like `values` or a type hint reduces ambiguity.

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
python -m uvicorn fbroom.main:app --reload --port 3009
```

Alternative runner and helper scripts

If running via the `uvicorn` CLI fails to bind on your machine, use the programmatic entrypoint added for development (it respects the `PORT` environment variable). The repo also includes two helper PowerShell scripts under `scripts/`:

- `scripts/run_dev.ps1` — runs the backend in development mode (hot reload) on port `3009` (dev-only features).
- `scripts/run_prod.ps1` — runs the backend in production-like mode (programmatic runner) on port `3010`.

Usage (PowerShell):

```powershell
# Development (dev-only features, hot reload)
.\scripts\run_dev.ps1

# Production-like (no reload, programmatic runner)
.\scripts\run_prod.ps1
```

If the default dev port `3009` is already in use on your system, change the `PORT` env var in the script or pass a different port explicitly (for example `3010`).

Running the packaged Tauri app against a specific backend port

When launching the packaged native app you can point it at a backend running on a non-default port by setting the `FALCONBROOM_BACKEND_URL` environment variable before starting the native executable. Example (PowerShell):

```powershell
$env:FALCONBROOM_BACKEND_URL='http://127.0.0.1:3010'
$env:RUST_LOG='debug'
.\src-tauri\target\release\falconbroom-tauri.exe 2>&1 | Tee-Object tauri_run.log
```

This is useful when `3009` is unavailable or you prefer to run the backend on a different port for testing.

2. Start frontend (in another terminal):

```bash
cd frontend
npm install
npm run dev
```

3. Open the Vite UI at `http://127.0.0.1:5174` (or run the Tauri app which points to that dev path). Use the file picker or enter `data/demo/customers.csv`, click `Suggest` to populate the recipe editor, then `Preview` and `Apply`.

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
- The Windows desktop Settings tab provides individual optional remote-service choices. Existing behavior is preserved by default. Local Source and Joins operations continue to work when optional integrations are disabled; remote lookup or Drive-dependent operations report that the service is disabled. Supabase authentication remains online. The configured remote backend requires HTTPS and is blocked if any optional-service choice is disabled; restart to switch backends. Saving waits for coordinated desktop requests and optional-service calls, but cannot stop work already accepted by a remote server. Arbitrary HTTP lookup and audit-export destinations can contact other services independently; independently started workers, cloud-synced folders and other applications are outside these controls.
- With the local Windows desktop backend, Source and Joins offer optional password-protected AES-256 ZIP downloads; ordinary downloads remain unchanged. The archive requires a compatible tool such as 7-Zip, and its member name and size remain visible. Use a long randomly generated passphrase and keep it safe; there is no password recovery. A strong passphrase matters particularly because the interoperable ZIP encryption format uses a relatively inexpensive password derivation. Encrypted downloads do not protect extracted files, existing copies, processing temporary files, or remote-server data. This option is recommended for financial, healthcare and personal information after the app's safeguards are assessed; the beta still recommends synthetic or non-sensitive data.
- The packaged app starts the bundled backend on loopback with a per-launch session secret. Keep this backend private; the session secret is not a defense against all software running under the same OS user.
- Settings provides a separately confirmed local-workspace cleanup for all accounts on the device. It removes uploads, outputs, recipes, inspections, history and snapshots, and abandoned lock-protected temporary workspaces. Active workspaces, older untracked temporary files, consent records, downloaded exports, backups and files elsewhere are not removed.
- On Windows, desktop cleanup requests wait for participating sidecar HTTP requests and startup migration to finish; new requests wait while cleanup runs. Desktop consent export and deletion jobs normally run within their initiating request; independently started consent workers running this version also use the request gate. If coordination times out, cleanup does not start. This does not coordinate older workers, other external programs or other devices.
- Automated Supabase account and cloud-data deletion is coming soon and is not available in the beta desktop app. Users can email the contact in the draft privacy notice, and the maintainer will promptly process Supabase account and cloud-data deletion requests manually, as soon as reasonably possible, after verifying identity. Do not bundle a Supabase service-role key; a trusted server-side deletion service and an end-to-end release test are still required.
- Desktop sign-in credentials use the Windows credential store when available and otherwise remain in memory for the session. Updating may sign out existing users. A storage warning means previously saved credentials may need manual removal.
- Windows desktop uploads, normalized uploads, conversion metadata, recipes, history, snapshots, and outputs from the integrated apply, patch, join, rollback and parquet routes use chunked AES-GCM. A random master key is protected by Windows DPAPI for the current user profile. Back up your workspace before first starting an updated build: the sidecar migrates existing uploads, outputs, recipes, history and snapshots on startup, including the legacy `output_from_text.csv` in the app-data root. If plaintext and encrypted copies conflict, startup stops rather than replacing either copy. The unreleased `FBVAULT1` prototype format is not supported; retain a backup before trying this revision.
- Managed Source and Joins CSV outputs use a bounded in-memory buffer where possible; larger CSVs and other supported output formats use lock-protected temporary workspaces before vault encryption. Rollback, patch and Parquet outputs also use these workspaces. This reduces plaintext exposure but does not encrypt the workspace itself or guarantee erasure.
- The local desktop UI streams uploads without multipart disk spooling; conversion may still need a plaintext workspace file. Small encrypted tables can be read in memory, and ordinary downloads normally stream from an authenticated encrypted snapshot rather than creating an additional plaintext staging file. Range requests still use plaintext staging. New workspaces use protected Windows ACLs for the owner and SYSTEM and require a filesystem supporting Windows access controls; failures are reported rather than silently using an unprotected workspace. Older workspaces are not retroactively protected.
- New Windows desktop inspection records use DPAPI directly. Existing inspection records remain plaintext until the separately confirmed **Protect existing inspection records** action in Settings. Back up data first; migrations cannot erase old copies, filesystem remnants, backups, or exports. Interrupted migration can be retried.
- New local Windows desktop consent receipts, consent job metadata and managed consent-job exports use the desktop vault. Settings provides separately confirmed actions for older receipts and job records. Back up first and close older app versions and workers; migration stops on unreadable or conflicting records and can be retried. Consent history can be downloaded as ordinary plaintext JSON or a password-protected AES ZIP; extracted files are plaintext and ZIP filenames and sizes remain visible. Use a long random passphrase and keep it safe.
- New local desktop audit entries are encrypted, minimize identifying data and use a transactionally coordinated integrity chain. Verification detects modifications or truncation against the stored chain head, but cannot detect deletion or rollback of the entire store without an external trusted checkpoint. Audit failures are logged and do not fall back to plaintext. Older audit files remain plaintext until the separately confirmed **Protect legacy audit files** action in Settings. That action encrypts existing files without establishing their historical integrity; exported legacy log entries are labeled unverified. Close older app versions and workers and back up first. Configured audit exports and other application logs can still contain plaintext.
- Other output-producing paths, older consent and audit records, backups, and exports outside those managed paths may remain plaintext. Upload parsing and processing can create plaintext temporary files, and some operations write plaintext before sealing it. After a crash, startup cleans abandoned lock-protected workspaces; older untracked temporary files and other plaintext remnants may remain. Deletion is not secure erasure. DPAPI-bound data is not protected from software running as the same Windows user, and losing access to the Windows profile can make encrypted data unreadable; there is no recovery password. Use device encryption and avoid sensitive data; the in-app privacy notice is a draft and requires review before release.

Desktop release security checklist
----------------------------------

- Acquire a code-signing certificate and configure a protected signing process before publishing. The current workflow generates SHA-256 checksums but does **not** sign the installer. Verify the Authenticode signature and timestamp on the final installer, then generate and publish its checksum. A checksum from the same download location is not proof of publisher identity.
- Test install, launch, sign-in, restart, sign-out, workspace deletion, and uninstall on a clean Windows machine. Verify the bundled privacy notice and that desktop analytics does not load. Do not publish an untested build.
- Before release, finalize the privacy notice and have qualified counsel assess applicable privacy and breach-notification obligations. Account deletion through a trusted service remains a release blocker for the advertised workflow.
- Prepare an incident response process: identify an owner and contact, restrict and preserve relevant evidence, contain the issue and revoke affected credentials, investigate scope and affected data, document decisions, and assess notification obligations and deadlines with counsel. Verify that monitoring, contacts, and backup/recovery procedures actually work; documenting them alone does not establish compliance.

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

API cURL examples
------------------

Suggest buckets (GET):

```bash
curl -G 'http://127.0.0.1:3009/suggest-buckets' \
	--data-urlencode 'path=data/demo/customers.csv' \
	--data-urlencode 'col=age' \
	--data-urlencode 'strategy=quantile' \
	--data-urlencode 'n_buckets=4'
```

Write Parquet (POST):

```bash
curl -X POST 'http://127.0.0.1:3009/write-parquet' \
	-H 'Content-Type: application/json' \
	-d '{"path":"data/demo/customers.csv","out_path":"customers.parquet","compression":"snappy"}'
```

These examples appear in the API docs at `/docs` when the backend is running.

Explain recipe steps (GET):

```bash
curl -G 'http://127.0.0.1:3009/recipes/test_explain_abcdef/explain' \
	--data-urlencode 'sample_rows=5'
```

Response (excerpt):

```json
{
	"id": "test_explain_abcdef",
	"explanations": [
		{
			"step": {"action": "normalize", "column": "name", "params": {"case": "lower"}},
			"reason": "name appears to be a text column, so normalization is safe and deterministic.",
			"confidence": 0.75,
			"preview": {"column": "name", "before": ["Alice","Bob"], "after": ["alice","bob"]}
		}
	]
}
```
