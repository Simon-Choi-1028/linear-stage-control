from __future__ import annotations

from typing import Any

import cv2
import numpy as np
from PySide6.QtWidgets import QCheckBox, QFormLayout, QLineEdit, QWidget

from .base import ExperimentWindowBase, ProcessedFrame
from .frame_sources import ensure_bgr
from .fwhm_processing import (
    FwhmResult,
    average_valid_fwhm,
    calculate_for_columns,
    is_valid_fwhm_result,
    make_synthetic_fwhm_frame,
)

FWHM_COLORS = [
    (40, 145, 255),
    (20, 210, 90),
    (255, 65, 70),
    (255, 220, 30),
    (235, 70, 255),
]


class FwhmWindow(ExperimentWindowBase):
    feature_key = "fwhm"
    feature_title = "FWHM Monitor"
    synthetic_source_name = "Synthetic FWHM stripe"
    default_roi = (0.0, 100.0, 20.0, 82.0)
    csv_fieldnames = [
        "timestamp",
        "source",
        "roi_left",
        "roi_right",
        "roi_top",
        "roi_bottom",
        "col",
        "roi_col_start",
        "roi_col_end",
        "roi_row_start",
        "roi_row_end",
        "peak",
        "saturated",
        "sigma_px",
        "fwhm_px",
        "center_row",
        "status",
        "average_valid_fwhm_px",
    ]

    def synthetic_factory(self, phase: float) -> np.ndarray:
        return make_synthetic_fwhm_frame(phase=phase)

    def build_processing_controls(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        self.columns_edit = QLineEdit("320, 640, 960")
        self.columns_edit.setToolTip("최대 5개 column을 comma/space로 입력합니다.")
        self.center_lines_check = QCheckBox("Show center lines")
        self.center_lines_check.setChecked(True)
        self.columns_edit.editingFinished.connect(self.request_processing_update)
        self.center_lines_check.toggled.connect(lambda _checked=False: self.request_processing_update())
        form.addRow("Columns", self.columns_edit)
        form.addRow("", self.center_lines_check)
        return widget

    def processing_state(self) -> dict[str, Any]:
        return {
            "columns": self._columns(),
            "show_center_lines": self.center_lines_check.isChecked(),
        }

    def process_frame(self, frame_bgr: np.ndarray, state: dict[str, Any]) -> ProcessedFrame:
        frame_bgr = ensure_bgr(frame_bgr)
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        roi = self.roi_from_state(gray.shape, state)
        x1, y1, x2, y2 = roi
        roi_inclusive = (x1, x2 - 1, y1, y2 - 1)
        results = calculate_for_columns(gray, state.get("columns", []), roi_inclusive)
        overlay = self._draw_overlay(frame_bgr, roi, results, bool(state.get("show_center_lines")))
        return ProcessedFrame(overlay, results, roi)

    def result_pairs(self, result: object) -> list[tuple[str, str]]:
        results = list(result) if isinstance(result, list) else []
        average = average_valid_fwhm(results)
        valid = sum(1 for item in results if is_valid_fwhm_result(item))
        pairs = [("Average FWHM", "--" if average is None else f"{average:.3f} px ({valid} cols)")]
        for item in results[:5]:
            value = f"{item.fwhm_px:.3f} px" if item.fwhm_px is not None else item.status
            if item.saturated:
                value = f"SAT {value}"
            pairs.append((f"Column {item.col}", value))
        if not results:
            pairs.append(("Columns", "No columns selected"))
        return pairs

    def csv_rows(self, result: object) -> list[dict[str, Any]]:
        results = list(result) if isinstance(result, list) else []
        average = average_valid_fwhm(results)
        context = self.measurement_context()
        rows = []
        for item in results:
            rows.append(
                {
                    "timestamp": context["timestamp"],
                    "source": context["source"],
                    "roi_left": context["roi_left"],
                    "roi_right": context["roi_right"],
                    "roi_top": context["roi_top"],
                    "roi_bottom": context["roi_bottom"],
                    "col": item.col,
                    "roi_col_start": item.roi_col_start,
                    "roi_col_end": item.roi_col_end,
                    "roi_row_start": item.roi_start,
                    "roi_row_end": item.roi_end,
                    "peak": item.peak,
                    "saturated": int(item.saturated),
                    "sigma_px": item.sigma_px,
                    "fwhm_px": item.fwhm_px,
                    "center_row": item.center_row,
                    "status": item.status,
                    "average_valid_fwhm_px": average,
                }
            )
        return rows

    def _columns(self) -> list[int]:
        raw = self.columns_edit.text().replace(",", " ").replace(";", " ")
        columns = []
        for token in raw.split():
            try:
                value = float(token)
            except ValueError:
                raise ValueError(f"Invalid FWHM column value: {token}") from None
            if not value.is_integer():
                raise ValueError(f"FWHM column must be an integer pixel index: {token}")
            columns.append(int(value))
            if len(columns) >= 5:
                break
        return columns

    def _draw_overlay(
        self,
        frame_bgr: np.ndarray,
        roi: tuple[int, int, int, int],
        results: list[FwhmResult],
        show_center_lines: bool,
    ) -> np.ndarray:
        output = frame_bgr.copy()
        x1, y1, x2, y2 = roi
        cv2.rectangle(output, (x1, y1), (x2 - 1, y2 - 1), (0, 190, 255), 2)
        if show_center_lines:
            h, w = output.shape[:2]
            cv2.line(output, (w // 2, 0), (w // 2, h - 1), (0, 255, 255), 1, cv2.LINE_AA)
            cv2.line(output, (0, h // 2), (w - 1, h // 2), (0, 255, 255), 1, cv2.LINE_AA)
        for idx, result in enumerate(results[:5]):
            color = FWHM_COLORS[idx % len(FWHM_COLORS)]
            col = result.col
            if 0 <= col < output.shape[1]:
                line_color = color if x1 <= col <= x2 - 1 else tuple(max(50, channel // 3) for channel in color)
                cv2.line(output, (col, y1), (col, y2 - 1), line_color, 2)
                y_src = int(result.center_row) if result.center_row is not None and np.isfinite(result.center_row) else y1
                y_src = max(0, min(output.shape[0] - 1, y_src))
                text = f"{result.fwhm_px:.2f}px" if result.fwhm_px is not None else result.status
                if result.saturated:
                    text = f"SAT {text}"
                cv2.putText(output, text, (min(col + 6, output.shape[1] - 140), y_src), cv2.FONT_HERSHEY_SIMPLEX, 0.55, line_color, 2)
        average = average_valid_fwhm(results)
        label = "Average FWHM: --" if average is None else f"Average FWHM: {average:.3f}px"
        cv2.rectangle(output, (12, 12), (430, 58), (0, 0, 0), -1)
        cv2.putText(output, label, (24, 43), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
        return output
