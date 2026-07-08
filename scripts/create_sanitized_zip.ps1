$out = 'samples/sanitized_snapshot_2026-07-06.zip'
if (Test-Path 'samples/sanitized') {
    Remove-Item -Force $out -ErrorAction SilentlyContinue
    Compress-Archive -Path 'samples/sanitized' -DestinationPath $out -Force
    $f = Get-Item $out
    Write-Host $f.FullName
    Write-Host $f.Length
} else {
    Write-Error 'samples/sanitized not found'
}
