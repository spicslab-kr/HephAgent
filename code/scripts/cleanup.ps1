# Remove everything that setup or a local run created, returning the tree to
# what is committed in git. Safe to run before `git add` / pushing.
# Run from the repository root:  powershell -ExecutionPolicy Bypass -File scripts\cleanup.ps1
$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$targets = @(".venv", ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist", ".sdlc")
foreach ($t in $targets) { if (Test-Path $t) { Remove-Item -Recurse -Force $t } }
Get-ChildItem -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem -Recurse -Directory -Filter "*.egg-info" | Remove-Item -Recurse -Force
Write-Host "Cleanup complete. Re-create the environment with scripts\setup.ps1"
