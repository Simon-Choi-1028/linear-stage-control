from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .error_model import ZABER_LDM210_XY_SPECS
from .scan import ScanPoint

POSITION_MIN_MM = 0.0
POSITION_MAX_MM = ZABER_LDM210_XY_SPECS.travel_range_mm
LARGE_POSITION_COUNT_WARNING = 5000
ISSUE_PREVIEW_LIMIT = 6


@dataclass(frozen=True)
class PositionValidationResult:
    errors: list[str]
    warnings: list[str]
    cell_errors: dict[tuple[int, int], str]
    cell_warnings: dict[tuple[int, int], str]

    @property
    def is_valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class PositionInputRow:
    index: int
    label: str
    x_text: str
    y_text: str
    velocity_text: str = ""
    capture_count_text: str = ""


def parse_position_rows(
    rows: list[PositionInputRow],
) -> tuple[list[ScanPoint], PositionValidationResult]:
    points: list[ScanPoint] = []
    errors: list[str] = []
    cell_errors: dict[tuple[int, int], str] = {}

    for row in rows:
        x_mm = _parse_position_cell(row.x_text, row.index, 2, "X", errors, cell_errors)
        y_mm = _parse_position_cell(row.y_text, row.index, 3, "Y", errors, cell_errors)
        move_velocity_mm_s = _parse_optional_positive_float_cell(
            row.velocity_text,
            row.index,
            4,
            "이동속도",
            errors,
            cell_errors,
        )
        capture_count = _parse_optional_int_cell(
            row.capture_count_text,
            row.index,
            5,
            "캡쳐 수",
            errors,
            cell_errors,
            minimum=1,
        )
        if x_mm is None or y_mm is None:
            continue
        if row.velocity_text and move_velocity_mm_s is None:
            continue
        if row.capture_count_text and capture_count is None:
            continue
        points.append(
            ScanPoint(
                index=row.index,
                label=row.label,
                x_mm=x_mm,
                y_mm=y_mm,
                move_velocity_mm_s=move_velocity_mm_s,
                capture_count=capture_count,
            )
        )

    if not points and not errors:
        errors.append("최소 1개 이상의 위치가 필요합니다.")

    validation = validate_scan_points(points)
    errors.extend(validation.errors)
    cell_errors.update(validation.cell_errors)
    return points, PositionValidationResult(
        errors=errors,
        warnings=validation.warnings,
        cell_errors=cell_errors,
        cell_warnings=validation.cell_warnings,
    )


def validate_scan_points(points: list[ScanPoint]) -> PositionValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    cell_errors: dict[tuple[int, int], str] = {}
    cell_warnings: dict[tuple[int, int], str] = {}
    duplicate_positions: dict[tuple[float, float], int] = {}
    blank_label_rows: list[int] = []

    for point in points:
        row_label = f"{point.index + 1}행"
        for column, axis, value in ((2, "X", point.x_mm), (3, "Y", point.y_mm)):
            if value < POSITION_MIN_MM or value > POSITION_MAX_MM:
                detail = (
                    f"{row_label} {axis}={compact_mm(value)} mm: "
                    f"허용 범위 {compact_mm(POSITION_MIN_MM)}-{compact_mm(POSITION_MAX_MM)} mm 밖입니다."
                )
                errors.append(detail)
                cell_errors[(point.index, column)] = detail

        if point.move_velocity_mm_s is not None and point.move_velocity_mm_s <= 0:
            detail = f"{row_label} 이동속도는 비워 두거나 0보다 커야 합니다."
            errors.append(detail)
            cell_errors[(point.index, 4)] = detail

        if point.capture_count is not None and point.capture_count < 1:
            detail = f"{row_label} 캡쳐 수는 비워 두거나 1 이상이어야 합니다."
            errors.append(detail)
            cell_errors[(point.index, 5)] = detail

        if not point.label.strip():
            blank_label_rows.append(point.index)

        key = (round(point.x_mm, 6), round(point.y_mm, 6))
        previous_row = duplicate_positions.get(key)
        if previous_row is None:
            duplicate_positions[key] = point.index
        else:
            detail = (
                f"{previous_row + 1}행과 {point.index + 1}행의 좌표가 같습니다 "
                f"({compact_mm(point.x_mm)}, {compact_mm(point.y_mm)} mm)."
            )
            warnings.append(detail)
            for duplicate_row in (previous_row, point.index):
                cell_warnings[(duplicate_row, 2)] = detail
                cell_warnings[(duplicate_row, 3)] = detail

    if blank_label_rows:
        detail = f"라벨이 비어 있는 위치가 {len(blank_label_rows)}개 있습니다."
        warnings.append(detail)
        for row in blank_label_rows:
            cell_warnings[(row, 1)] = detail

    if len(points) > LARGE_POSITION_COUNT_WARNING:
        warnings.append(f"위치가 {len(points)}개입니다. 장시간 run이 예상되므로 저장 공간과 카메라 발열을 확인하세요.")

    return PositionValidationResult(errors, warnings, cell_errors, cell_warnings)


def disabled_axis_variation_errors(
    points: list[ScanPoint],
    *,
    x_active: bool,
    y_active: bool,
) -> list[str]:
    errors: list[str] = []
    if not x_active and _distinct_axis_values(points, "x_mm") > 1:
        errors.append("X축이 비활성화되어 있지만 위치 목록에 서로 다른 X 값이 있습니다.")
    if not y_active and _distinct_axis_values(points, "y_mm") > 1:
        errors.append("Y축이 비활성화되어 있지만 위치 목록에 서로 다른 Y 값이 있습니다.")
    if not x_active and not y_active:
        errors.append("X/Y 중 최소 하나의 스테이지 축은 활성화해야 합니다.")
    return errors


def short_issue_text(issues: list[str]) -> str:
    if not issues:
        return ""
    head = issues[0]
    rest = len(issues) - 1
    return head if rest <= 0 else f"{head} 외 {rest}개"


def format_issue_list(title: str, issues: list[str]) -> str:
    preview = issues[:ISSUE_PREVIEW_LIMIT]
    lines = [title, ""]
    lines.extend(f"- {issue}" for issue in preview)
    if len(issues) > ISSUE_PREVIEW_LIMIT:
        lines.append(f"- ...외 {len(issues) - ISSUE_PREVIEW_LIMIT}개")
    return "\n".join(lines)


def compact_mm(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number) < 0.0001:
        number = 0.0
    text = f"{number:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _parse_position_cell(
    text: str,
    row: int,
    column: int,
    axis: str,
    errors: list[str],
    cell_errors: dict[tuple[int, int], str],
) -> float | None:
    if not text:
        detail = f"{row + 1}행 {axis} 위치가 비어 있습니다."
        errors.append(detail)
        cell_errors[(row, column)] = detail
        return None
    try:
        return float(text)
    except ValueError:
        detail = f"{row + 1}행 {axis} 위치가 숫자가 아닙니다: {text}"
        errors.append(detail)
        cell_errors[(row, column)] = detail
        return None


def _parse_optional_positive_float_cell(
    text: str,
    row: int,
    column: int,
    label: str,
    errors: list[str],
    cell_errors: dict[tuple[int, int], str],
) -> float | None:
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        detail = f"{row + 1}행 {label} 값이 숫자가 아닙니다: {text}"
        errors.append(detail)
        cell_errors[(row, column)] = detail
        return None
    if value <= 0:
        detail = f"{row + 1}행 {label} 값은 비워 두거나 0보다 커야 합니다: {text}"
        errors.append(detail)
        cell_errors[(row, column)] = detail
        return None
    return value


def _parse_optional_int_cell(
    text: str,
    row: int,
    column: int,
    label: str,
    errors: list[str],
    cell_errors: dict[tuple[int, int], str],
    minimum: int,
) -> int | None:
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        detail = f"{row + 1}행 {label} 값이 정수가 아닙니다: {text}"
        errors.append(detail)
        cell_errors[(row, column)] = detail
        return None
    if not number.is_integer():
        detail = f"{row + 1}행 {label} 값이 정수가 아닙니다: {text}"
        errors.append(detail)
        cell_errors[(row, column)] = detail
        return None
    value = int(number)
    if value < minimum:
        detail = f"{row + 1}행 {label} 값은 비워 두거나 {minimum} 이상이어야 합니다: {text}"
        errors.append(detail)
        cell_errors[(row, column)] = detail
        return None
    return value


def _distinct_axis_values(points: list[ScanPoint], attribute: str) -> int:
    return len({round(float(getattr(point, attribute)), 6) for point in points})
