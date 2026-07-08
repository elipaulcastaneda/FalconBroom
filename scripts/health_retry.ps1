param([int]$tries=30)
for ($i=0; $i -lt $tries; $i++) {
    $r = curl.exe -sS http://127.0.0.1:3009/health
    if ($LASTEXITCODE -eq 0) {
        Write-Host 'OK:'
        Write-Host $r
        exit 0
    } else {
        Write-Host "attempt $i failed"
        Start-Sleep -Seconds 1
    }
}
Write-Host 'health check failed after attempts'
exit 1
