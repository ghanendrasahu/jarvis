$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

if (Test-Path ".venv\Scripts\Activate.ps1") {
  .\.venv\Scripts\Activate.ps1
}

python -m app.gui_qt
