$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
  python -m venv .venv
}

.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
  throw "Failed to upgrade pip."
}
pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
  throw "Failed to install dependencies from requirements.txt."
}

Write-Host "Bootstrap complete."
Write-Host "If microphone support fails on Windows, install PyAudio with:"
Write-Host "  pip install pipwin"
Write-Host "  pipwin install pyaudio"
