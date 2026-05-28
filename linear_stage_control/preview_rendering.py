from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap


def qimage_from_array(array: object) -> QImage:
    arr = np.asarray(array)
    if arr.ndim == 2:
        arr8 = _to_uint8(arr)
        return QImage(
            arr8.data,
            arr8.shape[1],
            arr8.shape[0],
            arr8.strides[0],
            QImage.Format_Grayscale8,
        ).copy()
    if arr.ndim == 3:
        if arr.shape[2] == 1:
            return qimage_from_array(arr[:, :, 0])
        arr8 = _to_uint8(arr[:, :, :4])
        if arr8.shape[2] >= 4:
            return QImage(
                arr8.data,
                arr8.shape[1],
                arr8.shape[0],
                arr8.strides[0],
                QImage.Format_RGBA8888,
            ).copy()
        return QImage(
            arr8.data,
            arr8.shape[1],
            arr8.shape[0],
            arr8.strides[0],
            QImage.Format_RGB888,
        ).copy()
    raise ValueError(f"Unsupported live frame shape: {arr.shape}")


def render_preview_qimage(
    qimage: QImage,
    target_size: QSize,
    zoom_percent: int,
    center_x: float,
    center_y: float,
    show_grid: bool,
    show_cross: bool,
) -> tuple[QPixmap, tuple[int, int, int, int]]:
    crop_rect = preview_crop_rect(qimage, zoom_percent, center_x, center_y)
    crop_x, crop_y, crop_w, crop_h = crop_rect
    cropped = qimage.copy(crop_x, crop_y, crop_w, crop_h)
    pixmap = QPixmap.fromImage(cropped).scaled(
        target_size,
        Qt.KeepAspectRatio,
        Qt.SmoothTransformation,
    )
    if show_grid or show_cross:
        draw_preview_overlays(pixmap, show_grid=show_grid, show_cross=show_cross)
    return pixmap, crop_rect


def preview_crop_rect(
    qimage: QImage,
    zoom_percent: int,
    center_x: float,
    center_y: float,
) -> tuple[int, int, int, int]:
    source_w = max(1, qimage.width())
    source_h = max(1, qimage.height())
    zoom = max(1.0, float(zoom_percent) / 100.0)
    crop_w = max(1, min(source_w, int(round(source_w / zoom))))
    crop_h = max(1, min(source_h, int(round(source_h / zoom))))
    center_px = min(1.0, max(0.0, center_x)) * source_w
    center_py = min(1.0, max(0.0, center_y)) * source_h
    crop_x = int(round(center_px - crop_w / 2))
    crop_y = int(round(center_py - crop_h / 2))
    crop_x = max(0, min(source_w - crop_w, crop_x))
    crop_y = max(0, min(source_h - crop_h, crop_y))
    return crop_x, crop_y, crop_w, crop_h


def draw_preview_overlays(pixmap: QPixmap, show_grid: bool, show_cross: bool) -> None:
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    width = pixmap.width()
    height = pixmap.height()
    if show_grid:
        shadow_pen = QPen(QColor(0, 0, 0, 120), 2)
        line_pen = QPen(QColor(255, 255, 255, 185), 1)
        for index in range(1, 4):
            x = round(width * index / 4)
            y = round(height * index / 4)
            for pen in (shadow_pen, line_pen):
                painter.setPen(pen)
                painter.drawLine(x, 0, x, height)
                painter.drawLine(0, y, width, y)
    if show_cross:
        center_x = round(width / 2)
        center_y = round(height / 2)
        for pen in (QPen(QColor(0, 0, 0, 160), 3), QPen(QColor("#ff3b30"), 1)):
            painter.setPen(pen)
            painter.drawLine(center_x, 0, center_x, height)
            painter.drawLine(0, center_y, width, center_y)
        painter.setPen(QPen(QColor("#ff3b30"), 2))
        painter.drawEllipse(QPointF(center_x, center_y), 5, 5)
    painter.end()


def _to_uint8(array: np.ndarray) -> np.ndarray:
    arr = np.asarray(array)
    if arr.dtype == np.uint8:
        return np.ascontiguousarray(arr)
    arr_float = arr.astype(np.float32, copy=False)
    max_value = float(np.nanmax(arr_float)) if arr_float.size else 0.0
    min_value = float(np.nanmin(arr_float)) if arr_float.size else 0.0
    if max_value <= min_value:
        return np.zeros(arr.shape, dtype=np.uint8)
    scaled = (arr_float - min_value) * (255.0 / (max_value - min_value))
    return np.ascontiguousarray(np.clip(scaled, 0, 255).astype(np.uint8))
