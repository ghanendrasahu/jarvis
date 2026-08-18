<#
.SYNOPSIS
  Run JARVIS - the single launcher.
  Usage: .\scripts\run_jarvis.ps1 [-Api]
    (default)  launch the desktop HUD (embedded web deck included, no browser)
    -Api       run the standalone API server on http://0.0.0.0:8000 (LAN/phone access)
#>
param(
  [switch]$Api
)
$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

$py = "python"
$venvPy = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (Test-Path $venvPy) { $py = $venvPy }

if ($Api) {
  & $py -m uvicorn app.main:app --host 0.0.0.0 --port 8000
  exit $LASTEXITCODE
}

& $py -m app.gui