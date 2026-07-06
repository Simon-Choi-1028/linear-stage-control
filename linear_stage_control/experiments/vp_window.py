from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QFormLayout, QSpinBox, QWidget

from .base import ExperimentWindowBase, ProcessedFrame
from .vp_processing import VPResult, VPSettings, detect_virtual_point, draw_overlay, make_synthetic_v_frame


class VPWindow(ExperimentWindowBase):
    feature_key = "vp"
    feature_title = "Laser VP Detection"
    synthetic_source_name = "Synthetic V target"
    default_roi = (0.0, 100.0, 35.0, 75.0)
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
        "vp_x",
        "vp_y",
        "negative_slope",
        "positive_slope",
        "negative_rms_px",
        "positive_rms_px",
        "negative_point_count",
        "positive_point_count",
        "total_point_count",
    ]

    def synthetic_factory(self, phase: float) -> np.ndarray:
        return make_synthetic_v_frame(phase=phase)

    def build_processing_controls(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(0, 255)
        self.threshold_spin.setValue(40)
        self.auto_threshold_check = QCheckBox("Auto threshold (Otsu)")
        self.slope_window_spin = QSpinBox()
        self.slope_window_spin.setRange(3, 81)
        self.slope_window_spin.setSingleStep(2)
        self.slope_window_spin.setValue(21)
        self.min_abs_slope_spin = _double_spin(0.001, 0.5, 0.03, 3)
        self.min_arm_points_spin = QSpinBox()
        self.min_arm_points_spin.setRange(5, 300)
        self.min_arm_points_spin.setValue(25)
        self.max_rms_spin = _double_spin(0.5, 20.0, 5.0, 2)
        self.show_points_check = QCheckBox("Show thinning points")
        self.show_points_check.setChecked(True)
        self.show_arm_points_check = QCheckBox("Show arm points")
        self.show_arm_points_check.setChecked(True)
        self.show_lines_check = QCheckBox("Show fitted lines")
        self.show_lines_check.setChecked(True)
        for widget_item in (
            self.threshold_spin,
            self.auto_threshold_check,
            self.slope_window_spin,
            self.min_abs_slope_spin,
            self.min_arm_points_spin,
            self.max_rms_spin,
            self.show_points_check,
            self.show_arm_points_check,
            self.show_lines_check,
        ):
            _connect_change(widget_item, self.request_processing_update)
        form.addRow("Threshold", self.threshold_spin)
        form.addRow("", self.auto_threshold_check)
        form.addRow("Slope window", self.slope_window_spin)
        form.addRow("Min abs slope", self.min_abs_slope_spin)
        form.addRow("Min arm points", self.min_arm_points_spin)
        form.addRow("Max RMS px", self.max_rms_spin)
        form.addRow("", self.show_points_check)
        form.addRow("", self.show_arm_points_check)
        form.addRow("", self.show_lines_check)
        return widget

    def processing_state(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold_spin.value(),
            "auto_threshold": self.auto_threshold_check.isChecked(),
            "slope_window": self.slope_window_spin.value(),
            "min_abs_slope": self.min_abs_slope_spin.value(),
            "min_arm_points": self.min_arm_points_spin.value(),
            "max_fit_rms_px": self.max_rms_spin.value(),
            "show_points": self.show_points_check.isChecked(),
            "show_arm_points": self.show_arm_points_check.isChecked(),
            "show_lines": self.show_lines_check.isChecked(),
        }

    def process_frame(self, frame_bgr: np.ndarray, state: dict[str, Any]) -> ProcessedFrame:
        roi = self.roi_from_state(frame_bgr.shape, state)
        settings = VPSettings(
            threshold=int(state["threshold"]),
            auto_threshold=bool(state["auto_threshold"]),
            slope_window=int(state["slope_window"]),
            min_abs_slope=float(state["min_abs_slope"]),
            min_arm_points=int(state["min_arm_points"]),
            max_fit_rms_px=float(state["max_fit_rms_px"]),
        )
        result = detect_virtual_point(frame_bgr, roi, settings)
        overlay = draw_overlay(
            frame_bgr,
            result,
            show_points=bool(state["show_points"]),
            show_arm_points=bool(state["show_arm_points"]),
            show_lines=bool(state["show_lines"]),
        )
        return ProcessedFrame(overlay, result, roi)

    def result_pairs(self, result: object) -> list[tuple[str, str]]:
        if not isinstance(result, VPResult):
            return [("Status", "--")]
        pairs = [("Status", result.message)]
        if result.vp is not None:
            pairs.append(("VP", f"x={result.vp[0]:.3f}, y={result.vp[1]:.3f} px"))
        else:
            pairs.append(("VP", "--"))
        n_all = 0 if result.points is None else len(result.points)
        n_neg = 0 if result.negative_points is None else len(result.negative_points)
        n_pos = 0 if result.positive_points is None else len(result.positive_points)
        pairs.append(("Arm points", f"all {n_all}, neg {n_neg}, pos {n_pos}"))
        if result.negative_line is not None and result.positive_line is not None:
            pairs.extend(
                [
                    ("Negative line", f"m={result.negative_line.slope:+.4f}, RMS={result.negative_line.rms_px:.3f}px"),
                    ("Positive line", f"m={result.positive_line.slope:+.4f}, RMS={result.positive_line.rms_px:.3f}px"),
                ]
            )
        pairs.append(("Threshold", str(result.threshold_used)))
        return pairs

    def csv_rows(self, result: object) -> list[dict[str, Any]]:
        if not isinstance(result, VPResult):
            return []
        context = self.measurement_context()
        neg = result.negative_line
        pos = result.positive_line
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
                "vp_x": result.vp[0] if result.vp is not None else None,
                "vp_y": result.vp[1] if result.vp is not None else None,
                "negative_slope": neg.slope if neg is not None else None,
                "positive_slope": pos.slope if pos is not None else None,
                "negative_rms_px": neg.rms_px if neg is not None else None,
                "positive_rms_px": pos.rms_px if pos is not None else None,
                "negative_point_count": 0 if result.negative_points is None else len(result.negative_points),
                "positive_point_count": 0 if result.positive_points is None else len(result.positive_points),
                "total_point_count": 0 if result.points is None else len(result.points),
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
