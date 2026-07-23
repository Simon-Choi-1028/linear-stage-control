from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .gui_support import set_placeholder_color
from .gui_widgets import LinearPathPreviewWidget
from .scan import (
    LINEAR_PATH_MAX_POINTS,
    ScanPoint,
    linear_path_points,
    linear_path_points_by_spacing,
    linear_spacing_point_count,
)
from .text_formatting import (
    linear_distance,
    mm_text,
    optional_float_text,
    optional_int_text,
)

LINEAR_PATH_PREVIEW_MAX_POINTS = 1000


@dataclass(frozen=True)
class LinearPathDialogResult:
    points: list[ScanPoint]
    replace_existing: bool


@dataclass(frozen=True)
class _LinearPathInputs:
    x_start: float
    y_start: float
    x_stop: float
    y_stop: float
    label_prefix: str
    move_velocity_mm_s: float | None
    capture_count: int | None


def _sample_indices(total_count: int, max_points: int = LINEAR_PATH_PREVIEW_MAX_POINTS) -> tuple[int, ...]:
    if total_count < 1:
        return ()
    sample_count = min(total_count, max(2, int(max_points)))
    if sample_count == total_count:
        return tuple(range(total_count))
    return tuple(round(index * (total_count - 1) / (sample_count - 1)) for index in range(sample_count))


def _count_path_preview(
    *,
    x_start: float,
    y_start: float,
    x_stop: float,
    y_stop: float,
    count: int,
    max_points: int = LINEAR_PATH_PREVIEW_MAX_POINTS,
) -> tuple[list[tuple[float, float]], int]:
    if count < 2:
        raise ValueError("선형 경로는 최소 2개 이상의 위치가 필요합니다.")
    if count > LINEAR_PATH_MAX_POINTS:
        raise ValueError(f"선형 경로 위치 수는 {LINEAR_PATH_MAX_POINTS}개를 넘을 수 없습니다.")
    points: list[tuple[float, float]] = []
    for index in _sample_indices(count, max_points):
        ratio = index / (count - 1)
        points.append(
            (
                round(x_start + (x_stop - x_start) * ratio, 9),
                round(y_start + (y_stop - y_start) * ratio, 9),
            )
        )
    return points, count


def _spacing_path_preview(
    *,
    x_start: float,
    y_start: float,
    x_stop: float,
    y_stop: float,
    spacing_mm: float,
    max_points: int = LINEAR_PATH_PREVIEW_MAX_POINTS,
) -> tuple[list[tuple[float, float]], int]:
    if spacing_mm <= 0:
        raise ValueError("선형 경로 간격은 0보다 커야 합니다.")
    dx = x_stop - x_start
    dy = y_stop - y_start
    length = math.hypot(dx, dy)
    if length <= 0:
        raise ValueError("선형 경로 시작점과 끝점이 같습니다.")

    total_count = linear_spacing_point_count(
        x_start,
        y_start,
        x_stop,
        y_stop,
        spacing_mm,
    )
    base_count = int(math.floor(length / spacing_mm)) + 1
    has_separate_endpoint = total_count > base_count

    points: list[tuple[float, float]] = []
    for index in _sample_indices(total_count, max_points):
        distance = length if has_separate_endpoint and index == base_count else round(index * spacing_mm, 9)
        ratio = distance / length
        points.append(
            (
                round(x_start + dx * ratio, 9),
                round(y_start + dy * ratio, 9),
            )
        )
    return points, total_count


def show_linear_path_dialog(
    parent: QWidget,
    defaults: tuple[float, float, float, float],
    *,
    default_capture_count: int,
    append_start_index: int,
) -> LinearPathDialogResult | None:
    start_x, start_y, end_x, end_y = defaults
    default_spacing = max(0.001, linear_distance(start_x, start_y, end_x, end_y) / 10.0)
    dialog = QDialog(parent)
    dialog.setWindowTitle("선형 연속 경로 생성")
    dialog.resize(760, 520)
    layout = QVBoxLayout(dialog)
    body_layout = QHBoxLayout()
    form_widget = QWidget()
    form = QFormLayout(form_widget)

    start_x_edit = QLineEdit(mm_text(start_x))
    start_y_edit = QLineEdit(mm_text(start_y))
    end_x_edit = QLineEdit(mm_text(end_x))
    end_y_edit = QLineEdit(mm_text(end_y))
    basis_combo = QComboBox()
    basis_combo.addItem("간격/캡쳐", "spacing")
    basis_combo.addItem("위치 수", "count")
    spacing_edit = QLineEdit(mm_text(default_spacing))
    spacing_unit_combo = QComboBox()
    spacing_unit_combo.addItem("mm", "mm")
    spacing_unit_combo.addItem("μm", "um")
    spacing_row = QWidget()
    spacing_layout = QHBoxLayout(spacing_row)
    spacing_layout.setContentsMargins(0, 0, 0, 0)
    spacing_layout.setSpacing(6)
    spacing_layout.addWidget(spacing_edit, 1)
    spacing_layout.addWidget(spacing_unit_combo)
    count_spin = QSpinBox()
    count_spin.setRange(2, LINEAR_PATH_MAX_POINTS)
    count_spin.setValue(11)
    count_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
    label_prefix_edit = QLineEdit("line")
    velocity_edit = QLineEdit()
    velocity_edit.setPlaceholderText("비우면 촬영 설정 이동속도 사용")
    capture_count_edit = QLineEdit()
    capture_count_edit.setPlaceholderText(f"비우면 기본 캡쳐 {default_capture_count}장")
    set_placeholder_color(velocity_edit)
    set_placeholder_color(capture_count_edit)
    replace_check = QCheckBox("기존 위치를 지우고 생성")
    preview_widget = LinearPathPreviewWidget()
    preview_status = QLabel()
    preview_status.setWordWrap(True)

    form.addRow("시작 X mm", start_x_edit)
    form.addRow("시작 Y mm", start_y_edit)
    form.addRow("끝 X mm", end_x_edit)
    form.addRow("끝 Y mm", end_y_edit)
    form.addRow("생성 기준", basis_combo)
    form.addRow("간격/캡쳐", spacing_row)
    form.addRow("위치 수", count_spin)
    form.addRow("라벨 접두어", label_prefix_edit)
    form.addRow("이동속도 mm/s", velocity_edit)
    form.addRow("캡쳐 수", capture_count_edit)
    form.addRow("", replace_check)
    body_layout.addWidget(form_widget, 0)
    body_layout.addWidget(preview_widget, 1)
    layout.addLayout(body_layout)
    layout.addWidget(preview_status)

    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    ok_button = buttons.button(QDialogButtonBox.Ok)
    ok_button.setText("생성")
    buttons.button(QDialogButtonBox.Cancel).setText("취소")
    layout.addWidget(buttons)

    def read_inputs() -> _LinearPathInputs:
        x_start = float(start_x_edit.text().strip())
        y_start = float(start_y_edit.text().strip())
        x_stop = float(end_x_edit.text().strip())
        y_stop = float(end_y_edit.text().strip())
        move_velocity = optional_float_text(velocity_edit.text())
        if move_velocity is not None and move_velocity <= 0:
            raise ValueError("이동속도는 비워 두거나 0보다 커야 합니다.")
        capture_count = optional_int_text(capture_count_edit.text())
        if capture_count is not None and capture_count < 1:
            raise ValueError("캡쳐 수는 비워 두거나 1 이상이어야 합니다.")
        label_prefix = label_prefix_edit.text().strip() or "line"
        return _LinearPathInputs(
            x_start=x_start,
            y_start=y_start,
            x_stop=x_stop,
            y_stop=y_stop,
            label_prefix=label_prefix,
            move_velocity_mm_s=move_velocity,
            capture_count=capture_count,
        )

    def build_points(start_index: int = 0) -> list[ScanPoint]:
        inputs = read_inputs()
        if basis_combo.currentData() == "spacing":
            return list(
                linear_path_points_by_spacing(
                    x_start=inputs.x_start,
                    y_start=inputs.y_start,
                    x_stop=inputs.x_stop,
                    y_stop=inputs.y_stop,
                    spacing_mm=spacing_value_mm(),
                    label_prefix=inputs.label_prefix,
                    start_index=start_index,
                    move_velocity_mm_s=inputs.move_velocity_mm_s,
                    capture_count=inputs.capture_count,
                )
            )
        return list(
            linear_path_points(
                x_start=inputs.x_start,
                y_start=inputs.y_start,
                x_stop=inputs.x_stop,
                y_stop=inputs.y_stop,
                count=count_spin.value(),
                label_prefix=inputs.label_prefix,
                start_index=start_index,
                move_velocity_mm_s=inputs.move_velocity_mm_s,
                capture_count=inputs.capture_count,
            )
        )

    def spacing_value_mm() -> float:
        value = float(spacing_edit.text().strip())
        if spacing_unit_combo.currentData() == "um":
            return value / 1000.0
        return value

    def spacing_display_text() -> str:
        value = float(spacing_edit.text().strip())
        unit = str(spacing_unit_combo.currentData())
        if unit == "um":
            return f"{mm_text(value)} μm"
        return f"{mm_text(value)} mm"

    def update_linear_preview() -> None:
        try:
            inputs = read_inputs()
            basis: Literal["spacing", "count"] = "spacing" if basis_combo.currentData() == "spacing" else "count"
            if basis == "spacing":
                preview_points, point_count = _spacing_path_preview(
                    x_start=inputs.x_start,
                    y_start=inputs.y_start,
                    x_stop=inputs.x_stop,
                    y_stop=inputs.y_stop,
                    spacing_mm=spacing_value_mm(),
                )
                count_spin.blockSignals(True)
                count_spin.setValue(point_count)
                count_spin.blockSignals(False)
            else:
                preview_points, point_count = _count_path_preview(
                    x_start=inputs.x_start,
                    y_start=inputs.y_start,
                    x_stop=inputs.x_stop,
                    y_stop=inputs.y_stop,
                    count=count_spin.value(),
                )
            distance = linear_distance(
                inputs.x_start,
                inputs.y_start,
                inputs.x_stop,
                inputs.y_stop,
            )
            capture_count = inputs.capture_count or default_capture_count
            total_captures = point_count * capture_count
            spacing_text = f" | 간격 {spacing_display_text()}" if basis == "spacing" else ""
            summary = f"총 거리 {mm_text(distance)} mm{spacing_text} | 위치 {point_count}개 | 예상 {total_captures}장"
            preview_widget.set_path(preview_points, summary)
            preview_status.setText(summary)
            preview_status.setStyleSheet("color: #1f5f43; font-weight: 600;")
            ok_button.setEnabled(True)
        except Exception as exc:
            if basis_combo.currentData() == "spacing":
                count_spin.blockSignals(True)
                count_spin.setValue(2)
                count_spin.blockSignals(False)
            message = str(exc)
            preview_widget.set_path([], "", message)
            preview_status.setText(message)
            preview_status.setStyleSheet("color: #b42318; font-weight: 700;")
            ok_button.setEnabled(False)

    def sync_generation_mode() -> None:
        use_spacing = basis_combo.currentData() == "spacing"
        spacing_edit.setEnabled(use_spacing)
        spacing_unit_combo.setEnabled(use_spacing)
        count_spin.setReadOnly(use_spacing)
        update_linear_preview()

    spacing_unit = "mm"

    def sync_spacing_unit() -> None:
        nonlocal spacing_unit
        previous_unit = spacing_unit
        next_unit = str(spacing_unit_combo.currentData())
        try:
            value = float(spacing_edit.text().strip())
        except ValueError:
            spacing_unit = next_unit
            update_linear_preview()
            return

        spacing_mm = value / 1000.0 if previous_unit == "um" else value
        spacing_unit = next_unit
        spacing_edit.blockSignals(True)
        spacing_edit.setText(mm_text(spacing_mm * 1000.0 if next_unit == "um" else spacing_mm))
        spacing_edit.blockSignals(False)
        update_linear_preview()

    result: LinearPathDialogResult | None = None

    def accept_generated_path() -> None:
        nonlocal result
        try:
            replace_existing = replace_check.isChecked()
            start_index = 0 if replace_existing else append_start_index
            result = LinearPathDialogResult(
                points=build_points(start_index=start_index),
                replace_existing=replace_existing,
            )
        except Exception as exc:
            QMessageBox.warning(dialog, "경로 생성 오류", str(exc))
            return
        dialog.accept()

    basis_combo.currentIndexChanged.connect(sync_generation_mode)
    spacing_unit_combo.currentIndexChanged.connect(sync_spacing_unit)
    for editor in (
        start_x_edit,
        start_y_edit,
        end_x_edit,
        end_y_edit,
        spacing_edit,
        label_prefix_edit,
        velocity_edit,
        capture_count_edit,
    ):
        editor.textChanged.connect(update_linear_preview)
    count_spin.valueChanged.connect(update_linear_preview)
    buttons.accepted.connect(accept_generated_path)
    buttons.rejected.connect(dialog.reject)
    sync_generation_mode()

    if dialog.exec() == QDialog.Accepted:
        return result
    return None
