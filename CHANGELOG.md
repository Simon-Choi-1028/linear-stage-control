# Changelog

## v0.1.4 - 2026-05-28

- Added ms/s GUI input for stage settle time while keeping `stage.settle_s` as the canonical config value.
- Expanded optional Basler GenICam camera parameters for gain, frame rate, ROI, gamma, black level, binning, and decimation.
- Reworked live preview to use a continuous `GrabStrategy_LatestImageOnly` session instead of repeated single-frame grabs.
- Added a 50-200% live preview size slider with one-click reset.
- Added 100-800% preview zoom, click-to-center zoom targeting, grid overlay, and center crossline tools.
- Refined preview overlays to thin white guides with a 4x4 grid and non-obstructive center lines.
- Added a 2D linear path minimap with distance, point count, and expected capture summary.
- Changed image filenames to include label/point, X/Y position, timestamp, and capture index, and added `image_filename` metadata.

## v0.1.3 - 2026-05-28

- Added a release build workflow that produces both online/slim and offline/pylon-bundled installers.
- Expanded `update_manifest.json` with channel-aware installer metadata while keeping the online Setup as the updater default.
- Reworked the README opening section with direct installer downloads and official Basler/Zaber software links.
- Hardened packaged smoke testing by running it in a fresh PowerShell process with an explicit trace file.

## v0.1.2 - 2026-05-28

- Added an official Zaber SDK download helper for the Motion Library wheel and Device Database.
- Bundled the official Zaber Device Database when available and configured `Library.set_device_db_source(FILE, ...)` before device detection.
- Documented the Zaber SDK cache and offline Device DB path for field builds.

## v0.1.1 - 2026-05-28

- Packaged smoke tests now import and initialise the GUI instead of exiting early, so missing bundled dependencies are detected during build.
- Windows build scripts now treat the Basler pylon Runtime installer as optional and skip its installer post-run entry when it is not bundled.
- Basler camera configuration now supports model/device-class filters, editable pixel formats, pixel format fallback candidates, and broader output pixel format aliases.
- Acquisition and camera discovery worker threads were moved into `linear_stage_control.gui_workers` to reduce `gui_app.py` complexity.
- Added hardware-free regression tests for scan path generation, flexible position input, camera compatibility settings, and export format handling.
- Added automatic Basler live preview after camera detection, plus `Live 보기` / `Live 정지` GUI controls.
- Added X/Y axis enable flags for single-axis Zaber operation and preflight checks that block varying coordinates on disabled axes.
- Stage moves now poll `is_busy()` and cancel through the same acquisition worker-owned connection to avoid opening a second serial connection during stop.
- Added public GitHub Release update checking with SHA256-verified Setup downloads.
- Slim Windows builds now exclude the pylon Runtime installer by default and prune optional pypylon data-processing payloads.

## v0.1.0 - 2026-05-27

- Initial GUI release for Basler ace 2 and Zaber XY stage acquisition.
- Includes Korean GUI workflow, camera discovery, position table editing, linear path generation, capture preview, dataset export, and calibration error visualization.
- Windows installer: `LinearStageControlSetup.exe`
- Installer SHA256: `1E2071C0EC100E0DEE41CF16FDAB61BB59465C88151D7C250AF31F96C6F6EF1C`
