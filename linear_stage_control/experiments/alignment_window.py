from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QFormLayout, QSpinBox, QWidget

from .alignment_processing import (
    LineResult,
    ProcessingSettings,
    draw_overlay,
    make_synthetic_frame,
    process_laser_line,
)
from .base import ExperimentWindowBase, ProcessedFrame


class AlignmentWindow(ExperimentWindowBase):
    feature_key = "alignment"
    feature_title = "Laser Alignment"
    synthetic_source_name = "Synthetic laser line"
    default_roi = (5.0, 95.0, 35.0, 65.0)
    csv_fieldnames = [
        "timestamp",
        "source",
        "roi_left",
        "roi_right",
        "roi_top",
        "roi_bottom",
        "threshold_used",
        "ok",
        "message",
        "angle_deg",
        "slope",
        "intercept",
        "rms_px",
        "coverage_percent",
        "point_count",
        "is_aligned",
    ]

    def synthetic_factory(self, phase: float) -> np.ndarray:
        return make_synthetic_frame(phase=phase)

    def build_processing_controls(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(0, 255)
        self.threshold_spin.setValue(70)
        self.auto_threshold_check = QCheckBox("Auto threshold (Otsu)")
        self.show_points_check = QCheckBox("Show centroid points")
        self.show_points_check.setChecked(True)
        self.show_fit_check = QCheckBox("Show fitted line")
        self.show_fit_check.setChecked(True)
        self.angle_tolerance_spin = _double_spin(0.01, 2.0, 0.20, 3)
        self.max_rms_spin = _double_spin(0.1, 10.0, 1.50, 2)
        self.min_coverage_spin = _double_spin(1.0, 100.0, 55.0, 1)
        for widget_item in (
            self.threshold_spin,
            self.auto_threshold_check,
            self.show_points_check,
            self.show_fit_check,
            self.angle_tolerance_spin,
            self.max_rms_spin,
            self.min_coverage_spin,
        ):
            _connect_change(widget_item, self.request_processing_update)
        form.addRow("Threshold", self.threshold_spin)
        form.addRow("", self.auto_threshold_check)
        form.addRow("", self.show_points_check)
        form.addRow("", self.show_fit_check)
        form.addRow("Angle tol deg", self.angle_tolerance_spin)
        form.addRow("Max RMS px", self.max_rms_spin)
        form.addRow("Min coverage %", self.min_coverage_spin)
        return widget

    def processing_state(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold_spin.value(),
            "auto_threshold": self.auto_threshold_check.isChecked(),
            "show_points": self.show_points_check.isChecked(),
            "show_fit": self.show_fit_check.isChecked(),
            "angle_tolerance_deg": self.angle_tolerance_spin.value(),
            "max_rms_px": self.max_rms_spin.value(),
            "min_coverage_percent": self.min_coverage_spin.value(),
        }

    def process_frame(self, frame_bgr: np.ndarray, state: dict[str, Any]) -> ProcessedFrame:
        roi = self.roi_from_state(frame_bgr.shape, state)
        settings = ProcessingSettings(
            threshold=int(state["threshold"]),
            auto_threshold=bool(state["auto_threshold"]),
            angle_tolerance_deg=float(state["angle_tolerance_deg"]),
            max_rms_px=float(state["max_rms_px"]),
            min_coverage_percent=float(state["min_coverage_percent"]),
        )
        result = process_laser_line(frame_bgr, roi, settings)
        overlay = draw_overlay(frame_bgr, result, bool(state["show_points"]), bool(state["show_fit"]))
        return ProcessedFrame(overlay, result, roi)

    def result_pairs(self, result: object) -> list[tuple[str, str]]:
        if not isinstance(result, LineResult) or not result.ok:
            message = getattr(result, "message", "--")
            points = getattr(result, "point_count", 0)
            return [("Status", message), ("Points", str(points))]
        return [
            ("Status", "ALIGNED" if result.is_aligned else "ADJUST"),
            ("Angle", f"{result.angle_deg:+.4f} deg"),
            ("Line", f"y = {result.slope:+.6f}x {result.intercept:+.2f}"),
            ("RMS", f"{result.rms_px:.3f} px"),
            ("Coverage", f"{result.coverage_percent:.1f}%"),
            ("Points", str(result.point_count)),
            ("Threshold", str(result.threshold_used)),
        ]

    def csv_rows(self, result: object) -> list[dict[str, Any]]:
        if not isinstance(result, LineResult):
            return []
        context = self.measurement_context()
        return [
            {
                "timestamp": context["timestamp"],
                "source": context["source"],
                "roi_left": context["roi_left"],
                "roi_right": context["roi_right"],
                "roi_top": context["roi_top"],
                "roi_bottom": context["roi_bottom"],
                "threshold_used": result.threshold_used,
                "ok": int(result.ok),
                "message": result.message,
                "angle_deg": result.angle_deg,
                "slope": result.slope,
                "intercept": result.intercept,
                "rms_px": result.rms_px,
                "coverage_percent": result.coverage_percent,
                "point_count": result.point_count,
                "is_aligned": int(result.is_aligned),
            }
        ]


def _double_spin(minimum: float, maximum: float, value: float, decimals: int) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setDecimals(decimals)
    spin.setValue(value)
    return spin


def _connect_change(widget: QWidget, callback: Any) -> None:
    if isinstance(widget, QCheckBox):
        widget.toggled.connect(lambda _checked=False: callback())
    elif isinstance(widget, QSpinBox):
        widget.valueChanged.connect(lambda _value=0: callback())
    elif isinstance(widget, QDoubleSpinBox):
        widget.valueChanged.connect(lambda _value=0.0: callback())
