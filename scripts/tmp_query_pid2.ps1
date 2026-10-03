param([int]$ppid)
$p = Get-CimInstance Win32_Process -Filter "ProcessId=$ppid"
if ($p) { $p | Select-Object ProcessId,Name,CommandLine | Format-List * } else { Write-Output "No process $ppid" }
