<#
.SYNOPSIS
  Auto-start the JARVIS HUD. Used by VS Code (folder open) and Windows startup.
  Usage: auto_start.ps1 [run|on|off|status]
    run    (default) launch the HUD unless auto-start is disabled
    on     enable auto-start
    off    disable auto-start
    status show current auto-start setting
#>
$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$stateFile = Join-Path $root "data\auto_start.json"

function Get-State {
  if (Test-Path -LiteralPath $stateFile) {
    try {
      return [bool]((Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json).enabled)
    }
    catch { }
  }
  return $true
}

function Set-State([bool]$enabled) {
  $dir = Split-Path -Parent $stateFile
  if (-not (Test-Path -LiteralPath $dir)) {
    New-Item -ItemType Directory -Path $dir | Out-Null
  }
  @{ enabled = $enabled } | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
}

$action = if ($args.Count -gt 0) { $args[0] } else { "run" }

switch ($action.ToLower()) {
  "on" {
    Set-State $true
    Write-Host "JARVIS auto-start: ON"
  }
  "off" {
    Set-State $false
    Write-Host "JARVIS auto-start: OFF"
  }
  "status" {
    $on = Get-State
    Write-Host "JARVIS auto-start: $(if ($on) { 'ON' } else { 'OFF' })"
  }
  default {
    if (-not (Get-State)) {
      exit 0
    }
    $py = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $py)) {
      $py = "python"
    }
    Start-Process -FilePath $py -ArgumentList "-m", "app.gui" -WorkingDirectory $root -WindowStyle Hidden
  }
}