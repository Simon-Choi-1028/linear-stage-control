from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStyle,
    QWidget,
)

from ..gui_support import apply_button_icon
from ..gui_workers import ManualStageWorker
from ..position_validation import POSITION_MAX_MM, POSITION_MIN_MM
from ..stage import list_serial_ports
from ..text_formatting import number_text as _number_text, optional_float_text as _optional_float_text


class ManualStagePanel(QWidget):
    busy_changed = Signal(bool)
    log_message = Signal(str)

    def __init__(self, config_provider: Callable[[], dict[str, Any]]) -> None:
        super().__init__()
        self.config_provider = config_provider
        self.worker: ManualStageWorker | None = None
        self._closing = False
        self._released_worker_ids: set[int] = set()
        self.setObjectName("manualStagePanel")

        layout = QGridLayout(self)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(6)

        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.refresh_ports_button = QPushButton("새로고침")
        self.x_axis_check = QCheckBox("X축")
        self.y_axis_check = QCheckBox("Y축")
        self.x_axis_check.setChecked(True)
        self.y_axis_check.setChecked(True)
        self.x_edit = QLineEdit("0")
        self.y_edit = QLineEdit("0")
        self.velocity_edit = QLineEdit()
        self.velocity_edit.setPlaceholderText("기본 속도")
        self.step_spin = QDoubleSpinBox()
        self.step_spin.setRange(0.001, POSITION_MAX_MM - POSITION_MIN_MM)
        self.step_spin.setDecimals(3)
        self.step_spin.setSingleStep(0.1)
        self.step_spin.setValue(1.0)
        self.step_spin.setSuffix(" mm")
        self.step_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)

        self.position_button = QPushButton("위치 읽기")
        self.home_button = QPushButton("원점")
        self.move_button = QPushButton("이동")
        self.stop_button = QPushButton("정지")
        self.x_minus_button = QPushButton("X-")
        self.x_plus_button = QPushButton("X+")
        self.y_minus_button = QPushButton("Y-")
        self.y_plus_button = QPushButton("Y+")
        self.status_label = QLabel("대기 중")
        self.status_label.setObjectName("manualStageStatus")
        self.status_label.setWordWrap(True)

        style = self.style()
        apply_button_icon(self.refresh_ports_button, QStyle.SP_BrowserReload, "Zaber COM 포트 목록 새로고침")
        apply_button_icon(self.position_button, QStyle.SP_BrowserReload, "현재 Zaber 위치 읽기")
        apply_button_icon(self.home_button, QStyle.SP_DialogResetButton, "활성 축 원점 복귀 후 X105/Y105로 이동")
        apply_button_icon(self.move_button, QStyle.SP_ArrowForward, "입력한 X/Y 목표 좌표로 이동")
        apply_button_icon(self.stop_button, QStyle.SP_MediaStop, "현재 이동 정지 요청")
        self.x_minus_button.setIcon(style.standardIcon(QStyle.SP_ArrowLeft))
        self.x_plus_button.setIcon(style.standardIcon(QStyle.SP_ArrowRight))
        self.y_minus_button.setIcon(style.standardIcon(QStyle.SP_ArrowDown))
        self.y_plus_button.setIcon(style.standardIcon(QStyle.SP_ArrowUp))

        layout.addWidget(QLabel("포트"), 0, 0)
        layout.addWidget(self.port_combo, 0, 1, 1, 2)
        layout.addWidget(self.refresh_ports_button, 0, 3)
        layout.addWidget(QLabel("축"), 1, 0)
        layout.addWidget(self.x_axis_check, 1, 1)
        layout.addWidget(self.y_axis_check, 1, 2)
        layout.addWidget(QLabel("X mm"), 2, 0)
        layout.addWidget(self.x_edit, 2, 1)
        layout.addWidget(QLabel("Y mm"), 2, 2)
        layout.addWidget(self.y_edit, 2, 3)
        layout.addWidget(QLabel("속도"), 3, 0)
        layout.addWidget(self.velocity_edit, 3, 1)
        layout.addWidget(QLabel("Jog"), 3, 2)
        layout.addWidget(self.step_spin, 3, 3)
        layout.addWidget(self.position_button, 4, 0, 1, 2)
        layout.addWidget(self.home_button, 4, 2, 1, 2)
        layout.addWidget(self.move_button, 5, 0, 1, 2)
        layout.addWidget(self.stop_button, 5, 2, 1, 2)
        layout.addWidget(self.x_minus_button, 6, 0, 1, 2)
        layout.addWidget(self.x_plus_button, 6, 2, 1, 2)
        layout.addWidget(self.y_minus_button, 7, 0, 1, 2)
        layout.addWidget(self.y_plus_button, 7, 2, 1, 2)
        layout.addWidget(self.status_label, 8, 0, 1, 4)

        self.refresh_ports_button.clicked.connect(self.refresh_ports)
        self.position_button.clicked.connect(lambda: self.start_action("position"))
        self.home_button.clicked.connect(self.home)
        self.move_button.clicked.connect(self.move_absolute)
        self.stop_button.clicked.connect(self.stop_stage)
        self.x_minus_button.clicked.connect(lambda: self.jog(-self.step_spin.value(), 0.0))
        self.x_plus_button.clicked.connect(lambda: self.jog(self.step_spin.value(), 0.0))
        self.y_minus_button.clicked.connect(lambda: self.jog(0.0, -self.step_spin.value()))
        self.y_plus_button.clicked.connect(lambda: self.jog(0.0, self.step_spin.value()))
        self.refresh_ports()

    def close(self) -> bool:
        return self.stop_worker(wait_ms=1500, closing=True) and super().close()

    def has_running_worker(self) -> bool:
        return bool(self.worker is not None and self.worker.isRunning())

    def stop_worker(self, wait_ms: int = 0, *, closing: bool = False) -> bool:
        worker = self.worker
        if worker is None:
            return True
        if closing:
            self._closing = True
        if worker.isRunning():
            worker.request_stop()
            if wait_ms and not worker.wait(wait_ms):
                self.status_label.setText("정지 처리 중")
                self._set_busy(True)
                return False
        self._release_worker(worker)
        return True

    def refresh_ports(self) -> None:
        current = self.port_combo.currentText().strip()
        if not current:
            current = str((self.config_provider().get("stage") or {}).get("serial_port") or "COM3")
        self.port_combo.clear()
        seen: set[str] = set()
        for port in list_serial_ports():
            device = port.get("device", "")
            if not device or device in seen:
                continue
            seen.add(device)
            label = f"{device} - {port.get('description', '')}".strip(" -")
            self.port_combo.addItem(label, device)
        if current and current not in seen:
            self.port_combo.addItem(current, current)
        index = self.port_combo.findData(current)
        if index >= 0:
            self.port_combo.setCurrentIndex(index)

    def stage_config(self) -> dict[str, Any]:
        config = deepcopy(self.config_provider())
        stage = config.setdefault("stage", {})
        selected_port = self.port_combo.currentData() or self.port_combo.currentText().split(" - ")[0]
        stage["serial_port"] = str(selected_port or "COM3")
        velocity = _optional_float_text(self.velocity_edit.text())
        if velocity is not None:
            stage["move_velocity_mm_s"] = velocity
        axes = stage.setdefault("axes", {})
        axes.setdefault("x", {})["enabled"] = self.x_axis_check.isChecked()
        axes.setdefault("y", {})["enabled"] = self.y_axis_check.isChecked()
        return config

    def home(self) -> None:
        if self.worker is not None:
            self.status_label.setText("이전 수동 명령 정리 중")
            return
        self._set_busy(True)
        try:
            reply = QMessageBox.question(
                self,
                "원점 복귀",
                "활성화된 Zaber 축을 원점 복귀한 뒤 X=105, Y=105 mm로 50 mm/s 이동할까요?",
            )
        finally:
            if self.worker is None:
                self._set_busy(False)
        if reply == QMessageBox.StandardButton.Yes:
            self.start_action("home")

    def move_absolute(self) -> None:
        try:
            x_mm, y_mm = self._target_values()
        except ValueError as exc:
            QMessageBox.warning(self, "수동 이동 입력 오류", str(exc))
            return
        self.start_action("move", x_mm=x_mm, y_mm=y_mm)

    def jog(self, dx_mm: float, dy_mm: float) -> None:
        try:
            x_mm, y_mm = self._target_values()
        except ValueError as exc:
            QMessageBox.warning(self, "Jog 입력 오류", str(exc))
            return
        if self.x_axis_check.isChecked():
            x_mm += dx_mm
        if self.y_axis_check.isChecked():
            y_mm += dy_mm
        self.x_edit.setText(_number_text(x_mm))
        self.y_edit.setText(_number_text(y_mm))
        self.start_action("move", x_mm=x_mm, y_mm=y_mm)

    def stop_stage(self) -> None:
        if self.worker is not None:
            if self.worker.isRunning():
                self.worker.request_stop()
                self.status_label.setText("정지 요청됨")
                return
            self.status_label.setText("이전 명령 정리 중")
            return
        self.start_action("stop")

    def start_action(self, action: str, *, x_mm: float | None = None, y_mm: float | None = None) -> None:
        if self.worker is not None:
            self.status_label.setText("이전 수동 명령 정리 중")
            return
        try:
            config = self.stage_config()
            velocity = _optional_float_text(self.velocity_edit.text()) or config.get("stage", {}).get(
                "move_velocity_mm_s"
            )
        except Exception as exc:
            QMessageBox.warning(self, "수동 스테이지 설정 오류", str(exc))
            return
        self.status_label.setText("명령 준비 중")
        self._set_busy(True)
        try:
            worker = ManualStageWorker(config, action, x_mm=x_mm, y_mm=y_mm, velocity_mm_s=velocity)
            worker.status_changed.connect(self.status_label.setText)
            worker.position_done.connect(self._on_position)
            worker.action_done.connect(self._on_done)
            worker.action_failed.connect(self._on_failed)
            worker.finished.connect(lambda worker=worker: self._on_finished(worker))
            self.worker = worker
            worker.start()
        except Exception as exc:
            self.worker = None
            self._set_busy(False)
            QMessageBox.warning(self, "수동 스테이지 시작 오류", str(exc))

    def _target_values(self) -> tuple[float, float]:
        x_value = _optional_float_text(self.x_edit.text())
        y_value = _optional_float_text(self.y_edit.text())
        if self.x_axis_check.isChecked() and x_value is None:
            raise ValueError("X축이 활성화되어 있으면 X mm 값을 입력해야 합니다.")
        if self.y_axis_check.isChecked() and y_value is None:
            raise ValueError("Y축이 활성화되어 있으면 Y mm 값을 입력해야 합니다.")
        return float(x_value or 0.0), float(y_value or 0.0)

    def _on_position(self, position: object) -> None:
        try:
            x_mm, y_mm = position
        except (TypeError, ValueError):
            return
        if x_mm is not None:
            self.x_edit.setText(_number_text(x_mm))
        if y_mm is not None:
            self.y_edit.setText(_number_text(y_mm))

    def _on_done(self, message: str) -> None:
        self.status_label.setText(message)
        self.log_message.emit(f"수동 스테이지: {message}")

    def _on_failed(self, message: str) -> None:
        self.status_label.setText("수동 명령 실패")
        self.log_message.emit(f"수동 스테이지 오류: {message}")
        if not self._closing and self.isVisible():
            QMessageBox.warning(self, "수동 스테이지 오류", message)

    def _on_finished(self, worker: ManualStageWorker) -> None:
        self._release_worker(worker)
        self._closing = False

    def _release_worker(self, worker: ManualStageWorker) -> None:
        if self.worker is worker:
            self.worker = None
        worker_id = id(worker)
        if worker_id in self._released_worker_ids:
            return
        self._released_worker_ids.add(worker_id)
        worker.destroyed.connect(lambda *_args, worker_id=worker_id: self._released_worker_ids.discard(worker_id))
        if worker is not None:
            worker.deleteLater()
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        for button in (
            self.position_button,
            self.home_button,
            self.move_button,
            self.x_minus_button,
            self.x_plus_button,
            self.y_minus_button,
            self.y_plus_button,
        ):
            button.setEnabled(not busy)
        self.stop_button.setEnabled(True)
        self.busy_changed.emit(busy)
