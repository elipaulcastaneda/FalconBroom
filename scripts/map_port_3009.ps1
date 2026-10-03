Write-Output 'Mapping listeners for port 3009';
$connections = Get-NetTCPConnection -LocalPort 3009 -ErrorAction SilentlyContinue
if (-not $connections) { Write-Output 'NO_LISTENER_ON_3009'; exit 1 }
foreach ($c in $connections) { 
    $pid = $c.OwningProcess
    Write-Output "Local: $($c.LocalAddress):$($c.LocalPort) State: $($c.State) OwningProcess: $pid"
    $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
    if ($proc) { 
        Write-Output "Process: Id=$($proc.Id) Name=$($proc.ProcessName) Path=$($proc.Path)"
        $win = Get-CimInstance Win32_Process -Filter "ProcessId = $pid" -ErrorAction SilentlyContinue
        if ($win) { $win | Select-Object ProcessId, CommandLine | Format-List }
    } else {
        Write-Output 'Process not found for PID ' + $pid
    }
}
