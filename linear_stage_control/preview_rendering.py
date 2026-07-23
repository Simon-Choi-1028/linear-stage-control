from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap

MAX_PREVIEW_PIXELS = 64_000_000
MAX_PREVIEW_SOURCE_BYTES = 256 * 1024 * 1024
MAX_INTEGER_PREVIEW_LUT_VALUES = 1 << 16


def qimage_from_array(array: object) -> QImage:
    arr = np.asarray(array)
    _validate_preview_array(arr)
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
        if arr.shape[2] not in (3, 4):
            raise ValueError(f"Unsupported live frame channel count: {arr.shape[2]}")
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


def _validate_preview_array(arr: np.ndarray) -> None:
    if arr.ndim not in (2, 3):
        raise ValueError(f"Unsupported live frame shape: {arr.shape}")
    if any(int(size) <= 0 for size in arr.shape[:2]):
        raise ValueError(f"Live frame has an empty dimension: {arr.shape}")
    if arr.dtype == np.dtype("O") or np.issubdtype(arr.dtype, np.complexfloating):
        raise TypeError(f"Unsupported live frame dtype: {arr.dtype}")
    pixels = int(arr.shape[0]) * int(arr.shape[1])
    if pixels > MAX_PREVIEW_PIXELS:
        raise MemoryError(
            f"Preview frame is too large: {arr.shape[1]}x{arr.shape[0]} px "
            f"({pixels:,} px > {MAX_PREVIEW_PIXELS:,} px)"
        )
    if int(arr.nbytes) > MAX_PREVIEW_SOURCE_BYTES:
        raise MemoryError(f"Preview frame uses too much source memory: {arr.nbytes / (1024 * 1024):.1f} MiB")


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
    target_w = max(1, target_size.width())
    target_h = max(1, target_size.height())
    scale = min(target_w / crop_w, target_h / crop_h)
    render_w = max(1, min(target_w, round(crop_w * scale)))
    render_h = max(1, min(target_h, round(crop_h * scale)))
    pixmap = QPixmap(render_w, render_h)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    painter.drawImage(
        QRectF(0, 0, render_w, render_h),
        qimage,
        QRectF(crop_x, crop_y, crop_w, crop_h),
    )
    painter.end()
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
    painter.setRenderHint(QPainter.Antialiasing, False)
    width = pixmap.width()
    height = pixmap.height()
    x_positions: set[int] = set()
    y_positions: set[int] = set()
    if show_grid:
        x_positions.update(_overlay_positions(width, divisions=4))
        y_positions.update(_overlay_positions(height, divisions=4))
    if show_cross:
        x_positions.add(round(width / 2))
        y_positions.add(round(height / 2))
    pen = QPen(QColor(255, 255, 255, 190), 1, Qt.SolidLine)
    pen.setCosmetic(True)
    painter.setPen(pen)
    for x in sorted(_clamped_positions(x_positions, width)):
        painter.drawLine(x, 0, x, max(0, height - 1))
    for y in sorted(_clamped_positions(y_positions, height)):
        painter.drawLine(0, y, max(0, width - 1), y)
    painter.end()


def _overlay_positions(length: int, divisions: int) -> set[int]:
    if length <= 1 or divisions <= 1:
        return set()
    return {round(length * index / divisions) for index in range(1, divisions)}


def _clamped_positions(positions: set[int], length: int) -> set[int]:
    if length <= 0:
        return set()
    return {min(length - 1, max(0, position)) for position in positions}


def _to_uint8(array: np.ndarray) -> np.ndarray:
    arr = np.asarray(array)
    if arr.dtype == np.uint8:
        return np.ascontiguousarray(arr)
    if not arr.size:
        return np.zeros(arr.shape, dtype=np.uint8)
    if np.issubdtype(arr.dtype, np.integer):
        min_value = float(arr.min())
        max_value = float(arr.max())
        if max_value <= min_value:
            return np.zeros(arr.shape, dtype=np.uint8)
        if 1 << (arr.dtype.itemsize * 8) <= MAX_INTEGER_PREVIEW_LUT_VALUES:
            return _small_integer_to_uint8(arr, min_value, max_value)
        arr_float = arr.astype(np.float32, copy=False)
    else:
        arr_float = arr.astype(np.float32, copy=False)
        finite = np.isfinite(arr_float)
        if not finite.any():
            return np.zeros(arr.shape, dtype=np.uint8)
        finite_values = arr_float[finite]
        min_value = float(finite_values.min())
        max_value = float(finite_values.max())
    if max_value <= min_value:
        return np.zeros(arr.shape, dtype=np.uint8)
    scaled = (arr_float - min_value) * (255.0 / (max_value - min_value))
    return np.ascontiguousarray(np.clip(scaled, 0, 255).astype(np.uint8))


def _small_integer_to_uint8(array: np.ndarray, min_value: float, max_value: float) -> np.ndarray:
    """Scale an 8/16-bit integer frame through a small LUT instead of full-frame floats."""
    value_count = 1 << (array.dtype.itemsize * 8)
    values = np.arange(value_count, dtype=np.int32)
    if np.issubdtype(array.dtype, np.signedinteger):
        signed_max = int(np.iinfo(array.dtype).max)
        values[signed_max + 1 :] -= value_count

    scaled = values.astype(np.float32)
    scaled -= min_value
    scaled *= 255.0 / (max_value - min_value)
    np.clip(scaled, 0, 255, out=scaled)
    lookup = scaled.astype(np.uint8)
    return np.ascontiguousarray(lookup[array])
