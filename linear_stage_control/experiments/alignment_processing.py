from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees, radians, tan
from typing import Optional, Tuple

import cv2
import numpy as np

Rect = Tuple[int, int, int, int]


@dataclass
class ProcessingSettings:
    threshold: int = 70
    auto_threshold: bool = False
    min_column_pixels: int = 2
    min_intensity_sum: float = 15.0
    blur_ksize: int = 3
    fit_distance: int = cv2.DIST_HUBER
    angle_tolerance_deg: float = 0.20
    max_rms_px: float = 1.50
    min_coverage_percent: float = 55.0


@dataclass
class LineResult:
    ok: bool
    message: str
    angle_deg: Optional[float] = None
    slope: Optional[float] = None
    intercept: Optional[float] = None
    rms_px: Optional[float] = None
    coverage_percent: float = 0.0
    point_count: int = 0
    threshold_used: int = 0
    points: Optional[np.ndarray] = None
    roi: Optional[Rect] = None
    fit_endpoints: Optional[Tuple[Tuple[int, int], Tuple[int, int]]] = None
    is_aligned: bool = False


def normalize_roi(roi: Rect, frame_shape: Tuple[int, ...]) -> Rect:
    height, width = frame_shape[:2]
    x1, y1, x2, y2 = roi
    x1 = int(np.clip(x1, 0, width - 1))
    x2 = int(np.clip(x2, 1, width))
    y1 = int(np.clip(y1, 0, height - 1))
    y2 = int(np.clip(y2, 1, height))
    if x2 <= x1:
        x2 = min(width, x1 + 1)
    if y2 <= y1:
        y2 = min(height, y1 + 1)
    return x1, y1, x2, y2


def process_laser_line(frame_bgr: np.ndarray, roi: Rect, settings: ProcessingSettings) -> LineResult:
    roi = normalize_roi(roi, frame_bgr.shape)
    x1, y1, x2, y2 = roi
    roi_img = frame_bgr[y1:y2, x1:x2]
    if roi_img.size == 0:
        return LineResult(False, "ROI is empty", roi=roi)

    gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY) if roi_img.ndim == 3 else roi_img.copy()
    if settings.blur_ksize and settings.blur_ksize >= 3:
        k = settings.blur_ksize if settings.blur_ksize % 2 == 1 else settings.blur_ksize + 1
        gray = cv2.GaussianBlur(gray, (k, k), 0)

    threshold_used = int(settings.threshold)
    if settings.auto_threshold:
        threshold_used, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        threshold_used = int(threshold_used)
    else:
        _, mask = cv2.threshold(gray, threshold_used, 255, cv2.THRESH_BINARY)

    points = _centroid_thin(gray, mask, threshold_used, settings)
    if len(points) < 2:
        return LineResult(
            False,
            "Laser pixels not enough in ROI",
            point_count=len(points),
            threshold_used=threshold_used,
            points=points,
            roi=roi,
        )

    points[:, 0] += x1
    points[:, 1] += y1
    fit = cv2.fitLine(points.astype(np.float32), settings.fit_distance, 0, 0.01, 0.01)
    vx, vy, px, py = [float(v) for v in fit.reshape(-1)]
    if vx < 0:
        vx *= -1.0
        vy *= -1.0
    if abs(vx) < 1e-8:
        return LineResult(
            False,
            "Fitted line is nearly vertical",
            point_count=len(points),
            threshold_used=threshold_used,
            points=points,
            roi=roi,
        )

    slope = vy / vx
    intercept = py - slope * px
    angle_deg = _normalize_angle(degrees(atan2(vy, vx)))
    residuals = points[:, 1] - (slope * points[:, 0] + intercept)
    rms_px = float(np.sqrt(np.mean(residuals * residuals)))
    coverage_percent = float(100.0 * len(points) / max(1, x2 - x1))
    is_aligned = (
        abs(angle_deg) <= settings.angle_tolerance_deg
        and rms_px <= settings.max_rms_px
        and coverage_percent >= settings.min_coverage_percent
    )
    return LineResult(
        True,
        "OK",
        angle_deg=angle_deg,
        slope=slope,
        intercept=intercept,
        rms_px=rms_px,
        coverage_percent=coverage_percent,
        point_count=len(points),
        threshold_used=threshold_used,
        points=points,
        roi=roi,
        fit_endpoints=_line_endpoints_for_roi(slope, intercept, roi),
        is_aligned=is_aligned,
    )


def _centroid_thin(gray: np.ndarray, mask: np.ndarray, threshold: int, settings: ProcessingSettings) -> np.ndarray:
    height, width = gray.shape[:2]
    y_indices = np.arange(height, dtype=np.float32)
    points = []
    for x in range(width):
        active = mask[:, x] > 0
        if int(np.count_nonzero(active)) < settings.min_column_pixels:
            continue
        weights = gray[:, x].astype(np.float32) - float(threshold)
        weights[weights < 0.0] = 0.0
        weights *= active.astype(np.float32)
        weight_sum = float(weights.sum())
        if weight_sum < settings.min_intensity_sum:
            continue
        points.append((float(x), float((y_indices * weights).sum() / weight_sum)))
    return np.asarray(points, dtype=np.float32) if points else np.empty((0, 2), dtype=np.float32)


def _normalize_angle(angle_deg: float) -> float:
    while angle_deg > 90.0:
        angle_deg -= 180.0
    while angle_deg < -90.0:
        angle_deg += 180.0
    return angle_deg


def _line_endpoints_for_roi(slope: float, intercept: float, roi: Rect) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    x1, y1, x2, y2 = roi
    left_y = slope * x1 + intercept
    right_y = slope * (x2 - 1) + intercept
    return (
        (int(round(x1)), int(round(np.clip(left_y, y1, y2 - 1)))),
        (int(round(x2 - 1)), int(round(np.clip(right_y, y1, y2 - 1)))),
    )


def draw_overlay(
    frame_bgr: np.ndarray, result: LineResult, show_points: bool = True, show_fit: bool = True
) -> np.ndarray:
    output = _ensure_bgr(frame_bgr).copy()
    if result.roi is not None:
        x1, y1, x2, y2 = result.roi
        roi_color = (40, 220, 80) if result.is_aligned else (0, 190, 255)
        cv2.rectangle(output, (x1, y1), (x2 - 1, y2 - 1), roi_color, 2)
        cv2.line(output, (x1, y1), (x2 - 1, y1), (255, 180, 0), 1)
        cv2.line(output, (x1, y2 - 1), (x2 - 1, y2 - 1), (255, 180, 0), 1)
    if show_points and result.points is not None and len(result.points) > 0:
        for x, y in result.points[:: max(1, len(result.points) // 600)]:
            cv2.circle(output, (int(round(x)), int(round(y))), 1, (255, 255, 0), -1)
    if show_fit and result.fit_endpoints is not None:
        cv2.line(output, result.fit_endpoints[0], result.fit_endpoints[1], (0, 255, 255), 2)
    _draw_label(output, result)
    return output


def make_synthetic_frame(
    width: int = 1280,
    height: int = 720,
    angle_deg: float = 1.25,
    stripe_sigma: float = 2.0,
    noise_sigma: float = 3.0,
    phase: float = 0.0,
) -> np.ndarray:
    x = np.arange(width, dtype=np.float32)
    y = np.arange(height, dtype=np.float32)[:, None]
    center_y = height * 0.52 + tan(radians(angle_deg)) * (x - width * 0.5)
    center_y = center_y + 0.15 * np.sin(x * 0.025 + phase)
    stripe = 245.0 * np.exp(-0.5 * ((y - center_y[None, :]) / stripe_sigma) ** 2)
    if noise_sigma > 0:
        stripe += np.random.normal(0.0, noise_sigma, (height, width)).astype(np.float32)
    gray = np.clip(stripe, 0, 255).astype(np.uint8)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def _draw_label(image: np.ndarray, result: LineResult) -> None:
    cv2.rectangle(image, (12, 12), (560, 100), (0, 0, 0), -1)
    if not result.ok or result.angle_deg is None:
        line1 = "Laser angle: --"
        line2 = result.message
    else:
        state = "ALIGNED" if result.is_aligned else "ADJUST"
        line1 = f"Laser angle: {result.angle_deg:+.4f} deg  {state}"
        line2 = (
            f"y = {result.slope:+.6f}x {result.intercept:+.2f}   "
            f"RMS {result.rms_px:.2f}px   Coverage {result.coverage_percent:.1f}%"
        )
    cv2.putText(image, line1, (24, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
    cv2.putText(image, line2, (24, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (180, 240, 255), 1)


def _ensure_bgr(frame: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR) if frame.ndim == 2 else frame
