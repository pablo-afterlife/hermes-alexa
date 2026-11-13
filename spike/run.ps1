# Phase 0 spike on a fresh Windows machine: sets up Python 3.13 + aioamazondevices
# with uv (in bridge\.venv), then runs the login + speak test.
#
#   powershell -ExecutionPolicy Bypass -File spike\run.ps1
#
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv not found - installing it (https://astral.sh/uv)..."
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

$py = Join-Path $root "bridge\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    uv venv --python 3.13 bridge\.venv
    uv pip install --python $py "aioamazondevices==17.2.11"
}

& $py -I spike\login_and_speak.py
