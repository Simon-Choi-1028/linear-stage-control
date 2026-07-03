param(
  [Parameter(Mandatory = $true)][string]$AppExe,
  [Parameter(Mandatory = $true)][string]$TracePath,
  [int]$TimeoutMs = 120000
)

$ErrorActionPreference = "Stop"

$ResolvedAppExe = (Resolve-Path -LiteralPath $AppExe).Path
if ([System.IO.Path]::IsPathRooted($TracePath)) {
  $ResolvedTracePath = $TracePath
} else {
  $ResolvedTracePath = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $TracePath))
}
$TraceDir = Split-Path -Parent $ResolvedTracePath
if (-not (Test-Path -LiteralPath $TraceDir)) {
  New-Item -ItemType Directory -Path $TraceDir | Out-Null
}

Remove-Item -LiteralPath $ResolvedTracePath -ErrorAction SilentlyContinue

$processInfo = [System.Diagnostics.ProcessStartInfo]::new()
$processInfo.FileName = $ResolvedAppExe
$processInfo.Arguments = "--smoke-test"
$processInfo.WorkingDirectory = Split-Path -Parent $ResolvedAppExe
$processInfo.UseShellExecute = $false
$processInfo.CreateNoWindow = $true
$processInfo.Environment["QT_QPA_PLATFORM"] = "offscreen"
$processInfo.Environment["LINEAR_STAGE_SMOKE_TRACE"] = $ResolvedTracePath

$smoke = [System.Diagnostics.Process]::Start($processInfo)
if (-not $smoke.WaitForExit($TimeoutMs)) {
  Stop-Process -Id $smoke.Id -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 5
  exit 124
}

exit $smoke.ExitCode
