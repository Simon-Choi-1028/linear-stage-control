param(
  [string]$ZaberMotionVersion = "9.3.0",
  [switch]$SkipWheel
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$WheelDir = Join-Path $Root "sdk_downloads\python_wheels\zaber"
$ZaberDir = Join-Path $Root "sdk_downloads\zaber"
$DeviceDbUrl = "https://www.zaber.com/software/device-database/devices-public-v2.sqlite.lzma"
$DeviceDbPath = Join-Path $ZaberDir "devices-public-v2.sqlite.lzma"

New-Item -ItemType Directory -Force -Path $WheelDir, $ZaberDir | Out-Null

if (-not $SkipWheel) {
  $PythonExe = Join-Path $Root ".venv\Scripts\python.exe"
  if (-not (Test-Path -LiteralPath $PythonExe)) {
    $PythonExe = "py"
  }
  & $PythonExe -m pip download `
    --only-binary=:all: `
    --no-deps `
    --platform win_amd64 `
    --python-version 313 `
    --implementation py `
    --abi none `
    --dest $WheelDir `
    "zaber-motion==$ZaberMotionVersion"
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to download official Zaber Motion Library wheel."
  }
}

Invoke-WebRequest -Uri $DeviceDbUrl -OutFile $DeviceDbPath

$Artifacts = @()
Get-ChildItem -Path $WheelDir -Filter "zaber_motion-*.whl" -File | ForEach-Object {
  $Artifacts += [ordered]@{
    name = $_.Name
    path = $_.FullName
    sha256 = (Get-FileHash -Path $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    size_bytes = $_.Length
  }
}
$DeviceDb = Get-Item -LiteralPath $DeviceDbPath
$Artifacts += [ordered]@{
  name = $DeviceDb.Name
  path = $DeviceDb.FullName
  sha256 = (Get-FileHash -Path $DeviceDb.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
  size_bytes = $DeviceDb.Length
}

$ManifestPath = Join-Path $ZaberDir "zaber_sdk_manifest.json"
[ordered]@{
  source = "Zaber official Motion Library wheel and Device Database"
  downloaded_at = (Get-Date).ToUniversalTime().ToString("o")
  device_db_url = $DeviceDbUrl
  artifacts = $Artifacts
} | ConvertTo-Json -Depth 4 | Set-Content -Path $ManifestPath -Encoding UTF8

Write-Host "Downloaded Zaber SDK artifacts."
Write-Host "Manifest: $ManifestPath"
