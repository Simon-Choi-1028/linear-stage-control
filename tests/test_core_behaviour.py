from __future__ import annotations

import unittest

from linear_stage_control.camera import BaslerCamera, camera_settings_from_config
from linear_stage_control.dataset_exports import (
    DEFAULT_METADATA_FORMATS,
    SUPPORTED_METADATA_FORMATS,
    normalise_formats,
)
from linear_stage_control.scan import linear_path_points_by_spacing, points_from_records


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


if __name__ == "__main__":
    unittest.main()
