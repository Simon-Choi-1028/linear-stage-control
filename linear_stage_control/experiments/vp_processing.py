from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np

Rect = Tuple[int, int, int, int]
Point = Tuple[float, float]
Segment = Tuple[Tuple[int, int], Tuple[int, int]]


@dataclass
class VPSettings:
    threshold: int = 40
    auto_threshold: bool = False
    blur_ksize: int = 3
    min_column_pixels: int = 2
    min_intensity_sum: float = 15.0
    slope_window: int = 21
    min_abs_slope: float = 0.03
    min_arm_points: int = 25
    max_run_gap: int = 3
    max_fit_rms_px: float = 5.0
    use_largest_run: bool = True


@dataclass
class LineModel:
    slope: float
    intercept: float
    rms_px: float
    point_count: int
    segment: Segment


@dataclass
class VPResult:
    ok: bool
    message: str
    roi: Optional[Rect] = None
    threshold_used: int = 0
    points: Optional[np.ndarray] = None
    slopes: Optional[np.ndarray] = None
    negative_points: Optional[np.ndarray] = None
    positive_points: Optional[np.ndarray] = None
    negative_line: Optional[LineModel] = None
    positive_line: Optional[LineModel] = None
    vp: Optional[Point] = None


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


def detect_virtual_point(frame_bgr: np.ndarray, roi: Rect, settings: VPSettings) -> VPResult:
    roi = normalize_roi(roi, frame_bgr.shape)
    x1, y1, x2, y2 = roi
    roi_img = frame_bgr[y1:y2, x1:x2]
    if roi_img.size == 0:
        return VPResult(False, "ROI is empty", roi=roi)

    gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY) if roi_img.ndim == 3 else roi_img.copy()
    if settings.blur_ksize >= 3:
        ksize = settings.blur_ksize if settings.blur_ksize % 2 == 1 else settings.blur_ksize + 1
        gray = cv2.GaussianBlur(gray, (ksize, ksize), 0)

    threshold_used = int(settings.threshold)
    if settings.auto_threshold:
        threshold_used, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        threshold_used = int(threshold_used)
    else:
        _, mask = cv2.threshold(gray, threshold_used, 255, cv2.THRESH_BINARY)

    points = _centroid_thin(gray, mask, threshold_used, settings)
    if len(points) < max(6, settings.min_arm_points * 2):
        return VPResult(
            False,
            "Not enough thinned laser points",
            roi=roi,
            threshold_used=threshold_used,
            points=_offset_points(points, x1, y1),
        )

    points = _offset_points(points, x1, y1)
    points = points[np.argsort(points[:, 0])]
    slopes = _local_slopes(points, settings.slope_window)
    negative_points = _select_arm_points(points, slopes < -abs(settings.min_abs_slope), settings)
    positive_points = _select_arm_points(points, slopes > abs(settings.min_abs_slope), settings)

    if len(negative_points) < settings.min_arm_points:
        return VPResult(
            False,
            "Negative-slope arm not enough",
            roi=roi,
            threshold_used=threshold_used,
            points=points,
            slopes=slopes,
            negative_points=negative_points,
            positive_points=positive_points,
        )
    if len(positive_points) < settings.min_arm_points:
        return VPResult(
            False,
            "Positive-slope arm not enough",
            roi=roi,
            threshold_used=threshold_used,
            points=points,
            slopes=slopes,
            negative_points=negative_points,
            positive_points=positive_points,
        )

    negative_line = _fit_line(negative_points, roi)
    positive_line = _fit_line(positive_points, roi)
    if negative_line is None:
        return VPResult(False, "Negative arm fitting failed", roi=roi, threshold_used=threshold_used, points=points)
    if positive_line is None:
        return VPResult(False, "Positive arm fitting failed", roi=roi, threshold_used=threshold_used, points=points)
    if negative_line.rms_px > settings.max_fit_rms_px:
        return VPResult(
            False,
            "Negative arm RMS too high",
            roi=roi,
            threshold_used=threshold_used,
            points=points,
            slopes=slopes,
            negative_points=negative_points,
            positive_points=positive_points,
            negative_line=negative_line,
            positive_line=positive_line,
        )
    if positive_line.rms_px > settings.max_fit_rms_px:
        return VPResult(
            False,
            "Positive arm RMS too high",
            roi=roi,
            threshold_used=threshold_used,
            points=points,
            slopes=slopes,
            negative_points=negative_points,
            positive_points=positive_points,
            negative_line=negative_line,
            positive_line=positive_line,
        )

    vp = _intersect_lines(negative_line, positive_line)
    if vp is None:
        return VPResult(
            False,
            "Fitted arms are nearly parallel",
            roi=roi,
            threshold_used=threshold_used,
            points=points,
            slopes=slopes,
            negative_points=negative_points,
            positive_points=positive_points,
            negative_line=negative_line,
            positive_line=positive_line,
        )
    return VPResult(
        True,
        "OK",
        roi=roi,
        threshold_used=threshold_used,
        points=points,
        slopes=slopes,
        negative_points=negative_points,
        positive_points=positive_points,
        negative_line=negative_line,
        positive_line=positive_line,
        vp=vp,
    )


def draw_overlay(
    frame_bgr: np.ndarray,
    result: VPResult,
    show_points: bool = True,
    show_arm_points: bool = True,
    show_lines: bool = True,
) -> np.ndarray:
    output = cv2.cvtColor(frame_bgr, cv2.COLOR_GRAY2BGR) if frame_bgr.ndim == 2 else frame_bgr.copy()
    if result.roi is not None:
        x1, y1, x2, y2 = result.roi
        cv2.rectangle(output, (x1, y1), (x2 - 1, y2 - 1), (40, 220, 80) if result.ok else (0, 190, 255), 2)
        cv2.line(output, (x1, y1), (x2 - 1, y1), (255, 180, 0), 1)
        cv2.line(output, (x1, y2 - 1), (x2 - 1, y2 - 1), (255, 180, 0), 1)
    if show_points and result.points is not None and len(result.points) > 0:
        for x, y in result.points[:: max(1, len(result.points) // 800)]:
            cv2.circle(output, (int(round(x)), int(round(y))), 2, (35, 35, 190), -1)
    if show_arm_points:
        _draw_points(output, result.negative_points, (255, 80, 80))
        _draw_points(output, result.positive_points, (80, 230, 80))
    if show_lines:
        if result.negative_line is not None:
            cv2.line(output, result.negative_line.segment[0], result.negative_line.segment[1], (255, 70, 220), 2)
        if result.positive_line is not None:
            cv2.line(output, result.positive_line.segment[0], result.positive_line.segment[1], (80, 255, 120), 2)
    if result.vp is not None:
        center = (int(round(result.vp[0])), int(round(result.vp[1])))
        cv2.circle(output, center, 7, (0, 255, 255), 2)
        cv2.drawMarker(output, center, (0, 255, 255), markerType=cv2.MARKER_CROSS, markerSize=18, thickness=2)
    _draw_label(output, result)
    return output


def make_synthetic_v_frame(
    width: int = 1280,
    height: int = 1024,
    vertex: Tuple[float, float] = (640.0, 500.0),
    negative_slope: float = -0.35,
    positive_slope: float = 0.32,
    arm_length_px: int = 430,
    stripe_sigma: float = 2.0,
    noise_sigma: float = 2.5,
    phase: float = 0.0,
) -> np.ndarray:
    vx, vy = vertex
    x = np.arange(width, dtype=np.float32)
    y = np.arange(height, dtype=np.float32)[:, None]
    image = np.zeros((height, width), dtype=np.float32)
    left_mask = (x >= vx - arm_length_px) & (x <= vx)
    right_mask = (x >= vx) & (x <= vx + arm_length_px)
    left_center = vy + negative_slope * (x - vx) + 0.20 * np.sin(x * 0.04 + phase)
    right_center = vy + positive_slope * (x - vx) + 0.20 * np.sin(x * 0.04 + phase)
    left = 245.0 * np.exp(-0.5 * ((y - left_center[None, :]) / stripe_sigma) ** 2)
    right = 245.0 * np.exp(-0.5 * ((y - right_center[None, :]) / stripe_sigma) ** 2)
    image[:, left_mask] += left[:, left_mask]
    image[:, right_mask] += right[:, right_mask]
    if noise_sigma > 0:
        image += np.random.normal(0.0, noise_sigma, image.shape).astype(np.float32)
    return cv2.cvtColor(np.clip(image, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)


def _centroid_thin(gray: np.ndarray, mask: np.ndarray, threshold: int, settings: VPSettings) -> np.ndarray:
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


def _offset_points(points: np.ndarray, x_offset: int, y_offset: int) -> np.ndarray:
    if points is None or len(points) == 0:
        return np.empty((0, 2), dtype=np.float32)
    out = points.astype(np.float32, copy=True)
    out[:, 0] += float(x_offset)
    out[:, 1] += float(y_offset)
    return out


def _local_slopes(points: np.ndarray, window: int) -> np.ndarray:
    window = max(3, int(round(window)))
    if window % 2 == 0:
        window += 1
    half = window // 2
    slopes = np.full((len(points),), np.nan, dtype=np.float32)
    for idx in range(len(points)):
        segment = points[max(0, idx - half) : min(len(points), idx + half + 1)]
        if len(segment) < 3 or abs(float(segment[-1, 0] - segment[0, 0])) < 1e-6:
            continue
        slope, _intercept = np.polyfit(segment[:, 0], segment[:, 1], 1)
        slopes[idx] = float(slope)
    return slopes


def _select_arm_points(points: np.ndarray, mask: np.ndarray, settings: VPSettings) -> np.ndarray:
    mask = np.asarray(mask) & np.isfinite(mask)
    indices = np.flatnonzero(mask)
    if len(indices) == 0:
        return np.empty((0, 2), dtype=np.float32)
    if not settings.use_largest_run:
        return points[indices].astype(np.float32, copy=True)
    groups = []
    start = 0
    for idx in range(1, len(indices)):
        if indices[idx] - indices[idx - 1] > settings.max_run_gap + 1:
            groups.append(indices[start:idx])
            start = idx
    groups.append(indices[start:])
    best = max(groups, key=len)
    return points[best if len(best) >= settings.min_arm_points else indices].astype(np.float32, copy=True)


def _fit_line(points: np.ndarray, roi: Rect) -> Optional[LineModel]:
    if len(points) < 2:
        return None
    fit = cv2.fitLine(points.astype(np.float32), cv2.DIST_HUBER, 0, 0.01, 0.01)
    vx, vy, px, py = [float(v) for v in fit.reshape(-1)]
    if abs(vx) < 1e-8:
        return None
    slope = vy / vx
    intercept = py - slope * px
    residuals = points[:, 1] - (slope * points[:, 0] + intercept)
    return LineModel(
        float(slope),
        float(intercept),
        float(np.sqrt(np.mean(residuals * residuals))),
        int(len(points)),
        _line_segment_in_rect(slope, intercept, roi),
    )


def _intersect_lines(line_a: LineModel, line_b: LineModel) -> Optional[Point]:
    denom = line_a.slope - line_b.slope
    if abs(denom) < 1e-8:
        return None
    x = (line_b.intercept - line_a.intercept) / denom
    y = line_a.slope * x + line_a.intercept
    return (float(x), float(y)) if np.isfinite(x) and np.isfinite(y) else None


def _line_segment_in_rect(slope: float, intercept: float, roi: Rect) -> Segment:
    x1, y1, x2, y2 = roi
    x_min, x_max = float(x1), float(x2 - 1)
    y_min, y_max = float(y1), float(y2 - 1)
    candidates = []
    for x in (x_min, x_max):
        y = slope * x + intercept
        if y_min <= y <= y_max:
            candidates.append((x, y))
    if abs(slope) > 1e-8:
        for y in (y_min, y_max):
            x = (y - intercept) / slope
            if x_min <= x <= x_max:
                candidates.append((x, y))
    unique = []
    for point in candidates:
        if not any(np.hypot(point[0] - old[0], point[1] - old[1]) < 1.0 for old in unique):
            unique.append(point)
    if len(unique) >= 2:
        p1, p2 = max(
            ((a, b) for i, a in enumerate(unique) for b in unique[i + 1 :]),
            key=lambda pair: np.hypot(pair[0][0] - pair[1][0], pair[0][1] - pair[1][1]),
        )
        return _round_point(p1), _round_point(p2)
    return _round_point((x_min, float(np.clip(slope * x_min + intercept, y_min, y_max)))), _round_point(
        (x_max, float(np.clip(slope * x_max + intercept, y_min, y_max)))
    )


def _round_point(point: Tuple[float, float]) -> Tuple[int, int]:
    return int(round(point[0])), int(round(point[1]))


def _draw_points(image: np.ndarray, points: Optional[np.ndarray], color: Tuple[int, int, int]) -> None:
    if points is None or len(points) == 0:
        return
    for x, y in points[:: max(1, len(points) // 500)]:
        cv2.circle(image, (int(round(x)), int(round(y))), 2, color, -1)


def _draw_label(image: np.ndarray, result: VPResult) -> None:
    cv2.rectangle(image, (12, 12), (660, 120), (0, 0, 0), -1)
    line1 = (
        f"VP: x={result.vp[0]:.2f}, y={result.vp[1]:.2f}"
        if result.ok and result.vp is not None
        else f"VP: --   {result.message}"
    )
    neg = result.negative_line
    pos = result.positive_line
    if neg is not None and pos is not None:
        line2 = (
            f"neg m={neg.slope:+.4f}, RMS={neg.rms_px:.2f}px, n={neg.point_count}   "
            f"pos m={pos.slope:+.4f}, RMS={pos.rms_px:.2f}px, n={pos.point_count}"
        )
    else:
        n_neg = 0 if result.negative_points is None else len(result.negative_points)
        n_pos = 0 if result.positive_points is None else len(result.positive_points)
        n_all = 0 if result.points is None else len(result.points)
        line2 = f"points={n_all}   negative arm={n_neg}   positive arm={n_pos}"
    cv2.putText(image, line1, (24, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
    cv2.putText(image, line2, (24, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (185, 235, 255), 1)
    cv2.putText(
        image, f"threshold={result.threshold_used}", (24, 104), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (185, 235, 255), 1
    )
