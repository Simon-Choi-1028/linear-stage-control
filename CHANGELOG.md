# Changelog

## Unreleased

- Packaged smoke tests now import and initialise the GUI instead of exiting early, so missing bundled dependencies are detected during build.
- Windows build scripts now treat the Basler pylon Runtime installer as optional and skip its installer post-run entry when it is not bundled.
- Basler camera configuration now supports model/device-class filters, editable pixel formats, pixel format fallback candidates, and broader output pixel format aliases.
- Acquisition and camera discovery worker threads were moved into `linear_stage_control.gui_workers` to reduce `gui_app.py` complexity.
- Added hardware-free regression tests for scan path generation, flexible position input, camera compatibility settings, and export format handling.

## v0.1.0 - 2026-05-27

- Initial GUI release for Basler ace 2 and Zaber XY stage acquisition.
- Includes Korean GUI workflow, camera discovery, position table editing, linear path generation, capture preview, dataset export, and calibration error visualization.
- Windows installer: `LinearStageControlSetup.exe`
- Installer SHA256: `1E2071C0EC100E0DEE41CF16FDAB61BB59465C88151D7C250AF31F96C6F6EF1C`
