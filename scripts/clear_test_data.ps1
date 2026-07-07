<#
PowerShell script to safely clear runtime `data/` contents.
It prompts for confirmation and lists the top-level subfolders it will delete.
#>

$root = Join-Path (Get-Location) 'data'
if (-not (Test-Path $root)) {
    Write-Host "No data/ directory found at $root"
    exit 0
}

Write-Host "This will delete the contents of:"
Get-ChildItem -Path $root -Directory | ForEach-Object { Write-Host " - $_" }

$confirmation = Read-Host "Type DELETE to permanently remove all files under data/ (this cannot be undone)"
if ($confirmation -ne 'DELETE') {
    Write-Host "Aborting. No files were removed."
    exit 0
}

# Delete everything under data/ except .gitignore
Get-ChildItem -Path $root -Force -Recurse | Where-Object { $_.Name -ne '.gitignore' } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "data/ contents removed (except any .gitignore files)."
