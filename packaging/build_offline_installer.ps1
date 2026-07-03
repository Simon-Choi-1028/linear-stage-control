param(
  [string]$OutputDir = (Join-Path $env:USERPROFILE "Downloads"),
  [switch]$SkipSmoke,
  [switch]$SkipZaberSdkDownload
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)
$RuntimePath = Join-Path $Root "sdk_downloads\installers\pylon_Runtime_26.04.1.exe"
$ZaberDbPath = Join-Path $Root "sdk_downloads\zaber\devices-public-v2.sqlite"
$DistSetupPath = Join-Path $Root "dist\LinearStageControlSetup.exe"
$DistOfflineSetupPath = Join-Path $Root "dist\LinearStageControlSetup-Offline.exe"
$OutputSetupPath = Join-Path $OutputDir "LinearStageControlSetup-Offline.exe"
$OutputManifestPath = Join-Path $OutputDir "LinearStageControlSetup-Offline.manifest.json"
$OutputShaPath = Join-Path $OutputDir "LinearStageControlSetup-Offline.sha256.txt"

function Get-ProjectVersion {
  $versionLine = Select-String -Path (Join-Path $Root "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
  if ($versionLine -and $versionLine.Matches.Count) {
    return $versionLine.Matches[0].Groups[1].Value
  }
  return "0.0.0"
}

function Get-PinnedDependencies {
  $dependencies = [ordered]@{}
  Get-Content -Path (Join-Path $Root "requirements.txt") | ForEach-Object {
    $line = $_.Trim()
    if ($line -match '^([^#=\s]+)==(.+)$') {
      $dependencies[$Matches[1]] = $Matches[2]
    }
  }
  return $dependencies
}

if (-not (Test-Path -LiteralPath $RuntimePath)) {
  throw "Offline installer requires Basler pylon Runtime at $RuntimePath"
}
if (-not (Test-Path -LiteralPath $ZaberDbPath) -and $SkipZaberSdkDownload) {
  throw "Offline installer requires Zaber Device Database at $ZaberDbPath"
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$windowsBuildArgs = @{
  IncludePylonRuntime = $true
}
if ($SkipSmoke) {
  $windowsBuildArgs.SkipSmoke = $true
}
if ($SkipZaberSdkDownload) {
  $windowsBuildArgs.SkipZaberSdkDownload = $true
}

& (Join-Path $PSScriptRoot "build_windows.ps1") @windowsBuildArgs
& (Join-Path $PSScriptRoot "build_installer.ps1") -IncludePylonRuntime

if (-not (Test-Path -LiteralPath $DistSetupPath)) {
  throw "Installer build did not produce expected installer: $DistSetupPath"
}

Copy-Item -LiteralPath $DistSetupPath -Destination $DistOfflineSetupPath -Force
Copy-Item -LiteralPath $DistSetupPath -Destination $OutputSetupPath -Force

$hash = (Get-FileHash -Path $OutputSetupPath -Algorithm SHA256).Hash.ToLowerInvariant()
$item = Get-Item -LiteralPath $OutputSetupPath
$pythonExe = Join-Path $Root "build\.venv\Scripts\python.exe"
$pythonVersion = if (Test-Path -LiteralPath $pythonExe) {
  (& $pythonExe -c "import sys; print(sys.version.split()[0])").Trim()
} else {
  "unknown"
}

$bundledRuntimePath = Join-Path $Root "dist\LinearStageControl\_internal\sdk_downloads\installers\pylon_Runtime_26.04.1.exe"
$bundledZaberDbPath = Join-Path $Root "dist\LinearStageControl\_internal\sdk_downloads\zaber\devices-public-v2.sqlite"
if (-not (Test-Path -LiteralPath $bundledRuntimePath)) {
  throw "Built app payload does not include pylon Runtime: $bundledRuntimePath"
}
if (-not (Test-Path -LiteralPath $bundledZaberDbPath)) {
  throw "Built app payload does not include Zaber Device Database: $bundledZaberDbPath"
}

$manifest = [ordered]@{
  version = "v$(Get-ProjectVersion)"
  channel = "offline"
  asset_name = "LinearStageControlSetup-Offline.exe"
  output_path = $OutputSetupPath
  sha256 = $hash
  size_bytes = $item.Length
  size_mb = [math]::Round($item.Length / 1MB, 1)
  built_at_utc = (Get-Date).ToUniversalTime().ToString("o")
  python = [ordered]@{
    version = $pythonVersion
    requires = ">=3.13,<3.14"
  }
  dependencies = Get-PinnedDependencies
  bundled_payloads = [ordered]@{
    pylon_runtime = [ordered]@{
      version = "26.04.1"
      path = $RuntimePath
      sha256 = (Get-FileHash -Path $RuntimePath -Algorithm SHA256).Hash.ToLowerInvariant()
      size_bytes = (Get-Item -LiteralPath $RuntimePath).Length
    }
    zaber_device_database = [ordered]@{
      path = $ZaberDbPath
      sha256 = (Get-FileHash -Path $ZaberDbPath -Algorithm SHA256).Hash.ToLowerInvariant()
      size_bytes = (Get-Item -LiteralPath $ZaberDbPath).Length
    }
  }
}

$manifest | ConvertTo-Json -Depth 6 | Set-Content -Path $OutputManifestPath -Encoding UTF8
"$hash  LinearStageControlSetup-Offline.exe" | Set-Content -Path $OutputShaPath -Encoding ASCII

Write-Host "Offline installer ready:"
Write-Host "  setup   : $OutputSetupPath"
Write-Host "  manifest: $OutputManifestPath"
Write-Host "  sha256  : $OutputShaPath"
