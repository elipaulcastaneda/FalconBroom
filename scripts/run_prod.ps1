#!/usr/bin/env pwsh
# Start the backend in production-like mode on port 3010
$ErrorActionPreference = 'Stop'
if (Test-Path .\.venv\Scripts\Activate.ps1) {
    & .\.venv\Scripts\Activate.ps1
}
$env:PORT = '3010'
$env:ENV = 'production'
$env:DEV_RELOAD = 'false'
Write-Output "Starting backend in PROD-like mode on http://127.0.0.1:$env:PORT"
python fbroom\main.py
