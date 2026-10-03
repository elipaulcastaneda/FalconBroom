try {
    $r = Invoke-RestMethod -Uri 'http://127.0.0.1:3009/health' -TimeoutSec 5 -ErrorAction Stop
    Write-Output 'STATUS: 200'
    $r | ConvertTo-Json -Compress
} catch {
    Write-Output 'ERROR: ' + $_.Exception.Message
    exit 2
}
