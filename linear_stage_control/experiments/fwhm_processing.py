from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple

import numpy as np

try:
    from scipy.optimize import curve_fit

    SCIPY_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - handled in the GUI fallback path
    curve_fit = None
    SCIPY_IMPORT_ERROR = exc


FWHM_FACTOR = 2.355
SATURATION_THRESHOLD = 250.0
MIN_SIGNAL_AMPLITUDE = 1.0


@dataclass
class FwhmResult:
    col: int
    roi_start: int
    roi_end: int
    peak: float
    saturated: bool
    sigma_px: Optional[float]
    fwhm_px: Optional[float]
    center_row: Optional[float]
    status: str
    roi_col_start: Optional[int] = None
    roi_col_end: Optional[int] = None


def _gaussian(x: np.ndarray, offset: float, amplitude: float, mu: float, sigma: float) -> np.ndarray:
    return offset + amplitude * np.exp(-((x - mu) ** 2) / (2.0 * sigma**2))


def _direct_half_max_fwhm(
    y: np.ndarray,
    row_start: int,
    col: int,
    roi_end: int,
    peak: float,
    saturated: bool,
    baseline: float,
    status: str,
) -> FwhmResult:
    amplitude = float(peak - baseline)
    if not np.isfinite(amplitude) or amplitude < MIN_SIGNAL_AMPLITUDE:
        return FwhmResult(col, row_start, roi_end, peak, saturated, None, None, None, "LOW_SIGNAL")

    half_level = baseline + amplitude * 0.5
    above = y >= half_level
    if not np.any(above):
        return FwhmResult(col, row_start, roi_end, peak, saturated, None, None, None, status)

    peak_idx = int(np.argmax(y))
    left_idx = peak_idx
    while left_idx > 0 and above[left_idx - 1]:
        left_idx -= 1

    right_idx = peak_idx
    last_idx = y.size - 1
    while right_idx < last_idx and above[right_idx + 1]:
        right_idx += 1

    left_cross = float(left_idx)
    if left_idx > 0:
        y0 = float(y[left_idx - 1])
        y1 = float(y[left_idx])
        if y1 != y0:
            left_cross = (left_idx - 1) + (half_level - y0) / (y1 - y0)

    right_cross = float(right_idx)
    if right_idx < last_idx:
        y0 = float(y[right_idx])
        y1 = float(y[right_idx + 1])
        if y1 != y0:
            right_cross = right_idx + (half_level - y0) / (y1 - y0)

    fwhm = max(0.0, right_cross - left_cross)
    if fwhm <= 0:
        return FwhmResult(col, row_start, roi_end, peak, saturated, None, None, None, status)

    center = row_start + (left_cross + right_cross) * 0.5
    return FwhmResult(
        col,
        row_start,
        roi_end,
        peak,
        saturated,
        None,
        fwhm,
        center,
        "SAT_DIRECT" if saturated else "DIRECT",
    )


def sanitize_roi_rows(roi: Optional[Tuple[int, ...]], height: int) -> Tuple[int, int]:
    if height <= 0:
        return 0, 0
    if roi is None:
        return 0, height - 1
    if len(roi) >= 4:
        r0, r1 = sorted((int(roi[2]), int(roi[3])))
    else:
        r0, r1 = sorted((int(roi[0]), int(roi[1])))
    return max(0, min(height - 1, r0)), max(0, min(height - 1, r1))


def sanitize_roi_cols(roi: Optional[Tuple[int, ...]], width: int) -> Tuple[int, int]:
    if width <= 0:
        return 0, 0
    if roi is None or len(roi) < 4:
        return 0, width - 1
    c0, c1 = sorted((int(roi[0]), int(roi[1])))
    return max(0, min(width - 1, c0)), max(0, min(width - 1, c1))


def fit_profile(profile: Sequence[float], row_start: int, col: int, roi_end: int) -> FwhmResult:
    y = np.asarray(profile, dtype=np.float64)
    peak = float(np.max(y)) if y.size else float("nan")
    saturated = bool(np.isfinite(peak) and peak >= SATURATION_THRESHOLD)

    if y.size < 5:
        return FwhmResult(col, row_start, roi_end, peak, saturated, None, None, None, "ROI_TOO_SMALL")
    if not np.all(np.isfinite(y)):
        return FwhmResult(col, row_start, roi_end, peak, saturated, None, None, None, "BAD_DATA")

    baseline = float(np.percentile(y, 10.0))
    amplitude = max(float(peak - baseline), 0.0)
    if amplitude < MIN_SIGNAL_AMPLITUDE:
        return FwhmResult(col, row_start, roi_end, peak, saturated, None, None, None, "LOW_SIGNAL")
    if curve_fit is None:
        return _direct_half_max_fwhm(y, row_start, col, roi_end, peak, saturated, baseline, "NO_SCIPY")

    peak_idx = int(np.argmax(y))
    above_half = np.flatnonzero(y >= baseline + amplitude * 0.5)
    if above_half.size:
        first = int(above_half[0])
        last = int(above_half[-1])
        margin = max(10, int((last - first + 1) * 3))
        fit_start = max(0, first - margin)
        fit_stop = min(y.size, last + margin + 1)
    else:
        fit_start = max(0, peak_idx - 32)
        fit_stop = min(y.size, peak_idx + 33)
    if fit_stop - fit_start < 7:
        fit_start = 0
        fit_stop = y.size

    y_fit = y[fit_start:fit_stop]
    x = np.arange(row_start + fit_start, row_start + fit_stop, dtype=np.float64)
    weights = np.maximum(y - baseline, 0.0)
    if float(np.sum(weights)) > 0.0:
        full_x = np.arange(row_start, row_start + y.size, dtype=np.float64)
        mu0 = float(np.sum(full_x * weights) / np.sum(weights))
        var0 = float(np.sum(weights * (full_x - mu0) ** 2) / np.sum(weights))
        sigma0 = max(np.sqrt(max(var0, 0.0)), 1.0)
    else:
        mu0 = float(row_start + peak_idx)
        sigma0 = max(y.size / 8.0, 1.0)

    mu0 = min(float(x[-1]), max(float(x[0]), mu0))
    sigma_upper = max(float(y_fit.size) * 2.0, 1.0)
    try:
        params, _ = curve_fit(
            _gaussian,
            x,
            y_fit,
            p0=[baseline, amplitude, mu0, min(sigma0, sigma_upper)],
            bounds=([0.0, 0.0, float(x[0]), 0.1], [np.inf, np.inf, float(x[-1]), sigma_upper]),
            maxfev=5000,
        )
    except Exception:
        return _direct_half_max_fwhm(
            y,
            row_start,
            col,
            roi_end,
            peak,
            saturated,
            baseline,
            "SAT" if saturated else "FIT_FAIL",
        )

    sigma = float(abs(params[3]))
    center = float(params[2])
    if not np.isfinite(sigma) or sigma <= 0:
        direct = _direct_half_max_fwhm(
            y,
            row_start,
            col,
            roi_end,
            peak,
            saturated,
            baseline,
            "SAT" if saturated else "FIT_FAIL",
        )
        direct.center_row = center if np.isfinite(center) else direct.center_row
        return direct

    return FwhmResult(
        col=col,
        roi_start=row_start,
        roi_end=roi_end,
        peak=peak,
        saturated=saturated,
        sigma_px=sigma,
        fwhm_px=FWHM_FACTOR * sigma,
        center_row=center,
        status="SAT" if saturated else "OK",
    )


def calculate_for_columns(
    frame: np.ndarray,
    columns: Iterable[int],
    roi: Optional[Tuple[int, ...]] = None,
) -> list[FwhmResult]:
    if frame.ndim != 2:
        raise ValueError("FWHM calculation expects a 2D grayscale frame.")

    height, width = frame.shape
    r0, r1 = sanitize_roi_rows(roi, height)
    c0, c1 = sanitize_roi_cols(roi, width)
    results: list[FwhmResult] = []

    for col in columns:
        col = int(col)
        if col < 0 or col >= width:
            result = FwhmResult(col, r0, r1, float("nan"), False, None, None, None, "COL_RANGE")
        elif col < c0 or col > c1:
            result = FwhmResult(col, r0, r1, float("nan"), False, None, None, None, "ROI_X_RANGE")
        else:
            result = fit_profile(frame[r0 : r1 + 1, col], r0, col, r1)
        result.roi_col_start = c0
        result.roi_col_end = c1
        results.append(result)
    return results


def average_valid_fwhm(results: Iterable[FwhmResult]) -> Optional[float]:
    values = [item.fwhm_px for item in results if is_valid_fwhm_result(item)]
    return float(np.mean(values)) if values else None


def is_valid_fwhm_result(result: FwhmResult) -> bool:
    return (
        result.fwhm_px is not None
        and np.isfinite(result.fwhm_px)
        and not result.saturated
        and result.status in {"OK", "DIRECT"}
    )


def make_synthetic_fwhm_frame(
    width: int = 1280,
    height: int = 720,
    center_row: float | None = None,
    sigma_px: float = 3.0,
    noise_sigma: float = 2.0,
    phase: float = 0.0,
) -> np.ndarray:
    center = float(center_row if center_row is not None else height * 0.52)
    x = np.arange(width, dtype=np.float32)
    y = np.arange(height, dtype=np.float32)[:, None]
    wobble = 2.0 * np.sin(x * 0.02 + phase)
    stripe = 220.0 * np.exp(-0.5 * ((y - (center + wobble)[None, :]) / sigma_px) ** 2)
    background = 8.0 + 4.0 * np.sin(x * 0.008)[None, :]
    image = stripe + background
    if noise_sigma > 0:
        image += np.random.normal(0.0, noise_sigma, (height, width)).astype(np.float32)
    return np.clip(image, 0, 255).astype(np.uint8)
