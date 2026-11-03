!macro NSIS_HOOK_PREINSTALL
  nsExec::Exec 'taskkill /F /T /IM falconbroom-tauri.exe'
  nsExec::Exec 'taskkill /F /T /IM falconbroom-backend.exe'
  Sleep 1500
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  nsExec::Exec 'taskkill /F /T /IM falconbroom-tauri.exe'
  nsExec::Exec 'taskkill /F /T /IM falconbroom-backend.exe'
  Sleep 1500
!macroend
