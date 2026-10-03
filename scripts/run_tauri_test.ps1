$log='src-tauri\tauri_run.log'
if(Test-Path $log){Remove-Item $log -Force}
$proc = Start-Process -FilePath '.\src-tauri\target\release\falconbroom-tauri.exe' -PassThru
Add-Content $log ("Started falconbroom-tauri.exe pid=$($proc.Id) at $(Get-Date -Format o)")
for($i=0;$i -lt 12;$i++){
  Add-Content $log ("--- iter $i at $(Get-Date -Format o) ---")
  try{
    Add-Content $log (Get-NetTCPConnection -OwningProcess $proc.Id | Out-String)
  } catch {
    Add-Content $log "Get-NetTCPConnection failed"
  }
  Start-Sleep -Seconds 1
}
try{ Stop-Process -Id $proc.Id -Force } catch {}
Add-Content $log ("Stopped")
Get-Content $log -Encoding UTF8
