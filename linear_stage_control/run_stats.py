from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class RunStatsAccumulator:
    """Constant-memory statistics used by the GUI error summary."""

    record_count: int = 0
    threshold_failure_count: int = 0
    predicted_max_error_um_max: float | None = None
    predicted_max_error_um_sum: float = 0.0
    predicted_max_error_um_count: int = 0

    def add_record(self, record: dict[str, Any]) -> None:
        self.record_count += 1
        if record.get("status") != "ok":
            return

        predicted_max_error_um = _optional_float(record.get("predicted_max_error_um"))
        if predicted_max_error_um is not None:
            self.predicted_max_error_um_sum += predicted_max_error_um
            self.predicted_max_error_um_count += 1
            self.predicted_max_error_um_max = (
                predicted_max_error_um
                if self.predicted_max_error_um_max is None
                else max(self.predicted_max_error_um_max, predicted_max_error_um)
            )
        if record.get("within_error_threshold") is False:
            self.threshold_failure_count += 1

    def merge(self, other: RunStatsAccumulator) -> None:
        self.record_count += other.record_count
        self.threshold_failure_count += other.threshold_failure_count
        self.predicted_max_error_um_sum += other.predicted_max_error_um_sum
        self.predicted_max_error_um_count += other.predicted_max_error_um_count
        if other.predicted_max_error_um_max is not None:
            self.predicted_max_error_um_max = (
                other.predicted_max_error_um_max
                if self.predicted_max_error_um_max is None
                else max(self.predicted_max_error_um_max, other.predicted_max_error_um_max)
            )


def _optional_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
