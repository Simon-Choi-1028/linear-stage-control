from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StageManufacturerSpecs:
    model_name: str = "Zaber LDM/X-LDM-AE 210 mm crossed XY"
    source_url: str = "https://www.zaber.com/products/linear-stages/X-LDM-AE/specs"
    travel_range_mm: float = 210.0
    accuracy_unidirectional_um: float = 1.0
    repeatability_um: float = 0.08
    horizontal_runout_um: float = 5.0
    vertical_runout_um: float = 8.0
    pitch_mrad: float = 0.174
    roll_mrad: float = 0.087
    yaw_mrad: float = 0.087

    @property
    def axis_xy_worst_case_um(self) -> float:
        return abs(self.accuracy_unidirectional_um) + abs(self.repeatability_um) + abs(self.horizontal_runout_um)

    @property
    def radial_xy_worst_case_um(self) -> float:
        return math.hypot(self.axis_xy_worst_case_um, self.axis_xy_worst_case_um)


ZABER_LDM210_XY_SPECS = StageManufacturerSpecs()


@dataclass(frozen=True)
class ErrorBudgetSettings:
    stage_accuracy_um: float = 1.0
    stage_repeatability_um: float = 0.08
    horizontal_runout_um: float = 5.0
    vertical_runout_um: float = 8.0
    max_allowed_um: float = ZABER_LDM210_XY_SPECS.radial_xy_worst_case_um

    @property
    def axis_worst_case_um(self) -> float:
        return abs(self.stage_accuracy_um) + abs(self.stage_repeatability_um) + abs(self.horizontal_runout_um)

    @property
    def configured_worst_case_um(self) -> float:
        return math.hypot(self.axis_worst_case_um, self.axis_worst_case_um)


@dataclass(frozen=True)
class ErrorEstimate:
    measured_error_x_um: float | None
    measured_error_y_um: float | None
    measured_radial_error_um: float
    predicted_min_error_um: float
    predicted_max_error_um: float
    predicted_x_min_um: float | None
    predicted_x_max_um: float | None
    predicted_y_min_um: float | None
    predicted_y_max_um: float | None
    configured_budget_um: float
    max_allowed_um: float

    @property
    def within_threshold(self) -> bool:
        return self.predicted_max_error_um <= self.max_allowed_um

    def as_record(self) -> dict[str, float | bool | None]:
        return {
            "measured_error_x_um": self.measured_error_x_um,
            "measured_error_y_um": self.measured_error_y_um,
            "measured_radial_error_um": self.measured_radial_error_um,
            "predicted_min_error_um": self.predicted_min_error_um,
            "predicted_max_error_um": self.predicted_max_error_um,
            "predicted_x_min_um": self.predicted_x_min_um,
            "predicted_x_max_um": self.predicted_x_max_um,
            "predicted_y_min_um": self.predicted_y_min_um,
            "predicted_y_max_um": self.predicted_y_max_um,
            "configured_error_budget_um": self.configured_budget_um,
            "max_allowed_error_um": self.max_allowed_um,
            "within_error_threshold": self.within_threshold,
        }


def error_budget_from_config(config: dict[str, Any]) -> ErrorBudgetSettings:
    _ = config
    return ErrorBudgetSettings(
        stage_accuracy_um=ZABER_LDM210_XY_SPECS.accuracy_unidirectional_um,
        stage_repeatability_um=ZABER_LDM210_XY_SPECS.repeatability_um,
        horizontal_runout_um=ZABER_LDM210_XY_SPECS.horizontal_runout_um,
        vertical_runout_um=ZABER_LDM210_XY_SPECS.vertical_runout_um,
        max_allowed_um=ZABER_LDM210_XY_SPECS.radial_xy_worst_case_um,
    )


def fixed_calibration_record() -> dict[str, Any]:
    specs = ZABER_LDM210_XY_SPECS
    return {
        "stage_model": specs.model_name,
        "source_url": specs.source_url,
        "travel_range_mm": specs.travel_range_mm,
        "stage_accuracy_um": specs.accuracy_unidirectional_um,
        "stage_repeatability_um": specs.repeatability_um,
        "horizontal_runout_um": specs.horizontal_runout_um,
        "vertical_runout_um": specs.vertical_runout_um,
        "pitch_mrad": specs.pitch_mrad,
        "roll_mrad": specs.roll_mrad,
        "yaw_mrad": specs.yaw_mrad,
        "xy_axis_worst_case_um": specs.axis_xy_worst_case_um,
        "xy_radial_worst_case_um": specs.radial_xy_worst_case_um,
        "max_allowed_um": specs.radial_xy_worst_case_um,
    }


def estimate_position_error_um(
    error_x_mm: float | None,
    error_y_mm: float | None,
    budget: ErrorBudgetSettings,
    *,
    x_active: bool = True,
    y_active: bool = True,
) -> ErrorEstimate:
    if not x_active and not y_active:
        raise ValueError("At least one axis must be active to estimate position error.")

    error_x_um = error_x_mm * 1000.0 if x_active and error_x_mm is not None else None
    error_y_um = error_y_mm * 1000.0 if y_active and error_y_mm is not None else None
    radial_um = math.hypot(*(value for value in (error_x_um, error_y_um) if value is not None))
    active_axis_count = int(x_active) + int(y_active)
    configured_budget_um = budget.configured_worst_case_um if active_axis_count == 2 else budget.axis_worst_case_um
    max_allowed_um = budget.max_allowed_um if active_axis_count == 2 else budget.axis_worst_case_um
    predicted_min_um = max(0.0, radial_um - configured_budget_um)
    predicted_max_um = max(radial_um, configured_budget_um)
    axis_budget_um = budget.axis_worst_case_um
    return ErrorEstimate(
        measured_error_x_um=error_x_um,
        measured_error_y_um=error_y_um,
        measured_radial_error_um=radial_um,
        predicted_min_error_um=predicted_min_um,
        predicted_max_error_um=predicted_max_um,
        predicted_x_min_um=(error_x_um - axis_budget_um) if error_x_um is not None else None,
        predicted_x_max_um=(error_x_um + axis_budget_um) if error_x_um is not None else None,
        predicted_y_min_um=(error_y_um - axis_budget_um) if error_y_um is not None else None,
        predicted_y_max_um=(error_y_um + axis_budget_um) if error_y_um is not None else None,
        configured_budget_um=configured_budget_um,
        max_allowed_um=max_allowed_um,
    )
