param(
  [switch]$SkipSmoke,
  [switch]$SkipAppBuild,
  [switch]$IncludePylonRuntime,
  [switch]$SkipZaberSdkDownload
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
. (Join-Path $PSScriptRoot "build_provenance.ps1")
$DistDir = Join-Path $Root "dist"
$AppDir = Join-Path $DistDir "LinearStageControl"
$StageDir = Join-Path $DistDir "portable_package"
$ZipPath = Join-Path $DistDir "LinearStageControl-Portable.zip"
$ManifestPath = Join-Path $DistDir "portable_manifest.json"
$AppExe = Join-Path $AppDir "LinearStageControl.exe"
$PylonRuntimePayload = Join-Path $AppDir "_internal\sdk_downloads\installers\pylon_Runtime_26.04.1.exe"
$BuildProvenancePath = Join-Path $AppDir "build_provenance.json"

if ($SkipAppBuild) {
  if (-not (Test-Path -LiteralPath $AppExe)) {
    throw "Cannot reuse app build because the executable was not found: $AppExe"
  }
  if (-not (Test-Path -LiteralPath $BuildProvenancePath)) {
    throw "Cannot reuse app build because provenance is missing: $BuildProvenancePath"
  }
  try {
    $buildProvenance = Get-Content -LiteralPath $BuildProvenancePath -Raw -Encoding UTF8 | ConvertFrom-Json
  } catch {
    throw "Cannot reuse app build because provenance is invalid: $($_.Exception.Message)"
  }
  $expectedVersion = "v$(Get-ProjectVersion -ProjectRoot $Root)"
  $expectedFingerprint = Get-SourceFingerprint -ProjectRoot $Root
  if ($buildProvenance.version -ne $expectedVersion) {
    throw "Cannot reuse app build: provenance version $($buildProvenance.version) does not match $expectedVersion."
  }
  if ($buildProvenance.source_fingerprint -ne $expectedFingerprint) {
    throw "Cannot reuse app build: source fingerprint does not match the current worktree."
  }
  $hasPylonRuntime = Test-Path -LiteralPath $PylonRuntimePayload
  if ($IncludePylonRuntime -and -not $hasPylonRuntime) {
    throw "Cannot reuse app build: the requested pylon Runtime payload is missing: $PylonRuntimePayload"
  }
  if (-not $IncludePylonRuntime -and $hasPylonRuntime) {
    throw "Cannot reuse app build: a slim portable package must not contain the pylon Runtime payload: $PylonRuntimePayload"
  }
  if (-not $SkipSmoke) {
    $smokeTrace = Join-Path $DistDir "portable-smoke-test.log"
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "run_packaged_smoke.ps1") `
      -AppExe $AppExe -TracePath $smokeTrace -TimeoutMs 120000
    if ($LASTEXITCODE -eq 124) {
      throw "Reused app smoke test timed out after 120 seconds. Trace: $smokeTrace"
    }
    if ($LASTEXITCODE -ne 0) {
      throw "Reused app smoke test failed with exit code $LASTEXITCODE. Trace: $smokeTrace"
    }
  }
} else {
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
}

if (-not (Test-Path -LiteralPath $AppExe)) {
  throw "Portable app executable was not found: $AppExe"
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

$version = Get-ProjectVersion -ProjectRoot $Root
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
