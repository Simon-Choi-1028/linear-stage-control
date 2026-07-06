from __future__ import annotations

from typing import Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QLabel, QSlider, QWidget

Rect = Tuple[int, int, int, int]


class RoiControls(QWidget):
    roi_changed = Signal()

    def __init__(self, *, left: float = 0.0, right: float = 100.0, top: float = 0.0, bottom: float = 100.0) -> None:
        super().__init__()
        self.setObjectName("roiControls")
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)
        self.labels: dict[str, QLabel] = {}
        self.sliders: dict[str, QSlider] = {}
        for row, (key, label, value) in enumerate(
            (
                ("left", "Left %", left),
                ("right", "Right %", right),
                ("top", "Top %", top),
                ("bottom", "Bottom %", bottom),
            )
        ):
            name = QLabel(label)
            value_label = QLabel()
            value_label.setMinimumWidth(48)
            value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 1000)
            slider.setValue(int(round(value * 10)))
            slider.valueChanged.connect(self._on_changed)
            self.labels[key] = value_label
            self.sliders[key] = slider
            layout.addWidget(name, row, 0)
            layout.addWidget(slider, row, 1)
            layout.addWidget(value_label, row, 2)
        self.summary_label = QLabel("ROI: --")
        self.summary_label.setObjectName("roiSummary")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label, 4, 0, 1, 3)
        self._sync_labels()

    def percentages(self) -> tuple[float, float, float, float]:
        left = self.sliders["left"].value() / 10.0
        right = self.sliders["right"].value() / 10.0
        top = self.sliders["top"].value() / 10.0
        bottom = self.sliders["bottom"].value() / 10.0
        left = min(left, right - 0.1)
        right = max(right, left + 0.1)
        top = min(top, bottom - 0.1)
        bottom = max(bottom, top + 0.1)
        return left, right, top, bottom

    def roi_for_shape(self, shape: tuple[int, ...]) -> Rect:
        height, width = shape[:2]
        left, right, top, bottom = self.percentages()
        x1 = int(round(width * left / 100.0))
        x2 = int(round(width * right / 100.0))
        y1 = int(round(height * top / 100.0))
        y2 = int(round(height * bottom / 100.0))
        x1 = max(0, min(width - 1, x1))
        y1 = max(0, min(height - 1, y1))
        x2 = min(width, max(x1 + 2, x2))
        y2 = min(height, max(y1 + 2, y2))
        return x1, y1, x2, y2

    def update_summary(self, roi: Rect | None) -> None:
        if roi is None:
            self.summary_label.setText("ROI: --")
            return
        x1, y1, x2, y2 = roi
        self.summary_label.setText(f"ROI: x {x1}-{x2 - 1} px, y {y1}-{y2 - 1} px")

    def _on_changed(self) -> None:
        self._sync_labels()
        self.roi_changed.emit()

    def _sync_labels(self) -> None:
        for key, label in self.labels.items():
            label.setText(f"{self.sliders[key].value() / 10.0:.1f}")
