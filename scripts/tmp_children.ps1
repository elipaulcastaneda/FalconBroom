param([int]$ppid)
$children = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $ppid }
if ($children) { $children | Select-Object ProcessId,Name,CommandLine | Format-List * } else { Write-Output 'No children' }