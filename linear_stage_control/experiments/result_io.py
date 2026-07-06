from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np


def default_output_dir(feature_key: str) -> Path:
    return Path.home() / "Documents" / "LinearStageControl" / "experiment_outputs" / feature_key


def timestamp_for_filename() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_overlay(path: str | Path, overlay_bgr: Any) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = np.asarray(overlay_bgr)
    if image.ndim not in (2, 3) or image.size == 0:
        raise RuntimeError("Overlay image is empty or has an unsupported shape.")
    if image.ndim == 3 and image.shape[2] not in (3, 4):
        raise RuntimeError(f"Unsupported overlay channel count: {image.shape[2]}")
    if not cv2.imwrite(str(output_path), image):
        raise RuntimeError(f"Failed to save overlay: {output_path}")
    return output_path


def save_csv(path: str | Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> Path:
    row_list = list(rows)
    if not fieldnames:
        raise RuntimeError("CSV field list is empty.")
    if not row_list:
        raise RuntimeError("No measurement rows are available for CSV export.")
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in row_list:
            writer.writerow({field: _csv_value(row.get(field, "")) for field in fieldnames})
    return output_path


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)
