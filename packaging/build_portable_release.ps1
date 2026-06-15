param(
  [switch]$SkipSmoke,
  [switch]$IncludePylonRuntime,
  [switch]$SkipZaberSdkDownload
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$DistDir = Join-Path $Root "dist"
$AppDir = Join-Path $DistDir "LinearStageControl"
$StageDir = Join-Path $DistDir "portable_package"
$ZipPath = Join-Path $DistDir "LinearStageControl-Portable.zip"
$ManifestPath = Join-Path $DistDir "portable_manifest.json"

function Get-ProjectVersion {
  $VersionLine = Select-String -Path (Join-Path $Root "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
  if ($VersionLine -and $VersionLine.Matches.Count) {
    return $VersionLine.Matches[0].Groups[1].Value
  }
  throw "Could not read project version from pyproject.toml"
}

$buildArgs = @{}
if ($SkipSmoke) {
  $buildArgs.SkipSmoke = $true
}
if ($IncludePylonRuntime) {
  $buildArgs.IncludePylonRuntime = $true
}
if ($SkipZaberSdkDownload) {
  $buildArgs.SkipZaberSdkDownload = $true
}

& (Join-Path $PSScriptRoot "build_windows.ps1") @buildArgs
if ($LASTEXITCODE -ne 0) {
  throw "Windows package build failed with exit code $LASTEXITCODE"
}

$appExe = Join-Path $AppDir "LinearStageControl.exe"
if (-not (Test-Path -LiteralPath $appExe)) {
  throw "Portable app executable was not found: $appExe"
}

Remove-Item -LiteralPath $StageDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $ZipPath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $ManifestPath -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $StageDir -Force | Out-Null

$stagedAppDir = Join-Path $StageDir "LinearStageControl"
Copy-Item -LiteralPath $AppDir -Destination $stagedAppDir -Recurse -Force

$portableReadme = @"
LinearStageControl portable package

Run:
  LinearStageControl\LinearStageControl.exe

Notes:
  - This package is installer-free and does not register an automatic updater.
  - Keep the LinearStageControl folder contents together.
  - Install Basler pylon Runtime separately if the target PC does not already have it.
"@
$portableReadme | Set-Content -LiteralPath (Join-Path $StageDir "README-PORTABLE.txt") -Encoding UTF8

Compress-Archive -Path (Join-Path $StageDir "*") -DestinationPath $ZipPath -CompressionLevel Optimal -Force

$version = Get-ProjectVersion
$zipItem = Get-Item -LiteralPath $ZipPath
$zipHash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
$manifest = [ordered]@{
  version = "v$version"
  asset_name = "LinearStageControl-Portable.zip"
  sha256 = $zipHash
  size_bytes = $zipItem.Length
  distribution = "portable"
  entrypoint = "LinearStageControl\LinearStageControl.exe"
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $ManifestPath -Encoding UTF8

Remove-Item -LiteralPath $StageDir -Recurse -Force

Write-Host "Portable release package is ready:"
Write-Host "  zip     : $ZipPath ($([math]::Round($zipItem.Length / 1MB, 1)) MB)"
Write-Host "  sha256  : $zipHash"
Write-Host "  manifest: $ManifestPath"
