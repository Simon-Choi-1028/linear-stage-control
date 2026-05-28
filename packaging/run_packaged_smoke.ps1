param(
  [Parameter(Mandatory = $true)][string]$AppExe,
  [Parameter(Mandatory = $true)][string]$TracePath,
  [int]$TimeoutMs = 120000
)

$ErrorActionPreference = "Stop"

Remove-Item -LiteralPath $TracePath -ErrorAction SilentlyContinue
$env:QT_QPA_PLATFORM = "offscreen"
$env:LINEAR_STAGE_SMOKE_TRACE = $TracePath

$smoke = Start-Process -FilePath $AppExe -ArgumentList "--smoke-test" -PassThru -WindowStyle Normal
if (-not $smoke.WaitForExit($TimeoutMs)) {
  Stop-Process -Id $smoke.Id -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 5
  exit 124
}

exit $smoke.ExitCode
