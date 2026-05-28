param(
  [switch]$SkipSmoke,
  [switch]$IncludePylonRuntime
)

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

$pyinstallerArgs = @(
  "--noconfirm",
  "--clean",
  "--windowed",
  "--name", "LinearStageControl",
  "--paths", ".",
  "--distpath", "dist",
  "--workpath", ".pyinstaller_build",
  "--collect-submodules", "pypylon",
  "--collect-all", "zaber_motion",
  "--collect-binaries", "zaber_motion_bindings",
  "--collect-submodules", "serial"
)

$dataFiles = @(
  @{ Source = "config.example.yaml"; Target = "."; Required = $true },
  @{ Source = "positions.example.csv"; Target = "."; Required = $true },
  @{ Source = "README.md"; Target = "."; Required = $true },
  @{ Source = "rules.md"; Target = "."; Required = $true },
  @{ Source = "sdk_downloads\README.md"; Target = "sdk_downloads"; Required = $false }
)

if ($IncludePylonRuntime) {
  $dataFiles += @{ Source = "sdk_downloads\installers\pylon_Runtime_26.04.1.exe"; Target = "sdk_downloads\installers"; Required = $false }
} else {
  Write-Host "Slim build: pylon Runtime installer is not bundled. Use -IncludePylonRuntime for an offline installer."
}

foreach ($item in $dataFiles) {
  if (Test-Path -LiteralPath $item.Source) {
    $pyinstallerArgs += "--add-data"
    $pyinstallerArgs += "$($item.Source);$($item.Target)"
  } elseif ($item.Required) {
    throw "Required build data file is missing: $($item.Source)"
  } else {
    Write-Warning "Optional build data file is missing and will not be bundled: $($item.Source)"
  }
}

$pyinstallerArgs += "scripts\launch_gui.py"
& .\.venv\Scripts\pyinstaller.exe @pyinstallerArgs
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$internalDir = Join-Path $distTarget "_internal"
$pruneTargets = @(
  "pypylon\pylonDataProcessingPlugins",
  "pypylon\DataProcessingPluginsB"
)
if (-not $IncludePylonRuntime) {
  $pruneTargets += "sdk_downloads\installers\pylon_Runtime_26.04.1.exe"
}
foreach ($relativeTarget in $pruneTargets) {
  $target = Join-Path $internalDir $relativeTarget
  if (Test-Path -LiteralPath $target) {
    $resolvedTarget = Resolve-Path -LiteralPath $target
    $resolvedInternal = Resolve-Path -LiteralPath $internalDir
    if (-not $resolvedTarget.Path.StartsWith($resolvedInternal.Path, [System.StringComparison]::OrdinalIgnoreCase)) {
      throw "Refusing to prune outside dist internal directory: $resolvedTarget"
    }
    Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
    Write-Host "Pruned optional payload: $relativeTarget"
  }
}

$appExe = Join-Path $distTarget "LinearStageControl.exe"
if (-not $SkipSmoke) {
  Write-Host "Running packaged smoke test: $appExe --smoke-test"
  $smoke = Start-Process -FilePath $appExe -ArgumentList "--smoke-test" -Wait -PassThru -WindowStyle Hidden
  if ($smoke.ExitCode -ne 0) {
    throw "Packaged smoke test failed with exit code $($smoke.ExitCode)"
  }
}

Write-Host "Built: $distTarget\LinearStageControl.exe"
