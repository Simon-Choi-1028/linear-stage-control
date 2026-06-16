from __future__ import annotations

from dataclasses import dataclass

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
from .scan import LINEAR_PATH_MAX_POINTS, ScanPoint, linear_path_points, linear_path_points_by_spacing
from .text_formatting import (
    linear_distance,
    mm_text,
    optional_float_text,
    optional_int_text,
)


@dataclass(frozen=True)
class LinearPathDialogResult:
    points: list[ScanPoint]
    replace_existing: bool


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

    def build_points(start_index: int = 0) -> list[ScanPoint]:
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
        if basis_combo.currentData() == "spacing":
            return list(
                linear_path_points_by_spacing(
                    x_start=x_start,
                    y_start=y_start,
                    x_stop=x_stop,
                    y_stop=y_stop,
                    spacing_mm=spacing_value_mm(),
                    label_prefix=label_prefix,
                    start_index=start_index,
                    move_velocity_mm_s=move_velocity,
                    capture_count=capture_count,
                )
            )
        return list(
            linear_path_points(
                x_start=x_start,
                y_start=y_start,
                x_stop=x_stop,
                y_stop=y_stop,
                count=count_spin.value(),
                label_prefix=label_prefix,
                start_index=start_index,
                move_velocity_mm_s=move_velocity,
                capture_count=capture_count,
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
            points = build_points()
            if basis_combo.currentData() == "spacing":
                count_spin.blockSignals(True)
                count_spin.setValue(len(points))
                count_spin.blockSignals(False)
            distance = linear_distance(
                float(start_x_edit.text().strip()),
                float(start_y_edit.text().strip()),
                float(end_x_edit.text().strip()),
                float(end_y_edit.text().strip()),
            )
            capture_count = optional_int_text(capture_count_edit.text()) or default_capture_count
            total_captures = len(points) * capture_count
            spacing_text = f" | 간격 {spacing_display_text()}" if basis_combo.currentData() == "spacing" else ""
            summary = f"총 거리 {mm_text(distance)} mm{spacing_text} | 위치 {len(points)}개 | 예상 {total_captures}장"
            preview_widget.set_path([(point.x_mm, point.y_mm) for point in points], summary)
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
