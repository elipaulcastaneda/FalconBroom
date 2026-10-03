$procs = Get-Process -Name python -ErrorAction SilentlyContinue
if (!$procs) { Write-Output "No python processes" ; exit 0 }
foreach ($p in $procs) {
  $id = $p.Id
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$id"
  $proc | Select-Object ProcessId,Name,CommandLine | Format-List *
}