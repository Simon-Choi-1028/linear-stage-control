param(
  [switch]$IncludePylonRuntime
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")

function Get-ProjectVersion {
  $VersionLine = Select-String -Path (Join-Path $Root "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
  if ($VersionLine -and $VersionLine.Matches.Count) {
    return $VersionLine.Matches[0].Groups[1].Value
  }
  return "0.0.0"
}

function Invoke-FallbackInstallerBuild {
  $SetupPath = Join-Path $Root "dist\LinearStageControlSetup.exe"
  Remove-Item -LiteralPath $SetupPath -Force -ErrorAction SilentlyContinue

  $DistPath = Join-Path $Root "dist"
  $PayloadPath = Join-Path $Root "dist\LinearStageControl"
  $InstallerScript = Join-Path $Root "packaging\fallback_installer.py"
  $InstallerWorkPath = Join-Path $Root ".pyinstaller_build\installer"
  $InstallerSpecPath = Join-Path $Root ".pyinstaller_build\installer_spec"
  $AddData = "$PayloadPath;LinearStageControl"
  & (Join-Path $Root ".venv\Scripts\pyinstaller.exe") `
    "--noconfirm" `
    "--clean" `
    "--onefile" `
    "--windowed" `
    "--name" "LinearStageControlSetup" `
    "--distpath" $DistPath `
    "--workpath" $InstallerWorkPath `
    "--specpath" $InstallerSpecPath `
    "--add-data" $AddData `
    $InstallerScript

  if ($LASTEXITCODE -ne 0) {
    throw "Fallback installer build failed with exit code $LASTEXITCODE"
  }
}

$Version = Get-ProjectVersion
$InnoCandidates = @(
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
  "C:\Program Files\Inno Setup 6\ISCC.exe"
)

$Iscc = $null
foreach ($candidate in $InnoCandidates) {
  if (Test-Path -LiteralPath $candidate) {
    $Iscc = $candidate
    break
  }
}

if (-not $Iscc) {
  $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
  if ($command) {
    $Iscc = $command.Source
  }
}

if (-not $Iscc) {
  Write-Warning "Inno Setup 6 was not found. Building fallback installer."
}

$AppExe = Join-Path $Root "dist\LinearStageControl\LinearStageControl.exe"
if (-not (Test-Path -LiteralPath $AppExe)) {
  $WindowsBuildArgs = @()
  if ($IncludePylonRuntime) {
    $WindowsBuildArgs += "-IncludePylonRuntime"
  }
  & (Join-Path $PSScriptRoot "build_windows.ps1") @WindowsBuildArgs
}

$PylonRuntime = Join-Path $Root "dist\LinearStageControl\_internal\sdk_downloads\installers\pylon_Runtime_26.04.1.exe"
if ((Test-Path -LiteralPath $PylonRuntime) -and -not $IncludePylonRuntime) {
  throw (
    "pylon Runtime payload was found in dist, but this is a slim installer build. " +
    "Run packaging\build_windows.ps1 without -IncludePylonRuntime first, " +
    "or pass -IncludePylonRuntime to build an offline installer intentionally."
  )
}

if ($Iscc) {
  Push-Location $PSScriptRoot
  try {
    & $Iscc "/DMyAppVersion=$Version" (Join-Path $PSScriptRoot "LinearStageControl.iss")
    if ($LASTEXITCODE -ne 0) {
      throw "Inno Setup failed with exit code $LASTEXITCODE"
    }
  } catch {
    Write-Warning "$($_.Exception.Message) Building fallback installer."
    Invoke-FallbackInstallerBuild
  } finally {
    Pop-Location
  }
} else {
  Invoke-FallbackInstallerBuild
}

$SetupPath = Join-Path $Root "dist\LinearStageControlSetup.exe"
if (-not (Test-Path -LiteralPath $SetupPath)) {
  throw "Installer build did not produce expected installer: $SetupPath"
}
if (Test-Path -LiteralPath $SetupPath) {
  $Hash = (Get-FileHash -Path $SetupPath -Algorithm SHA256).Hash.ToLowerInvariant()
  $Size = (Get-Item -LiteralPath $SetupPath).Length
  $Manifest = [ordered]@{
    version = "v$Version"
    asset_name = "LinearStageControlSetup.exe"
    sha256 = $Hash
    size_bytes = $Size
  }
  $ManifestPath = Join-Path $Root "dist\update_manifest.json"
  $Manifest | ConvertTo-Json | Set-Content -Path $ManifestPath -Encoding UTF8
  Write-Host "Wrote update manifest: $ManifestPath"
}
