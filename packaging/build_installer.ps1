$ErrorActionPreference = "Stop"

$Root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
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
  Write-Error "Inno Setup 6 was not found. Install it with: winget install --id JRSoftware.InnoSetup --source winget"
}

$AppExe = Join-Path $Root "dist\LinearStageControl\LinearStageControl.exe"
if (-not (Test-Path -LiteralPath $AppExe)) {
  & (Join-Path $PSScriptRoot "build_windows.ps1")
}

& $Iscc (Join-Path $PSScriptRoot "LinearStageControl.iss")

$SetupPath = Join-Path $Root "dist\LinearStageControlSetup.exe"
if (Test-Path -LiteralPath $SetupPath) {
  $VersionLine = Select-String -Path (Join-Path $PSScriptRoot "LinearStageControl.iss") -Pattern '#define MyAppVersion "([^"]+)"' | Select-Object -First 1
  $Version = if ($VersionLine -and $VersionLine.Matches.Count) { $VersionLine.Matches[0].Groups[1].Value } else { "0.0.0" }
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
