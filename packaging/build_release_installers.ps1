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

function Get-ProjectVersion {
  $VersionLine = Select-String -Path (Join-Path $Root "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
  if ($VersionLine -and $VersionLine.Matches.Count) {
    return $VersionLine.Matches[0].Groups[1].Value
  }
  return "0.0.0"
}

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

function Remove-DistPylonRuntimePayload {
  $payload = Join-Path $Root "dist\LinearStageControl\_internal\sdk_downloads\installers\pylon_Runtime_26.04.1.exe"
  if (-not (Test-Path -LiteralPath $payload)) {
    return
  }
  $internalDir = Join-Path $Root "dist\LinearStageControl\_internal"
  $resolvedPayload = Resolve-Path -LiteralPath $payload
  $resolvedInternal = Resolve-Path -LiteralPath $internalDir
  if (-not $resolvedPayload.Path.StartsWith($resolvedInternal.Path, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to remove pylon payload outside dist internal directory: $resolvedPayload"
  }
  Remove-Item -LiteralPath $resolvedPayload -Force
  Write-Host "Removed offline pylon Runtime payload from dist after offline installer build."
}

function Assert-DistPylonRuntimePayload {
  param(
    [switch]$ShouldExist
  )

  $payload = Join-Path $Root "dist\LinearStageControl\_internal\sdk_downloads\installers\pylon_Runtime_26.04.1.exe"
  $exists = Test-Path -LiteralPath $payload
  if ($ShouldExist -and -not $exists) {
    throw "Offline build did not include the Basler pylon Runtime payload: $payload"
  }
  if (-not $ShouldExist -and $exists) {
    throw "Slim online build unexpectedly includes the Basler pylon Runtime payload: $payload"
  }
}

function Invoke-WindowsPackageBuild {
  param(
    [switch]$IncludePylonRuntime,
    [switch]$ForceSkipSmoke
  )

  $buildArgs = @{}
  if ($IncludePylonRuntime) {
    $buildArgs.IncludePylonRuntime = $true
  }
  if ($SkipSmoke -or $ForceSkipSmoke) {
    $buildArgs.SkipSmoke = $true
  }
  if ($SkipZaberSdkDownload) {
    $buildArgs.SkipZaberSdkDownload = $true
  }

  & (Join-Path $PSScriptRoot "build_windows.ps1") @buildArgs
  Assert-DistPylonRuntimePayload -ShouldExist:$IncludePylonRuntime
}

function Invoke-InstallerBuild {
  param(
    [switch]$IncludePylonRuntime
  )

  $installerArgs = @{}
  if ($IncludePylonRuntime) {
    $installerArgs.IncludePylonRuntime = $true
  }
  & (Join-Path $PSScriptRoot "build_installer.ps1") @installerArgs
}

$version = Get-ProjectVersion

Write-Host "Building online installer for v$version"
Invoke-WindowsPackageBuild
Invoke-InstallerBuild
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
  Invoke-WindowsPackageBuild -IncludePylonRuntime -ForceSkipSmoke
  Invoke-InstallerBuild -IncludePylonRuntime
  Copy-Item -LiteralPath $OnlineSetup -Destination $OfflineSetup -Force
  $offlineInfo = Get-InstallerInfo `
    -Path $OfflineSetup `
    -Name "LinearStageControlSetup-Offline.exe" `
    -Channel "offline" `
    -Description "Offline installer with the Basler pylon Runtime installer bundled."

  Remove-DistPylonRuntimePayload
  Assert-DistPylonRuntimePayload
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
