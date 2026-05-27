from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PIL import Image
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStyle,
    QVBoxLayout,
    QWidget,
)


class ImagePreviewLabel(QLabel):
    double_clicked = Signal()

    def mouseDoubleClickEvent(self, event: object) -> None:
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


class ParameterAdjustRow(QWidget):
    def __init__(
        self,
        label_text: str,
        editor: QWidget,
        deltas: tuple[int, ...],
        unit_text: str,
        tooltip: str,
        adjust_value: Callable[[int], None],
    ) -> None:
        super().__init__()
        self.setObjectName("parameterRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        label = QLabel(label_text)
        label.setObjectName("parameterLabel")
        label.setToolTip(tooltip)
        editor.setToolTip(tooltip)
        editor.setMinimumWidth(78)
        editor.setMaximumWidth(94)

        left = QWidget()
        left_layout = QHBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        left_layout.addWidget(label)
        left_layout.addWidget(editor)

        button_group = QWidget()
        button_layout = QHBoxLayout(button_group)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(6)
        for delta in deltas:
            button = QPushButton(f"{delta:+d}")
            button.setObjectName("parameterButton")
            button.setFixedWidth(40)
            tooltip_label = label_text.replace("\n", " ")
            button.setToolTip(f"{tooltip_label} 값을 {delta:+d}{unit_text} 조정")
            button.clicked.connect(lambda _checked=False, step=delta: adjust_value(step))
            button_layout.addWidget(button)

        layout.addWidget(left)
        layout.addStretch(1)
        layout.addWidget(button_group)


class FullscreenImageWindow(QMainWindow):
    def __init__(self, image_path: Path):
        super().__init__()
        self.image_path = image_path
        self.original_pixmap = _pixmap_from_image_path(image_path)
        self.scale_factor = 1.0
        self.fit_to_window = True
        self.setWindowTitle(f"전체화면 이미지 - {image_path.name}")

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        button_row = QHBoxLayout()
        self.fit_button = QPushButton("화면 맞춤")
        self.actual_button = QPushButton("100%")
        self.zoom_in_button = QPushButton("확대")
        self.zoom_out_button = QPushButton("축소")
        self.close_button = QPushButton("닫기")
        _apply_button_icon(self.fit_button, QStyle.SP_TitleBarMaxButton, "창 크기에 맞춰 표시")
        _apply_button_icon(self.actual_button, QStyle.SP_FileDialogDetailedView, "원본 크기 100%로 표시")
        _apply_button_icon(self.zoom_in_button, QStyle.SP_ArrowUp, "이미지 확대")
        _apply_button_icon(self.zoom_out_button, QStyle.SP_ArrowDown, "이미지 축소")
        _apply_button_icon(self.close_button, QStyle.SP_DialogCloseButton, "전체화면 창 닫기")
        for button in (
            self.fit_button,
            self.actual_button,
            self.zoom_in_button,
            self.zoom_out_button,
        ):
            button_row.addWidget(button)
        button_row.addStretch(1)
        button_row.addWidget(self.close_button)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setObjectName("fullscreenImage")
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidget(self.image_label)
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignCenter)

        layout.addLayout(button_row)
        layout.addWidget(self.scroll_area, 1)
        self.setCentralWidget(root)

        self.fit_button.clicked.connect(self.set_fit_mode)
        self.actual_button.clicked.connect(self.set_actual_size)
        self.zoom_in_button.clicked.connect(lambda: self.zoom(1.25))
        self.zoom_out_button.clicked.connect(lambda: self.zoom(0.8))
        self.close_button.clicked.connect(self.close)

        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #101317; color: #f2f4f6; }
            QPushButton {
                background: #ffffff;
                color: #1e2329;
                border: 1px solid #c4cbd3;
                border-radius: 5px;
                padding: 7px 11px;
                min-height: 24px;
            }
            QLabel#fullscreenImage { background: #050607; }
            QScrollArea { border: 1px solid #2a3036; background: #050607; }
            """
        )
        self.update_image()

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)
        if self.fit_to_window:
            self.update_image()

    def keyPressEvent(self, event: object) -> None:
        if getattr(event, "key", lambda: None)() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def set_fit_mode(self) -> None:
        self.fit_to_window = True
        self.update_image()

    def set_actual_size(self) -> None:
        self.fit_to_window = False
        self.scale_factor = 1.0
        self.update_image()

    def zoom(self, factor: float) -> None:
        if self.original_pixmap.isNull():
            return
        self.fit_to_window = False
        self.scale_factor = max(0.05, min(20.0, self.scale_factor * factor))
        self.update_image()

    def update_image(self) -> None:
        if self.original_pixmap.isNull():
            self.image_label.setText("이미지를 불러올 수 없습니다.")
            return

        if self.fit_to_window:
            viewport = self.scroll_area.viewport().size()
            target = self.original_pixmap.scaled(
                viewport,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        else:
            target = self.original_pixmap.scaled(
                max(1, int(self.original_pixmap.width() * self.scale_factor)),
                max(1, int(self.original_pixmap.height() * self.scale_factor)),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        self.image_label.setPixmap(target)
        self.image_label.resize(target.size())


class ErrorChartWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, Any]] = []
        self.setMinimumHeight(220)

    def set_records(self, records: list[dict[str, Any]]) -> None:
        self.records = list(records)
        self.update()

    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(14, 14, -14, -14)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        painter.setPen(QPen(QColor("#cfd5dc"), 1))
        painter.drawRoundedRect(rect, 5, 5)

        candles = [_error_candle(record) for record in self.records]
        candles = [candle for candle in candles if candle is not None]
        if not candles:
            painter.setPen(QColor("#5a636d"))
            painter.drawText(rect, Qt.AlignCenter, "아직 오차 데이터가 없습니다")
            painter.end()
            return

        limit = _record_float(self.records[-1], "max_allowed_error_um", 0.0)
        plot = rect.adjusted(54, 24, -16, -36)
        max_value = max(max(candle["high"] for candle in candles), limit, 1.0)
        scale_max = max_value * 1.15

        painter.setPen(QPen(QColor("#d9dee4"), 1))
        for i in range(5):
            y = plot.bottom() - int(plot.height() * i / 4)
            painter.drawLine(plot.left(), y, plot.right(), y)
            label = _um_text(scale_max * i / 4)
            painter.setPen(QColor("#66707a"))
            painter.drawText(8, y - 8, 52, 16, Qt.AlignRight | Qt.AlignVCenter, label)
            painter.setPen(QPen(QColor("#d9dee4"), 1))

        if limit > 0:
            limit_y = plot.bottom() - int(plot.height() * limit / scale_max)
            painter.setPen(QPen(QColor("#b43d3d"), 2, Qt.DashLine))
            painter.drawLine(plot.left(), limit_y, plot.right(), limit_y)
            painter.drawText(plot.left() + 6, limit_y - 18, f"제한 {_um_text(limit)} um")

        slot_width = plot.width() / max(1, len(candles))
        candle_width = max(2, min(22, int(slot_width * 0.56)))

        def y_for(value: float) -> int:
            return plot.bottom() - int(plot.height() * value / scale_max)

        zero_y = y_for(0.0)
        painter.setPen(QPen(QColor("#aeb6bf"), 1))
        painter.drawLine(plot.left(), zero_y, plot.right(), zero_y)

        for index, candle in enumerate(candles):
            center_x = int(plot.left() + slot_width * (index + 0.5))
            x = int(center_x - candle_width / 2)
            center_x = x + candle_width // 2
            low_y = y_for(candle["low"])
            measured_y = y_for(candle["measured"])
            high_y = y_for(candle["high"])
            color = QColor("#2f8f68") if limit <= 0 or candle["high"] <= limit else QColor("#c94f4f")

            painter.setPen(QPen(QColor("#2d3741"), 1))
            painter.drawLine(center_x, high_y, center_x, low_y)
            painter.drawLine(center_x - 4, low_y, center_x + 4, low_y)
            painter.drawLine(center_x - 4, high_y, center_x + 4, high_y)

            body_top = min(measured_y, high_y)
            body_bottom = max(measured_y, high_y)
            body_height = max(3, body_bottom - body_top)
            painter.fillRect(x, body_top, candle_width, body_height, color)
            painter.setPen(QPen(QColor("#1e2329"), 1))
            painter.drawRect(x, body_top, candle_width, body_height)
            painter.drawLine(x - 2, measured_y, x + candle_width + 2, measured_y)

        painter.setPen(QColor("#1e2329"))
        painter.drawText(rect.left() + 10, rect.top() + 6, "캡처별 예측 오차 범위 캔들 (um)")
        painter.drawText(plot.left(), rect.bottom() - 22, "캡처 순서")
        painter.end()


def _apply_button_icon(
    button: QPushButton,
    standard_pixmap: QStyle.StandardPixmap,
    tooltip: str,
    icon_size: int = 18,
) -> None:
    button.setIcon(QApplication.style().standardIcon(standard_pixmap))
    button.setIconSize(QSize(icon_size, icon_size))
    button.setToolTip(tooltip)


def _pixmap_from_image_path(path: Path) -> QPixmap:
    image = Image.open(path).convert("RGB")
    data = image.tobytes("raw", "RGB")
    qimage = QImage(data, image.width, image.height, image.width * 3, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(qimage)


def _error_candle(record: dict[str, Any]) -> dict[str, float] | None:
    if record.get("status") != "ok" or record.get("predicted_max_error_um") == "":
        return None
    high = _record_float(record, "predicted_max_error_um", 0.0)
    measured = _record_float(record, "measured_radial_error_um", 0.0)
    configured_budget = _record_float(record, "configured_error_budget_um", 0.0)
    low = _record_float(
        record,
        "predicted_min_error_um",
        max(0.0, measured - configured_budget),
    )
    low = max(0.0, min(low, measured, high))
    measured = max(low, min(measured, high))
    high = max(low, measured, high)
    return {"low": low, "measured": measured, "high": high}


def _record_float(record: dict[str, Any], key: str, default: float) -> float:
    try:
        value = record.get(key, default)
        if value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _um_text(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number) < 0.01:
        number = 0.0
    text = f"{number:.2f}".rstrip("0").rstrip(".")
    return text or "0"
