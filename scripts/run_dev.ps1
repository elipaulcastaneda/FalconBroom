#!/usr/bin/env pwsh
# Start the backend in development mode (reload, dev-only features) on port 3009
$ErrorActionPreference = 'Stop'
if (Test-Path .\.venv\Scripts\Activate.ps1) {
    & .\.venv\Scripts\Activate.ps1
}
$env:PORT = '3009'
$env:ENV = 'development'
$env:DEV_RELOAD = 'true'
Write-Output "Starting backend in DEV mode on http://127.0.0.1:$env:PORT (reload=$env:DEV_RELOAD)"
python -m uvicorn fbroom.main:app --host 127.0.0.1 --port $env:PORT --reload --log-level debug
