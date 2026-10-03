try {
  $r = Invoke-WebRequest -Uri http://127.0.0.1:3009/health -UseBasicParsing -TimeoutSec 5
  Write-Output 'HTTP OK'
  $r.StatusCode
} catch {
  Write-Output ('ERR: ' + $_.Exception.Message)
}