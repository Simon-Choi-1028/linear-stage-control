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
