from __future__ import annotations

import os
import unittest

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from linear_stage_control.camera import PYLON_IMPORT_ERROR, BaslerCamera, camera_settings_from_config
from linear_stage_control.dataset import point_name
from linear_stage_control.dataset_exports import (
    DEFAULT_METADATA_FORMATS,
    SUPPORTED_METADATA_FORMATS,
    normalise_formats,
)
from linear_stage_control.error_model import ErrorBudgetSettings, estimate_position_error_um
from linear_stage_control.position_validation import disabled_axis_variation_errors
from linear_stage_control.scan import linear_path_points_by_spacing, points_from_records
from linear_stage_control.stage import configure_zaber_device_database, stage_settings_from_config
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

    def test_live_preview_size_slider_changes_preview_height(self) -> None:
        from linear_stage_control.gui_app import MainWindow

        app = QApplication.instance() or QApplication([])
        window = MainWindow(start_device_scan=False)
        base_height = window.preview_label.minimumHeight()
        window.live_size_slider.setValue(150)
        app.processEvents()

        self.assertEqual(window.live_size_label.text(), "150%")
        self.assertGreater(window.preview_label.minimumHeight(), base_height)

        window.live_size_reset_button.click()
        app.processEvents()
        self.assertEqual(window.live_size_label.text(), "100%")
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
            image.pixelColor(x, y).name()
            for x in range(0, image.width(), 40)
            for y in range(0, image.height(), 40)
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


if __name__ == "__main__":
    unittest.main()
