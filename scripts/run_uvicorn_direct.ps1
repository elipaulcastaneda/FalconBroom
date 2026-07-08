param(
    [string]$Host = '127.0.0.1',
    [int]$Port = 3009,
    [switch]$LifespanOff
)

# Locate virtualenv python if present
$venvPython = Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
    $python = $venvPython
} else {
    $python = 'python'
}

$lifespanArg = if ($LifespanOff.IsPresent -or $env:LIFESPAN_OFF -eq '1' -or $env:LIFESPAN_OFF -eq 'true') { '--lifespan off' } else { '' }

Write-Host "Starting uvicorn on $Host:$Port (lifespan: $(if ($lifespanArg) { 'off' } else { 'on' }))"
& $python -m uvicorn fbroom.main:app --host $Host --port $Port --log-level debug $lifespanArg
