$files = git ls-files -- 'data/demo/*'
if (-not (Test-Path -Path samples/demo)) { New-Item -ItemType Directory -Path samples/demo -Force | Out-Null }
foreach ($f in $files) {
    $leaf = Split-Path $f -Leaf
    git mv $f (Join-Path 'samples/demo' $leaf)
}
git status --porcelain
git commit -m 'Move demo data to samples/demo'
