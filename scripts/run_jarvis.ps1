<#
.SYNOPSIS
  Run JARVIS - the single launcher.
  Usage: .\scripts\run_jarvis.ps1 [-Cli]
    (default)  launch the desktop HUD
    -Cli       run the interactive text CLI (voice diagnostics: /voice devices, /voice outputs)
#>
param(
  [switch]$Cli
)
$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

$py = "python"
$venvPy = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (Test-Path $venvPy) { $py = $venvPy }

if ($Cli) {
  & $py -m app.cli
  exit $LASTEXITCODE
}

& $py -m app.gui