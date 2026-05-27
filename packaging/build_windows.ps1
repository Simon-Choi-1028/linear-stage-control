$ErrorActionPreference = "Stop"

$Root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
Set-Location $Root

if (-not (Test-Path -LiteralPath ".venv")) {
  py -3.13 -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe -m pip install --no-build-isolation -e .

$distTarget = Join-Path $Root "dist\LinearStageControl"
Get-Process -Name "LinearStageControl" -ErrorAction SilentlyContinue | Stop-Process -Force
if (Test-Path -LiteralPath $distTarget) {
  Remove-Item -LiteralPath $distTarget -Recurse -Force
}

& .\.venv\Scripts\pyinstaller.exe `
  --noconfirm `
  --clean `
  --windowed `
  --name LinearStageControl `
  --paths . `
  --distpath dist `
  --workpath .pyinstaller_build `
  --collect-all pypylon `
  --collect-all zaber_motion `
  --collect-binaries zaber_motion_bindings `
  --collect-submodules serial `
  --add-data "config.example.yaml;." `
  --add-data "positions.example.csv;." `
  --add-data "README.md;." `
  --add-data "rules.md;." `
  --add-data "sdk_downloads\README.md;sdk_downloads" `
  --add-data "sdk_downloads\installers\pylon_Runtime_26.04.1.exe;sdk_downloads\installers" `
  scripts\launch_gui.py

Write-Host "Built: $distTarget\LinearStageControl.exe"
