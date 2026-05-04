$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

if (Test-Path ".venv\Scripts\Activate.ps1") {
  .\.venv\Scripts\Activate.ps1
}

$env:JARVIS_AUTO_START_VOICE = "false"
if ($args.Count -gt 0 -and $args[0].ToLower() -eq "voice") {
  $env:JARVIS_AUTO_START_VOICE = "true"
}

python -m app.gui
