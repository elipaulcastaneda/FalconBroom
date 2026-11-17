!macro NSIS_HOOK_PREINSTALL
  nsExec::Exec 'taskkill /F /T /IM falconbroom-tauri.exe'
  nsExec::Exec 'taskkill /F /T /IM falconbroom-backend.exe'
  Sleep 1500
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    IfSilent +3
    MessageBox MB_YESNO|MB_ICONEXCLAMATION "Deleting app data removes your encrypted files and Windows-protected keys. A recovery key alone does not back up your files. Back up the data and export a recovery file for every profile first. Close all FalconBroom versions and do not reopen them during uninstall.$\r$\n$\r$\nContinue with deletion?" IDYES +2
    Abort
  ${EndIf}
  nsis_tauri_utils::FindProcessCurrentUser "falconbroom-tauri.exe"
  Pop $0
  ${If} $0 = 0
    IfSilent +2
    MessageBox MB_OK|MB_ICONSTOP "Close all FalconBroom windows before uninstalling."
    SetErrorLevel 1
    Abort
  ${EndIf}
  nsis_tauri_utils::FindProcessCurrentUser "falconbroom-backend.exe"
  Pop $0
  ${If} $0 = 0
    IfSilent +2
    MessageBox MB_OK|MB_ICONSTOP "FalconBroom is still shutting down. Close all versions and workers, wait a moment, and retry."
    SetErrorLevel 1
    Abort
  ${EndIf}
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    SetShellVarContext current
    System::Call 'kernel32::GetCurrentProcessId() i .r2'
    nsExec::ExecToStack '"$INSTDIR\falconbroom-backend.exe" --verify-uninstall-recovery --data-dir "$APPDATA\${BUNDLEID}" --uninstaller-pid $2'
    Pop $0
    Pop $1
    ${If} $0 != 0
      IfSilent +2
      MessageBox MB_OK|MB_ICONSTOP "App data was not deleted. A current recovery export for every vault could not be verified, a vault key is missing, or app data is redirected or inaccessible. Export recovery for each profile, or preserve the data and investigate."
      SetErrorLevel 1
      Abort
    ${EndIf}
  ${EndIf}
!macroend
