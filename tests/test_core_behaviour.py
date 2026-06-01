from __future__ import annotations

import json
import lzma
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from linear_stage_control.camera import PYLON_IMPORT_ERROR, BaslerCamera, camera_settings_from_config
from linear_stage_control.dataset import DatasetRun, DatasetSettings, base_capture_record, point_name
from linear_stage_control.dataset_exports import (
    DEFAULT_METADATA_FORMATS,
    SUPPORTED_METADATA_FORMATS,
    normalise_formats,
)
from linear_stage_control.error_model import ErrorBudgetSettings, estimate_position_error_um
from linear_stage_control.exceptions import StageConnectionError
from linear_stage_control.position_validation import disabled_axis_variation_errors, validate_scan_points
from linear_stage_control.scan import linear_path_points_by_spacing, points_from_records
from linear_stage_control.stage import (
    AxisAddress,
    StageMoveCancelled,
    StageSettings,
    ZaberXYStage,
    _resolve_device_db_path,
    _validate_sqlite_device_db,
    configure_zaber_device_database,
    prepare_zaber_device_db_path,
    stage_settings_from_config,
)
from linear_stage_control.updater import is_newer_version, sha256_file, verify_file_sha256

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _FakeAxis:
    def __init__(
        self,
        *,
        fail_on_move: bool = False,
        fail_on_position: bool = False,
        move_exception: Exception | None = None,
        stop_exception: Exception | None = None,
        stays_busy_until_stopped: bool = False,
        position: float = 0.0,
    ):
        self.fail_on_move = fail_on_move
        self.fail_on_position = fail_on_position
        self.move_exception = move_exception
        self.stop_exception = stop_exception
        self.stays_busy_until_stopped = stays_busy_until_stopped
        self.position = position
        self.move_calls = 0
        self.stop_calls = 0
        self.stopped = False
        self.moved = False

    def move_absolute(self, *_args: object, **_kwargs: object) -> None:
        self.move_calls += 1
        if self.move_exception is not None:
            raise self.move_exception
        if self.fail_on_move:
            raise RuntimeError("move failed")
        self.moved = True
        self.stopped = False

    def is_busy(self) -> bool:
        return self.stays_busy_until_stopped and self.moved and not self.stopped

    def stop(self, *, wait_until_idle: bool = False) -> None:
        _ = wait_until_idle
        self.stop_calls += 1
        if self.stop_exception is not None:
            raise self.stop_exception
        self.stopped = True

    def get_position(self, *_args: object, **_kwargs: object) -> float:
        if self.fail_on_position:
            raise RuntimeError("inactive axis should not be read")
        return self.position


class ScanInputTests(unittest.TestCase):
    def test_linear_spacing_includes_exact_endpoint(self) -> None:
        points = list(
            linear_path_points_by_spacing(
                x_start=0,
                y_start=0,
                x_stop=1,
                y_stop=0,
                spacing_mm=0.3,
                move_velocity_mm_s=25,
                capture_count=2,
            )
        )

        self.assertEqual([point.x_mm for point in points], [0, 0.3, 0.6, 0.9, 1.0])
        self.assertTrue(all(point.move_velocity_mm_s == 25 for point in points))
        self.assertTrue(all(point.capture_count == 2 for point in points))

    def test_position_records_accept_common_column_aliases(self) -> None:
        points = points_from_records(
            [
                {
                    "name": "sample-a",
                    "target_x_mm": "1.25",
                    "target_y_mm": "2.5",
                    "speed_mm_s": "10",
                    "frames": "3",
                }
            ]
        )

        self.assertEqual(points[0].label, "sample-a")
        self.assertEqual(points[0].x_mm, 1.25)
        self.assertEqual(points[0].y_mm, 2.5)
        self.assertEqual(points[0].move_velocity_mm_s, 10)
        self.assertEqual(points[0].capture_count, 3)

    def test_blank_position_label_falls_back_to_point_name(self) -> None:
        points = points_from_records([{"label": " ", "x_mm": "0.5", "y_mm": "0.0"}])

        self.assertEqual(points[0].label, "point_0000")


class CameraCompatibilityTests(unittest.TestCase):
    def test_camera_settings_accept_optional_basler_filters_and_candidates(self) -> None:
        settings = camera_settings_from_config(
            {
                "camera": {
                    "model_name": "ace 2",
                    "device_class": "BaslerGigE",
                    "pixel_format": "Auto",
                    "pixel_format_candidates": "Mono8, Mono16, BayerRG8",
                }
            }
        )

        self.assertEqual(settings.model_name, "ace 2")
        self.assertEqual(settings.device_class, "BaslerGigE")
        self.assertEqual(settings.pixel_format, "Auto")
        self.assertEqual(settings.pixel_format_candidates, ("Mono8", "Mono16", "BayerRG8"))

    def test_camera_settings_parse_extended_optional_parameters(self) -> None:
        settings = camera_settings_from_config(
            {
                "camera": {
                    "gain": "1.5",
                    "acquisition_frame_rate": "10",
                    "width": "640",
                    "height": "480",
                    "offset_x": "16",
                    "offset_y": "8",
                    "gamma": "0.8",
                    "black_level": "2",
                    "binning_x": "2",
                    "binning_y": "2",
                    "decimation_x": "",
                    "decimation_y": None,
                }
            }
        )

        self.assertEqual(settings.gain, 1.5)
        self.assertEqual(settings.acquisition_frame_rate, 10)
        self.assertEqual(settings.width, 640)
        self.assertEqual(settings.height, 480)
        self.assertEqual(settings.offset_x, 16)
        self.assertEqual(settings.offset_y, 8)
        self.assertEqual(settings.gamma, 0.8)
        self.assertEqual(settings.black_level, 2)
        self.assertEqual(settings.binning_x, 2)
        self.assertEqual(settings.binning_y, 2)
        self.assertIsNone(settings.decimation_x)
        self.assertIsNone(settings.decimation_y)

    def test_output_pixel_format_aliases_resolve_to_pylon_pixel_types(self) -> None:
        if PYLON_IMPORT_ERROR is not None:
            self.skipTest(f"pylon Runtime not available: {PYLON_IMPORT_ERROR}")
        for output_format in ("Mono8", "Mono16", "RGB8", "BGR8"):
            camera = BaslerCamera(camera_settings_from_config({"camera": {"output_pixel_format": output_format}}))
            self.assertIsInstance(camera._output_pixel_type(), int)


class ExportFormatTests(unittest.TestCase):
    def test_normalise_formats_deduplicates_and_maps_yml(self) -> None:
        formats = normalise_formats(
            "csv, jsonl, yml, csv",
            DEFAULT_METADATA_FORMATS,
            SUPPORTED_METADATA_FORMATS,
        )

        self.assertEqual(formats, ("csv", "jsonl", "yaml"))


class StageAxisSettingsTests(unittest.TestCase):
    def test_stage_settings_parse_enabled_axes_and_default_two_axis_controller(self) -> None:
        settings = stage_settings_from_config({"stage": {"serial_port": "COM9", "axes": {"y": {"enabled": False}}}})

        self.assertTrue(settings.x.enabled)
        self.assertFalse(settings.y.enabled)
        self.assertEqual((settings.x.device_index, settings.x.axis_number), (0, 1))
        self.assertEqual((settings.y.device_index, settings.y.axis_number), (0, 2))

    def test_stage_settle_ms_alias_is_used_when_settle_s_is_blank(self) -> None:
        settings = stage_settings_from_config({"stage": {"settle_s": "", "settle_ms": 250}})

        self.assertEqual(settings.settle_s, 0.25)

    def test_stage_boolean_strings_are_parsed_explicitly(self) -> None:
        settings = stage_settings_from_config(
            {
                "stage": {
                    "identify_devices": "false",
                    "home_on_start": "0",
                    "use_bundled_device_db": "no",
                    "axes": {
                        "x": {"enabled": "true"},
                        "y": {"enabled": "off"},
                    },
                }
            }
        )

        self.assertFalse(settings.identify_devices)
        self.assertFalse(settings.home_on_start)
        self.assertFalse(settings.use_bundled_device_db)
        self.assertTrue(settings.x.enabled)
        self.assertFalse(settings.y.enabled)

    def test_stage_invalid_axis_config_raises_user_error(self) -> None:
        with self.assertRaises(StageConnectionError):
            stage_settings_from_config({"stage": {"axes": {"x": {"device_index": -1}}}})

        with self.assertRaises(StageConnectionError):
            stage_settings_from_config({"stage": {"axes": {"y": {"enabled": "sometimes"}}}})

    def test_disabled_axis_must_remain_constant(self) -> None:
        points = points_from_records(
            [
                {"x_mm": 0, "y_mm": 0},
                {"x_mm": 1, "y_mm": 0},
            ]
        )

        self.assertTrue(disabled_axis_variation_errors(points, x_active=False, y_active=True))
        self.assertFalse(disabled_axis_variation_errors(points, x_active=True, y_active=False))

    def test_zaber_device_database_path_is_optional(self) -> None:
        settings = stage_settings_from_config({"stage": {"use_bundled_device_db": False}})

        self.assertIsNone(configure_zaber_device_database(settings))

    def test_zaber_device_database_sqlite_header_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sqlite_path = Path(directory) / "devices-public-v2.sqlite"
            sqlite_path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 32)
            bad_path = Path(directory) / "bad.sqlite"
            bad_path.write_bytes(b"not sqlite")

            self.assertTrue(_validate_sqlite_device_db(sqlite_path))
            self.assertFalse(_validate_sqlite_device_db(bad_path))

    def test_zaber_device_database_lzma_is_decompressed_to_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous_localappdata = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = directory
            try:
                lzma_path = Path(directory) / "devices-public-v2.sqlite.lzma"
                lzma_path.write_bytes(lzma.compress(b"SQLite format 3\x00" + b"\x00" * 32))

                resolved = prepare_zaber_device_db_path(lzma_path)

                self.assertEqual(resolved.name, "devices-public-v2.sqlite")
                self.assertTrue(_validate_sqlite_device_db(resolved))
                self.assertTrue(str(resolved).startswith(directory))
            finally:
                if previous_localappdata is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = previous_localappdata

    def test_zaber_device_database_prefers_uncompressed_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            zaber_dir = root / "sdk_downloads" / "zaber"
            zaber_dir.mkdir(parents=True)
            sqlite_path = zaber_dir / "devices-public-v2.sqlite"
            sqlite_path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 32)
            (zaber_dir / "devices-public-v2.sqlite.lzma").write_bytes(
                lzma.compress(b"SQLite format 3\x00" + b"\x01" * 32)
            )
            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                settings = stage_settings_from_config({"stage": {"use_bundled_device_db": True}})

                resolved = _resolve_device_db_path(settings)
            finally:
                os.chdir(old_cwd)

            self.assertEqual(resolved, sqlite_path.resolve())

    def test_zaber_device_database_skips_invalid_explicit_path_and_uses_bundled_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bad_path = root / "bad.sqlite"
            bad_path.write_bytes(b"not sqlite")
            zaber_dir = root / "sdk_downloads" / "zaber"
            zaber_dir.mkdir(parents=True)
            sqlite_path = zaber_dir / "devices-public-v2.sqlite"
            sqlite_path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 32)
            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                settings = StageSettings(
                    serial_port="COM_TEST",
                    device_db_path=str(bad_path),
                    use_bundled_device_db=True,
                )

                resolved = _resolve_device_db_path(settings)
            finally:
                os.chdir(old_cwd)

            self.assertEqual(resolved, sqlite_path.resolve())

    def test_stage_move_stops_started_axis_when_second_axis_command_fails(self) -> None:
        stage = ZaberXYStage(StageSettings(serial_port="COM_TEST"))
        x_axis = _FakeAxis()
        y_axis = _FakeAxis(fail_on_move=True)
        stage.x_axis = x_axis  # type: ignore[assignment]
        stage.y_axis = y_axis  # type: ignore[assignment]

        with self.assertRaises(StageConnectionError):
            stage.move_absolute_mm(1.0, 2.0)

        self.assertEqual(x_axis.move_calls, 1)
        self.assertEqual(y_axis.move_calls, 1)
        self.assertEqual(x_axis.stop_calls, 1)

    def test_stage_cancel_uses_open_axis_stop_and_raises_cancelled(self) -> None:
        stage = ZaberXYStage(
            StageSettings(
                serial_port="COM_TEST",
                x=AxisAddress(0, 1, True),
                y=AxisAddress(0, 2, False),
            )
        )
        x_axis = _FakeAxis(stays_busy_until_stopped=True)
        stage.x_axis = x_axis  # type: ignore[assignment]

        with self.assertRaises(StageMoveCancelled):
            stage.move_absolute_mm(1.0, 0.0, cancel_requested=lambda: True)

        self.assertEqual(x_axis.stop_calls, 1)

    def test_stage_inactive_axis_is_not_commanded_or_read(self) -> None:
        stage = ZaberXYStage(
            StageSettings(
                serial_port="COM_TEST",
                x=AxisAddress(0, 1, True),
                y=AxisAddress(0, 2, False),
            )
        )
        x_axis = _FakeAxis(position=1.25)
        y_axis = _FakeAxis(fail_on_position=True)
        stage.x_axis = x_axis  # type: ignore[assignment]
        stage.y_axis = y_axis  # type: ignore[assignment]

        stage.move_absolute_mm(1.25, 99.0)
        self.assertEqual(stage.position_mm(), (1.25, None))
        self.assertEqual(x_axis.move_calls, 1)
        self.assertEqual(y_axis.move_calls, 0)

    def test_stage_serial_busy_exception_becomes_user_recovery_message(self) -> None:
        from zaber_motion.exceptions import SerialPortBusyException

        stage = ZaberXYStage(
            StageSettings(
                serial_port="COM_TEST",
                x=AxisAddress(0, 1, True),
                y=AxisAddress(0, 2, False),
            )
        )
        stage.x_axis = _FakeAxis(move_exception=SerialPortBusyException("port busy"))  # type: ignore[assignment]

        with self.assertRaises(StageConnectionError) as context:
            stage.move_absolute_mm(1.0, 0.0)

        self.assertIn("COM", context.exception.user_message)
        self.assertIn("중지", context.exception.user_message)

    def test_stage_stop_failure_becomes_user_recovery_message(self) -> None:
        stage = ZaberXYStage(
            StageSettings(
                serial_port="COM_TEST",
                x=AxisAddress(0, 1, True),
                y=AxisAddress(0, 2, False),
            )
        )
        stage.x_axis = _FakeAxis(stop_exception=RuntimeError("stop failed"))  # type: ignore[assignment]

        with self.assertRaises(StageConnectionError) as context:
            stage.stop(wait_until_idle=False)

        self.assertIn("정지", context.exception.user_message)


class ErrorModelTests(unittest.TestCase):
    def test_single_axis_error_excludes_inactive_axis(self) -> None:
        estimate = estimate_position_error_um(
            error_x_mm=0.002,
            error_y_mm=None,
            budget=ErrorBudgetSettings(),
            x_active=True,
            y_active=False,
        )

        record = estimate.as_record()
        self.assertEqual(record["measured_error_x_um"], 2.0)
        self.assertIsNone(record["measured_error_y_um"])
        self.assertEqual(record["measured_radial_error_um"], 2.0)
        self.assertEqual(record["configured_error_budget_um"], ErrorBudgetSettings().axis_worst_case_um)


class UpdaterTests(unittest.TestCase):
    def test_version_compare_and_sha256_verification(self) -> None:
        self.assertTrue(is_newer_version("v0.1.1", "0.1.0"))
        self.assertFalse(is_newer_version("v0.1.0", "0.1.1"))
        self.assertTrue(verify_file_sha256(__file__, sha256_file(__file__)))


class GuiSmokeTests(unittest.TestCase):
    def test_live_frame_updates_preview_without_hardware(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        window.preview_mode = "live"
        window.on_live_frame(np.zeros((24, 32), dtype=np.uint8), {"live_fps": 9.75})
        app.processEvents()

        self.assertFalse(window.preview_label.pixmap().isNull())
        self.assertEqual(window.live_status_label.text(), "Live 9.8 FPS")
        window.close()

    def test_live_first_frame_resets_to_full_frame_fit(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        frame = np.arange(48 * 64, dtype=np.uint16).reshape(48, 64)
        window.preview_mode = "live"
        window.preview_zoom_slider.setValue(300)
        window.live_first_frame_pending = True

        window.on_live_frame(frame, {"live_fps": 10})
        app.processEvents()

        self.assertEqual(window.preview_zoom_slider.value(), 100)
        self.assertEqual(window.preview_crop_rect, (0, 0, 64, 48))
        window.close()

    def test_live_parameter_update_sends_current_exposure_to_worker(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        class FakeLiveWorker:
            def __init__(self) -> None:
                self.requests: list[dict[str, object]] = []

            def isRunning(self) -> bool:
                return True

            def request_settings_update(self, config: dict[str, object]) -> None:
                self.requests.append(config)

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        fake_worker = FakeLiveWorker()
        window.live_worker = fake_worker  # type: ignore[assignment]
        window.exposure_spin.setValue(12345)

        window.apply_live_parameter_update()
        app.processEvents()

        self.assertEqual(fake_worker.requests[-1]["camera"]["exposure_us"], 12345)
        window.live_worker = None
        window.close()

    def test_live_preview_resize_handle_changes_preview_height(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        base_height = window.preview_label.minimumHeight()
        window.resize_preview_by_drag(120)
        app.processEvents()

        self.assertGreater(window.preview_label.minimumHeight(), base_height)
        self.assertIsNotNone(window.preview_user_min_height)

        window.live_size_reset_button.click()
        app.processEvents()
        self.assertIsNone(window.preview_user_min_height)
        self.assertEqual(window.preview_label.minimumHeight(), window.preview_base_min_height)
        window.close()

    def test_responsive_layout_allows_narrow_and_short_windows(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        window.show()
        app.processEvents()

        window.resize(980, 760)
        app.processEvents()
        QTest.qWait(120)
        app.processEvents()
        window.update_responsive_layout()

        self.assertEqual(window.main_splitter.orientation(), Qt.Vertical)
        self.assertLessEqual(window.width(), 990)
        self.assertLessEqual(window.height(), 795)
        self.assertGreaterEqual(window.preview_label.height(), 150)
        self.assertGreaterEqual(window.preview_tabs.height(), 160)

        window.resize(1440, 640)
        app.processEvents()
        QTest.qWait(120)
        app.processEvents()
        window.update_responsive_layout()

        self.assertEqual(window.main_splitter.orientation(), Qt.Horizontal)
        self.assertLessEqual(window.height(), 705)
        window.close()

    def test_diagnostics_tab_and_manual_stage_controls_exist(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        app.processEvents()

        tab_names = [window.preview_tabs.tabText(index) for index in range(window.preview_tabs.count())]
        self.assertIn("진단", tab_names)
        self.assertEqual(window.manual_stage_status_label.text(), "대기 중")
        window.manual_x_edit.setText("1.5")
        window.manual_y_edit.setText("-2.0")
        self.assertEqual(window._manual_target_values(), (1.5, -2.0))
        window.close()

    def test_app_state_centralizes_button_enablement(self) -> None:
        from linear_stage_control.app_state import AppRunState
        from linear_stage_control.gui_app import MainWindow

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        app.processEvents()

        window.apply_state(AppRunState.ACQUIRING)
        self.assertFalse(window.start_button.isEnabled())
        self.assertTrue(window.stop_button.isEnabled())
        self.assertFalse(window.camera_scan_button.isEnabled())
        self.assertFalse(window.manual_move_button.isEnabled())

        window.apply_state(AppRunState.CANCELLING)
        self.assertFalse(window.stop_button.isEnabled())

        window.apply_state(AppRunState.IDLE)
        self.assertTrue(window.start_button.isEnabled())
        self.assertFalse(window.stop_button.isEnabled())
        self.assertFalse(window.manual_stop_button.isEnabled())
        window.close()

    def test_preflight_parses_string_axis_enabled_and_reports_single_axis_conflict(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        points = points_from_records([{"x_mm": 0, "y_mm": 0}, {"x_mm": 1, "y_mm": 0}])
        config = {
            "camera": {"pixel_format": "Mono8", "exposure_us": 5000},
            "dataset": {"output_root": str(Path(tempfile.gettempdir()) / "LinearStageControl-QC")},
            "scan": {"default_capture_count": 1},
            "stage": {
                "serial_port": "COM_TEST",
                "axes": {
                    "x": {"enabled": "false", "device_index": 0, "axis_number": 1},
                    "y": {"enabled": "true", "device_index": 0, "axis_number": 2},
                },
            },
            "updates": {"enabled": False},
        }

        issues = window.collect_preflight_issues(points, config, validate_scan_points(points))

        conflict_details = [issue.detail for issue in issues if issue.item == "단일축 운용" and issue.status == "오류"]
        self.assertTrue(conflict_details)
        self.assertTrue(any("X" in detail for detail in conflict_details))
        window.close()

    def test_preview_zoom_grid_and_cross_render_without_hardware(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        window.preview_mode = "live"
        frame = np.arange(48 * 64, dtype=np.uint16).reshape(48, 64)
        window.preview_zoom_slider.setValue(200)
        window.preview_grid_check.setChecked(True)
        window.preview_cross_check.setChecked(True)
        window.on_live_frame(frame, {})
        app.processEvents()

        self.assertEqual(window.preview_zoom_label.text(), "200%")
        self.assertTrue(window.preview_grid_check.isChecked())
        self.assertTrue(window.preview_cross_check.isChecked())
        self.assertFalse(window.preview_label.pixmap().isNull())
        self.assertIsNotNone(window.preview_crop_rect)
        self.assertLess(window.preview_crop_rect[2], frame.shape[1])

        window.set_preview_center_from_label(
            window.preview_label.width() / 2,
            window.preview_label.height() / 2,
        )
        self.assertGreaterEqual(window.preview_center_x, 0.0)
        self.assertLessEqual(window.preview_center_x, 1.0)
        window.close()

    def test_preview_overlay_uses_thin_white_guides(self) -> None:
        from linear_stage_control.preview_rendering import draw_preview_overlays

        app = QApplication.instance() or QApplication([])
        pixmap = QPixmap(80, 80)
        pixmap.fill(QColor("#000000"))

        draw_preview_overlays(pixmap, show_grid=True, show_cross=True)
        app.processEvents()

        image = pixmap.toImage()
        for position in (20, 40, 60):
            vertical = image.pixelColor(position, 8)
            horizontal = image.pixelColor(8, position)
            self.assertGreater(vertical.red(), 100)
            self.assertGreater(horizontal.red(), 100)
            self.assertLess(abs(vertical.red() - vertical.green()), 3)
            self.assertLess(abs(horizontal.red() - horizontal.green()), 3)

        center = image.pixelColor(40, 40)
        self.assertLess(abs(center.red() - center.green()), 3)
        self.assertLess(abs(center.red() - center.blue()), 3)

    def test_linear_path_preview_widget_renders_without_hardware(self) -> None:
        from linear_stage_control.gui_widgets import LinearPathPreviewWidget

        app = QApplication.instance() or QApplication([])
        widget = LinearPathPreviewWidget()
        widget.resize(QSize(360, 220))
        widget.set_path([(0, 0), (0.5, 0.25), (1.0, 0.5)], "총 거리 1.118 mm | 위치 3개")
        pixmap = QPixmap(widget.size())
        widget.render(pixmap)
        app.processEvents()

        image = pixmap.toImage()
        colors = {
            image.pixelColor(x, y).name() for x in range(0, image.width(), 40) for y in range(0, image.height(), 40)
        }
        self.assertGreater(len(colors), 1)


class DatasetNamingTests(unittest.TestCase):
    def test_point_name_includes_label_position_timestamp_and_capture_index(self) -> None:
        point = points_from_records([{"label": "sample a", "x_mm": "0.5", "y_mm": "-1.25"}])[0]

        name = point_name(point, "20260528T153012_123456+0900", 7)

        self.assertEqual(
            name,
            "sample_a_x0.500mm_y-1.250mm_20260528T153012_123456+0900_cap007",
        )

    def test_dataset_manifest_includes_version_record_count_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            point = points_from_records([{"label": "sample a", "x_mm": "0.5", "y_mm": "-1.25"}])[0]
            settings = DatasetSettings(output_root=Path(directory), metadata_formats=("csv", "jsonl"))

            with DatasetRun(settings, {"dataset": {}}, [point], "config.yaml") as dataset:
                image_path = dataset.image_path(point, "20260528T153012_123456+0900", 1)
                image_path.write_bytes(b"fake image bytes")
                record = base_capture_record(dataset.run_id, point)
                record.update(
                    {
                        "status": "ok",
                        "capture_index": 1,
                        "capture_count": 1,
                        "image_path": str(image_path.relative_to(dataset.run_dir)),
                        "image_filename": image_path.name,
                    }
                )
                dataset.write_capture(record)
                run_dir = dataset.run_dir

            manifest_path = run_dir / "dataset_manifest.json"
            legacy_manifest_path = run_dir / "manifest.json"
            self.assertTrue(manifest_path.exists())
            self.assertTrue(legacy_manifest_path.exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(manifest["record_count"], 1)
            self.assertIn("app_version", manifest)
            image_entries = [entry for entry in manifest["files"] if entry["role"] == "image"]
            self.assertEqual(len(image_entries), 1)
            self.assertEqual(image_entries[0]["path"], f"images/{image_path.name}")
            self.assertEqual(image_entries[0]["sha256"], sha256_file(image_path))


class DiagnosticsTests(unittest.TestCase):
    def test_collect_diagnostics_runs_without_hardware_checks(self) -> None:
        from linear_stage_control.diagnostics import collect_diagnostics

        with tempfile.TemporaryDirectory() as directory:
            results = collect_diagnostics(
                {"stage": {"serial_port": "COM_TEST"}, "updates": {"enabled": False}},
                output_root=directory,
                check_camera=False,
                check_updates=False,
            )

        items = {result.item for result in results}
        self.assertIn("Basler pylon/Python", items)
        self.assertIn("Zaber COM 포트", items)
        self.assertIn("저장 폴더 권한", items)


if __name__ == "__main__":
    unittest.main()
