param(
  [string]$Tag = "",
  [string]$Repo = "Simon-Choi-1028/linear-stage-control",
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$ManifestPath = Join-Path $Root "dist\update_manifest.json"
$OnlineSetup = Join-Path $Root "dist\LinearStageControlSetup.exe"
$OfflineSetup = Join-Path $Root "dist\LinearStageControlSetup-Offline.exe"
$ChangeLog = Join-Path $Root "CHANGELOG.md"

function Get-ProjectVersion {
  $VersionLine = Select-String -Path (Join-Path $Root "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
  if ($VersionLine -and $VersionLine.Matches.Count) {
    return $VersionLine.Matches[0].Groups[1].Value
  }
  throw "Could not read project version from pyproject.toml"
}

function Assert-Asset {
  param([Parameter(Mandatory = $true)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) {
    throw "Release asset missing: $Path. Run packaging\build_release_installers.ps1 first."
  }
}

if (-not $Tag) {
  $Tag = "v$(Get-ProjectVersion)"
}

Assert-Asset $ManifestPath
Assert-Asset $OnlineSetup
if (Test-Path -LiteralPath $OfflineSetup) {
  $Assets = @($OnlineSetup, $OfflineSetup, $ManifestPath)
} else {
  $Assets = @($OnlineSetup, $ManifestPath)
}

$Gh = Get-Command gh -ErrorAction SilentlyContinue
if (-not $Gh) {
  throw "GitHub CLI 'gh' was not found. Install gh or upload the assets manually."
}

$GhArgs = @(
  "release", "create", $Tag
  "--repo", $Repo
  "--title", $Tag
  "--notes-file", $ChangeLog
)
$GhArgs += $Assets

if ($DryRun) {
  Write-Host "Dry run only. Command would be:"
  Write-Host "gh $($GhArgs -join ' ')"
  exit 0
}

& gh @GhArgs
if ($LASTEXITCODE -ne 0) {
  throw "GitHub release creation failed with exit code $LASTEXITCODE"
}
