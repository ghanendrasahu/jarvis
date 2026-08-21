<#
.SYNOPSIS
  Remove generated caches and prune Chromium profile cache folders.
  Keeps saved logins (Cookies, Local Storage, IndexedDB) in data/browser_profile.
#>
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$rootCaches = @(".pytest_cache", ".ruff_cache", ".mypy_cache")
foreach ($dir in $rootCaches) {
  if (Test-Path -LiteralPath $dir) {
    Remove-Item -LiteralPath $dir -Recurse -Force
    Write-Host "Removed $dir"
  }
}

foreach ($base in @(".", "app", "scripts")) {
  Get-ChildItem -LiteralPath $base -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '\\\.venv\\' } |
    ForEach-Object {
      Remove-Item -LiteralPath $_.FullName -Recurse -Force
      Write-Host "Removed $($_.FullName)"
    }
}

$profile = "data\browser_profile"
$cacheDirs = @(
  "Default\Cache",
  "Default\Code Cache",
  "Default\GPUCache",
  "Default\DawnGraphiteCache",
  "Default\DawnWebGPUCache",
  "Default\GrShaderCache",
  "Default\ShaderCache",
  "Default\Service Worker\CacheStorage",
  "Default\Service Worker\ScriptCache",
  "GraphiteDawnCache",
  "GPUPersistentCache",
  "GrShaderCache",
  "ShaderCache",
  "extensions_crx_cache"
)
foreach ($rel in $cacheDirs) {
  $target = Join-Path $profile $rel
  if (Test-Path -LiteralPath $target) {
    Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Pruned $rel"
  }
}

Write-Host "Clean complete."