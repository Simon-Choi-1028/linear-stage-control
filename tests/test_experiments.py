from __future__ import annotations

import csv
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _wait_for(condition, app: QApplication, timeout_ms: int = 3000) -> bool:
    elapsed = 0
    while elapsed < timeout_ms:
        app.processEvents()
        if condition():
            return True
        QTest.qWait(50)
        elapsed += 50
    return False


class ExperimentProcessingTests(unittest.TestCase):
    def test_large_linear_path_preview_is_sampled_without_materializing_all_points(self) -> None:
        from linear_stage_control.linear_path_dialog import (
            LINEAR_PATH_PREVIEW_MAX_POINTS,
            _count_path_preview,
            _spacing_path_preview,
        )

        count_points, count = _count_path_preview(
            x_start=0.0,
            y_start=0.0,
            x_stop=210.0,
            y_stop=105.0,
            count=250_000,
        )
        self.assertEqual(count, 250_000)
        self.assertEqual(len(count_points), LINEAR_PATH_PREVIEW_MAX_POINTS)
        self.assertEqual(count_points[0], (0.0, 0.0))
        self.assertEqual(count_points[-1], (210.0, 105.0))

        spacing_points, spacing_count = _spacing_path_preview(
            x_start=0.0,
            y_start=0.0,
            x_stop=210.0,
            y_stop=0.0,
            spacing_mm=0.001,
        )
        self.assertEqual(spacing_count, 210_001)
        self.assertEqual(len(spacing_points), LINEAR_PATH_PREVIEW_MAX_POINTS)
        self.assertEqual(spacing_points[0], (0.0, 0.0))
        self.assertEqual(spacing_points[-1], (210.0, 0.0))

    def test_spacing_preview_count_matches_generator_at_rounding_boundary(self) -> None:
        from linear_stage_control.linear_path_dialog import _spacing_path_preview
        from linear_stage_control.scan import linear_path_points_by_spacing

        length = 97.24465319429918
        spacing = 0.0004795739728333083

        _preview_points, preview_count = _spacing_path_preview(
            x_start=0.0,
            y_start=0.0,
            x_stop=length,
            y_stop=0.0,
            spacing_mm=spacing,
        )
        generated_count = sum(
            1
            for _point in linear_path_points_by_spacing(
                x_start=0.0,
                y_start=0.0,
                x_stop=length,
                y_stop=0.0,
                spacing_mm=spacing,
            )
        )

        self.assertEqual(preview_count, 202_775)
        self.assertEqual(generated_count, preview_count)

    def test_fullscreen_scale_respects_render_pixel_budget(self) -> None:
        from linear_stage_control.gui_widgets import (
            MAX_FULLSCREEN_RENDER_PIXELS,
            _bounded_fullscreen_size,
        )

        width, height, scale = _bounded_fullscreen_size(4000, 3000, 20.0)
        self.assertLessEqual(width * height, MAX_FULLSCREEN_RENDER_PIXELS)
        self.assertLess(scale, 20.0)
        self.assertAlmostEqual(width / height, 4 / 3, places=3)

    def test_frame_source_validation_rejects_unsupported_or_huge_frames(self) -> None:
        from linear_stage_control.experiments.frame_sources import MAX_FRAME_PIXELS, validate_frame_array

        with self.assertRaises(ValueError):
            validate_frame_array(np.zeros((8, 8, 2), dtype=np.uint8), source_name="bad")
        with self.assertRaises(TypeError):
            validate_frame_array(np.array([[object()]], dtype=object), source_name="bad")

        backing = np.zeros((1,), dtype=np.uint8)
        huge = np.lib.stride_tricks.as_strided(backing, shape=(MAX_FRAME_PIXELS + 1, 1), strides=(0, 0))
        with self.assertRaises(MemoryError):
            validate_frame_array(huge, source_name="huge")

    def test_fwhm_synthetic_gaussian_profile(self) -> None:
        from linear_stage_control.experiments.fwhm_processing import calculate_for_columns, make_synthetic_fwhm_frame

        frame = make_synthetic_fwhm_frame(width=640, height=480, sigma_px=3.0, noise_sigma=0.0, phase=0.0)
        result = calculate_for_columns(frame, [320], (0, 639, 120, 360))[0]
        self.assertEqual(result.status, "OK")
        self.assertIsNotNone(result.fwhm_px)
        self.assertAlmostEqual(result.fwhm_px or 0.0, 2.355 * 3.0, delta=0.5)

    def test_fwhm_error_statuses(self) -> None:
        from linear_stage_control.experiments.fwhm_processing import calculate_for_columns

        frame = np.zeros((20, 20), dtype=np.uint8)
        self.assertEqual(calculate_for_columns(frame, [5], (0, 19, 0, 19))[0].status, "LOW_SIGNAL")
        self.assertEqual(calculate_for_columns(frame, [5], (0, 19, 0, 2))[0].status, "ROI_TOO_SMALL")
        self.assertEqual(calculate_for_columns(frame, [15], (0, 9, 0, 19))[0].status, "ROI_X_RANGE")

    def test_fwhm_average_excludes_saturated_results(self) -> None:
        from linear_stage_control.experiments.fwhm_processing import FwhmResult, average_valid_fwhm

        results = [
            FwhmResult(1, 0, 10, 120.0, False, 2.0, 4.71, 5.0, "OK"),
            FwhmResult(2, 0, 10, 255.0, True, 100.0, 235.5, 5.0, "SAT"),
        ]

        self.assertAlmostEqual(average_valid_fwhm(results) or 0.0, 4.71)

    def test_alignment_synthetic_angles(self) -> None:
        from linear_stage_control.experiments.alignment_processing import (
            ProcessingSettings,
            make_synthetic_frame,
            process_laser_line,
        )

        settings = ProcessingSettings(threshold=40, auto_threshold=False, angle_tolerance_deg=0.1)
        roi = (0, 180, 1280, 560)
        for angle in (-2.0, -0.5, 0.0, 1.25, 2.0):
            frame = make_synthetic_frame(width=1280, height=720, angle_deg=angle, noise_sigma=0.0)
            result = process_laser_line(frame, roi, settings)
            self.assertTrue(result.ok, result.message)
            self.assertIsNotNone(result.angle_deg)
            self.assertAlmostEqual(result.angle_deg or 0.0, angle, delta=0.06)

    def test_vp_synthetic_target(self) -> None:
        from linear_stage_control.experiments.vp_processing import (
            VPSettings,
            detect_virtual_point,
            make_synthetic_v_frame,
        )

        frame = make_synthetic_v_frame(width=1280, height=1024, vertex=(640.0, 500.0), noise_sigma=0.0)
        result = detect_virtual_point(
            frame,
            (0, 0, 1280, 1024),
            VPSettings(threshold=40, min_arm_points=40, max_fit_rms_px=2.0),
        )
        self.assertTrue(result.ok, result.message)
        self.assertIsNotNone(result.vp)
        self.assertAlmostEqual((result.vp or (0.0, 0.0))[0], 640.0, delta=2.0)
        self.assertAlmostEqual((result.vp or (0.0, 0.0))[1], 500.0, delta=2.0)


class ExperimentGuiTests(unittest.TestCase):
    def test_live_worker_applies_frame_backpressure(self) -> None:
        from linear_stage_control.experiments.base import ExperimentLiveWorker, ProcessedFrame
        from linear_stage_control.experiments.frame_sources import FrameSource

        app = QApplication.instance() or QApplication([])
        processed_count = 0

        class FastSource(FrameSource):
            name = "fast synthetic"

            def read(self) -> np.ndarray:
                return np.zeros((32, 32, 3), dtype=np.uint8)

        def process(frame: np.ndarray, _state: dict) -> ProcessedFrame:
            nonlocal processed_count
            processed_count += 1
            return ProcessedFrame(frame, {"ok": True}, (0, 0, 32, 32))

        worker = ExperimentLiveWorker(lambda: FastSource(), process, {}, interval_ms=10)
        frames: list[object] = []
        worker.frame_ready.connect(lambda *_args: frames.append(object()))
        worker.start()
        try:
            self.assertTrue(_wait_for(lambda: len(frames) == 1, app, timeout_ms=1000))
            QTest.qWait(250)
            app.processEvents()
            self.assertEqual(len(frames), 1)
            self.assertEqual(processed_count, 1)
        finally:
            worker.request_stop()
            self.assertTrue(worker.wait(1000))
            worker.deleteLater()

    def test_stop_source_timeout_keeps_running_worker_reference(self) -> None:
        from linear_stage_control.experiments.fwhm_window import FwhmWindow

        class StuckWorker:
            def __init__(self) -> None:
                self.stop_requested = False
                self.deleted = False

            def request_stop(self) -> None:
                self.stop_requested = True

            def wait(self, _timeout_ms: int) -> bool:
                return False

            def isRunning(self) -> bool:
                return True

            def deleteLater(self) -> None:
                self.deleted = True

        app = QApplication.instance() or QApplication([])
        window = FwhmWindow()
        window.stop_source(wait_ms=1500)
        fake_worker = StuckWorker()
        window.worker = fake_worker  # type: ignore[assignment]
        try:
            self.assertFalse(window.stop_source(wait_ms=1))
            self.assertIs(window.worker, fake_worker)
            self.assertTrue(fake_worker.stop_requested)
            self.assertFalse(fake_worker.deleted)
            window.start_synthetic_source()
            self.assertIs(window.worker, fake_worker)
        finally:
            window.worker = None
            window.close()
            app.processEvents()

    def test_processing_updates_are_coalesced_under_rapid_input(self) -> None:
        from linear_stage_control.experiments.fwhm_window import FwhmWindow

        class FakeWorker:
            def __init__(self) -> None:
                self.requests: list[dict] = []

            def request_processing_state(self, state: dict) -> None:
                self.requests.append(state)

        app = QApplication.instance() or QApplication([])
        window = FwhmWindow()
        window.stop_source(wait_ms=1500)
        fake_worker = FakeWorker()
        window.worker = fake_worker  # type: ignore[assignment]
        try:
            for value in range(25):
                window.columns_edit.setText(str(300 + value))
                window.request_processing_update()
            self.assertTrue(_wait_for(lambda: len(fake_worker.requests) == 1, app, timeout_ms=1000))
            self.assertEqual(fake_worker.requests[0]["columns"], [324])
        finally:
            window.worker = None
            window.close()
            app.processEvents()

    def test_fwhm_column_input_rejects_invalid_or_fractional_tokens(self) -> None:
        from linear_stage_control.experiments.fwhm_window import FwhmWindow

        app = QApplication.instance() or QApplication([])
        window = FwhmWindow()
        window.stop_source(wait_ms=1500)
        try:
            window.columns_edit.setText("320 bad")
            with self.assertRaises(ValueError):
                window.processing_state()
            window.columns_edit.setText("320.5")
            with self.assertRaises(ValueError):
                window.processing_state()
        finally:
            window.close()
            app.processEvents()

    def test_stale_processed_frame_does_not_replace_current_measurement(self) -> None:
        from linear_stage_control.experiments.fwhm_window import FwhmWindow

        class FakeWorker:
            consumed = False

            def mark_frame_consumed(self) -> None:
                self.consumed = True

        app = QApplication.instance() or QApplication([])
        window = FwhmWindow()
        window.stop_source(wait_ms=1500)
        fake_worker = FakeWorker()
        window.worker = fake_worker  # type: ignore[assignment]
        window._processing_state_version = 3
        window._invalidate_latest_measurement("test")
        try:
            window._on_frame_ready_from_worker(
                fake_worker,  # type: ignore[arg-type]
                np.zeros((32, 32, 3), dtype=np.uint8),
                [],
                "old source",
                (0, 0, 32, 32),
                1,
                2,
                "2026-07-06T00:00:00.000+09:00",
            )
            self.assertTrue(fake_worker.consumed)
            self.assertIsNone(window.latest_measurement)
        finally:
            window.worker = None
            window.close()
            app.processEvents()

    def test_csv_rows_use_latest_measurement_context(self) -> None:
        from linear_stage_control.experiments.fwhm_window import FwhmWindow

        app = QApplication.instance() or QApplication([])
        window = FwhmWindow()
        try:
            self.assertTrue(_wait_for(lambda: window.latest_measurement is not None, app))
            measurement = window.latest_measurement
            self.assertIsNotNone(measurement)
            rows = window.csv_rows(measurement.result)  # type: ignore[union-attr]
            self.assertTrue(rows)
            self.assertEqual(rows[0]["source"], measurement.source_name)  # type: ignore[union-attr]
            self.assertEqual(rows[0]["timestamp"], measurement.timestamp)  # type: ignore[union-attr]
            self.assertEqual(rows[0]["roi_right"], measurement.roi[2] - 1)  # type: ignore[union-attr]
        finally:
            window.close()
            app.processEvents()

    def test_save_csv_rejects_empty_measurement_rows(self) -> None:
        from linear_stage_control.experiments.result_io import save_csv

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                save_csv(Path(directory) / "empty.csv", ["timestamp"], [])

    def test_manual_stage_rejects_second_command_until_worker_cleaned(self) -> None:
        import linear_stage_control.experiments.manual_stage_panel as panel_module

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

        original = panel_module.ManualStageWorker
        panel_module.ManualStageWorker = PendingManualStageWorker
        try:
            from linear_stage_control.experiments.manual_stage_panel import ManualStagePanel

            app = QApplication.instance() or QApplication([])
            panel = ManualStagePanel(lambda: {"stage": {"serial_port": "COM_FAKE"}})
            panel.start_action("position")
            panel.start_action("move", x_mm=1.0, y_mm=1.0)
            self.assertEqual(len(created), 1)
            self.assertIs(panel.worker, created[0])
            panel.close()
            app.processEvents()
        finally:
            panel_module.ManualStageWorker = original

    def test_launcher_locks_cards_while_window_active(self) -> None:
        from linear_stage_control.experiments.fwhm_window import FwhmWindow
        from linear_stage_control.experiments.launcher import ExperimentLauncherWindow

        app = QApplication.instance() or QApplication([])
        launcher = ExperimentLauncherWindow()
        launcher.open_experiment(FwhmWindow)
        app.processEvents()
        self.assertIsNotNone(launcher.active_window)
        self.assertTrue(all(not button.isEnabled() for button in launcher.card_buttons))
        launcher.active_window.close()
        app.processEvents()
        QTest.qWait(100)
        app.processEvents()
        self.assertIsNone(launcher.active_window)
        self.assertTrue(all(button.isEnabled() for button in launcher.card_buttons))
        launcher.close()

    def test_experiment_windows_render_synthetic_preview(self) -> None:
        from linear_stage_control.experiments.alignment_window import AlignmentWindow
        from linear_stage_control.experiments.fwhm_window import FwhmWindow
        from linear_stage_control.experiments.vp_window import VPWindow

        app = QApplication.instance() or QApplication([])
        for window_class in (FwhmWindow, AlignmentWindow, VPWindow):
            window = window_class()
            self.assertTrue(_wait_for(lambda window=window: window.latest_measurement is not None, app))
            self.assertIsNotNone(window.latest_measurement)
            self.assertIsNotNone(window.latest_measurement.overlay_bgr)
            self.assertIsNotNone(window.latest_measurement.result)
            self.assertGreater(window.result_table.rowCount(), 0)
            self.assertFalse(window.preview_label.pixmap().isNull())
            window.close()
            app.processEvents()

    def test_result_csv_fields_are_written(self) -> None:
        from linear_stage_control.experiments.fwhm_window import FwhmWindow
        from linear_stage_control.experiments.result_io import save_csv

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            window = FwhmWindow()
            self.assertTrue(_wait_for(lambda: window.latest_measurement is not None, app))
            measurement = window.latest_measurement
            self.assertIsNotNone(measurement)
            path = Path(directory) / "fwhm.csv"
            save_csv(path, window.csv_fieldnames, window.csv_rows(measurement.result))  # type: ignore[union-attr]
            with path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertTrue(rows)
            self.assertIn("average_valid_fwhm_px", rows[0])
            self.assertIn("fwhm_px", rows[0])
            window.close()

    def test_manual_stage_panel_uses_worker_signals(self) -> None:
        import linear_stage_control.experiments.manual_stage_panel as panel_module

        class FakeManualStageWorker(QThread):
            status_changed = Signal(str)
            position_done = Signal(object)
            action_done = Signal(str)
            action_failed = Signal(str)

            def __init__(self, *_args, **_kwargs) -> None:
                super().__init__()
                self.stop_requested = False

            def request_stop(self) -> None:
                self.stop_requested = True

            def run(self) -> None:
                self.status_changed.emit("fake running")
                self.position_done.emit((1.25, 2.5))
                self.action_done.emit("fake done")

        original = panel_module.ManualStageWorker
        panel_module.ManualStageWorker = FakeManualStageWorker
        try:
            from linear_stage_control.experiments.manual_stage_panel import ManualStagePanel

            app = QApplication.instance() or QApplication([])
            panel = ManualStagePanel(lambda: {"stage": {"serial_port": "COM_FAKE"}})
            panel.start_action("position")
            self.assertTrue(_wait_for(lambda: panel.worker is None, app))
            self.assertEqual(panel.x_edit.text(), "1.25")
            self.assertEqual(panel.y_edit.text(), "2.5")
            self.assertEqual(panel.status_label.text(), "fake done")
            panel.close()
        finally:
            panel_module.ManualStageWorker = original


if __name__ == "__main__":
    unittest.main()
