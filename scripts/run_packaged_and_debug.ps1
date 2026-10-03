# Start the packaged Tauri exe, run the debug delete flow, tail backend logs, then stop the exe
param(
    [int] $HealthWaitSec = 5
)

$exe = Join-Path -Path (Get-Location) -ChildPath 'src-tauri\target\release\falconbroom-tauri.exe'
if (-not (Test-Path $exe)) {
    Write-Error "Packaged exe not found at $exe"
    exit 2
}

Write-Output "Starting packaged exe: $exe"
$proc = Start-Process -FilePath $exe -PassThru
Write-Output "Started pid=$($proc.Id)"
Start-Sleep -Seconds $HealthWaitSec

Write-Output "Running debug_delete_flow.py"
try {
    python .\scripts\debug_delete_flow.py
} catch {
    Write-Output "Error running debug_delete_flow.py: $_"
}

Write-Output "--- Backend log tail (last 200 lines) ---"
if (Test-Path run_uv_lifespan_off.log) {
    Get-Content run_uv_lifespan_off.log -Tail 200
} else {
    Write-Output 'Backend log not found'
}

Write-Output "Stopping packaged exe (pid=$($proc.Id))"
try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
Write-Output 'Done'
