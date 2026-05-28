param(
  [switch]$SkipOffline,
  [switch]$SkipSmoke,
  [switch]$SkipZaberSdkDownload
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$OnlineSetup = Join-Path $Root "dist\LinearStageControlSetup.exe"
$OnlineNamedSetup = Join-Path $Root "dist\LinearStageControlSetup-Online.exe"
$OfflineSetup = Join-Path $Root "dist\LinearStageControlSetup-Offline.exe"
$ManifestPath = Join-Path $Root "dist\update_manifest.json"

function Get-InstallerInfo {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$Channel,
    [Parameter(Mandatory = $true)][string]$Description
  )

  if (-not (Test-Path -LiteralPath $Path)) {
    throw "Installer was not found: $Path"
  }

  $item = Get-Item -LiteralPath $Path
  [ordered]@{
    channel = $Channel
    name = $Name
    sha256 = (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    size_bytes = $item.Length
    size_mb = [math]::Round($item.Length / 1MB, 1)
    description = $Description
  }
}

function Invoke-WindowsBuild {
  param(
    [switch]$IncludePylonRuntime,
    [switch]$ForceSkipSmoke
  )

  $buildArgs = @()
  if ($IncludePylonRuntime) {
    $buildArgs += "-IncludePylonRuntime"
  }
  if ($SkipSmoke -or $ForceSkipSmoke) {
    $buildArgs += "-SkipSmoke"
  }
  if ($SkipZaberSdkDownload) {
    $buildArgs += "-SkipZaberSdkDownload"
  }

  & (Join-Path $PSScriptRoot "build_windows.ps1") @buildArgs
  & (Join-Path $PSScriptRoot "build_installer.ps1")
}

$versionLine = Select-String -Path (Join-Path $PSScriptRoot "LinearStageControl.iss") -Pattern '#define MyAppVersion "([^"]+)"' | Select-Object -First 1
$version = if ($versionLine -and $versionLine.Matches.Count) { $versionLine.Matches[0].Groups[1].Value } else { "0.0.0" }

Write-Host "Building online installer for v$version"
Invoke-WindowsBuild
Copy-Item -LiteralPath $OnlineSetup -Destination $OnlineNamedSetup -Force
$onlineInfo = Get-InstallerInfo `
  -Path $OnlineSetup `
  -Name "LinearStageControlSetup.exe" `
  -Channel "online" `
  -Description "Slim online installer. Basler pylon Runtime is downloaded or installed separately when needed."

$offlineInfo = $null
if (-not $SkipOffline) {
  $runtimePath = Join-Path $Root "sdk_downloads\installers\pylon_Runtime_26.04.1.exe"
  if (-not (Test-Path -LiteralPath $runtimePath)) {
    throw "Offline installer requires Basler pylon Runtime at $runtimePath"
  }

  Write-Host "Building offline installer for v$version"
  Write-Host "Offline build reuses the online smoke result because only the pylon Runtime installer payload changes."
  Invoke-WindowsBuild -IncludePylonRuntime -ForceSkipSmoke
  Copy-Item -LiteralPath $OnlineSetup -Destination $OfflineSetup -Force
  $offlineInfo = Get-InstallerInfo `
    -Path $OfflineSetup `
    -Name "LinearStageControlSetup-Offline.exe" `
    -Channel "offline" `
    -Description "Offline installer with the Basler pylon Runtime installer bundled."

  Copy-Item -LiteralPath $OnlineNamedSetup -Destination $OnlineSetup -Force
}

$assets = @($onlineInfo)
if ($offlineInfo) {
  $assets += $offlineInfo
}
$offlineAssetName = if ($offlineInfo) { "LinearStageControlSetup-Offline.exe" } else { $null }

$manifest = [ordered]@{
  version = "v$version"
  asset_name = "LinearStageControlSetup.exe"
  sha256 = $onlineInfo.sha256
  size_bytes = $onlineInfo.size_bytes
  channels = [ordered]@{
    default = "online"
    online = "LinearStageControlSetup.exe"
    offline = $offlineAssetName
  }
  assets = $assets
}

$manifest | ConvertTo-Json -Depth 5 | Set-Content -Path $ManifestPath -Encoding UTF8

Write-Host "Release installers are ready:"
Write-Host "  online : $OnlineSetup ($($onlineInfo.size_mb) MB)"
if ($offlineInfo) {
  Write-Host "  offline: $OfflineSetup ($($offlineInfo.size_mb) MB)"
}
Write-Host "  manifest: $ManifestPath"
