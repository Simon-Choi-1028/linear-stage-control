from __future__ import annotations

import json
import lzma
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PySide6.QtCore import QSize, Qt, QThread
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from linear_stage_control.camera import (
    PYLON_IMPORT_ERROR,
    BaslerCamera,
    apply_camera_orientation,
    camera_settings_from_config,
)
from linear_stage_control.dataset import (
    DatasetRun,
    DatasetSettings,
    base_capture_record,
    point_name,
    validate_image_output_plan,
)
from linear_stage_control.dataset_exports import (
    DEFAULT_METADATA_FORMATS,
    SUPPORTED_METADATA_FORMATS,
    normalise_formats,
)
from linear_stage_control.error_model import ErrorBudgetSettings, estimate_position_error_um
from linear_stage_control.exceptions import StageConnectionError
from linear_stage_control.position_validation import disabled_axis_variation_errors, validate_scan_points
from linear_stage_control.scan import linear_path_points_by_spacing, points_from_config, points_from_records
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
        home_exception: Exception | None = None,
        stop_exception: Exception | None = None,
        stays_busy_until_stopped: bool = False,
        position: float = 0.0,
        homed: bool = True,
    ):
        self.fail_on_move = fail_on_move
        self.fail_on_position = fail_on_position
        self.move_exception = move_exception
        self.home_exception = home_exception
        self.stop_exception = stop_exception
        self.stays_busy_until_stopped = stays_busy_until_stopped
        self.position = position
        self.homed = homed
        self.move_calls = 0
        self.home_calls = 0
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

    def is_homed(self) -> bool:
        return self.homed

    def home(self) -> None:
        self.home_calls += 1
        if self.home_exception is not None:
            raise self.home_exception
        self.homed = True


class _FakeDevice:
    def __init__(self, axes: dict[int, _FakeAxis]):
        self._axes = axes
        self.axis_count = max(axes) if axes else 0
        self.device_address = 1
        self.serial_number = 12345
        self.name = "Fake Zaber"

    def get_axis(self, axis_number: int) -> _FakeAxis:
        return self._axes[axis_number]


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

    def test_linear_path_accepts_micrometre_spacing_config(self) -> None:
        points = points_from_config(
            {
                "scan": {
                    "linear_path": {
                        "start_x_mm": 0,
                        "start_y_mm": 0,
                        "end_x_mm": 1,
                        "end_y_mm": 0,
                        "spacing_um": 250,
                    }
                }
            }
        )

        self.assertEqual([point.x_mm for point in points], [0, 0.25, 0.5, 0.75, 1.0])

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

    def test_camera_settings_default_to_rotated_lab_mount(self) -> None:
        self.assertTrue(camera_settings_from_config({"camera": {}}).rotate_180)
        self.assertFalse(camera_settings_from_config({"camera": {"rotate_180": "false"}}).rotate_180)

    def test_camera_orientation_rotates_arrays_180_degrees(self) -> None:
        array = np.arange(12, dtype=np.uint8).reshape(3, 4)

        rotated = apply_camera_orientation(array, rotate_180=True)
        unchanged = apply_camera_orientation(array, rotate_180=False)

        np.testing.assert_array_equal(rotated, array[::-1, ::-1])
        np.testing.assert_array_equal(unchanged, array)
        self.assertTrue(rotated.flags.c_contiguous)

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
    def test_stage_settings_parse_enabled_axes_and_default_chained_stages(self) -> None:
        settings = stage_settings_from_config({"stage": {"serial_port": "COM9", "axes": {"y": {"enabled": False}}}})

        self.assertTrue(settings.x.enabled)
        self.assertFalse(settings.y.enabled)
        self.assertEqual((settings.x.device_index, settings.x.axis_number), (0, 1))
        self.assertEqual((settings.y.device_index, settings.y.axis_number), (1, 1))

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

    def test_stage_resolves_legacy_y_axis_to_second_chained_device(self) -> None:
        stage = ZaberXYStage(
            StageSettings(
                serial_port="COM_TEST",
                x=AxisAddress(0, 1, True),
                y=AxisAddress(0, 2, True),
            )
        )
        x_axis = _FakeAxis()
        y_axis = _FakeAxis()
        stage.devices = [_FakeDevice({1: x_axis}), _FakeDevice({1: y_axis})]

        self.assertIs(stage._resolve_axis(stage.settings.x, "X"), x_axis)
        self.assertIs(stage._resolve_axis(stage.settings.y, "Y"), y_axis)

    def test_stage_resolves_default_chained_y_axis_to_single_two_axis_controller(self) -> None:
        stage = ZaberXYStage(
            StageSettings(
                serial_port="COM_TEST",
                x=AxisAddress(0, 1, True),
                y=AxisAddress(1, 1, True),
            )
        )
        x_axis = _FakeAxis()
        y_axis = _FakeAxis()
        stage.devices = [_FakeDevice({1: x_axis, 2: y_axis})]

        self.assertIs(stage._resolve_axis(stage.settings.x, "X"), x_axis)
        self.assertIs(stage._resolve_axis(stage.settings.y, "Y"), y_axis)

    def test_stage_axis_count_mismatch_reports_mapping_guidance(self) -> None:
        stage = ZaberXYStage(
            StageSettings(
                serial_port="COM_TEST",
                x=AxisAddress(0, 1, True),
                y=AxisAddress(0, 3, True),
            )
        )
        stage.devices = [_FakeDevice({1: _FakeAxis(), 2: _FakeAxis()})]

        with self.assertRaises(StageConnectionError) as context:
            stage._resolve_axis(stage.settings.y, "Y")

        self.assertIn("device 1 axis 1", context.exception.user_message)

    def test_stage_home_axis_mismatch_becomes_user_mapping_error(self) -> None:
        from zaber_motion.exceptions import InvalidDataException

        stage = ZaberXYStage(
            StageSettings(
                serial_port="COM_TEST",
                x=AxisAddress(0, 1, True),
                y=AxisAddress(0, 2, False),
            )
        )
        stage.x_axis = _FakeAxis(
            homed=False,
            home_exception=InvalidDataException("Response device or axis does not match: 1 != 1 || 0 != 2"),
        )  # type: ignore[assignment]

        with self.assertRaises(StageConnectionError) as context:
            stage.home()

        self.assertIn("device 1 axis 1", context.exception.user_message)

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

    def test_live_preview_defaults_to_four_by_three_frame(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        app.processEvents()

        self.assertEqual(window.preview_label.width() * 3, window.preview_label.height() * 4)
        self.assertIn("4:3", window.live_size_hint_label.text())
        window.close()

    def test_live_preview_resize_handle_changes_preview_size(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        base_size = window.preview_label.size()
        window.resize_preview_by_drag(70, 120)
        app.processEvents()

        self.assertGreater(window.preview_label.width(), base_size.width())
        self.assertGreater(window.preview_label.height(), base_size.height())
        self.assertNotEqual(window.preview_label.width() * 3, window.preview_label.height() * 4)
        self.assertIsNotNone(window.preview_user_size)
        self.assertIsNotNone(window.preview_user_min_height)
        self.assertIn("custom", window.live_size_hint_label.text())

        window.live_size_reset_button.click()
        app.processEvents()
        self.assertIsNone(window.preview_user_size)
        self.assertIsNone(window.preview_user_min_height)
        self.assertEqual(window.preview_label.minimumHeight(), window.preview_base_min_height)
        self.assertEqual(window.preview_label.width() * 3, window.preview_label.height() * 4)
        window.close()

    def test_acquisition_worker_reference_survives_run_done_until_finished(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        worker = QThread()
        window.worker = worker  # type: ignore[assignment]
        window.on_run_done("C:/tmp/run", False)
        app.processEvents()

        self.assertIs(window.worker, worker)
        self.assertEqual(window.pending_run_result, ("C:/tmp/run", False))

        window.cleanup_finished_worker()
        app.processEvents()

        self.assertIsNone(window.worker)
        self.assertIsNone(window.pending_run_result)
        window.close()

    def test_failed_run_restarts_live_only_after_worker_cleanup(self) -> None:
        from linear_stage_control import gui_app
        from linear_stage_control.gui_app import MainWindow

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        worker = QThread()
        window.worker = worker  # type: ignore[assignment]
        restart_calls: list[bool] = []
        original_critical = gui_app.QMessageBox.critical
        gui_app.QMessageBox.critical = lambda *args, **kwargs: None  # type: ignore[assignment]
        window.restart_live_preview_after_run = lambda: restart_calls.append(True)  # type: ignore[method-assign]
        try:
            window.on_run_failed("boom")
            window.on_run_done("", True)
            app.processEvents()

            self.assertIs(window.worker, worker)
            self.assertEqual(restart_calls, [])

            window.cleanup_finished_worker()
            app.processEvents()

            self.assertIsNone(window.worker)
            self.assertEqual(len(restart_calls), 1)
        finally:
            gui_app.QMessageBox.critical = original_critical
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
        self.assertGreaterEqual(window.preview_command_bar.height(), 78)
        self.assertGreaterEqual(window.preview_tool_bar.height(), 48)

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

    def test_gui_build_config_defaults_camera_rotate_180_enabled(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)

        config = window.build_config([])
        self.assertTrue(config["camera"]["rotate_180"])

        window.camera_rotate_180_check.setChecked(False)
        config = window.build_config([])
        self.assertFalse(config["camera"]["rotate_180"])
        window.close()

    def test_manual_home_moves_to_center_at_fixed_velocity(self) -> None:
        from linear_stage_control import gui_workers

        class FakeStage:
            instances: list[FakeStage] = []

            def __init__(self, _settings: object) -> None:
                self.home_calls = 0
                self.moves: list[tuple[float, float, float | None]] = []
                FakeStage.instances.append(self)

            def __enter__(self) -> FakeStage:
                return self

            def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
                pass

            def home(self) -> None:
                self.home_calls += 1

            def move_absolute_mm(
                self,
                x_mm: float,
                y_mm: float,
                *,
                velocity_mm_s: float | None = None,
                cancel_requested: object = None,
            ) -> None:
                del cancel_requested
                self.moves.append((x_mm, y_mm, velocity_mm_s))

            def position_mm(self) -> tuple[float, float]:
                return (105.0, 105.0)

        original_stage = gui_workers.ZaberXYStage
        gui_workers.ZaberXYStage = FakeStage  # type: ignore[assignment]
        done_messages: list[str] = []
        positions: list[object] = []
        try:
            worker = gui_workers.ManualStageWorker(
                {"stage": {"serial_port": "COM_TEST"}},
                "home",
                velocity_mm_s=1.0,
            )
            worker.action_done.connect(done_messages.append)
            worker.position_done.connect(positions.append)

            worker.run()

            stage = FakeStage.instances[-1]
            self.assertEqual(stage.home_calls, 1)
            self.assertEqual(stage.moves, [(105.0, 105.0, 50.0)])
            self.assertEqual(positions[-1], (105.0, 105.0))
            self.assertIn("중앙 이동", done_messages[-1])
        finally:
            gui_workers.ZaberXYStage = original_stage

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

    def test_preflight_allows_decimal_filename_suffixes_and_reports_estimated_time(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        points = points_from_records([{"x_mm": 0, "y_mm": 0}, {"x_mm": 0, "y_mm": 0}])
        config = {
            "camera": {"pixel_format": "Mono8", "exposure_us": 5000},
            "dataset": {"output_root": str(Path(tempfile.gettempdir()) / "LinearStageControl-QC")},
            "scan": {"default_capture_count": 1},
            "stage": {
                "serial_port": "COM_TEST",
                "settle_s": 0.2,
                "move_velocity_mm_s": 10,
                "axes": {
                    "x": {"enabled": True, "device_index": 0, "axis_number": 1},
                    "y": {"enabled": True, "device_index": 0, "axis_number": 2},
                },
            },
            "updates": {"enabled": False},
        }

        issues = window.collect_preflight_issues(points, config, validate_scan_points(points))

        filename_errors = [issue.detail for issue in issues if issue.item == "이미지 파일명" and issue.status == "오류"]
        filename_passes = [issue.detail for issue in issues if issue.item == "이미지 파일명" and issue.status == "통과"]
        duration_details = [issue.detail for issue in issues if issue.item == "예상 소요시간"]
        self.assertFalse(filename_errors)
        self.assertTrue(filename_passes)
        self.assertTrue(duration_details)
        self.assertTrue(any("안정화" in detail for detail in duration_details))
        window.close()

    def test_run_duration_estimate_includes_settle_exposure_and_known_moves(self) -> None:
        from linear_stage_control.gui_app import estimate_run_duration

        points = points_from_records([{"x_mm": 3, "y_mm": 4}, {"x_mm": 6, "y_mm": 8}])
        config = {
            "camera": {"exposure_us": 10_000},
            "scan": {"default_capture_count": 1},
            "stage": {
                "serial_port": "COM_TEST",
                "home_on_start": True,
                "settle_s": 0.2,
                "move_velocity_mm_s": 10,
                "axes": {
                    "x": {"enabled": True, "device_index": 0, "axis_number": 1},
                    "y": {"enabled": True, "device_index": 0, "axis_number": 2},
                },
            },
        }

        estimate = estimate_run_duration(points, config)

        self.assertAlmostEqual(estimate.seconds, 1.42, places=6)
        self.assertIn("안정화 0.4초", estimate.detail)

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
    def test_point_name_uses_truncated_decimal_xy(self) -> None:
        first = points_from_records([{"label": "sample a", "x_mm": "33.3339", "y_mm": "0.0009"}])[0]
        second = points_from_records([{"label": "sample b", "x_mm": "0.5", "y_mm": "210"}])[0]

        self.assertEqual(point_name(first, "20260528T153012_123456+0900", 1), "X033.333_Y000.000")
        self.assertEqual(point_name(first, "20260528T153012_123456+0900", 2), "X033.333_Y000.000_C02")
        self.assertEqual(point_name(second), "X000.500_Y210.000")

    def test_dataset_image_path_uses_decimal_xy_and_collision_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            points = points_from_records(
                [
                    {"label": "sample a", "x_mm": "33.3339", "y_mm": "0", "capture_count": "2"},
                    {"label": "sample b", "x_mm": "33.3339", "y_mm": "0"},
                ]
            )
            settings = DatasetSettings(output_root=Path(directory), metadata_formats=("csv", "jsonl"))

            with DatasetRun(settings, {"scan": {"default_capture_count": 1}}, points, "config.yaml") as dataset:
                first = dataset.image_path(points[0], "20260528T153012_123456+0900", 1)
                second_capture = dataset.image_path(points[0], "20260528T153012_123456+0900", 2)
                duplicate_point = dataset.image_path(points[1], "20260528T153012_123456+0900", 1)

            self.assertEqual(first.name, "X033.333_Y000.000.png")
            self.assertEqual(second_capture.name, "X033.333_Y000.000_C02.png")
            self.assertEqual(duplicate_point.name, "X033.333_Y000.000_P0002.png")

    def test_image_output_plan_allows_fractional_duplicate_and_multi_capture_names(self) -> None:
        points = points_from_records(
            [
                {"x_mm": "0.5", "y_mm": "0", "capture_count": "2"},
                {"x_mm": "0.5", "y_mm": "0"},
            ]
        )
        self.assertEqual(validate_image_output_plan(points), [])

    def test_dataset_open_accepts_fractional_image_output_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            point = points_from_records([{"x_mm": "0.5", "y_mm": "0"}])[0]
            settings = DatasetSettings(output_root=Path(directory), metadata_formats=("csv", "jsonl"))

            with DatasetRun(settings, {"scan": {"default_capture_count": 1}}, [point], "config.yaml") as dataset:
                image_path = dataset.image_path(point, "20260528T153012_123456+0900", 1)

            self.assertEqual(image_path.name, "X000.500_Y000.000.png")

    def test_dataset_manifest_includes_version_record_count_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            point = points_from_records([{"label": "sample a", "x_mm": "0", "y_mm": "0"}])[0]
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
