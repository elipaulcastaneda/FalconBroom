param(
    [int] $Iterations = 5,
    [int] $HealthWaitSec = 5,
    [int] $BetweenSec = 2,
    [string] $OutLog = "packaged_debug_loop.log"
)

$exe = Join-Path -Path (Get-Location) -ChildPath 'src-tauri\target\release\falconbroom-tauri.exe'
if (-not (Test-Path $exe)) {
    Write-Error "Packaged exe not found at $exe"
    exit 2
}

Write-Output "Starting packaged debug loop: Iterations=$Iterations -> app=$exe -> output=$OutLog"
"=== Run start: $(Get-Date) ===" | Out-File -FilePath $OutLog -Encoding utf8 -Append
for ($i=1; $i -le $Iterations; $i++) {
    ('--- Iteration {0} of {1}: {2} ---' -f $i, $Iterations, (Get-Date)) | Out-File -FilePath $OutLog -Encoding utf8 -Append

    # ensure backend health is OK before starting frontend
    $healthUrl = 'http://127.0.0.1:3009/health'
    $ok = $false
    for ($w=0; $w -lt 30; $w++) {
        try {
            $r = Invoke-WebRequest -Uri $healthUrl -Method GET -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
        } catch { $r = $null }
        if ($r -and $r.StatusCode -eq 200) { $ok = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ok) {
        "Health check failed before starting exe (iteration $i). Skipping." | Out-File -FilePath $OutLog -Encoding utf8 -Append
        continue
    }

    $proc = Start-Process -FilePath $exe -PassThru
    "Started packaged exe pid=$($proc.Id)" | Out-File -FilePath $OutLog -Encoding utf8 -Append
    Start-Sleep -Seconds $HealthWaitSec

    "Running debug_delete_flow.py" | Out-File -FilePath $OutLog -Encoding utf8 -Append
    try {
        $out = & python .\scripts\debug_delete_flow.py 2>&1
        $out | Out-File -FilePath $OutLog -Encoding utf8 -Append
    } catch {
        "Error running debug_delete_flow.py: $_" | Out-File -FilePath $OutLog -Encoding utf8 -Append
    }

    "--- Backend log excerpt (tail 100) ---" | Out-File -FilePath $OutLog -Encoding utf8 -Append
    if (Test-Path run_uv_lifespan_off.log) {
        Get-Content run_uv_lifespan_off.log -Tail 100 | Out-File -FilePath $OutLog -Encoding utf8 -Append
    } else {
        'Backend log not found' | Out-File -FilePath $OutLog -Encoding utf8 -Append
    }

    "Stopping packaged exe pid=$($proc.Id)" | Out-File -FilePath $OutLog -Encoding utf8 -Append
    try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}

    Start-Sleep -Seconds $BetweenSec
}

"=== Run end: $(Get-Date) ===" | Out-File -FilePath $OutLog -Encoding utf8 -Append
Write-Output "Loop complete; log at $OutLog"
