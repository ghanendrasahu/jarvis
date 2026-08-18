$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Push-Location $root

$py = "python"
if (Test-Path ".venv\Scripts\python.exe") { $py = (Join-Path $root ".venv\Scripts\python.exe") }

$log = Join-Path $root "data\web_ui.log"
$err = Join-Path $root "data\web_ui_err.log"
New-Item -ItemType Directory -Force -Path (Join-Path $root "data") | Out-Null

Start-Process -FilePath $py -ArgumentList @(
  "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"
) -RedirectStandardOutput $log -RedirectStandardError $err -WindowStyle Hidden

$ok = $false
for ($i = 0; $i -lt 20; $i++) {
  Start-Sleep -Seconds 1
  try {
    $null = Invoke-WebRequest -Uri "http://127.0.0.1:8000/v1/health" -UseBasicParsing -TimeoutSec 3
    $ok = $true
    break
  } catch {
    # not up yet
  }
}

if ($ok) {
  Start-Process "http://127.0.0.1:8000"
} else {
  Write-Host "JARVIS web UI failed to start. Check data\web_ui.log"
}

Pop-Location