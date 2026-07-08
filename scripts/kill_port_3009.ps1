# Kill any process listening on TCP port 3009
$pids = @(Get-NetTCPConnection -LocalPort 3009 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
if ($pids.Count -gt 0) {
    foreach ($pid in $pids) {
        try {
            $proc = Get-Process -Id $pid -ErrorAction Stop
            Write-Host "Stopping PID $pid ($($proc.ProcessName))"
            Stop-Process -Id $pid -Force -ErrorAction Stop
            Write-Host "Stopped PID $pid"
        } catch {
            Write-Host ('Failed to stop PID ' + $pid + ': ' + $_.Exception.Message)
        }
    }
} else {
    Write-Host "No listeners on port 3009"
}

Start-Sleep -Milliseconds 300
$res = Get-NetTCPConnection -LocalPort 3009 -ErrorAction SilentlyContinue
if ($res) {
    $res | Select-Object LocalAddress,LocalPort,State,OwningProcess | Format-Table -AutoSize
} else {
    Write-Host "No listeners remain on port 3009"
}
