from __future__ import annotations

import os
import unittest

import numpy as np
from PySide6.QtWidgets import QApplication

from linear_stage_control.camera import PYLON_IMPORT_ERROR, BaslerCamera, camera_settings_from_config
from linear_stage_control.dataset_exports import (
    DEFAULT_METADATA_FORMATS,
    SUPPORTED_METADATA_FORMATS,
    normalise_formats,
)
from linear_stage_control.error_model import ErrorBudgetSettings, estimate_position_error_um
from linear_stage_control.position_validation import disabled_axis_variation_errors
from linear_stage_control.scan import linear_path_points_by_spacing, points_from_records
from linear_stage_control.stage import stage_settings_from_config
from linear_stage_control.updater import is_newer_version, sha256_file, verify_file_sha256

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


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

    def test_disabled_axis_must_remain_constant(self) -> None:
        points = points_from_records(
            [
                {"x_mm": 0, "y_mm": 0},
                {"x_mm": 1, "y_mm": 0},
            ]
        )

        self.assertTrue(disabled_axis_variation_errors(points, x_active=False, y_active=True))
        self.assertFalse(disabled_axis_variation_errors(points, x_active=True, y_active=False))


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
        window.on_live_frame(np.zeros((24, 32), dtype=np.uint8), {})
        app.processEvents()

        self.assertFalse(window.preview_label.pixmap().isNull())
        window.close()


if __name__ == "__main__":
    unittest.main()
