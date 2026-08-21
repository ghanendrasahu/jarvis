<#
.SYNOPSIS
  JARVIS unified launcher.
  Usage: .\jarvirun.ps1 <command> [options]

  Commands:
    run          (default) Launch the desktop HUD
    cli          Run the interactive text CLI
    setup        Create venv and install dependencies
    clean        Remove generated caches and browser cache
    autostart    Manage auto-start (on|off|status)
#>
param(
  [Parameter(Position=0)]
  [string]$Command = "run",

  [Parameter(Position=1)]
  [string]$Arg
)
$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }
Set-Location $root

function Get-Python {
  $venvPy = Join-Path $root ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPy) { return $venvPy }
  return "python"
}

switch ($Command.ToLower()) {

  "run" {
    $py = Get-Python
    & $py -m app.gui
    exit $LASTEXITCODE
  }

  "cli" {
    $py = Get-Python
    & $py -m app.cli
    exit $LASTEXITCODE
  }

  "setup" {
    if (-not (Test-Path -LiteralPath ".venv")) {
      python -m venv .venv
    }
    .\.venv\Scripts\Activate.ps1
    python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip." }
    pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Failed to install dependencies." }
    Write-Host "Setup complete."
    Write-Host "If microphone support fails on Windows, run:"
    Write-Host "  pip install pipwin && pipwin install pyaudio"
  }

  "clean" {
    $rootCaches = @(".pytest_cache", ".ruff_cache", ".mypy_cache")
    foreach ($dir in $rootCaches) {
      if (Test-Path -LiteralPath $dir) {
        Remove-Item -LiteralPath $dir -Recurse -Force
        Write-Host "Removed $dir"
      }
    }
    foreach ($base in @(".", "app")) {
      Get-ChildItem -LiteralPath $base -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch '\\\.venv\\' } |
        ForEach-Object {
          Remove-Item -LiteralPath $_.FullName -Recurse -Force
          Write-Host "Removed $($_.FullName)"
        }
    }
    $profile = "data\browser_profile"
    $cacheDirs = @(
      "Default\Cache", "Default\Code Cache", "Default\GPUCache",
      "Default\DawnGraphiteCache", "Default\DawnWebGPUCache",
      "Default\GrShaderCache", "Default\ShaderCache",
      "Default\Service Worker\CacheStorage", "Default\Service Worker\ScriptCache",
      "GraphiteDawnCache", "GPUPersistentCache", "GrShaderCache",
      "ShaderCache", "extensions_crx_cache"
    )
    foreach ($rel in $cacheDirs) {
      $target = Join-Path $profile $rel
      if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "Pruned $rel"
      }
    }
    Write-Host "Clean complete."
  }

  "autostart" {
    $stateFile = Join-Path $root "data\auto_start.json"

    function Get-State {
      if (Test-Path -LiteralPath $stateFile) {
        try { return [bool]((Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json).enabled) }
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

    $action = if ($Arg) { $Arg } else { "run" }

    switch ($action.ToLower()) {
      "on"     { Set-State $true;  Write-Host "JARVIS auto-start: ON" }
      "off"    { Set-State $false; Write-Host "JARVIS auto-start: OFF" }
      "status" { $on = Get-State;  Write-Host "JARVIS auto-start: $(if ($on) { 'ON' } else { 'OFF' })" }
      default {
        if (-not (Get-State)) { exit 0 }
        $pyw = Join-Path $root ".venv\Scripts\pythonw.exe"
        $py = Get-Python
        if (Test-Path -LiteralPath $pyw) { $py = $pyw }
        Start-Process -FilePath $py -ArgumentList "-m", "app.gui" -WorkingDirectory $root
      }
    }
  }

  default {
    Write-Host "Unknown command: $Command"
    Write-Host "Usage: .\jarvirun.ps1 <run|cli|setup|clean|autostart>"
    exit 1
  }
}
