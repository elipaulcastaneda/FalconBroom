param(
    [ValidateSet('npm','tauri','cmd')]
    [string] $FrontendType = 'npm',
    [string] $FrontendCmd = "npm run dev",
    [string] $FrontendCwd = "",
    [int] $Port = 3009,
    [int] $MaxWaitSec = 60
)

Write-Output "Waiting up to $MaxWaitSec seconds for http://127.0.0.1:$Port/health to return 200..."
$sw = [Diagnostics.Stopwatch]::StartNew()
while ($sw.Elapsed.TotalSeconds -lt $MaxWaitSec) {
    try {
        $healthUrl = "http://127.0.0.1:$Port/health"
        try {
            $r = Invoke-WebRequest -Uri $healthUrl -Method GET -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
        } catch {
            $r = $null
        }
        if ($r -and $r.StatusCode -eq 200) {
            Write-Output "Health OK at $healthUrl. Starting frontend: $FrontendCmd (cwd: $FrontendCwd)"
            switch ($FrontendType) {
                'npm' {
                    if ([string]::IsNullOrWhiteSpace($FrontendCwd)) {
                        Start-Process -NoNewWindow -FilePath powershell -ArgumentList "-NoProfile","-Command",$FrontendCmd
                    } else {
                        Start-Process -NoNewWindow -FilePath powershell -ArgumentList "-NoProfile","-Command",$FrontendCmd -WorkingDirectory $FrontendCwd
                    }
                }
                'tauri' {
                    $exePath = Join-Path -Path 'src-tauri' -ChildPath 'target\release\falconbroom-tauri.exe'
                    if (Test-Path $exePath) {
                        # Ensure the packaged Tauri app uses the already-running backend
                        $env:FALCONBROOM_BACKEND_URL = "http://127.0.0.1:$Port"
                        Write-Output "Set FALCONBROOM_BACKEND_URL=$env:FALCONBROOM_BACKEND_URL"
                        Write-Output "Starting packaged Tauri exe: $exePath"
                        Start-Process -FilePath $exePath -WorkingDirectory (Split-Path $exePath)
                    } else {
                        Write-Output "Packaged exe not found; running 'cargo tauri dev' in src-tauri"
                        # Export env var so the dev tauri process also uses the external backend
                        $env:FALCONBROOM_BACKEND_URL = "http://127.0.0.1:$Port"
                        Write-Output "Set FALCONBROOM_BACKEND_URL=$env:FALCONBROOM_BACKEND_URL"
                        Start-Process -FilePath powershell -ArgumentList "-NoProfile","-Command","cargo tauri dev" -WorkingDirectory (Join-Path -Path (Get-Location) -ChildPath 'src-tauri')
                    }
                }
                'cmd' {
                    if ([string]::IsNullOrWhiteSpace($FrontendCwd)) {
                        Start-Process -NoNewWindow -FilePath powershell -ArgumentList "-NoProfile","-Command",$FrontendCmd
                    } else {
                        Start-Process -NoNewWindow -FilePath powershell -ArgumentList "-NoProfile","-Command",$FrontendCmd -WorkingDirectory $FrontendCwd
                    }
                }
            }
            exit 0
        }
    } catch {
        # ignore transient errors
    }
    Start-Sleep -Milliseconds 500
}
Write-Error "Timed out waiting for http://127.0.0.1:$Port/health after $MaxWaitSec seconds"
exit 2
