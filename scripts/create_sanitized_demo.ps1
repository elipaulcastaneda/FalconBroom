$files = @(
  'samples/sanitized/users/user_98861109.json',
  'samples/sanitized/users/user_09104ed4.json',
  'samples/sanitized/users/user_243fe315.json',
  'samples/sanitized/users/user_40622c1f.json',
  'samples/sanitized/users/user_713f66ed.json',
  'samples/sanitized/users/user_9cfc1395.json',
  'samples/sanitized/users/user_b7c7b1fb.json',
  'samples/sanitized/users/user_cd4d41a9.json',
  'samples/sanitized/invites/invite_308b9bdb01c549538c978cf62b9fb730.json',
  'samples/sanitized/sessions/session_061f2adbeb2240a38a1af524e6b5f37f.json',
  'samples/sanitized/sessions/session_3123d0498cc44ea382249a9ac4bce004.json',
  'samples/sanitized/sessions/session_840af445a0ed4aad97fba0d7fd3374ab.json',
  'samples/sanitized/sessions/session_e985b58f47ac410f8f7c281c987dfda9.json',
  'samples/sanitized/sessions/session_f804f285a10e4b6b82a075f8b264d370.json',
  'samples/sanitized/recipes/recipe_1781137674169_ae460201.json',
  'samples/sanitized/recipes/recipe_1781142104842_ae38e314.json',
  'samples/sanitized/recipes/recipe_1781676185085_7e7599b9.json',
  'samples/sanitized/recipes/recipe_1781719692535_779a7051.json',
  'samples/sanitized/recipes/recipe_1781726536157_24e0dabe.json',
  'samples/sanitized/recipes/recipe_1781940642018_ee136c75.json',
  'samples/sanitized/recipes/recipe_1782013502971_70e82c1f.json',
  'samples/sanitized/recipes/test_explain_029207.json',
  'samples/sanitized/recipes/test_run_1234.json',
  'samples/sanitized/uploads/data3300_airbnb_data_raw_nashville_03625475.csv',
  'samples/sanitized/uploads/patched_data3300_airbnb_data_raw_nashville_a13cfeeb_2006752_9a70_7c90f522.csv',
  'samples/sanitized/inspections/inspection_0ad1b38e.json',
  'samples/sanitized/inspections/inspection_20edab25.json',
  'samples/sanitized/queue/propagation/prop_retry_2da698581f654bfa8c9d621f8872352b.json',
  'samples/sanitized/queue/propagation/prop_retry_2f6bed077c634ee28f3de3cc45d3d992.json'
)

New-Item -ItemType Directory -Path samples/sanitized_demo -Force | Out-Null

foreach ($f in $files) {
  if (Test-Path $f) {
    $rel = $f -replace '^samples\\sanitized\\',''
    $dest = Join-Path 'samples/sanitized_demo' $rel
    $destDir = Split-Path $dest -Parent
    if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
    Copy-Item -Path $f -Destination $dest -Force
    Write-Host "COPIED: $f"
  } else {
    Write-Host "MISSING: $f"
  }
}

# Stage and commit
git add .gitignore samples/sanitized_demo
$commitMsg = 'Add curated sanitized demo subset and ignore bulk sanitized outputs'
if ((git commit -m $commitMsg) -ne $null) {
  # commit succeeded
  exit 0
} else {
  # try setting local git user and commit again
  git config user.email 'devnull@example.com'
  git config user.name 'Dev Bot'
  git commit -m $commitMsg
}
