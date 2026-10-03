$log='src-tauri\tauri_run_full.log'
if(Test-Path $log){Remove-Item $log -Force}
Add-Content $log ("Starting packaged Tauri test at $(Get-Date -Format o)")
# If a process owns port 3009, record and stop it
try{
  $conn = Get-NetTCPConnection -LocalPort 3009 -ErrorAction SilentlyContinue
  if($conn){
    $pids = $conn | Select-Object -ExpandProperty OwningProcess -Unique
    foreach($pid in $pids){
      Add-Content $log ("Existing owner of 3009: PID=$pid")
      try{ $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue; if($proc){ Add-Content $log ("Process $($proc.Id) $($proc.ProcessName) found; stopping") ; Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue } } catch {}
    }
  } else { Add-Content $log "No existing owner of 3009" }
} catch { Add-Content $log ("Get-NetTCPConnection check failed: $_") }

# Launch packaged exe
$proc = Start-Process -FilePath '.\src-tauri\target\release\falconbroom-tauri.exe' -PassThru
Add-Content $log ("Started falconbroom-tauri.exe pid=$($proc.Id) at $(Get-Date -Format o)")

# Poll for up to 20s to see if a process binds 3009 and whether uvicorn/fbroom.main exists
for($i=0;$i -lt 20;$i++){
  Add-Content $log ("--- iter $i at $(Get-Date -Format o) ---")
  try{ Add-Content $log (Get-NetTCPConnection -LocalPort 3009 | Out-String) } catch { Add-Content $log "Get-NetTCPConnection -LocalPort 3009 failed" }
  try{ Add-Content $log (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*uvicorn*' -or $_.CommandLine -like '*fbroom.main*' } | Select-Object ProcessId,CommandLine | Out-String) } catch { Add-Content $log "process commandline search failed" }
  Start-Sleep -Seconds 1
}

# Capture processes related to Tauri/webview
try{ Add-Content $log (Get-Process | Where-Object { $_.ProcessName -match 'edge|msedge|WebView|webview' } | Select-Object Id,ProcessName | Out-String) } catch {}

# Stop the packaged app and record
try{ Stop-Process -Id $proc.Id -Force } catch {}
Add-Content $log ("Stopped packaged Tauri exe")
Get-Content $log -Encoding UTF8
