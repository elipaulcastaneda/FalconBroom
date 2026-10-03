Write-Output "Process info for PID 7260";
Get-Process -Id 7260 -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,Path,StartTime | Format-List;
$win = Get-CimInstance Win32_Process -Filter "ProcessId = 7260" -ErrorAction SilentlyContinue;
if ($win) { $win | Select-Object ProcessId,CommandLine | Format-List } else { Write-Output "No CIM entry for PID 7260" }
