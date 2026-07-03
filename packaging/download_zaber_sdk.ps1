param(
  [string]$ZaberMotionVersion = "9.3.0",
  [switch]$SkipWheel
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$WheelDir = Join-Path $Root "sdk_downloads\python_wheels\zaber"
$ZaberDir = Join-Path $Root "sdk_downloads\zaber"
$DeviceDbUrl = "https://www.zaber.com/software/device-database/devices-public-v2.sqlite.lzma"
$DeviceDbLzmaPath = Join-Path $ZaberDir "devices-public-v2.sqlite.lzma"
$DeviceDbSqlitePath = Join-Path $ZaberDir "devices-public-v2.sqlite"

New-Item -ItemType Directory -Force -Path $WheelDir, $ZaberDir | Out-Null

$PythonExe = Join-Path $Root "build\.venv\Scripts\python.exe"
$PythonPrefixArgs = @()
if (Test-Path -LiteralPath $PythonExe) {
  $VenvVersion = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
  if ($VenvVersion -ne "3.13") {
    $PythonExe = "py"
    $PythonPrefixArgs = @("-3.13")
  }
} else {
  $PythonExe = "py"
  $PythonPrefixArgs = @("-3.13")
}

if (-not $SkipWheel) {
  & $PythonExe @PythonPrefixArgs -m pip download `
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

Invoke-WebRequest -Uri $DeviceDbUrl -OutFile $DeviceDbLzmaPath

$DecompressScript = @'
import lzma
import pathlib
import sys

src = pathlib.Path(sys.argv[1])
dst = pathlib.Path(sys.argv[2])
data = lzma.open(src, "rb").read()
if not data.startswith(b"SQLite format 3\x00"):
    raise SystemExit(f"Downloaded Zaber Device DB does not decompress to SQLite: {src}")
dst.write_bytes(data)
'@
$DecompressScript | & $PythonExe @PythonPrefixArgs - $DeviceDbLzmaPath $DeviceDbSqlitePath
if ($LASTEXITCODE -ne 0) {
  throw "Failed to decompress and verify the official Zaber Device Database."
}

$Artifacts = @()
Get-ChildItem -Path $WheelDir -Filter "zaber_motion-*.whl" -File | ForEach-Object {
  $Artifacts += [ordered]@{
    name = $_.Name
    path = $_.FullName
    sha256 = (Get-FileHash -Path $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    size_bytes = $_.Length
  }
}
foreach ($DeviceDbPath in @($DeviceDbLzmaPath, $DeviceDbSqlitePath)) {
  $DeviceDb = Get-Item -LiteralPath $DeviceDbPath
  $Artifacts += [ordered]@{
    name = $DeviceDb.Name
    path = $DeviceDb.FullName
    sha256 = (Get-FileHash -Path $DeviceDb.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    size_bytes = $DeviceDb.Length
  }
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
