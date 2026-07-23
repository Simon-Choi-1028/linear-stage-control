from __future__ import annotations

import csv
import json
import lzma
import os
import tempfile
import tomllib
import tracemalloc
import unittest
from pathlib import Path

import numpy as np
from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from linear_stage_control.camera import (
    CAMERA_CAPTURE_PARAMETER_FIELDS,
    PYLON_IMPORT_ERROR,
    BaslerCamera,
    apply_camera_orientation,
    camera_settings_from_config,
    save_original_capture,
)
from linear_stage_control.dataset import (
    DatasetRun,
    DatasetSettings,
    base_capture_record,
    dataset_settings_from_config,
    validate_image_output_plan,
)
from linear_stage_control.disk_writer import AsyncCaptureDiskWriter, CaptureDiskWriteJob
from linear_stage_control.error_model import ErrorBudgetSettings, estimate_position_error_um
from linear_stage_control.exceptions import DatasetWriteError, StageConnectionError
from linear_stage_control.position_validation import (
    PositionInputRow,
    disabled_axis_variation_errors,
    parse_position_rows,
    validate_scan_points,
)
from linear_stage_control.scan import (
    ScanPoint,
    linear_path_points,
    linear_path_points_by_spacing,
    points_from_config,
    points_from_records,
)
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
        stays_busy_after_stop: bool = False,
        position: float = 0.0,
        homed: bool = True,
    ):
        self.fail_on_move = fail_on_move
        self.fail_on_position = fail_on_position
        self.move_exception = move_exception
        self.home_exception = home_exception
        self.stop_exception = stop_exception
        self.stays_busy_until_stopped = stays_busy_until_stopped
        self.stays_busy_after_stop = stays_busy_after_stop
        self.position = position
        self.homed = homed
        self.move_calls = 0
        self.home_calls = 0
        self.home_wait_until_idle_values: list[bool] = []
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
        if self.stays_busy_after_stop and self.moved:
            return True
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

    def home(self, *, wait_until_idle: bool = True) -> None:
        self.home_calls += 1
        self.home_wait_until_idle_values.append(wait_until_idle)
        if self.home_exception is not None:
            raise self.home_exception
        self.homed = True
        self.moved = True
        self.stopped = False


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

    def test_linear_path_allows_two_hundred_thousand_points(self) -> None:
        points = list(
            linear_path_points(
                x_start=0,
                y_start=0,
                x_stop=1,
                y_stop=0,
                count=200_000,
            )
        )

        self.assertEqual(len(points), 200_000)
        self.assertEqual(points[0].x_mm, 0)
        self.assertEqual(points[-1].x_mm, 1)

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

    def test_position_validation_rejects_non_finite_values(self) -> None:
        points, validation = parse_position_rows(
            [
                PositionInputRow(
                    index=0,
                    label="invalid",
                    x_text="nan",
                    y_text="inf",
                    velocity_text="inf",
                    capture_count_text="nan",
                )
            ]
        )

        self.assertEqual(points, [])
        self.assertGreaterEqual(len(validation.errors), 4)
        self.assertTrue(any("유한한" in error for error in validation.errors))


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
        defaults = camera_settings_from_config({"camera": {}})
        self.assertTrue(defaults.rotate_180)
        self.assertFalse(defaults.flip_horizontal)
        self.assertFalse(defaults.flip_vertical)
        settings = camera_settings_from_config(
            {"camera": {"rotate_180": "false", "flip_horizontal": "true", "flip_vertical": "1"}}
        )
        self.assertFalse(settings.rotate_180)
        self.assertTrue(settings.flip_horizontal)
        self.assertTrue(settings.flip_vertical)

    def test_capture_parameter_snapshot_uses_actual_camera_readbacks(self) -> None:
        class Feature:
            def __init__(self, value: object, *, fail: bool = False):
                self.value = value
                self.fail = fail

            def GetValue(self) -> object:
                if self.fail:
                    raise RuntimeError("unreadable")
                return self.value

        class DeviceInfo:
            def GetModelName(self) -> str:
                return "Actual ace 2"

            def GetSerialNumber(self) -> str:
                return "40123456"

            def GetUserDefinedName(self) -> str:
                return "Lab camera"

            def GetDeviceClass(self) -> str:
                return "BaslerGigE"

        class Camera:
            PixelFormat = Feature("Mono12")
            ExposureTime = Feature(None, fail=True)
            ExposureTimeAbs = Feature(4321.5)
            Gain = Feature(2.25)
            AcquisitionFrameRate = Feature(17.5)
            Width = Feature(1920)
            Height = Feature(1200)
            OffsetX = Feature(8)
            OffsetY = Feature(4)
            TriggerMode = Feature("On")
            TriggerSelector = Feature("FrameStart")
            TriggerSource = Feature("Software")

            def GetDeviceInfo(self) -> DeviceInfo:
                return DeviceInfo()

        camera = BaslerCamera(
            camera_settings_from_config(
                {
                    "camera": {
                        "pixel_format": "Mono8",
                        "exposure_us": 5000,
                        "output_pixel_format": "Mono16",
                        "timeout_ms": 7000,
                        "rotate_180": False,
                        "flip_horizontal": True,
                    }
                }
            )
        )
        camera.camera = Camera()

        snapshot = camera.capture_parameter_snapshot()

        self.assertEqual(set(snapshot), set(CAMERA_CAPTURE_PARAMETER_FIELDS))
        self.assertEqual(snapshot["camera_model_name"], "Actual ace 2")
        self.assertEqual(snapshot["camera_serial_number"], "40123456")
        self.assertEqual(snapshot["camera_pixel_format"], "Mono12")
        self.assertEqual(snapshot["camera_exposure_us"], 4321.5)
        self.assertEqual(snapshot["camera_gain"], 2.25)
        self.assertEqual(snapshot["camera_acquisition_frame_rate_hz"], 17.5)
        self.assertEqual(snapshot["camera_width_px"], 1920)
        self.assertEqual(snapshot["camera_height_px"], 1200)
        self.assertEqual(snapshot["camera_gamma"], "")
        self.assertEqual(snapshot["camera_output_pixel_format"], "Mono16")
        self.assertEqual(snapshot["camera_timeout_ms"], 7000)
        self.assertFalse(snapshot["camera_rotate_180"])
        self.assertTrue(snapshot["camera_flip_horizontal"])

    def test_camera_orientation_rotates_arrays_180_degrees(self) -> None:
        array = np.arange(12, dtype=np.uint8).reshape(3, 4)

        rotated = apply_camera_orientation(array, rotate_180=True)
        unchanged = apply_camera_orientation(array, rotate_180=False)

        np.testing.assert_array_equal(rotated, array[::-1, ::-1])
        np.testing.assert_array_equal(unchanged, array)
        self.assertTrue(rotated.flags.c_contiguous)

    def test_camera_orientation_detaches_arrays_without_transform(self) -> None:
        array = np.arange(12, dtype=np.uint8).reshape(3, 4)

        detached = apply_camera_orientation(array, rotate_180=False)

        np.testing.assert_array_equal(detached, array)
        self.assertTrue(detached.flags.c_contiguous)
        self.assertFalse(np.shares_memory(detached, array))

    def test_async_capture_disk_writer_saves_image_and_npy(self) -> None:
        array = np.arange(16, dtype=np.uint8).reshape(4, 4)
        metadata = {
            "captured_at": "2026-06-17T10:00:00.000+09:00",
            "completed_at": "2026-06-17T10:00:00.010+09:00",
            "pixel_type": "Mono8",
            "width": 4,
            "height": 4,
            "camera_timestamp_ns": 123,
            "block_id": 456,
            "camera_pixel_format": "Mono12",
            "camera_exposure_us": 4321.5,
        }
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "capture.png"
            npy_path = Path(directory) / "capture.npy"
            with AsyncCaptureDiskWriter() as writer:
                future = writer.submit(
                    CaptureDiskWriteJob(
                        image_path=image_path,
                        npy_path=npy_path,
                        array=array,
                        metadata=metadata,
                    )
                )
                result = future.result(timeout=10)

            self.assertTrue(image_path.exists())
            self.assertTrue(npy_path.exists())
            self.assertEqual(result.image_path, image_path)
            self.assertEqual(result.npy_path, npy_path)
            self.assertEqual(result.dtype, "uint8")
            self.assertEqual(result.shape, (4, 4))
            self.assertGreaterEqual(result.disk_write_duration_ms, 0.0)
            self.assertEqual(result.camera_parameters["camera_pixel_format"], "Mono12")
            self.assertEqual(result.camera_parameters["camera_exposure_us"], 4321.5)
            np.testing.assert_array_equal(np.load(npy_path), array)

    def test_original_capture_rejects_metadata_dimension_mismatch(self) -> None:
        array = np.zeros((4, 4), dtype=np.uint8)
        metadata = {
            "captured_at": "2026-06-17T10:00:00.000+09:00",
            "completed_at": "2026-06-17T10:00:00.010+09:00",
            "width": 5,
            "height": 4,
        }
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(DatasetWriteError):
                save_original_capture(Path(directory) / "capture.png", array, metadata)
            self.assertFalse((Path(directory) / "capture.png").exists())

    def test_qimage_conversion_rejects_unsupported_or_huge_preview_frames(self) -> None:
        from linear_stage_control.preview_rendering import MAX_PREVIEW_PIXELS, qimage_from_array

        with self.assertRaises(ValueError):
            qimage_from_array(np.zeros((8, 8, 2), dtype=np.uint8))

        backing = np.zeros((1,), dtype=np.uint8)
        huge = np.lib.stride_tricks.as_strided(backing, shape=(MAX_PREVIEW_PIXELS + 1, 1), strides=(0, 0))
        with self.assertRaises(MemoryError):
            qimage_from_array(huge)

    def test_uint16_preview_scaling_preserves_dynamic_range_without_full_frame_float_copies(self) -> None:
        from linear_stage_control.preview_rendering import _to_uint8

        values = np.array([[100, 101, 1000], [2048, 4094, 4095]], dtype=np.uint16)
        expected_float = values.astype(np.float32)
        expected = np.clip(
            (expected_float - float(values.min())) * (255.0 / (float(values.max()) - float(values.min()))),
            0,
            255,
        ).astype(np.uint8)
        np.testing.assert_array_equal(_to_uint8(values), expected)

        frame = np.arange(2048 * 2048, dtype=np.uint16).reshape(2048, 2048)
        tracemalloc.start()
        try:
            converted = _to_uint8(frame)
            _current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        self.assertEqual(converted.shape, frame.shape)
        self.assertTrue(converted.flags.c_contiguous)
        self.assertLess(peak_bytes, frame.nbytes * 2)

    def test_live_camera_setting_warnings_are_consumed_per_call(self) -> None:
        settings = camera_settings_from_config({"camera": {"gain": 1.5}})
        camera = BaslerCamera(settings)
        camera.camera = object()
        camera.warnings.append("warning retained from camera open")

        for _ in range(100):
            warnings = camera.apply_live_settings(settings)
            self.assertEqual(len(warnings), 1)
            self.assertIn("Gain/GainRaw", warnings[0])
            self.assertEqual(camera.warnings, ["warning retained from camera open"])

    def test_camera_orientation_flips_arrays_after_rotation(self) -> None:
        array = np.arange(12, dtype=np.uint8).reshape(3, 4)

        horizontal = apply_camera_orientation(array, rotate_180=False, flip_horizontal=True)
        vertical = apply_camera_orientation(array, rotate_180=False, flip_vertical=True)
        combined = apply_camera_orientation(array, rotate_180=True, flip_vertical=True, flip_horizontal=True)

        np.testing.assert_array_equal(horizontal, array[:, ::-1])
        np.testing.assert_array_equal(vertical, array[::-1, :])
        np.testing.assert_array_equal(combined, array)
        self.assertTrue(combined.flags.c_contiguous)

    def test_output_pixel_format_aliases_resolve_to_pylon_pixel_types(self) -> None:
        if PYLON_IMPORT_ERROR is not None:
            self.skipTest(f"pylon Runtime not available: {PYLON_IMPORT_ERROR}")
        for output_format in ("Mono8", "Mono16", "RGB8", "BGR8"):
            camera = BaslerCamera(camera_settings_from_config({"camera": {"output_pixel_format": output_format}}))
            self.assertIsInstance(camera._output_pixel_type(), int)


class ProcessGuardTests(unittest.TestCase):
    def test_pylon_viewer_tasklist_csv_detection(self) -> None:
        from linear_stage_control.process_guard import running_pylon_viewer_processes

        sample = "\n".join(
            [
                '"notepad.exe","100","Console","1","1,000 K"',
                '"pylonviewer.exe","200","Console","1","2,000 K"',
                '"BaslerPylonViewerApp.exe","300","Console","1","3,000 K"',
            ]
        )

        viewers = running_pylon_viewer_processes(sample)

        self.assertEqual([viewer.name for viewer in viewers], ["pylonviewer.exe", "BaslerPylonViewerApp.exe"])
        self.assertEqual([viewer.pid for viewer in viewers], [200, 300])


class LaserControlTests(unittest.TestCase):
    def test_laser_command_uses_percent_protocol(self) -> None:
        from linear_stage_control.laser import read_response_line, send_laser_command

        class FakeSerial:
            def __init__(self) -> None:
                self.timeout: float | None = 1.0
                self.commands: list[bytes] = []
                self.responses = [b"L42\n", b"OK 42 PWM 64\n"]

            def write(self, data: bytes) -> int:
                self.commands.append(data)
                return len(data)

            def flush(self) -> None:
                return

            def readline(self) -> bytes:
                return self.responses.pop(0) if self.responses else b""

        ser = FakeSerial()

        command = send_laser_command(ser, 42)
        response = read_response_line(ser, {"L42"}, 1.0)

        self.assertEqual(command, b"L42\n")
        self.assertEqual(ser.commands, [b"L42\n"])
        self.assertEqual(response, "OK 42 PWM 64")

    def test_laser_settings_validate_percent_and_defaults(self) -> None:
        from linear_stage_control.exceptions import LaserConnectionError
        from linear_stage_control.laser import laser_percent_from_config, laser_settings_from_config

        settings = laser_settings_from_config({"laser": {"serial_port": "COM9", "expect_response": "false"}})

        self.assertEqual(settings.serial_port, "COM9")
        self.assertEqual(settings.baud_rate, 9600)
        self.assertFalse(settings.expect_response)
        self.assertEqual(laser_percent_from_config({"laser": {"percent": "100"}}), 100)
        with self.assertRaises(LaserConnectionError):
            laser_percent_from_config({"laser": {"percent": 101}})


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

        with self.assertRaises(StageConnectionError):
            stage_settings_from_config({"stage": {"move_velocity_mm_s": "nan"}})

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

    def test_stage_home_uses_non_blocking_command_and_can_be_cancelled(self) -> None:
        stage = ZaberXYStage(
            StageSettings(
                serial_port="COM_TEST",
                x=AxisAddress(0, 1, True),
                y=AxisAddress(0, 2, False),
            )
        )
        x_axis = _FakeAxis(homed=False, stays_busy_until_stopped=True)
        stage.x_axis = x_axis  # type: ignore[assignment]

        with self.assertRaises(StageMoveCancelled) as context:
            stage.home(cancel_requested=lambda: True)

        self.assertEqual(x_axis.home_wait_until_idle_values, [False])
        self.assertEqual(x_axis.stop_calls, 1)
        self.assertEqual(context.exception.user_message, "사용자 중지 요청으로 스테이지 이동을 취소했습니다.")

    def test_stage_cancel_busy_wait_has_a_deadline(self) -> None:
        from linear_stage_control import stage as stage_module

        stage = ZaberXYStage(
            StageSettings(
                serial_port="COM_TEST",
                x=AxisAddress(0, 1, True),
                y=AxisAddress(0, 2, False),
            )
        )
        x_axis = _FakeAxis(
            stays_busy_until_stopped=True,
            stays_busy_after_stop=True,
        )
        stage.x_axis = x_axis  # type: ignore[assignment]
        original_timeout_s = stage_module.STAGE_STOP_WAIT_TIMEOUT_S
        stage_module.STAGE_STOP_WAIT_TIMEOUT_S = 0.01
        try:
            with self.assertRaises(StageMoveCancelled) as context:
                stage.move_absolute_mm(1.0, 0.0, cancel_requested=lambda: True)
        finally:
            stage_module.STAGE_STOP_WAIT_TIMEOUT_S = original_timeout_s

        self.assertEqual(x_axis.stop_calls, 1)
        self.assertIn("remained busy", context.exception.developer_message)
        self.assertEqual(context.exception.user_message, "사용자 중지 요청으로 스테이지 이동을 취소했습니다.")

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

    def test_stage_rejects_out_of_bounds_move_before_command(self) -> None:
        stage = ZaberXYStage(
            StageSettings(
                serial_port="COM_TEST",
                x=AxisAddress(0, 1, True),
                y=AxisAddress(0, 2, False),
            )
        )
        x_axis = _FakeAxis()
        stage.x_axis = x_axis  # type: ignore[assignment]

        with self.assertRaises(StageConnectionError):
            stage.move_absolute_mm(-0.001, 0.0)
        with self.assertRaises(StageConnectionError):
            stage.move_absolute_mm(float("inf"), 0.0)
        with self.assertRaises(StageConnectionError):
            stage.move_absolute_mm(1.0, 0.0, velocity_mm_s=0.0)

        self.assertEqual(x_axis.move_calls, 0)

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

    def test_stage_stop_attempts_every_axis_when_one_stop_fails(self) -> None:
        stage = ZaberXYStage(StageSettings(serial_port="COM_TEST"))
        x_axis = _FakeAxis(stop_exception=RuntimeError("x stop failed"))
        y_axis = _FakeAxis()
        stage.x_axis = x_axis  # type: ignore[assignment]
        stage.y_axis = y_axis  # type: ignore[assignment]

        with self.assertRaises(StageConnectionError):
            stage.stop(wait_until_idle=False)

        self.assertEqual(x_axis.stop_calls, 1)
        self.assertEqual(y_axis.stop_calls, 1)


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

    def test_project_package_and_changelog_versions_match(self) -> None:
        from linear_stage_control import __version__

        root = Path(__file__).resolve().parents[1]
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        project_version = project["project"]["version"]
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")

        self.assertEqual(__version__, project_version)
        self.assertIn(f"## v{project_version} - ", changelog)

    def test_build_tools_are_not_runtime_dependencies(self) -> None:
        root = Path(__file__).resolve().parents[1]
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        runtime_dependencies = [str(item).lower() for item in project["dependencies"]]
        build_dependencies = [str(item).lower() for item in project["optional-dependencies"]["build"]]
        build_script = (root / "packaging" / "build_windows.ps1").read_text(encoding="utf-8")

        self.assertFalse(any(item.startswith("pyinstaller") for item in runtime_dependencies))
        self.assertTrue(any(item.startswith("pyinstaller") for item in build_dependencies))
        self.assertIn('-e ".[build]"', build_script)
        self.assertNotIn('"--collect-all", "scipy"', build_script)
        self.assertIn('"--hidden-import", "scipy._external.array_api_compat.numpy.fft"', build_script)
        self.assertIn("source_fingerprint", build_script)
        portable_script = (root / "packaging" / "build_portable_release.ps1").read_text(encoding="utf-8")
        self.assertIn("Get-SourceFingerprint", portable_script)
        self.assertIn("source fingerprint does not match", portable_script)


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

    def test_live_capture_saves_visible_preview_pixmap(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            window = MainWindow(start_device_scan=False)
            window.output_root_edit.setText(directory)
            window.preview_mode = "live"
            window.preview_zoom_slider.setValue(200)
            window.preview_grid_check.setChecked(True)
            window.preview_cross_check.setChecked(True)
            window.on_live_frame(np.arange(48 * 64, dtype=np.uint16).reshape(48, 64), {"live_fps": 10})
            app.processEvents()
            saved_size = window.preview_label.pixmap().size()

            window.capture_live_preview()
            app.processEvents()

            files = list((Path(directory) / "live_captures").glob("live_*.png"))
            self.assertEqual(len(files), 1)
            self.assertTrue(files[0].exists())
            self.assertEqual(window.preview_mode, "live")
            self.assertEqual(window.captures_table.rowCount(), 1)
            loaded = QPixmap(str(files[0]))
            self.assertFalse(loaded.isNull())
            self.assertEqual(loaded.size(), saved_size)
            window.close()

    def test_capture_results_model_keeps_recent_rows_for_large_run(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        base_record = {
            "status": "ok",
            "capture_index": 1,
            "capture_count": 1,
            "target_x_mm": 1.0,
            "target_y_mm": 2.0,
            "actual_x_mm": 1.0,
            "actual_y_mm": 2.0,
            "measured_radial_error_um": 0.1,
            "predicted_min_error_um": 0.0,
            "predicted_max_error_um": 0.2,
            "within_error_threshold": True,
            "max_allowed_error_um": 10.0,
            "configured_error_budget_um": 5.0,
            "image_path": "",
            "absolute_image_path": "",
        }

        for index in range(20_000):
            record = dict(base_record)
            record["index"] = index
            record["label"] = f"point_{index:06d}"
            record["unused_camera_payload"] = "not retained by the UI"
            window._queue_capture_for_ui(record, add_stats=True)
        self.assertEqual(len(window.pending_capture_rows), 1000)
        window.flush_capture_ui_updates()
        app.processEvents()

        self.assertLessEqual(window.captures_table.rowCount(), 1000)
        self.assertEqual(
            (window.capture_results_model.record_at(window.captures_table.rowCount() - 1) or {})["index"],
            19_999,
        )
        self.assertEqual(window.run_stats.record_count, 20_000)
        self.assertNotIn(
            "unused_camera_payload",
            window.capture_results_model.record_at(window.captures_table.rowCount() - 1) or {},
        )
        window.close()

    def test_acquisition_worker_coalesces_capture_status_and_large_progress_updates(self) -> None:
        from linear_stage_control.gui_workers import AcquisitionWorker

        QApplication.instance() or QApplication([])
        worker = AcquisitionWorker({}, [], Path("config.yaml"), "", False)
        capture_wakes: list[bool] = []
        status_wakes: list[bool] = []
        worker.capture_updates_available.connect(lambda: capture_wakes.append(True))
        worker.status_available.connect(lambda: status_wakes.append(True))
        total = 25_000_000_000

        for index in range(20_000):
            worker._queue_capture_update(
                {
                    "status": "ok",
                    "index": index,
                    "predicted_max_error_um": 1.5,
                    "within_error_threshold": True,
                },
                progress=(index + 1, total),
            )
            worker._queue_status(f"capture {index}")

        records, stats, progress = worker.take_capture_updates()
        self.assertEqual(capture_wakes, [True])
        self.assertEqual(status_wakes, [True])
        self.assertEqual(len(records), 1000)
        self.assertEqual(records[0]["index"], 19_000)
        self.assertEqual(records[-1]["index"], 19_999)
        self.assertEqual(stats.record_count, 20_000)
        self.assertEqual(stats.predicted_max_error_um_count, 20_000)
        self.assertEqual(progress, (20_000, total))
        self.assertEqual(worker.take_latest_status(), "capture 19999")

        worker._queue_capture_update({"status": "error", "index": 20_000})
        worker._queue_status("next")
        self.assertEqual(len(capture_wakes), 2)
        self.assertEqual(len(status_wakes), 2)
        worker.deleteLater()

    def test_progress_bar_scales_totals_larger_than_qt_int_range(self) -> None:
        from linear_stage_control.gui_app import LARGE_RUN_PROGRESS_SCALE, MainWindow

        QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        total = 25_000_000_000

        window._set_capture_progress(total // 2, total)
        self.assertEqual(window.progress_bar.maximum(), LARGE_RUN_PROGRESS_SCALE)
        self.assertEqual(window.progress_bar.value(), LARGE_RUN_PROGRESS_SCALE // 2)

        window._set_capture_progress(total, total)
        self.assertEqual(window.progress_bar.value(), LARGE_RUN_PROGRESS_SCALE)
        window.close()

    def test_rapid_preview_and_position_events_are_debounced(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        for index in range(200):
            window.resize_preview_by_drag(index % 3 - 1, index % 5 - 2)
            window.schedule_position_feedback()
        QTest.qWait(320)
        app.processEvents()

        self.assertGreaterEqual(window.preview_label.width(), 240)
        self.assertFalse(window.position_feedback_timer.isActive())
        window.close()

    def test_large_position_list_defers_live_validation(self) -> None:
        from linear_stage_control.gui_app import LIVE_POSITION_VALIDATION_LIMIT, MainWindow

        QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        original_row_count = window.positions_model.rowCount
        window.positions_model.rowCount = lambda *_: LIVE_POSITION_VALIDATION_LIMIT + 1  # type: ignore[method-assign]
        window.read_positions_with_validation = lambda: self.fail("large live validation should be deferred")  # type: ignore[method-assign]

        window.refresh_position_feedback()

        self.assertIn("촬영 시작 시", window.position_status_label.text())
        window.positions_model.rowCount = original_row_count  # type: ignore[method-assign]
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

    def test_live_preview_blocks_when_pylon_viewer_is_running(self) -> None:
        from linear_stage_control import gui_app
        from linear_stage_control.gui_app import MainWindow
        from linear_stage_control.process_guard import RunningProcess

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        window.camera_combo.addItem("자동 선택", "")
        window.camera_combo.addItem("Basler test", "123")
        original_warning = gui_app.QMessageBox.warning
        original_processes = gui_app.running_pylon_viewer_processes
        gui_app.QMessageBox.warning = lambda *args, **kwargs: None  # type: ignore[assignment]
        gui_app.running_pylon_viewer_processes = lambda: [RunningProcess("pylonviewer.exe", 200)]  # type: ignore[assignment]
        try:
            window.start_live_preview()
            app.processEvents()

            self.assertIsNone(window.live_worker)
            self.assertEqual(window.live_status_label.text(), "pylon Viewer 실행 중")
            self.assertIn("Viewer를 닫은 뒤", window.preview_label.text())
        finally:
            gui_app.QMessageBox.warning = original_warning
            gui_app.running_pylon_viewer_processes = original_processes
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

    def test_live_restart_does_not_stack_workers_when_stop_is_pending(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        class StuckLiveWorker:
            def __init__(self) -> None:
                self.stop_requested = False

            def isRunning(self) -> bool:
                return True

            def request_stop(self) -> None:
                self.stop_requested = True

            def wait(self, _timeout_ms: int) -> bool:
                return False

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        window.camera_combo.addItem("자동 선택", "")
        window.camera_combo.addItem("Basler test", "123")
        fake_worker = StuckLiveWorker()
        window.live_worker = fake_worker  # type: ignore[assignment]

        window.start_live_preview()
        app.processEvents()

        self.assertIs(window.live_worker, fake_worker)
        self.assertTrue(fake_worker.stop_requested)
        self.assertEqual(window.live_status_label.text(), "이전 Live 정리 중")
        window.live_worker = None
        window.close()

    def test_stale_live_finished_signal_does_not_release_replacement_worker(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        class FakeLiveWorker:
            def __init__(self) -> None:
                self.deleted = False

            def deleteLater(self) -> None:
                self.deleted = True

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        old_worker = FakeLiveWorker()
        replacement_worker = FakeLiveWorker()
        window.live_worker = replacement_worker  # type: ignore[assignment]

        window.on_live_finished(old_worker)  # type: ignore[arg-type]
        app.processEvents()

        self.assertIs(window.live_worker, replacement_worker)
        self.assertTrue(old_worker.deleted)
        self.assertFalse(replacement_worker.deleted)
        window.live_worker = None
        window.close()

    def test_laser_command_coalesces_to_latest_value_and_off_bypasses_delay(self) -> None:
        import time

        from linear_stage_control.gui_app import MainWindow

        QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        started: list[int] = []
        window._start_laser_command = started.append  # type: ignore[method-assign]
        window.laser_worker = object()  # type: ignore[assignment]

        window.send_laser_percent_from_ui(10)
        window.send_laser_percent_from_ui(20)
        self.assertEqual(window.pending_laser_percent, 20)
        self.assertEqual(started, [])

        window.laser_worker = None
        window.flush_pending_laser_command()
        self.assertEqual(started, [20])

        window.last_laser_command_monotonic = time.monotonic()
        window.send_laser_percent_from_ui(0)
        self.assertEqual(started, [20, 0])
        self.assertIsNone(window.pending_laser_percent)
        window.close()

    def test_update_waits_for_worker_cleanup_and_confirmation_before_closing(self) -> None:
        from linear_stage_control import gui_app
        from linear_stage_control.gui_app import MainWindow

        class FinishedUpdateWorker:
            def __init__(self) -> None:
                self.deleted = False

            def deleteLater(self) -> None:
                self.deleted = True

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        worker = FinishedUpdateWorker()
        window.update_download_worker = worker  # type: ignore[assignment]
        events: list[str] = []
        original_information = gui_app.QMessageBox.information
        gui_app.QMessageBox.information = lambda *_args, **_kwargs: events.append("confirmed")  # type: ignore[assignment]
        window._close_for_pending_update = lambda: events.append("close")  # type: ignore[method-assign]
        try:
            window.on_update_download_done("C:/tmp/verified-setup.exe")
            self.assertEqual(events, [])

            window.on_update_download_finished()
            app.processEvents()

            self.assertEqual(events, ["confirmed", "close"])
            self.assertTrue(worker.deleted)
            self.assertIsNone(window.update_download_worker)
        finally:
            gui_app.QMessageBox.information = original_information
            window.pending_update_installer_path = None
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
        self.assertIn("custom", window.live_size_hint_label.text())

        window.live_size_reset_button.click()
        app.processEvents()
        self.assertIsNone(window.preview_user_size)
        self.assertEqual(window.preview_label.minimumHeight(), window.preview_base_size.height())
        self.assertEqual(window.preview_label.width() * 3, window.preview_label.height() * 4)
        window.close()

    def test_acquisition_worker_reference_survives_run_done_until_finished(self) -> None:
        from linear_stage_control.gui_app import MainWindow
        from linear_stage_control.gui_workers import AcquisitionWorker

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        worker = AcquisitionWorker({}, [], Path("config.yaml"), "", False)
        window.worker = worker
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
        from linear_stage_control.gui_workers import AcquisitionWorker

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        worker = AcquisitionWorker({}, [], Path("config.yaml"), "", False)
        window.worker = worker
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

    def test_live_worker_coalesces_pending_frames(self) -> None:
        from linear_stage_control.gui_workers import LivePreviewWorker

        worker = LivePreviewWorker({"camera": {}}, fps=10)
        first = np.zeros((2, 2), dtype=np.uint8)
        second = np.ones((2, 2), dtype=np.uint8)
        third = np.full((2, 2), 2, dtype=np.uint8)

        self.assertTrue(worker._store_latest_frame(first, {"frame": 1}))
        self.assertFalse(worker._store_latest_frame(second, {"frame": 2}))
        frame = worker.take_latest_frame()
        self.assertIsNotNone(frame)
        array, metadata = frame
        np.testing.assert_array_equal(array, second)
        self.assertEqual(metadata["frame"], 2)

        self.assertTrue(worker._store_latest_frame(third, {"frame": 3}))
        frame = worker.take_latest_frame()
        self.assertIsNotNone(frame)
        array, metadata = frame
        np.testing.assert_array_equal(array, third)
        self.assertEqual(metadata["frame"], 3)

    def test_image_write_queue_size_can_disable_async_writes(self) -> None:
        from linear_stage_control.gui_workers import (
            _image_write_queue_max_bytes_from_config,
            _image_write_queue_needs_drain,
            _image_write_queue_size_from_config,
        )

        self.assertEqual(
            _image_write_queue_size_from_config({"dataset": {"async_image_writes": False}}),
            0,
        )
        self.assertEqual(
            _image_write_queue_size_from_config(
                {"dataset": {"async_image_writes": True, "image_write_queue_size": 99}}
            ),
            8,
        )
        self.assertEqual(
            _image_write_queue_max_bytes_from_config({"dataset": {"image_write_queue_max_mib": 128}}),
            128 * 1024 * 1024,
        )
        self.assertEqual(
            _image_write_queue_max_bytes_from_config({"dataset": {"image_write_queue_max_mib": 1}}),
            16 * 1024 * 1024,
        )
        mib = 1024 * 1024
        self.assertFalse(
            _image_write_queue_needs_drain(
                pending_count=0,
                pending_bytes=0,
                next_array_bytes=512 * mib,
                queue_size=2,
                max_bytes=256 * mib,
            )
        )
        self.assertTrue(
            _image_write_queue_needs_drain(
                pending_count=1,
                pending_bytes=200 * mib,
                next_array_bytes=100 * mib,
                queue_size=2,
                max_bytes=256 * mib,
            )
        )
        self.assertTrue(
            _image_write_queue_needs_drain(
                pending_count=2,
                pending_bytes=2 * mib,
                next_array_bytes=1 * mib,
                queue_size=2,
                max_bytes=256 * mib,
            )
        )

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
        self.assertGreaterEqual(window.preview_command_bar.height(), 106)
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
        self.assertEqual(window.laser_percent_spin.minimum(), 0)
        self.assertEqual(window.laser_percent_spin.maximum(), 100)
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
        self.assertFalse(config["camera"]["flip_horizontal"])
        self.assertFalse(config["camera"]["flip_vertical"])
        self.assertEqual(window.capture_log_format_label.text(), "CSV (카메라 파라미터 포함)")
        self.assertNotIn("metadata_formats", config["dataset"])
        self.assertNotIn("summary_formats", config["dataset"])
        self.assertNotIn("write_jsonl", config["dataset"])
        self.assertEqual(config["laser"]["baud_rate"], 9600)
        self.assertEqual(config["laser"]["percent"], 0)

        window.camera_rotate_180_check.setChecked(False)
        window.camera_flip_horizontal_check.setChecked(True)
        window.camera_flip_vertical_check.setChecked(True)
        window.laser_percent_spin.setValue(37)
        config = window.build_config([])
        self.assertFalse(config["camera"]["rotate_180"])
        self.assertTrue(config["camera"]["flip_horizontal"])
        self.assertTrue(config["camera"]["flip_vertical"])
        self.assertEqual(config["laser"]["percent"], 37)

        live_config = window.build_live_config()
        self.assertFalse(live_config["camera"]["rotate_180"])
        self.assertTrue(live_config["camera"]["flip_horizontal"])
        self.assertTrue(live_config["camera"]["flip_vertical"])
        self.assertIs(window.camera_rotate_180_check.parentWidget(), window.live_orientation_bar)
        self.assertIs(window.camera_flip_horizontal_check.parentWidget(), window.live_orientation_bar)
        self.assertIs(window.camera_flip_vertical_check.parentWidget(), window.live_orientation_bar)
        window.close()

    def test_saved_config_keeps_positions_on_disk_but_not_in_window_memory(self) -> None:
        from linear_stage_control import gui_app
        from linear_stage_control.config import load_config
        from linear_stage_control.gui_app import MainWindow

        QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        window.set_positions(
            [
                ScanPoint(index=1, x_mm=1.0, y_mm=2.0),
                ScanPoint(index=2, x_mm=3.0, y_mm=4.0),
            ]
        )
        original_dialog = gui_app.QFileDialog.getSaveFileName
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "saved.yaml"
                gui_app.QFileDialog.getSaveFileName = lambda *_args, **_kwargs: (str(path), "")  # type: ignore[assignment]

                window.save_config_dialog()

                saved = load_config(path)
                self.assertEqual(len(saved["scan"]["positions"]), 2)
                self.assertNotIn("positions", window.config["scan"])
        finally:
            gui_app.QFileDialog.getSaveFileName = original_dialog
            window.close()

    def test_manual_home_moves_to_center_at_fixed_velocity(self) -> None:
        from linear_stage_control import gui_workers

        class FakeStage:
            instances: list[FakeStage] = []

            def __init__(self, _settings: object) -> None:
                self.home_calls = 0
                self.home_cancel_requested: object = None
                self.moves: list[tuple[float, float, float | None]] = []
                FakeStage.instances.append(self)

            def __enter__(self) -> FakeStage:
                return self

            def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
                pass

            def home(self, *, cancel_requested: object = None) -> None:
                self.home_cancel_requested = cancel_requested
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
            self.assertTrue(callable(stage.home_cancel_requested))
            self.assertEqual(stage.moves, [(105.0, 105.0, 50.0)])
            self.assertEqual(positions[-1], (105.0, 105.0))
            self.assertIn("중앙 이동", done_messages[-1])
        finally:
            gui_workers.ZaberXYStage = original_stage

    def test_manual_stage_cancellation_is_not_reported_as_failure(self) -> None:
        from linear_stage_control import gui_workers

        class CancelledStage:
            def __init__(self, _settings: object) -> None:
                return

            def __enter__(self) -> CancelledStage:
                return self

            def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
                return

            def home(self, *, cancel_requested: object = None) -> None:
                _ = cancel_requested
                raise StageMoveCancelled("cancelled")

        original_stage = gui_workers.ZaberXYStage
        gui_workers.ZaberXYStage = CancelledStage  # type: ignore[assignment]
        done_messages: list[str] = []
        failed_messages: list[str] = []
        try:
            worker = gui_workers.ManualStageWorker({"stage": {"serial_port": "COM_TEST"}}, "home")
            worker.action_done.connect(done_messages.append)
            worker.action_failed.connect(failed_messages.append)

            worker.run()

            self.assertEqual(done_messages, ["수동 명령 중지됨"])
            self.assertEqual(failed_messages, [])
        finally:
            gui_workers.ZaberXYStage = original_stage

    def test_manual_stage_action_rejects_second_command_until_cleanup(self) -> None:
        from linear_stage_control import gui_app
        from linear_stage_control.gui_app import MainWindow

        created: list[QThread] = []

        class PendingManualStageWorker(QThread):
            status_changed = Signal(str)
            position_done = Signal(object)
            action_done = Signal(str)
            action_failed = Signal(str)

            def __init__(self, *_args, **_kwargs) -> None:
                super().__init__()
                created.append(self)

            def start(self, priority: QThread.Priority = QThread.InheritPriority) -> None:  # type: ignore[override]
                return

            def request_stop(self) -> None:
                return

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        original_worker = gui_app.ManualStageWorker
        gui_app.ManualStageWorker = PendingManualStageWorker  # type: ignore[assignment]
        try:
            window.start_manual_stage_action("position")
            window.start_manual_stage_action("move", x_mm=1.0, y_mm=1.0)

            self.assertEqual(len(created), 1)
            self.assertIs(window.manual_stage_worker, created[0])
            self.assertEqual(window.manual_stage_status_label.text(), "이전 수동 명령 정리 중")
        finally:
            gui_app.ManualStageWorker = original_worker
            window.manual_stage_worker = None
            window.close()
            app.processEvents()

    def test_close_is_deferred_while_camera_discovery_thread_is_running(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        class StuckCameraWorker:
            def isRunning(self) -> bool:
                return True

            def wait(self, _timeout_ms: int) -> bool:
                return False

        class CloseEvent:
            def __init__(self) -> None:
                self.ignored = False

            def ignore(self) -> None:
                self.ignored = True

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        event = CloseEvent()
        window.camera_scan_worker = StuckCameraWorker()  # type: ignore[assignment]
        window.capture_ui_timer.start()
        window.run_status_timer.start()

        window.closeEvent(event)
        app.processEvents()

        self.assertTrue(event.ignored)
        self.assertTrue(window.capture_ui_timer.isActive())
        self.assertTrue(window.run_status_timer.isActive())
        window.camera_scan_worker = None
        window.close()

    def test_deferred_close_preserves_acquisition_lifecycle_until_finished_signal(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        class StoppedAcquisitionWorker:
            def __init__(self) -> None:
                self.running = True
                self.deleted = False

            def isRunning(self) -> bool:
                return self.running

            def request_stop(self) -> None:
                return

            def wait(self, _timeout_ms: int) -> bool:
                self.running = False
                return True

            def deleteLater(self) -> None:
                self.deleted = True

            def take_latest_status(self) -> str:
                return ""

            def take_capture_updates(self) -> tuple[list[dict[str, object]], object, None]:
                from linear_stage_control.run_stats import RunStatsAccumulator

                return [], RunStatsAccumulator(), None

        class StuckCameraWorker:
            def isRunning(self) -> bool:
                return True

            def wait(self, _timeout_ms: int) -> bool:
                return False

        class CloseEvent:
            def __init__(self) -> None:
                self.ignored = False

            def ignore(self) -> None:
                self.ignored = True

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        worker = StoppedAcquisitionWorker()
        event = CloseEvent()
        window.worker = worker  # type: ignore[assignment]
        window.camera_scan_worker = StuckCameraWorker()  # type: ignore[assignment]

        window.closeEvent(event)  # type: ignore[arg-type]
        app.processEvents()

        self.assertTrue(event.ignored)
        self.assertIs(window.worker, worker)
        self.assertFalse(worker.deleted)

        window.camera_scan_worker = None
        window.on_run_done("C:/tmp/run", True)
        window.on_worker_finished()
        app.processEvents()

        self.assertIsNone(window.worker)
        self.assertTrue(worker.deleted)
        self.assertIsNone(window.pending_run_result)
        window.close()

    def test_closed_fullscreen_viewer_releases_main_window_reference(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "preview.png"
            pixmap = QPixmap(16, 12)
            pixmap.fill(QColor("#2f80ed"))
            self.assertTrue(pixmap.save(str(image_path)))
            window.current_image_path = image_path

            window.open_fullscreen_image()
            viewer = window.image_viewer
            self.assertIsNotNone(viewer)
            assert viewer is not None
            self.assertTrue(viewer.testAttribute(Qt.WA_DeleteOnClose))

            viewer.close()
            app.processEvents()

            self.assertIsNone(window.image_viewer)
            self.assertTrue(viewer.original_pixmap.isNull())
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

    def test_preflight_blocks_start_when_pylon_viewer_is_running(self) -> None:
        from PySide6.QtWidgets import QDialogButtonBox

        from linear_stage_control import gui_app
        from linear_stage_control.gui_app import MainWindow
        from linear_stage_control.process_guard import RunningProcess

        QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        window.camera_combo.addItem("자동 선택", "")
        window.camera_combo.addItem("Basler test", "123")
        points = points_from_records([{"x_mm": 0, "y_mm": 0}])
        config = {
            "camera": {"pixel_format": "Mono8", "exposure_us": 5000},
            "dataset": {"output_root": str(Path(tempfile.gettempdir()) / "LinearStageControl-QC")},
            "scan": {"default_capture_count": 1},
            "stage": {
                "serial_port": "COM_TEST",
                "settle_s": 0.2,
                "axes": {
                    "x": {"enabled": True, "device_index": 0, "axis_number": 1},
                    "y": {"enabled": True, "device_index": 0, "axis_number": 2},
                },
            },
            "updates": {"enabled": False},
        }
        original_processes = gui_app.running_pylon_viewer_processes
        gui_app.running_pylon_viewer_processes = lambda: [RunningProcess("pylonviewer.exe", 200)]  # type: ignore[assignment]
        try:
            issues = window.collect_preflight_issues(points, config, validate_scan_points(points))
            viewer_errors = [issue for issue in issues if issue.item == "pylon Viewer" and issue.status == "오류"]
            self.assertTrue(viewer_errors)

            dialog = window.build_preflight_dialog(points, config, validate_scan_points(points))
            buttons = dialog.findChild(QDialogButtonBox)
            self.assertIsNotNone(buttons)
            self.assertFalse(buttons.button(QDialogButtonBox.Ok).isEnabled())
            dialog.close()
        finally:
            gui_app.running_pylon_viewer_processes = original_processes
            window.close()

    def test_run_duration_estimate_includes_all_planned_phases(self) -> None:
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

        self.assertAlmostEqual(estimate.seconds, 12.68, places=6)
        self.assertEqual(len(estimate.capture_cumulative_s), 2)
        self.assertAlmostEqual(estimate.capture_cumulative_s[-1], estimate.seconds, places=6)
        self.assertIn("준비 2초", estimate.detail)
        self.assertIn("원점 8초", estimate.detail)
        self.assertIn("안정화 0.4초", estimate.detail)
        self.assertIn("위치 0.2초", estimate.detail)
        self.assertIn("저장/UI 0.76초", estimate.detail)
        self.assertNotIn("종료", estimate.detail)

    def test_run_duration_estimate_does_not_allocate_per_capture_for_large_runs(self) -> None:
        from linear_stage_control.gui_app import estimate_run_duration

        point = ScanPoint(index=1, x_mm=1.0, y_mm=1.0, capture_count=50_001)
        config = {
            "camera": {"exposure_us": 1_000},
            "scan": {"default_capture_count": 1},
            "stage": {
                "serial_port": "COM_TEST",
                "home_on_start": False,
                "settle_s": 0.0,
                "move_velocity_mm_s": 10,
            },
        }

        estimate = estimate_run_duration([point], config)

        self.assertEqual(estimate.capture_cumulative_s, ())
        self.assertGreater(estimate.seconds, 0)

    def test_run_timing_display_scales_remaining_from_phase_plan(self) -> None:
        import time

        from linear_stage_control.gui_app import MainWindow

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        window.start_run_timing(2, 20.0, (10.0, 15.0))
        window.run_started_monotonic = time.monotonic() - 5.0
        window.run_completed_captures = 1

        window.update_run_timing_display()
        app.processEvents()

        self.assertIn("남은 5초", window.progress_detail_label.text())
        window.close()

    def test_run_timing_handles_finish_times_outside_platform_range(self) -> None:
        from linear_stage_control import gui_app
        from linear_stage_control.gui_app import MainWindow

        QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        original_localtime = gui_app.time.localtime

        def out_of_range(_timestamp: float) -> object:
            raise OSError(22, "Invalid argument")

        gui_app.time.localtime = out_of_range  # type: ignore[assignment]
        try:
            window.start_run_timing(25_000_000_000, 100_000_000_000.0)
            self.assertIn("계산 범위 초과", window.progress_detail_label.text())
        finally:
            gui_app.time.localtime = original_localtime
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
    def test_dataset_settings_parse_disk_flush_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = dataset_settings_from_config(
                {
                    "dataset": {
                        "output_root": directory,
                        "metadata_flush_records": "25",
                        "metadata_flush_interval_s": "0.5",
                    }
                }
            )

        self.assertEqual(settings.metadata_flush_records, 25)
        self.assertEqual(settings.metadata_flush_interval_s, 0.5)

    def test_legacy_metadata_config_is_ignored_by_csv_only_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = dataset_settings_from_config(
                {
                    "dataset": {
                        "output_root": directory,
                        "write_jsonl": True,
                        "metadata_formats": ["csv", "jsonl", "json", "tsv", "yaml", "xlsx"],
                        "summary_formats": ["json", "yaml", "md"],
                    }
                }
            )

        self.assertFalse(hasattr(settings, "write_jsonl"))
        self.assertFalse(hasattr(settings, "metadata_formats"))
        self.assertFalse(hasattr(settings, "summary_formats"))

    def test_dataset_image_path_uses_decimal_xy_and_collision_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            points = points_from_records(
                [
                    {"label": "sample a", "x_mm": "33.3339", "y_mm": "0", "capture_count": "2"},
                    {"label": "sample b", "x_mm": "33.3339", "y_mm": "0"},
                ]
            )
            settings = DatasetSettings(output_root=Path(directory))

            with DatasetRun(settings, {"scan": {"default_capture_count": 1}}, points, "config.yaml") as dataset:
                first = dataset.image_path(points[0], 1)
                second_capture = dataset.image_path(points[0], 2)
                duplicate_point = dataset.image_path(points[1], 1)

            self.assertEqual(first.name, "X033.333_Y000.000.png")
            self.assertEqual(second_capture.name, "X033.333_Y000.000_C02.png")
            self.assertEqual(duplicate_point.name, "X033.333_Y000.000_P0002.png")

    def test_image_name_plan_keeps_one_entry_per_point_for_large_capture_counts(self) -> None:
        point = ScanPoint(index=1, x_mm=0.5, y_mm=0.0, capture_count=1_000_000)
        dataset = DatasetRun(
            DatasetSettings(output_root=Path(".")),
            {"scan": {"default_capture_count": 1}},
            [point],
            "config.yaml",
        )

        self.assertEqual(dataset.image_name_stems, {1: "X000.500_Y000.000"})
        self.assertEqual(dataset.image_path(point, 1_000_000).name, "X000.500_Y000.000_C1000000.png")

    def test_image_output_plan_allows_fractional_duplicate_and_multi_capture_names(self) -> None:
        points = points_from_records(
            [
                {"x_mm": "0.5", "y_mm": "0", "capture_count": "2"},
                {"x_mm": "0.5", "y_mm": "0"},
            ]
        )
        self.assertEqual(validate_image_output_plan(points), [])

    def test_image_output_plan_rejects_duplicate_point_indexes(self) -> None:
        points = [
            ScanPoint(index=7, x_mm=0.0, y_mm=0.0),
            ScanPoint(index=7, x_mm=1.0, y_mm=1.0),
        ]

        errors = validate_image_output_plan(points)

        self.assertEqual(errors, ["Duplicate point index in image output plan: 7."])

    def test_image_output_plan_rejects_non_finite_coordinates(self) -> None:
        errors = validate_image_output_plan([ScanPoint(index=0, x_mm=float("nan"), y_mm=0.0)])

        self.assertEqual(errors, ["Invalid image coordinate: nan"])

    def test_dataset_open_accepts_fractional_image_output_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            point = points_from_records([{"x_mm": "0.5", "y_mm": "0"}])[0]
            settings = DatasetSettings(output_root=Path(directory))

            with DatasetRun(settings, {"scan": {"default_capture_count": 1}}, [point], "config.yaml") as dataset:
                image_path = dataset.image_path(point, 1)

            self.assertEqual(image_path.name, "X000.500_Y000.000.png")

    def test_dataset_manifest_includes_version_record_count_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            point = points_from_records([{"label": "sample a", "x_mm": "0", "y_mm": "0"}])[0]
            settings = DatasetSettings(output_root=Path(directory), manifest_detail="full")

            with DatasetRun(settings, {"dataset": {}}, [point], "config.yaml") as dataset:
                image_path = dataset.image_path(point, 1)
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
            legacy_manifest = json.loads(legacy_manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(legacy_manifest, manifest)
            self.assertEqual(manifest["record_count"], 1)
            self.assertIn("app_version", manifest)
            image_entries = [entry for entry in manifest["files"] if entry["role"] == "image"]
            self.assertEqual(len(image_entries), 1)
            self.assertEqual(image_entries[0]["path"], f"images/{image_path.name}")
            self.assertEqual(image_entries[0]["sha256"], sha256_file(image_path))

    def test_dataset_streams_records_without_accumulating_default_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            points = points_from_records(
                [{"label": f"sample {index}", "x_mm": str(index / 1000), "y_mm": "0"} for index in range(50)]
            )
            settings = DatasetSettings(output_root=Path(directory))

            with DatasetRun(settings, {"scan": {"default_capture_count": 1}}, points, "config.yaml") as dataset:
                for point in points:
                    record = base_capture_record(dataset.run_id, point)
                    record.update({"status": "ok", "capture_index": 1, "capture_count": 1})
                    dataset.write_capture(record)
                self.assertEqual(len(dataset.image_name_stems), len(points))
                self.assertEqual(dataset.record_count, 50)
                run_dir = dataset.run_dir

            manifest = json.loads((run_dir / "dataset_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["record_count"], 50)
            self.assertNotIn("files", manifest)
            self.assertEqual(manifest["metadata_format"], "csv")
            self.assertEqual(manifest["metadata_files"], {"csv": "captures.csv"})
            self.assertEqual(manifest["summary_files"], {})
            self.assertFalse((run_dir / "summary.json").exists())

    def test_capture_csv_includes_camera_parameters_used_for_the_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            point = points_from_records([{"label": "sample", "x_mm": "1", "y_mm": "2"}])[0]
            settings = DatasetSettings(output_root=Path(directory))

            with DatasetRun(settings, {"scan": {"default_capture_count": 1}}, [point], "config.yaml") as dataset:
                record = base_capture_record(dataset.run_id, point)
                record.update(
                    {
                        "status": "ok",
                        "capture_index": 1,
                        "capture_count": 1,
                        "camera_pixel_format": "Mono12",
                        "camera_exposure_us": 4321.5,
                        "camera_gain": 2.25,
                        "camera_width_px": 1920,
                        "camera_height_px": 1200,
                        "camera_trigger_source": "Software",
                    }
                )
                dataset.write_capture(record)
                csv_path = dataset.csv_path

            with csv_path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)

            self.assertEqual(len(rows), 1)
            self.assertTrue(set(CAMERA_CAPTURE_PARAMETER_FIELDS).issubset(reader.fieldnames or []))
            self.assertEqual(rows[0]["camera_pixel_format"], "Mono12")
            self.assertEqual(rows[0]["camera_exposure_us"], "4321.5")
            self.assertEqual(rows[0]["camera_gain"], "2.25")
            self.assertEqual(rows[0]["camera_width_px"], "1920")
            self.assertEqual(rows[0]["camera_height_px"], "1200")
            self.assertEqual(rows[0]["camera_trigger_source"], "Software")

    def test_timing_fields_remain_in_capture_csv_schema(self) -> None:
        from linear_stage_control.dataset import CAPTURE_FIELDS

        point = points_from_records([{"x_mm": 0, "y_mm": 0}])[0]
        record = base_capture_record("run", point)
        timing_fields = [
            "move_duration_ms",
            "settle_duration_ms",
            "capture_duration_ms",
            "disk_write_duration_ms",
        ]

        for field in timing_fields:
            self.assertIn(field, CAPTURE_FIELDS)
            self.assertIn(field, record)

    def test_run_stats_keeps_only_constant_memory_gui_error_summary_values(self) -> None:
        from linear_stage_control.run_stats import RunStatsAccumulator

        stats = RunStatsAccumulator()
        stats.add_record(
            {
                "status": "ok",
                "predicted_max_error_um": 12.5,
                "within_error_threshold": False,
            }
        )
        stats.add_record({"status": "error", "error_message": "not retained"})

        self.assertEqual(stats.record_count, 2)
        self.assertEqual(stats.predicted_max_error_um_count, 1)
        self.assertEqual(stats.predicted_max_error_um_sum, 12.5)
        self.assertEqual(stats.predicted_max_error_um_max, 12.5)
        self.assertEqual(stats.threshold_failure_count, 1)
        self.assertFalse(hasattr(stats, "error_messages"))

    def test_legacy_export_requests_still_produce_only_capture_csv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            points = points_from_records(
                [{"label": f"sample {index}", "x_mm": str(index / 1000), "y_mm": "0"} for index in range(3)]
            )
            config = {
                "dataset": {
                    "write_jsonl": True,
                    "metadata_formats": ["csv", "jsonl", "json", "tsv", "yaml", "xlsx"],
                    "summary_formats": ["json", "yaml", "md"],
                },
                "scan": {
                    "default_capture_count": 1,
                    "estimated_export_overhead_s": 0.5,
                    "estimated_export_per_capture_s": 0.02,
                },
            }
            settings = dataset_settings_from_config(config, output_override=directory)

            with DatasetRun(settings, config, points, "config.yaml") as dataset:
                for point in points:
                    record = base_capture_record(dataset.run_id, point)
                    record.update({"status": "ok", "capture_index": 1, "capture_count": 1})
                    dataset.write_capture(record)
                run_dir = dataset.run_dir

            with (run_dir / "captures.csv").open(newline="", encoding="utf-8") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 3)
            self.assertFalse(hasattr(settings, "write_jsonl"))
            self.assertFalse(hasattr(settings, "metadata_formats"))
            self.assertFalse(hasattr(settings, "summary_formats"))
            config_snapshot = (run_dir / "config.yaml").read_text(encoding="utf-8")
            self.assertNotIn("metadata_formats", config_snapshot)
            self.assertNotIn("summary_formats", config_snapshot)
            self.assertNotIn("write_jsonl", config_snapshot)
            self.assertNotIn("estimated_export_", config_snapshot)
            for filename in (
                "captures.jsonl",
                "captures.json",
                "captures.tsv",
                "captures.yaml",
                "captures.xlsx",
                "summary.json",
                "summary.yaml",
                "summary.md",
            ):
                self.assertFalse((run_dir / filename).exists(), filename)


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
        self.assertIn("pylon Viewer", items)
        self.assertIn("Laser RS485 COM", items)
        self.assertIn("Zaber COM 포트", items)
        self.assertIn("저장 폴더 권한", items)


if __name__ == "__main__":
    unittest.main()
