param(
  [switch]$SkipSmoke,
  [switch]$IncludePylonRuntime,
  [switch]$SkipZaberSdkDownload
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
$runningApps = Get-Process -Name "LinearStageControl" -ErrorAction SilentlyContinue
if ($runningApps) {
  $runningApps | Stop-Process -Force
  foreach ($process in $runningApps) {
    try {
      $process.WaitForExit(10000) | Out-Null
    } catch {
      Write-Warning "Could not wait for LinearStageControl process $($process.Id) to exit: $($_.Exception.Message)"
    }
  }
}
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

$excludeModules = @(
  "PySide6.Qt3DAnimation",
  "PySide6.Qt3DCore",
  "PySide6.Qt3DExtras",
  "PySide6.Qt3DInput",
  "PySide6.Qt3DLogic",
  "PySide6.Qt3DRender",
  "PySide6.QtBluetooth",
  "PySide6.QtCharts",
  "PySide6.QtDataVisualization",
  "PySide6.QtGraphs",
  "PySide6.QtHelp",
  "PySide6.QtHttpServer",
  "PySide6.QtLocation",
  "PySide6.QtMultimedia",
  "PySide6.QtMultimediaWidgets",
  "PySide6.QtNetworkAuth",
  "PySide6.QtNfc",
  "PySide6.QtOpenGLWidgets",
  "PySide6.QtPdf",
  "PySide6.QtPdfWidgets",
  "PySide6.QtPositioning",
  "PySide6.QtQml",
  "PySide6.QtQuick",
  "PySide6.QtQuick3D",
  "PySide6.QtQuickControls2",
  "PySide6.QtRemoteObjects",
  "PySide6.QtScxml",
  "PySide6.QtSensors",
  "PySide6.QtSerialBus",
  "PySide6.QtSpatialAudio",
  "PySide6.QtStateMachine",
  "PySide6.QtTextToSpeech",
  "PySide6.QtWebChannel",
  "PySide6.QtWebEngineCore",
  "PySide6.QtWebEngineQuick",
  "PySide6.QtWebEngineWidgets",
  "PySide6.QtWebSockets",
  "PySide6.QtXml",
  "matplotlib",
  "pandas",
  "scipy",
  "tkinter"
)
foreach ($module in $excludeModules) {
  $pyinstallerArgs += "--exclude-module"
  $pyinstallerArgs += $module
}

$dataFiles = @(
  @{ Source = "config.example.yaml"; Target = "."; Required = $true },
  @{ Source = "positions.example.csv"; Target = "."; Required = $true },
  @{ Source = "README.md"; Target = "."; Required = $true },
  @{ Source = "rules.md"; Target = "."; Required = $true },
  @{ Source = "sdk_downloads\README.md"; Target = "sdk_downloads"; Required = $false },
  @{ Source = "sdk_downloads\zaber\devices-public-v2.sqlite.lzma"; Target = "sdk_downloads\zaber"; Required = $false }
)

if ($IncludePylonRuntime) {
  $dataFiles += @{ Source = "sdk_downloads\installers\pylon_Runtime_26.04.1.exe"; Target = "sdk_downloads\installers"; Required = $false }
} else {
  Write-Host "Slim build: pylon Runtime installer is not bundled. Use -IncludePylonRuntime for an offline installer."
}

$zaberDeviceDb = Join-Path $Root "sdk_downloads\zaber\devices-public-v2.sqlite.lzma"
if (-not (Test-Path -LiteralPath $zaberDeviceDb) -and -not $SkipZaberSdkDownload) {
  Write-Host "Zaber Device Database is missing; downloading official SDK artifacts."
  & (Join-Path $PSScriptRoot "download_zaber_sdk.ps1") -SkipWheel
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
  "PySide6\Qt6Pdf.dll",
  "PySide6\Qt6Qml.dll",
  "PySide6\Qt6QmlMeta.dll",
  "PySide6\Qt6QmlModels.dll",
  "PySide6\Qt6QmlWorkerScript.dll",
  "PySide6\Qt6Quick.dll",
  "PySide6\Qt6VirtualKeyboard.dll",
  "PySide6\plugins\platforminputcontexts\qtvirtualkeyboardplugin.dll",
  "PySide6\translations",
  "PylonDataProcessingCore_v6.dll",
  "pypylon\_pylondataprocessing.pyd",
  "pypylon\PylonDataProcessingCore_v6.dll",
  "pypylon\PylonDataProcessing_v4.dll",
  "pypylon\PylonDataProcessing_v4.sig",
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
  $smokeTrace = Join-Path $Root "dist\smoke-test.log"
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "run_packaged_smoke.ps1") -AppExe $appExe -TracePath $smokeTrace -TimeoutMs 120000
  if ($LASTEXITCODE -eq 124) {
    throw "Packaged smoke test timed out after 120 seconds. Trace: $smokeTrace"
  }
  if ($LASTEXITCODE -ne 0) {
    throw "Packaged smoke test failed with exit code $LASTEXITCODE. Trace: $smokeTrace"
  }
}

Write-Host "Built: $distTarget\LinearStageControl.exe"
