from __future__ import annotations

import csv
import math
import os
import sys
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from PIL import Image
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontDatabase, QImage, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSpinBox,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .camera import iso_timestamp
from .config import ConfigError, load_config
from .dataset_exports import DEFAULT_METADATA_FORMATS, DEFAULT_SUMMARY_FORMATS
from .error_model import (
    ZABER_LDM210_XY_SPECS,
    error_budget_from_config,
    fixed_calibration_record,
)
from .gui_workers import AcquisitionWorker, CameraDiscoveryWorker
from .gui_widgets import ErrorChartWidget, FullscreenImageWindow, ImagePreviewLabel, ParameterAdjustRow
from .position_validation import (
    POSITION_MAX_MM,
    POSITION_MIN_MM,
    PositionInputRow,
    PositionValidationResult,
    format_issue_list,
    parse_position_rows,
    short_issue_text,
)
from .scan import (
    ScanPoint,
    default_capture_count_from_config,
    linear_path_points,
    linear_path_points_by_spacing,
    points_from_config,
    points_from_file,
    total_capture_count,
)
from .stage import list_serial_ports


APP_TITLE = "XY 스테이지 캡처"
POSITION_PLACEHOLDER_ROLE = Qt.UserRole + 50


@dataclass(frozen=True)
class PreflightIssue:
    item: str
    status: str
    detail: str


class MainWindow(QMainWindow):
    def __init__(self, start_device_scan: bool = True) -> None:
        super().__init__()
        _apply_default_font()
        self.setWindowTitle(APP_TITLE)
        self.resize(1320, 820)
        self.config_path = Path("config.yaml")
        self.config: dict[str, Any] = {}
        self.worker: AcquisitionWorker | None = None
        self.camera_scan_worker: CameraDiscoveryWorker | None = None
        self.current_run_dir: Path | None = None
        self.current_image_path: Path | None = None
        self.image_viewer: FullscreenImageWindow | None = None
        self.error_records: list[dict[str, Any]] = []
        self._camera_signature: tuple[str, ...] = ()
        self._preferred_camera_serial = ""
        self._camera_user_touched = False
        self._layout_is_narrow: bool | None = None
        self._build_ui()
        self._apply_style()
        self._load_initial_config()
        if start_device_scan:
            self.refresh_devices()
        else:
            self._set_camera_scan_state("idle", "대기", "스모크 테스트 모드")

    def closeEvent(self, event: object) -> None:
        if self.camera_scan_worker is not None and self.camera_scan_worker.isRunning():
            self.camera_scan_worker.wait(2000)
        super().closeEvent(event)

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)
        self.update_responsive_layout()

    def _build_ui(self) -> None:
        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(10, 8, 10, 8)
        self.load_config_button = QPushButton("설정 불러오기")
        self.save_config_button = QPushButton("설정 저장")
        self.refresh_button = QPushButton("장비 새로고침")
        self.open_dataset_button = QPushButton("데이터셋 열기")
        self.open_dataset_button.setEnabled(False)
        _apply_button_icon(self.load_config_button, QStyle.SP_DialogOpenButton, "YAML 설정 파일 불러오기")
        _apply_button_icon(self.save_config_button, QStyle.SP_DialogSaveButton, "현재 설정 저장")
        _apply_button_icon(self.refresh_button, QStyle.SP_BrowserReload, "카메라와 스테이지 포트 새로고침")
        _apply_button_icon(self.open_dataset_button, QStyle.SP_DirOpenIcon, "최근 데이터셋 폴더 열기")
        toolbar_layout.addWidget(self.load_config_button)
        toolbar_layout.addWidget(self.save_config_button)
        toolbar_layout.addWidget(self.refresh_button)
        toolbar_layout.addStretch(1)
        toolbar_layout.addWidget(self.open_dataset_button)

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.control_panel = self._build_control_panel()
        self.control_scroll = QScrollArea()
        self.control_scroll.setWidgetResizable(True)
        self.control_scroll.setFrameShape(QScrollArea.NoFrame)
        self.control_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.control_scroll.setWidget(self.control_panel)
        self.preview_panel = self._build_preview_panel()
        self.main_splitter.addWidget(self.control_scroll)
        self.main_splitter.addWidget(self.preview_panel)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([470, 850])

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(toolbar)
        root_layout.addWidget(self.main_splitter, 1)
        self.setCentralWidget(root)

        self.load_config_button.clicked.connect(self.load_config_dialog)
        self.save_config_button.clicked.connect(self.save_config_dialog)
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.open_dataset_button.clicked.connect(self.open_current_dataset)
        self.update_responsive_layout()

    def update_responsive_layout(self) -> None:
        if not hasattr(self, "main_splitter"):
            return
        is_narrow = self.width() < 980
        if self._layout_is_narrow == is_narrow:
            return
        self._layout_is_narrow = is_narrow
        if is_narrow:
            self.main_splitter.setOrientation(Qt.Vertical)
            self.control_scroll.setMinimumWidth(0)
            self.preview_label.setMinimumHeight(260)
            self.main_splitter.setSizes([380, 560])
        else:
            self.main_splitter.setOrientation(Qt.Horizontal)
            self.control_scroll.setMinimumWidth(420)
            self.preview_label.setMinimumHeight(360)
            self.main_splitter.setSizes([470, max(650, self.width() - 470)])

    def _build_control_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 10, 12)
        layout.setSpacing(10)

        layout.addWidget(self._build_device_group())
        layout.addWidget(self._build_settings_group())
        layout.addWidget(self._build_positions_group(), 1)
        layout.addWidget(self._build_run_group())
        return panel

    def _build_device_group(self) -> QGroupBox:
        group = QGroupBox("장비")
        form = QFormLayout(group)
        self.camera_combo = QComboBox()
        self.stage_port_combo = QComboBox()
        self.camera_scan_button = QPushButton("자동검색")
        self.camera_scan_state_label = QLabel("대기")
        self.camera_scan_state_label.setAlignment(Qt.AlignCenter)
        self.camera_scan_state_label.setObjectName("cameraScanState")
        self.camera_scan_state_label.setProperty("state", "idle")
        self.camera_status_label = QLabel("카메라 검색 대기 중")
        self.camera_status_label.setObjectName("cameraStatus")
        self.camera_status_label.setProperty("state", "idle")
        self.camera_status_label.setWordWrap(True)
        self.camera_status_icon = QLabel()
        self.camera_status_icon.setPixmap(
            QApplication.style().standardIcon(QStyle.SP_MessageBoxWarning).pixmap(QSize(18, 18))
        )
        self.camera_status_icon.setVisible(False)
        self.camera_combo.setToolTip("촬영에 사용할 Basler 카메라입니다. 기본값: 자동 선택")
        self.stage_port_combo.setToolTip("Zaber 스테이지가 연결된 COM 포트입니다. 기본값: COM3")
        self.camera_scan_button.setToolTip("LAN/GigE Basler 카메라를 한 번 검색합니다. 반복 검색은 수행하지 않습니다.")
        self.camera_scan_state_label.setToolTip("카메라 자동검색 상태입니다: 대기, 탐색중, 성공, 실패")
        self.camera_status_label.setToolTip("마지막 Basler 카메라 검색 결과와 선택된 장비를 표시합니다.")
        _apply_button_icon(self.camera_scan_button, QStyle.SP_BrowserReload, "LAN Basler 카메라 자동검색 실행")
        camera_scan_row = QWidget()
        camera_scan_layout = QHBoxLayout(camera_scan_row)
        camera_scan_layout.setContentsMargins(0, 0, 0, 0)
        camera_scan_layout.setSpacing(8)
        camera_scan_layout.addWidget(self.camera_scan_button)
        camera_scan_layout.addWidget(self.camera_scan_state_label)
        camera_scan_layout.addStretch(1)
        camera_status_row = QWidget()
        camera_status_layout = QHBoxLayout(camera_status_row)
        camera_status_layout.setContentsMargins(0, 0, 0, 0)
        camera_status_layout.setSpacing(6)
        camera_status_layout.addWidget(self.camera_status_icon)
        camera_status_layout.addWidget(self.camera_status_label, 1)
        form.addRow("카메라", self.camera_combo)
        form.addRow("자동검색", camera_scan_row)
        form.addRow("검색 결과", camera_status_row)
        form.addRow("스테이지 포트", self.stage_port_combo)
        self.camera_scan_button.clicked.connect(lambda: self.start_camera_scan("manual"))
        self.camera_combo.activated.connect(self.on_camera_combo_activated)
        return group

    def _build_settings_group(self) -> QGroupBox:
        group = QGroupBox("촬영 설정")
        form = QFormLayout(group)

        output_row = QWidget()
        output_layout = QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        self.output_root_edit = QLineEdit()
        self.output_browse_button = QPushButton("찾기")
        _apply_button_icon(self.output_browse_button, QStyle.SP_DirOpenIcon, "저장 폴더 선택")
        self.output_browse_button.setMinimumWidth(72)
        output_layout.addWidget(self.output_root_edit, 1)
        output_layout.addWidget(self.output_browse_button)

        self.exposure_spin = QSpinBox()
        self.exposure_spin.setRange(1, 10_000_000)
        self.exposure_spin.setSuffix("us")
        self.exposure_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.settle_spin = QSpinBox()
        self.settle_spin.setRange(0, 60_000)
        self.settle_spin.setSuffix("ms")
        self.settle_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.velocity_edit = QLineEdit()
        self.velocity_edit.setPlaceholderText("장비 기본값")
        _set_placeholder_color(self.velocity_edit)
        self.capture_count_spin = QSpinBox()
        self.capture_count_spin.setRange(1, 100_000)
        self.capture_count_spin.setValue(1)
        self.capture_count_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.pixel_format_combo = QComboBox()
        self.pixel_format_combo.setEditable(True)
        self.pixel_format_combo.addItems(
            [
                "Mono8",
                "Mono10",
                "Mono12",
                "Mono16",
                "BayerRG8",
                "BayerGB8",
                "BayerGR8",
                "BayerBG8",
                "RGB8",
                "BGR8",
                "Auto",
            ]
        )
        self.software_trigger_check = QCheckBox("소프트웨어 트리거")
        self.save_numpy_check = QCheckBox("NPY 저장")
        self.skip_home_check = QCheckBox("원점 복귀 생략")
        self.metadata_format_checks = self._format_checks(DEFAULT_METADATA_FORMATS)
        self.summary_format_checks = self._format_checks(DEFAULT_SUMMARY_FORMATS)
        self.exposure_row = ParameterAdjustRow(
            "노출",
            self.exposure_spin,
            (-1000, -100, -10, 10, 100, 1000),
            "us",
            "카메라 노출 시간을 조정합니다. 기본값: 5000us",
            lambda delta: self._adjust_spin_value(self.exposure_spin, delta),
        )
        self.settle_row = ParameterAdjustRow(
            "안정화",
            self.settle_spin,
            (-100, -10, -5, 5, 10, 100),
            "ms",
            "스테이지 이동 완료 후 촬영 전 대기 시간을 조정합니다. 기본값: 200ms",
            lambda delta: self._adjust_spin_value(self.settle_spin, delta),
        )
        self.velocity_row = ParameterAdjustRow(
            "이동속도\nmm/s",
            self.velocity_edit,
            (-100, -10, -5, 5, 10, 100),
            "mm/s",
            "Zaber 스테이지 이동 속도를 조정합니다. 기본값: 비워두면 장비 기본 속도",
            self._adjust_velocity_value,
        )
        self.capture_count_row = ParameterAdjustRow(
            "기본 캡쳐\n장",
            self.capture_count_spin,
            (-100, -10, -1, 1, 10, 100),
            "장",
            "위치별 캡쳐 수가 비어 있을 때 사용할 기본 촬영 횟수입니다. 기본값: 1장",
            lambda delta: self._adjust_spin_value(self.capture_count_spin, delta),
        )
        self._set_settings_tooltips()

        form.addRow("저장 위치", output_row)
        form.addRow(self.exposure_row)
        form.addRow(self.settle_row)
        form.addRow(self.velocity_row)
        form.addRow(self.capture_count_row)
        form.addRow("픽셀 형식", self.pixel_format_combo)
        form.addRow("메타데이터", self._format_check_grid(self.metadata_format_checks))
        form.addRow("요약", self._format_check_grid(self.summary_format_checks))
        form.addRow("실행 옵션", self._option_check_box())

        self.output_browse_button.clicked.connect(self.browse_output_root)
        self.velocity_edit.textChanged.connect(self.refresh_position_feedback)
        self.capture_count_spin.valueChanged.connect(self.refresh_position_feedback)
        return group

    def _format_checks(self, formats: tuple[str, ...]) -> dict[str, QCheckBox]:
        checks: dict[str, QCheckBox] = {}
        for item in formats:
            label = "Markdown" if item == "md" else item.upper()
            check = QCheckBox(label)
            check.setChecked(True)
            checks[item] = check
        return checks

    def _format_check_grid(self, checks: dict[str, QCheckBox]) -> QWidget:
        widget = QWidget()
        widget.setObjectName("formatBox")
        layout = QGridLayout(widget)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(4)
        for index, check in enumerate(checks.values()):
            layout.addWidget(check, index // 3, index % 3)
        return widget

    def _option_check_box(self) -> QWidget:
        widget = QWidget()
        widget.setObjectName("optionBox")
        layout = QGridLayout(widget)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(5)
        for index, check in enumerate(
            (self.software_trigger_check, self.save_numpy_check, self.skip_home_check)
        ):
            layout.addWidget(check, index // 2, index % 2)
        return widget

    def _set_settings_tooltips(self) -> None:
        self.output_root_edit.setToolTip("run별 데이터셋을 저장할 폴더입니다. 기본값: Documents/LinearStageControl/datasets")
        self.output_browse_button.setToolTip("데이터셋 저장 폴더 선택")
        self.capture_count_spin.setToolTip("위치별 캡쳐 수가 비어 있을 때 사용할 기본 촬영 횟수입니다. 기본값: 1")
        self.pixel_format_combo.setToolTip("Basler 원본 픽셀 형식입니다. 기본값: Mono8")
        self.software_trigger_check.setToolTip("카메라 FrameStart software trigger를 사용합니다. 기본값: 켜짐")
        self.save_numpy_check.setToolTip("원본 배열을 NPY로 추가 저장합니다. 기본값: 꺼짐")
        self.skip_home_check.setToolTip("run 시작 시 Zaber 원점 복귀를 생략합니다. 기본값: 꺼짐")
        for name, check in self.metadata_format_checks.items():
            check.setToolTip(f"run 종료 후 captures.{name} 메타데이터를 저장합니다.")
        for name, check in self.summary_format_checks.items():
            suffix = "md" if name == "md" else name
            check.setToolTip(f"run 종료 후 summary.{suffix} 요약 파일을 저장합니다.")

    def _adjust_spin_value(self, spin: QSpinBox, delta: int) -> None:
        spin.setValue(max(spin.minimum(), min(spin.maximum(), spin.value() + delta)))

    def _adjust_velocity_value(self, delta: int) -> None:
        try:
            current = _optional_float_text(self.velocity_edit.text()) or 0.0
        except ValueError:
            current = 0.0
        value = max(0.0, current + delta)
        self.velocity_edit.setText("" if value <= 0 else _number_text(value))

    def _stage_specs_rows(self) -> list[tuple[str, str]]:
        specs = ZABER_LDM210_XY_SPECS
        return [
            ("모델/구성", specs.model_name),
            ("이동 범위", f"{_mm_text(specs.travel_range_mm)} mm"),
            ("단축 정확도", f"{_um_text(specs.accuracy_unidirectional_um)} um"),
            ("반복 정밀도", f"< {_um_text(specs.repeatability_um)} um"),
            ("수평 런아웃", f"< {_um_text(specs.horizontal_runout_um)} um"),
            ("수직 런아웃", f"< {_um_text(specs.vertical_runout_um)} um"),
            ("XY 단축 worst-case", f"{_um_text(specs.axis_xy_worst_case_um)} um"),
            ("XY 반경 worst-case", f"{_um_text(specs.radial_xy_worst_case_um)} um"),
        ]

    def show_stage_specs_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Zaber 제조사 스펙")
        dialog.resize(560, 380)
        layout = QVBoxLayout(dialog)

        note = QLabel(
            "Zaber 210 mm LDM/X-LDM-AE crossed XY 스테이지 제조사 스펙을 "
            "고정 기준으로 사용합니다. 이 값은 촬영 중 사용자가 수정하지 않습니다."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        rows = self._stage_specs_rows()
        table = QTableWidget(len(rows), 2)
        table.setObjectName("stageSpecs")
        table.setHorizontalHeaderLabels(["항목", "고정값"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setFocusPolicy(Qt.NoFocus)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        for row, (name, value) in enumerate(rows):
            name_item = QTableWidgetItem(name)
            value_item = QTableWidgetItem(value)
            for item in (name_item, value_item):
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setToolTip("Zaber 공식 210 mm LDM/X-LDM-AE 스펙 기준 고정값")
            value_item.setTextAlignment(Qt.AlignCenter)
            table.setItem(row, 0, name_item)
            table.setItem(row, 1, value_item)
        layout.addWidget(table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _build_positions_group(self) -> QGroupBox:
        group = QGroupBox("이동 위치")
        layout = QVBoxLayout(group)
        button_grid = QGridLayout()
        button_grid.setContentsMargins(0, 0, 0, 0)
        button_grid.setHorizontalSpacing(6)
        button_grid.setVerticalSpacing(6)
        self.add_row_button = QPushButton("추가")
        self.delete_row_button = QPushButton("삭제")
        self.import_csv_button = QPushButton("파일 불러오기")
        self.export_csv_button = QPushButton("CSV 저장")
        self.linear_path_button = QPushButton("선형 경로")
        self.clear_rows_button = QPushButton("비우기")
        _apply_button_icon(self.add_row_button, QStyle.SP_FileDialogNewFolder, "위치 행 추가")
        _apply_button_icon(self.delete_row_button, QStyle.SP_TrashIcon, "선택 위치 삭제")
        _apply_button_icon(self.import_csv_button, QStyle.SP_DialogOpenButton, "CSV/TSV/TXT/JSON/YAML/XLSX 위치 목록 불러오기")
        _apply_button_icon(self.export_csv_button, QStyle.SP_DialogSaveButton, "현재 위치 목록 CSV 저장")
        _apply_button_icon(self.linear_path_button, QStyle.SP_FileDialogDetailedView, "시작점과 끝점을 잇는 선형 연속 경로 생성 설정")
        _apply_button_icon(self.clear_rows_button, QStyle.SP_LineEditClearButton, "위치 목록 비우기")
        button_grid.addWidget(self.add_row_button, 0, 0)
        button_grid.addWidget(self.delete_row_button, 0, 1)
        button_grid.addWidget(self.import_csv_button, 0, 2)
        button_grid.addWidget(self.linear_path_button, 1, 0)
        button_grid.addWidget(self.export_csv_button, 1, 1)
        button_grid.addWidget(self.clear_rows_button, 1, 2)

        self.positions_table = QTableWidget(0, 6)
        self.positions_table.setHorizontalHeaderLabels(["#", "라벨", "X mm", "Y mm", "속도\nmm/s", "캡쳐 수"])
        self.positions_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.positions_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.positions_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.positions_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.positions_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.positions_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.positions_table.setToolTip(
            "속도와 캡쳐 수는 비워 두면 촬영 설정의 이동속도/기본 캡쳐 수를 사용합니다."
        )
        self.positions_table.verticalHeader().setVisible(False)
        self.positions_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.positions_table.itemChanged.connect(self.on_position_item_changed)
        self.position_status_label = QLabel(
            f"위치 범위: {_mm_text(POSITION_MIN_MM)}-{_mm_text(POSITION_MAX_MM)} mm"
        )
        self.position_status_label.setObjectName("positionStatus")
        self.position_status_label.setWordWrap(True)

        layout.addLayout(button_grid)
        layout.addWidget(self.positions_table, 1)
        layout.addWidget(self.position_status_label)

        self.add_row_button.clicked.connect(lambda: self.add_position_row())
        self.delete_row_button.clicked.connect(self.delete_selected_positions)
        self.import_csv_button.clicked.connect(self.import_positions_csv)
        self.export_csv_button.clicked.connect(self.export_positions_csv)
        self.linear_path_button.clicked.connect(self.generate_linear_path_dialog)
        self.clear_rows_button.clicked.connect(self.clear_positions)
        return group

    def _build_run_group(self) -> QGroupBox:
        group = QGroupBox("진행")
        layout = QGridLayout(group)
        self.start_button = QPushButton("시작")
        self.stop_button = QPushButton("중지")
        self.start_button.setObjectName("runControlButton")
        self.stop_button.setObjectName("runControlButton")
        self.start_button.setMinimumHeight(42)
        self.stop_button.setMinimumHeight(42)
        _apply_button_icon(self.start_button, QStyle.SP_MediaPlay, "촬영 run 시작")
        _apply_button_icon(self.stop_button, QStyle.SP_MediaStop, "현재 run 중지 요청")
        self.stop_button.setEnabled(False)
        self.run_status_label = QLabel("대기 중")
        self.run_status_label.setObjectName("runStatus")
        self.progress_detail_label = QLabel("0/0")
        self.progress_detail_label.setObjectName("progressDetail")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        layout.addWidget(self.start_button, 0, 0)
        layout.addWidget(self.stop_button, 0, 1)
        layout.addWidget(self.run_status_label, 1, 0, 1, 2)
        layout.addWidget(self.progress_bar, 2, 0, 1, 2)
        layout.addWidget(self.progress_detail_label, 3, 0, 1, 2)
        self.start_button.clicked.connect(self.start_run)
        self.stop_button.clicked.connect(self.stop_run)
        return group

    def _build_preview_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 12, 12, 12)

        self.preview_label = ImagePreviewLabel("이미지 없음")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(360)
        self.preview_label.setObjectName("preview")
        self.preview_label.double_clicked.connect(self.open_fullscreen_image)
        self.preview_info_label = QLabel("촬영 이미지를 선택하면 아래 칸에 위치와 오차가 표시됩니다")
        self.preview_info_label.setWordWrap(True)
        self.preview_info_label.setObjectName("previewInfo")
        self.fullscreen_button = QPushButton("전체화면 보기")
        _apply_button_icon(self.fullscreen_button, QStyle.SP_TitleBarMaxButton, "이미지를 전체화면 확대 창으로 열기")
        self.fullscreen_button.setEnabled(False)
        self.fullscreen_button.clicked.connect(self.open_fullscreen_image)
        preview_info_row = QHBoxLayout()
        preview_info_row.addWidget(self.preview_info_label, 1)
        preview_info_row.addWidget(self.fullscreen_button)

        self.preview_metrics_table = QTableWidget(1, 11)
        self.preview_metrics_table.setObjectName("previewMetrics")
        self.preview_metrics_table.setHorizontalHeaderLabels(
            [
                "#",
                "캡쳐",
                "라벨",
                "목표 X\nmm",
                "목표 Y\nmm",
                "실제 X\nmm",
                "실제 Y\nmm",
                "반경\num",
                "예측 하한\num",
                "예측 상한\num",
                "판정",
            ]
        )
        self.preview_metrics_table.verticalHeader().setVisible(False)
        self.preview_metrics_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.preview_metrics_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.preview_metrics_table.setFocusPolicy(Qt.NoFocus)
        self.preview_metrics_table.setMinimumHeight(86)
        self.preview_metrics_table.setMaximumHeight(96)
        for column in range(self.preview_metrics_table.columnCount()):
            self.preview_metrics_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.preview_metrics_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._set_preview_metric_values(["-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-"])

        tabs = QTabWidget()
        captures_tab = QWidget()
        captures_layout = QVBoxLayout(captures_tab)
        self.captures_table = QTableWidget(0, 13)
        self.captures_table.setHorizontalHeaderLabels(
            [
                "#",
                "캡쳐",
                "라벨",
                "상태",
                "목표 X\nmm",
                "목표 Y\nmm",
                "실제 X\nmm",
                "실제 Y\nmm",
                "반경\num",
                "예측 하한\num",
                "예측 상한\num",
                "판정",
                "이미지",
            ]
        )
        for column in range(self.captures_table.columnCount()):
            self.captures_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.captures_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.captures_table.horizontalHeader().setSectionResizeMode(12, QHeaderView.Stretch)
        self.captures_table.verticalHeader().setVisible(False)
        self.captures_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.captures_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.captures_table.cellClicked.connect(self.preview_capture_row)
        captures_layout.addWidget(self.captures_table)

        error_tab = QWidget()
        error_layout = QVBoxLayout(error_tab)
        error_basis_row = QHBoxLayout()
        self.error_basis_label = QLabel(
            f"오차 기준: Zaber 210 mm 고정 / XY {_um_text(ZABER_LDM210_XY_SPECS.radial_xy_worst_case_um)} um"
        )
        self.error_basis_label.setObjectName("errorBasis")
        self.specs_button = QPushButton("스펙 보기")
        _apply_button_icon(self.specs_button, QStyle.SP_MessageBoxInformation, "Zaber 제조사 고정 스펙 확인")
        self.specs_button.clicked.connect(self.show_stage_specs_dialog)
        error_basis_row.addWidget(self.error_basis_label, 1)
        error_basis_row.addWidget(self.specs_button)
        self.error_summary_table = QTableWidget(1, 6)
        self.error_summary_table.setObjectName("errorSummary")
        self.error_summary_table.setHorizontalHeaderLabels(
            ["상태", "Worst-case\num", "허용 한계\num", "예측 최대\num", "평균\num", "한계 초과"]
        )
        self.error_summary_table.verticalHeader().setVisible(False)
        self.error_summary_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.error_summary_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.error_summary_table.setFocusPolicy(Qt.NoFocus)
        self.error_summary_table.setMinimumHeight(96)
        self.error_summary_table.setMaximumHeight(112)
        for column in range(self.error_summary_table.columnCount()):
            self.error_summary_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Stretch)
        self._set_error_summary_values(["촬영 전", "-", "-", "-", "-", "-"])
        self.error_chart = ErrorChartWidget()
        error_layout.addLayout(error_basis_row)
        error_layout.addWidget(self.error_summary_table)
        error_layout.addWidget(self.error_chart, 1)

        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        log_layout.addWidget(self.log_edit)

        tabs.addTab(captures_tab, "촬영 목록")
        tabs.addTab(error_tab, "오차")
        tabs.addTab(log_tab, "로그")

        layout.addWidget(self.preview_label, 3)
        layout.addLayout(preview_info_row)
        layout.addWidget(self.preview_metrics_table)
        layout.addWidget(tabs, 2)
        return panel

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f5f6f7; color: #1e2329; }
            QGroupBox {
                border: 1px solid #d2d7dd;
                border-radius: 6px;
                margin-top: 10px;
                padding: 10px;
                font-weight: 600;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
            QPushButton {
                background: #ffffff;
                border: 1px solid #c4cbd3;
                border-radius: 5px;
                padding: 7px 11px;
                min-height: 24px;
            }
            QPushButton:hover { background: #edf5f1; border-color: #7ca58f; }
            QPushButton:pressed { background: #dceae3; }
            QPushButton:disabled { color: #8c96a0; background: #eceff1; }
            QPushButton#parameterButton {
                background: #ffffff;
                border: 1px solid #9aa5af;
                border-radius: 4px;
                padding: 3px 0;
                min-height: 22px;
                font-weight: 600;
                font-size: 8pt;
            }
            QPushButton#parameterButton:hover {
                background: #eef8f2;
                border-color: #2f8f68;
            }
            QPushButton#runControlButton {
                min-height: 40px;
                font-weight: 700;
                font-size: 10pt;
            }
            QLabel#parameterLabel {
                min-width: 58px;
                font-weight: 600;
            }
            QCheckBox {
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #89949f;
                border-radius: 2px;
                background: #ffffff;
            }
            QCheckBox::indicator:checked {
                background: #dff2e8;
                border: 2px solid #2f8f68;
            }
            QWidget#formatBox, QWidget#optionBox {
                background: #ffffff;
                border: 1px solid #cfd5dc;
                border-radius: 5px;
            }
            QLabel#cameraScanState {
                border: 1px solid #cfd5dc;
                border-radius: 5px;
                padding: 6px 10px;
                min-width: 54px;
                font-weight: 700;
                qproperty-alignment: AlignCenter;
            }
            QLabel#cameraScanState[state="idle"] {
                background: #ffffff;
                color: #4f5963;
            }
            QLabel#cameraScanState[state="searching"] {
                background: #e9f2ff;
                border-color: #8fb3e8;
                color: #24568f;
            }
            QLabel#cameraScanState[state="success"] {
                background: #eef8f2;
                border-color: #96c5a8;
                color: #1f5f43;
            }
            QLabel#cameraScanState[state="failure"] {
                background: #fff0ef;
                border-color: #d99a96;
                color: #7a2420;
            }
            QLabel#cameraStatus[state="idle"] { color: #4f5963; }
            QLabel#cameraStatus[state="searching"] { color: #24568f; font-weight: 600; }
            QLabel#cameraStatus[state="success"] { color: #1f5f43; font-weight: 600; }
            QLabel#cameraStatus[state="failure"] { color: #7a2420; font-weight: 700; }
            QLineEdit, QComboBox, QSpinBox, QTableWidget, QPlainTextEdit {
                background: #ffffff;
                border: 1px solid #cfd5dc;
                border-radius: 4px;
                padding: 4px;
            }
            QLineEdit:disabled, QSpinBox:disabled {
                background: #eceff1;
                color: #6f7983;
            }
            QScrollArea { background: #f5f6f7; }
            QHeaderView::section {
                background: #e7eaee;
                border: 0;
                border-right: 1px solid #cfd5dc;
                padding: 5px;
                font-weight: 600;
            }
            QLabel#preview {
                background: #151719;
                color: #c7cdd4;
                border-radius: 6px;
                border: 1px solid #2a3036;
            }
            QTableWidget#errorSummary, QTableWidget#previewMetrics, QTableWidget#stageSpecs, QTableWidget#preflightTable {
                background: #ffffff;
                border: 1px solid #cfd5dc;
                border-radius: 5px;
                gridline-color: #d7dde3;
            }
            QTableWidget#errorSummary::item, QTableWidget#previewMetrics::item, QTableWidget#stageSpecs::item, QTableWidget#preflightTable::item {
                padding: 6px;
                font-weight: 600;
            }
            QLabel#positionStatus {
                background: #ffffff;
                border: 1px solid #cfd5dc;
                border-radius: 5px;
                padding: 7px;
            }
            QLabel#positionStatus[state="ok"] {
                background: #eef8f2;
                border-color: #96c5a8;
                color: #1f5f43;
            }
            QLabel#positionStatus[state="warning"] {
                background: #fff8df;
                border-color: #d6b95e;
                color: #6d560b;
            }
            QLabel#positionStatus[state="error"] {
                background: #fff0ef;
                border-color: #d99a96;
                color: #7a2420;
            }
            QLabel#previewInfo {
                background: #ffffff;
                border: 1px solid #cfd5dc;
                border-radius: 5px;
                padding: 7px;
            }
            QLabel#errorBasis {
                background: #ffffff;
                border: 1px solid #cfd5dc;
                border-radius: 5px;
                padding: 7px;
                font-weight: 600;
            }
            QLabel#runStatus {
                background: #ffffff;
                border: 1px solid #cfd5dc;
                border-radius: 5px;
                padding: 7px;
                font-weight: 600;
            }
            QLabel#progressDetail {
                color: #4f5963;
                padding: 2px 4px;
            }
            QProgressBar {
                background: #ffffff;
                border: 1px solid #cfd5dc;
                border-radius: 4px;
                text-align: center;
                height: 18px;
            }
            QProgressBar::chunk { background: #2f8f68; border-radius: 3px; }
            """
        )

    def _load_initial_config(self) -> None:
        for path in (
            Path("config.yaml"),
            app_base_dir() / "config.yaml",
            bundled_resource("config.example.yaml"),
            Path("config.example.yaml"),
        ):
            if path.exists():
                self.config_path = path
                self.config = load_config(path)
                self.apply_config(self.config, path)
                return
        self.config = {}
        self.apply_config(self.config, self.config_path)

    def apply_config(self, config: dict[str, Any], config_path: Path) -> None:
        self.config = deepcopy(config)
        self.config_path = config_path
        camera = config.get("camera", {})
        stage = config.get("stage", {})
        dataset = config.get("dataset", {})
        self._preferred_camera_serial = str(camera.get("serial_number") or "")
        self._camera_user_touched = False

        self.output_root_edit.setText(str(dataset.get("output_root", "output/datasets")))
        self.exposure_spin.setValue(int(camera.get("exposure_us", 5000) or 5000))
        self.settle_spin.setValue(int(float(stage.get("settle_s", 0.2)) * 1000))
        self.velocity_edit.setText("" if stage.get("move_velocity_mm_s") in (None, "") else str(stage.get("move_velocity_mm_s")))
        self.capture_count_spin.setValue(default_capture_count_from_config(config))
        self.pixel_format_combo.setCurrentText(str(camera.get("pixel_format", "Mono8")))
        self.software_trigger_check.setChecked(bool(camera.get("use_software_trigger", True)))
        self.save_numpy_check.setChecked(bool(dataset.get("save_numpy", False)))
        self.skip_home_check.setChecked(False)
        metadata_default = (
            DEFAULT_METADATA_FORMATS
            if bool(dataset.get("write_jsonl", True))
            else tuple(item for item in DEFAULT_METADATA_FORMATS if item != "jsonl")
        )
        self._set_format_checks(
            self.metadata_format_checks,
            dataset.get("metadata_formats", metadata_default),
        )
        self._set_format_checks(
            self.summary_format_checks,
            dataset.get("summary_formats", DEFAULT_SUMMARY_FORMATS),
        )

        self._set_combo_text(self.stage_port_combo, str(stage.get("serial_port", "COM3")))
        self.set_positions(points_from_config(config, base_dir=config_path.parent))
        self.update_error_summary()
        self.log(f"설정 불러옴: {config_path}")

    def refresh_devices(self) -> None:
        self.refresh_stage_ports()
        self.start_camera_scan("manual")

    def refresh_stage_ports(self) -> None:
        current_port = self.stage_port_combo.currentText() or "COM3"
        self.stage_port_combo.clear()
        ports = list_serial_ports()
        if not ports:
            self.stage_port_combo.addItem(current_port)
        else:
            for port in ports:
                self.stage_port_combo.addItem(
                    f"{port['device']} - {port['description']}",
                    port["device"],
                )
            self._set_combo_data(self.stage_port_combo, current_port)

    def start_camera_scan(self, reason: str = "manual") -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        if self.camera_scan_worker is not None and self.camera_scan_worker.isRunning():
            return

        self._set_camera_scan_state("searching", "탐색중", "Basler LAN 카메라 검색 중...")
        self.camera_scan_button.setEnabled(False)
        self.camera_scan_worker = CameraDiscoveryWorker(reason)
        self.camera_scan_worker.cameras_found.connect(self.on_cameras_found)
        self.camera_scan_worker.scan_failed.connect(self.on_camera_scan_failed)
        self.camera_scan_worker.finished.connect(self.on_camera_scan_finished)
        self.camera_scan_worker.start()

    def on_cameras_found(self, cameras: list[dict[str, str]], reason: str) -> None:
        if self.worker is not None and self.worker.isRunning():
            return

        previous_signature = self._camera_signature
        signature = _camera_signature(cameras)
        self._camera_signature = signature
        selected_label = self.populate_camera_combo(cameras)
        count = len(cameras)

        if count:
            self._set_camera_scan_state(
                "success",
                "성공",
                f"Basler 카메라 {count}대 감지 | 선택: {selected_label}",
            )
        else:
            self._set_camera_scan_state(
                "failure",
                "실패",
                "LAN/USB에서 Basler 카메라가 감지되지 않음",
            )

        if reason == "manual" or signature != previous_signature:
            names = ", ".join(_camera_display_name(camera) for camera in cameras) or "없음"
            self.log(f"Basler 카메라 감지: {names}")

    def populate_camera_combo(self, cameras: list[dict[str, str]]) -> str:
        current_camera_serial = self.camera_combo.currentData()
        desired_serial = str(current_camera_serial or self._preferred_camera_serial or "")
        self.camera_combo.clear()
        self.camera_combo.addItem("자동 선택", "")
        serials: list[str] = []
        for camera in cameras:
            label = _camera_display_name(camera)
            serial = camera.get("serial", "")
            if serial:
                serials.append(serial)
            self.camera_combo.addItem(label or "Basler 카메라", camera.get("serial", ""))

        selected_serial = ""
        if desired_serial and desired_serial in serials:
            selected_serial = desired_serial
        elif cameras and not self._camera_user_touched and not desired_serial:
            selected_serial = serials[0] if serials else ""

        if selected_serial:
            self._set_combo_data(self.camera_combo, selected_serial)
        else:
            self.camera_combo.setCurrentIndex(0)
        return self.camera_combo.currentText()

    def on_camera_scan_failed(self, message: str, reason: str) -> None:
        self._set_camera_scan_state("failure", "실패", f"카메라 검색 실패: {message}")
        if reason == "manual":
            self.log(f"카메라 검색 실패: {message}")

    def on_camera_scan_finished(self) -> None:
        self.camera_scan_worker = None
        if self.worker is None or not self.worker.isRunning():
            self.camera_scan_button.setEnabled(True)

    def _set_camera_scan_state(self, state: str, label: str, detail: str) -> None:
        self.camera_scan_state_label.setText(label)
        self.camera_scan_state_label.setProperty("state", state)
        self.camera_scan_state_label.style().unpolish(self.camera_scan_state_label)
        self.camera_scan_state_label.style().polish(self.camera_scan_state_label)
        self.camera_status_label.setProperty("state", state)
        self.camera_status_label.style().unpolish(self.camera_status_label)
        self.camera_status_label.style().polish(self.camera_status_label)
        self.camera_status_icon.setVisible(state == "failure")
        self.camera_status_label.setText(detail)

    def on_camera_combo_activated(self, index: int) -> None:
        self._camera_user_touched = True
        self._preferred_camera_serial = str(self.camera_combo.itemData(index) or "")

    def browse_output_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", self.output_root_edit.text())
        if path:
            self.output_root_edit.setText(path)

    def load_config_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "설정 불러오기",
            str(self.config_path),
            "YAML 파일 (*.yaml *.yml)",
        )
        if not path:
            return
        try:
            config = load_config(path)
            self.apply_config(config, Path(path))
        except (ConfigError, ValueError, OSError) as exc:
            QMessageBox.critical(self, "설정 오류", str(exc))

    def save_config_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "설정 저장",
            str(self.config_path),
            "YAML 파일 (*.yaml *.yml)",
        )
        if not path:
            return
        try:
            config = self.build_config()
        except Exception as exc:
            QMessageBox.warning(self, "입력 오류", str(exc))
            return
        Path(path).write_text(
            yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        self.config_path = Path(path)
        self.config = config
        self.log(f"설정 저장됨: {path}")

    def add_position_row(self, point: ScanPoint | None = None, update_feedback: bool = True) -> None:
        previous_block_state = self.positions_table.blockSignals(True)
        row = self.positions_table.rowCount()
        try:
            self.positions_table.insertRow(row)
            point = point or ScanPoint(row, 0.0, 0.0, "")
            values = [
                str(row),
                point.label,
                _mm_text(point.x_mm),
                _mm_text(point.y_mm),
                "" if point.move_velocity_mm_s is None else _number_text(point.move_velocity_mm_s),
                "" if point.capture_count is None else str(point.capture_count),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if column in (0, 2, 3, 4, 5):
                    item.setTextAlignment(Qt.AlignCenter)
                if column == 4:
                    item.setToolTip("비워 두면 촬영 설정의 이동속도를 사용합니다.")
                if column == 5:
                    item.setToolTip("비워 두면 촬영 설정의 기본 캡쳐 수를 사용합니다.")
                self.positions_table.setItem(row, column, item)
        finally:
            self.positions_table.blockSignals(previous_block_state)
        if update_feedback:
            self.refresh_position_feedback()

    def set_positions(self, points: list[ScanPoint]) -> None:
        previous_block_state = self.positions_table.blockSignals(True)
        try:
            self.positions_table.setRowCount(0)
            for point in points:
                self.add_position_row(point, update_feedback=False)
            self.reindex_positions()
        finally:
            self.positions_table.blockSignals(previous_block_state)
        self.refresh_position_feedback()

    def read_positions(self) -> list[ScanPoint]:
        points, validation = self.read_positions_with_validation()
        self.apply_position_validation_feedback(validation)
        if validation.errors:
            raise ValueError(format_issue_list("위치 입력을 확인하세요.", validation.errors))
        return points

    def read_positions_with_validation(self) -> tuple[list[ScanPoint], PositionValidationResult]:
        rows = [
            PositionInputRow(
                index=row,
                label=self._table_text(self.positions_table, row, 1),
                x_text=self._table_text(self.positions_table, row, 2),
                y_text=self._table_text(self.positions_table, row, 3),
                velocity_text=self._table_text(self.positions_table, row, 4),
                capture_count_text=self._table_text(self.positions_table, row, 5),
            )
            for row in range(self.positions_table.rowCount())
        ]
        return parse_position_rows(rows)

    def on_position_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() in (4, 5) and item.data(POSITION_PLACEHOLDER_ROLE):
            item.setData(POSITION_PLACEHOLDER_ROLE, False)
            item.setForeground(QBrush(QColor("#1e2329")))
        self.refresh_position_feedback()

    def refresh_position_feedback(self, *_: object) -> None:
        if not hasattr(self, "position_status_label"):
            return
        points, validation = self.read_positions_with_validation()
        self.apply_position_validation_feedback(
            validation,
            point_count=len(points),
            capture_total=total_capture_count(points, self.capture_count_spin.value()),
        )

    def apply_position_validation_feedback(
        self,
        validation: PositionValidationResult,
        point_count: int | None = None,
        capture_total: int | None = None,
    ) -> None:
        previous_block_state = self.positions_table.blockSignals(True)
        try:
            for row in range(self.positions_table.rowCount()):
                for column in range(self.positions_table.columnCount()):
                    item = self.positions_table.item(row, column)
                    if item is None:
                        continue
                    item.setBackground(QColor("#ffffff"))
                    item.setForeground(QBrush(QColor("#1e2329")))
                    item.setToolTip(_position_cell_tooltip(column))
                    if column in (4, 5):
                        self._apply_position_placeholder(row, column)

            for (row, column), detail in validation.cell_warnings.items():
                item = self.positions_table.item(row, column)
                if item is not None:
                    item.setBackground(QColor("#fff4cc"))
                    item.setToolTip(detail)

            for (row, column), detail in validation.cell_errors.items():
                item = self.positions_table.item(row, column)
                if item is not None:
                    item.setBackground(QColor("#ffe1df"))
                    item.setToolTip(detail)
        finally:
            self.positions_table.blockSignals(previous_block_state)

        if point_count is None:
            points = self.read_positions_with_validation()[0]
            point_count = len(points)
            capture_total = total_capture_count(points, self.capture_count_spin.value())
        if capture_total is None:
            capture_total = point_count
        if validation.errors:
            self.position_status_label.setText(
                f"위치 오류 {len(validation.errors)}개 | {short_issue_text(validation.errors)}"
            )
            self.position_status_label.setProperty("state", "error")
        elif validation.warnings:
            self.position_status_label.setText(
                f"{point_count}개 위치 / {capture_total}장 촬영 | 경고 {len(validation.warnings)}개 | {short_issue_text(validation.warnings)}"
            )
            self.position_status_label.setProperty("state", "warning")
        else:
            self.position_status_label.setText(
                f"{point_count}개 위치 / {capture_total}장 촬영 | {_mm_text(POSITION_MIN_MM)}-{_mm_text(POSITION_MAX_MM)} mm 범위 검사 통과"
            )
            self.position_status_label.setProperty("state", "ok")
        self.position_status_label.style().unpolish(self.position_status_label)
        self.position_status_label.style().polish(self.position_status_label)

    def _apply_position_placeholder(self, row: int, column: int) -> None:
        item = self.positions_table.item(row, column)
        if item is None:
            item = QTableWidgetItem()
            item.setTextAlignment(Qt.AlignCenter)
            self.positions_table.setItem(row, column, item)

        text = item.text().strip()
        is_placeholder = bool(item.data(POSITION_PLACEHOLDER_ROLE))
        if text and not is_placeholder:
            item.setData(POSITION_PLACEHOLDER_ROLE, False)
            item.setForeground(QBrush(QColor("#1e2329")))
            return

        item.setData(POSITION_PLACEHOLDER_ROLE, True)
        item.setText(self._position_placeholder_text(column))
        item.setForeground(QBrush(QColor("#8c96a0")))
        item.setTextAlignment(Qt.AlignCenter)
        item.setToolTip(_position_cell_tooltip(column))

    def _position_placeholder_text(self, column: int) -> str:
        if column == 4:
            try:
                velocity = _optional_float_text(self.velocity_edit.text())
            except ValueError:
                velocity = None
            return f"({_number_text(velocity)})" if velocity is not None else "(장비 기본값)"
        if column == 5:
            return f"({self.capture_count_spin.value()})"
        return ""

    def delete_selected_positions(self) -> None:
        rows = sorted({index.row() for index in self.positions_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.positions_table.removeRow(row)
        self.reindex_positions()
        self.refresh_position_feedback()

    def clear_positions(self) -> None:
        self.positions_table.setRowCount(0)
        self.refresh_position_feedback()

    def generate_linear_path_dialog(self) -> None:
        start_x, start_y, end_x, end_y = self._linear_path_defaults()
        default_spacing = max(0.001, _linear_distance(start_x, start_y, end_x, end_y) / 10.0)
        dialog = QDialog(self)
        dialog.setWindowTitle("선형 연속 경로 생성")
        dialog.resize(460, 390)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()

        start_x_edit = QLineEdit(_mm_text(start_x))
        start_y_edit = QLineEdit(_mm_text(start_y))
        end_x_edit = QLineEdit(_mm_text(end_x))
        end_y_edit = QLineEdit(_mm_text(end_y))
        basis_combo = QComboBox()
        basis_combo.addItem("간격 mm/캡쳐", "spacing")
        basis_combo.addItem("위치 수", "count")
        spacing_edit = QLineEdit(_mm_text(default_spacing))
        count_spin = QSpinBox()
        count_spin.setRange(2, 100_000)
        count_spin.setValue(11)
        count_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        label_prefix_edit = QLineEdit("line")
        velocity_edit = QLineEdit()
        velocity_edit.setPlaceholderText("비우면 촬영 설정 이동속도 사용")
        capture_count_edit = QLineEdit()
        capture_count_edit.setPlaceholderText(f"비우면 기본 캡쳐 {self.capture_count_spin.value()}장")
        _set_placeholder_color(velocity_edit)
        _set_placeholder_color(capture_count_edit)
        replace_check = QCheckBox("기존 위치를 지우고 생성")

        for editor, tooltip in (
            (start_x_edit, "선형 경로 시작 X 좌표입니다. 단위: mm"),
            (start_y_edit, "선형 경로 시작 Y 좌표입니다. 단위: mm"),
            (end_x_edit, "선형 경로 끝 X 좌표입니다. 단위: mm"),
            (end_y_edit, "선형 경로 끝 Y 좌표입니다. 단위: mm"),
            (basis_combo, "선형 경로 생성 기준입니다. 기본값: 간격 mm/캡쳐"),
            (spacing_edit, "경로를 따라 몇 mm마다 한 위치를 생성할지 지정합니다. 끝점은 항상 포함됩니다."),
            (count_spin, "시작점과 끝점을 포함한 총 위치 개수입니다."),
            (label_prefix_edit, "생성될 위치 라벨 접두어입니다. 예: line_0001"),
            (velocity_edit, "생성 위치에 고정할 이동속도입니다. 비우면 촬영 설정 이동속도를 따릅니다."),
            (capture_count_edit, "생성 위치에 고정할 캡쳐 수입니다. 비우면 기본 캡쳐 수를 따릅니다."),
            (replace_check, "켜면 현재 위치 테이블을 비우고 새 경로만 넣습니다."),
        ):
            editor.setToolTip(tooltip)

        form.addRow("시작 X mm", start_x_edit)
        form.addRow("시작 Y mm", start_y_edit)
        form.addRow("끝 X mm", end_x_edit)
        form.addRow("끝 Y mm", end_y_edit)
        form.addRow("생성 기준", basis_combo)
        form.addRow("간격 mm/캡쳐", spacing_edit)
        form.addRow("위치 수", count_spin)
        form.addRow("라벨 접두어", label_prefix_edit)
        form.addRow("이동속도 mm/s", velocity_edit)
        form.addRow("캡쳐 수", capture_count_edit)
        form.addRow("", replace_check)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("생성")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        layout.addWidget(buttons)

        def sync_generation_mode() -> None:
            use_spacing = basis_combo.currentData() == "spacing"
            spacing_edit.setEnabled(use_spacing)
            count_spin.setReadOnly(use_spacing)
            count_spin.setEnabled(not use_spacing)
            update_linear_count_preview()

        def update_linear_count_preview() -> None:
            if basis_combo.currentData() != "spacing":
                return
            try:
                calculated_count = _linear_spacing_point_count(
                    float(start_x_edit.text().strip()),
                    float(start_y_edit.text().strip()),
                    float(end_x_edit.text().strip()),
                    float(end_y_edit.text().strip()),
                    float(spacing_edit.text().strip()),
                )
            except Exception:
                calculated_count = 2
            count_spin.setValue(max(2, min(count_spin.maximum(), calculated_count)))

        basis_combo.currentIndexChanged.connect(sync_generation_mode)
        for editor in (start_x_edit, start_y_edit, end_x_edit, end_y_edit, spacing_edit):
            editor.textChanged.connect(update_linear_count_preview)
        sync_generation_mode()

        def accept_generated_path() -> None:
            try:
                x_start = float(start_x_edit.text().strip())
                y_start = float(start_y_edit.text().strip())
                x_stop = float(end_x_edit.text().strip())
                y_stop = float(end_y_edit.text().strip())
                move_velocity = _optional_float_text(velocity_edit.text())
                if move_velocity is not None and move_velocity <= 0:
                    raise ValueError("이동속도는 비워 두거나 0보다 커야 합니다.")
                capture_count = _optional_int_text(capture_count_edit.text())
                if capture_count is not None and capture_count < 1:
                    raise ValueError("캡쳐 수는 비워 두거나 1 이상이어야 합니다.")
                start_index = 0 if replace_check.isChecked() else self.positions_table.rowCount()
                if basis_combo.currentData() == "spacing":
                    spacing_mm = float(spacing_edit.text().strip())
                    points = list(
                        linear_path_points_by_spacing(
                            x_start=x_start,
                            y_start=y_start,
                            x_stop=x_stop,
                            y_stop=y_stop,
                            spacing_mm=spacing_mm,
                            label_prefix=label_prefix_edit.text().strip() or "line",
                            start_index=start_index,
                            move_velocity_mm_s=move_velocity,
                            capture_count=capture_count,
                        )
                    )
                else:
                    points = list(
                        linear_path_points(
                            x_start=x_start,
                            y_start=y_start,
                            x_stop=x_stop,
                            y_stop=y_stop,
                            count=count_spin.value(),
                            label_prefix=label_prefix_edit.text().strip() or "line",
                            start_index=start_index,
                            move_velocity_mm_s=move_velocity,
                            capture_count=capture_count,
                        )
                    )
            except Exception as exc:
                QMessageBox.warning(dialog, "경로 생성 오류", str(exc))
                return

            if replace_check.isChecked():
                self.set_positions(points)
            else:
                for point in points:
                    self.add_position_row(point, update_feedback=False)
                self.reindex_positions()
                self.refresh_position_feedback()
            self.log(f"선형 경로 생성: {len(points)}개 위치")
            dialog.accept()

        buttons.accepted.connect(accept_generated_path)
        buttons.rejected.connect(dialog.reject)
        dialog.exec()

    def _linear_path_defaults(self) -> tuple[float, float, float, float]:
        selected_rows = sorted({index.row() for index in self.positions_table.selectedIndexes()})
        if len(selected_rows) >= 2:
            first, last = selected_rows[0], selected_rows[-1]
            return (
                _safe_float_text(self._table_text(self.positions_table, first, 2), 0.0),
                _safe_float_text(self._table_text(self.positions_table, first, 3), 0.0),
                _safe_float_text(self._table_text(self.positions_table, last, 2), 10.0),
                _safe_float_text(self._table_text(self.positions_table, last, 3), 0.0),
            )
        if self.positions_table.rowCount() > 0:
            row = self.positions_table.rowCount() - 1
            start_x = _safe_float_text(self._table_text(self.positions_table, row, 2), 0.0)
            start_y = _safe_float_text(self._table_text(self.positions_table, row, 3), 0.0)
            return (start_x, start_y, min(POSITION_MAX_MM, start_x + 10.0), start_y)
        return (0.0, 0.0, min(POSITION_MAX_MM, 10.0), 0.0)

    def reindex_positions(self) -> None:
        for row in range(self.positions_table.rowCount()):
            item = self.positions_table.item(row, 0) or QTableWidgetItem()
            item.setText(str(row))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.positions_table.setItem(row, 0, item)

    def import_positions_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "위치 파일 불러오기",
            str(Path.cwd()),
            "위치 파일 (*.csv *.tsv *.txt *.json *.jsonl *.ndjson *.yaml *.yml *.xlsx);;모든 파일 (*.*)",
        )
        if not path:
            return
        try:
            points = points_from_file(path)
            self.set_positions(points)
            _, validation = self.read_positions_with_validation()
            self.log(f"위치 목록 불러옴: {path}")
            if validation.errors:
                QMessageBox.warning(
                    self,
                    "위치 확인 필요",
                    format_issue_list("불러온 위치에 오류가 있습니다.", validation.errors),
                )
            elif validation.warnings:
                QMessageBox.information(
                    self,
                    "위치 경고",
                    format_issue_list("불러온 위치에 경고가 있습니다.", validation.warnings),
                )
        except Exception as exc:
            QMessageBox.critical(self, "불러오기 오류", str(exc))

    def export_positions_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "위치 CSV 저장",
            str(Path("positions.csv")),
            "CSV 파일 (*.csv)",
        )
        if not path:
            return
        try:
            points = self.read_positions()
        except Exception as exc:
            QMessageBox.warning(self, "입력 오류", str(exc))
            return
        with Path(path).open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=["label", "x_mm", "y_mm", "move_velocity_mm_s", "capture_count"],
            )
            writer.writeheader()
            for point in points:
                writer.writerow(
                    {
                        "label": point.label,
                        "x_mm": point.x_mm,
                        "y_mm": point.y_mm,
                        "move_velocity_mm_s": point.move_velocity_mm_s or "",
                        "capture_count": point.capture_count or "",
                    }
                )
        self.log(f"위치 목록 저장됨: {path}")

    def build_config(self, points: list[ScanPoint] | None = None) -> dict[str, Any]:
        config = deepcopy(self.config)
        camera = config.setdefault("camera", {})
        stage = config.setdefault("stage", {})
        dataset = config.setdefault("dataset", {})
        scan = config.setdefault("scan", {})

        selected_serial = self.camera_combo.currentData()
        camera["serial_number"] = selected_serial or None
        camera["pixel_format"] = self.pixel_format_combo.currentText()
        camera["exposure_us"] = self.exposure_spin.value()
        camera["use_software_trigger"] = self.software_trigger_check.isChecked()
        camera.setdefault("trigger_selector", "FrameStart")
        camera.setdefault("trigger_source", "Software")
        camera.setdefault("timeout_ms", 5000)

        stage["serial_port"] = self.stage_port_combo.currentData() or self.stage_port_combo.currentText().split(" - ")[0]
        stage["settle_s"] = self.settle_spin.value() / 1000.0
        stage["move_velocity_mm_s"] = _optional_float_text(self.velocity_edit.text())

        dataset["output_root"] = self.output_root_edit.text() or "output/datasets"
        dataset["image_format"] = "tiff"
        dataset["save_numpy"] = self.save_numpy_check.isChecked()
        dataset["metadata_formats"] = self._checked_formats(self.metadata_format_checks) or ["csv"]
        dataset["summary_formats"] = self._checked_formats(self.summary_format_checks)
        dataset["write_jsonl"] = "jsonl" in dataset["metadata_formats"]

        config["calibration"] = fixed_calibration_record()

        resolved_points = points if points is not None else self.read_positions()
        scan["default_capture_count"] = self.capture_count_spin.value()
        scan["positions"] = [_point_config_record(point) for point in resolved_points]
        scan["positions_file"] = None
        return config

    def collect_preflight_issues(
        self,
        points: list[ScanPoint],
        config: dict[str, Any],
        position_validation: PositionValidationResult,
    ) -> list[PreflightIssue]:
        issues: list[PreflightIssue] = []

        if points:
            min_x = min(point.x_mm for point in points)
            max_x = max(point.x_mm for point in points)
            min_y = min(point.y_mm for point in points)
            max_y = max(point.y_mm for point in points)
            default_capture_count = default_capture_count_from_config(config)
            total_captures = total_capture_count(points, default_capture_count)
            override_count = sum(
                1
                for point in points
                if point.move_velocity_mm_s is not None or point.capture_count is not None
            )
            status = "경고" if position_validation.warnings else "통과"
            detail = (
                f"{len(points)}개 위치 / {total_captures}장 촬영 | "
                f"X {_mm_text(min_x)}-{_mm_text(max_x)} mm, "
                f"Y {_mm_text(min_y)}-{_mm_text(max_y)} mm"
            )
            if override_count:
                detail += f" | 위치별 속도/캡쳐 override {override_count}개"
            if position_validation.warnings:
                detail += f" | {short_issue_text(position_validation.warnings)}"
            issues.append(PreflightIssue("위치 목록", status, detail))
        else:
            issues.append(PreflightIssue("위치 목록", "오류", "최소 1개 이상의 위치가 필요합니다."))

        detected_cameras = max(0, self.camera_combo.count() - 1)
        if detected_cameras <= 0:
            issues.append(
                PreflightIssue(
                    "Basler 카메라",
                    "오류",
                    "감지된 카메라가 없습니다. LAN 연결, pylon IP 설정, 장비 새로고침을 확인하세요.",
                )
            )
        else:
            selected_camera = self.camera_combo.currentText() or "자동 선택"
            issues.append(
                PreflightIssue(
                    "Basler 카메라",
                    "통과",
                    f"{detected_cameras}대 감지 | 선택: {selected_camera}",
                )
            )

        stage = config.get("stage", {})
        selected_port = str(stage.get("serial_port") or "").strip()
        try:
            detected_ports = {port["device"] for port in list_serial_ports()}
        except Exception as exc:
            detected_ports = set()
            issues.append(PreflightIssue("스테이지 포트", "경고", f"COM 포트 조회 실패: {exc}"))

        if not selected_port:
            issues.append(PreflightIssue("스테이지 포트", "오류", "Zaber COM 포트가 비어 있습니다."))
        elif detected_ports and selected_port in detected_ports:
            issues.append(PreflightIssue("스테이지 포트", "통과", f"{selected_port} 감지됨"))
        elif detected_ports:
            issues.append(
                PreflightIssue(
                    "스테이지 포트",
                    "경고",
                    f"{selected_port}가 현재 포트 목록에 없습니다. 감지 포트: {', '.join(sorted(detected_ports))}",
                )
            )
        else:
            issues.append(
                PreflightIssue(
                    "스테이지 포트",
                    "경고",
                    f"현재 감지된 COM 포트가 없습니다. 설정값 {selected_port}로 실행됩니다.",
                )
            )

        issues.append(self._axis_mapping_issue(stage))

        dataset = config.get("dataset", {})
        output_root = str(dataset.get("output_root") or "").strip()
        if not output_root:
            issues.append(PreflightIssue("저장 폴더", "오류", "저장 위치가 비어 있습니다."))
        else:
            resolved_output = Path(os.path.expandvars(output_root)).expanduser()
            try:
                resolved_output.mkdir(parents=True, exist_ok=True)
                if resolved_output.is_dir():
                    issues.append(PreflightIssue("저장 폴더", "통과", str(resolved_output)))
                else:
                    issues.append(PreflightIssue("저장 폴더", "오류", f"폴더가 아닙니다: {resolved_output}"))
            except OSError as exc:
                issues.append(PreflightIssue("저장 폴더", "오류", f"폴더 생성/접근 실패: {exc}"))

        velocity = stage.get("move_velocity_mm_s")
        if velocity is not None and float(velocity) <= 0:
            issues.append(PreflightIssue("이동 속도", "오류", "이동 속도는 비워 두거나 0보다 큰 값이어야 합니다."))
        elif velocity is None:
            issues.append(PreflightIssue("이동 속도", "통과", "Zaber 장비 기본 속도 사용"))
        else:
            issues.append(PreflightIssue("이동 속도", "통과", f"{_number_text(velocity)} mm/s"))

        camera = config.get("camera", {})
        issues.append(
            PreflightIssue(
                "촬영 설정",
                "통과",
                f"{camera.get('pixel_format', 'Mono8')} | 노출 {camera.get('exposure_us')} us | "
                f"안정화 {self.settle_spin.value()} ms | 기본 캡쳐 {default_capture_count_from_config(config)}장",
            )
        )
        metadata_formats = ", ".join(str(item).upper() for item in dataset.get("metadata_formats", ["csv"]))
        summary_formats = ", ".join(str(item).upper() for item in dataset.get("summary_formats", [])) or "없음"
        issues.append(
            PreflightIssue(
                "출력 포맷",
                "통과",
                f"메타데이터 {metadata_formats} | 요약 {summary_formats}",
            )
        )
        issues.append(
            PreflightIssue(
                "오차 기준",
                "통과",
                f"Zaber 210 mm 고정 스펙 | XY worst-case {_um_text(ZABER_LDM210_XY_SPECS.radial_xy_worst_case_um)} um",
            )
        )
        return issues

    def _axis_mapping_issue(self, stage: dict[str, Any]) -> PreflightIssue:
        axes = stage.get("axes", {})
        try:
            x_axis = axes.get("x", {}) or {}
            y_axis = axes.get("y", {}) or {}
            x_address = (
                int(x_axis.get("device_index", 0)),
                int(x_axis.get("axis_number", 1)),
            )
            y_address = (
                int(y_axis.get("device_index", 1)),
                int(y_axis.get("axis_number", 1)),
            )
        except (TypeError, ValueError) as exc:
            return PreflightIssue("축 매핑", "오류", f"X/Y 축 설정을 숫자로 해석할 수 없습니다: {exc}")

        detail = (
            f"X device {x_address[0]} axis {x_address[1]} | "
            f"Y device {y_address[0]} axis {y_address[1]}"
        )
        if x_address == y_address:
            return PreflightIssue("축 매핑", "오류", f"X/Y가 같은 축을 가리킵니다. {detail}")
        return PreflightIssue("축 매핑", "통과", detail)

    def build_preflight_dialog(
        self,
        points: list[ScanPoint],
        config: dict[str, Any],
        position_validation: PositionValidationResult,
    ) -> QDialog:
        issues = self.collect_preflight_issues(points, config, position_validation)
        has_errors = any(issue.status == "오류" for issue in issues)

        dialog = QDialog(self)
        dialog.setWindowTitle("촬영 전 점검")
        dialog.resize(760, 460)
        layout = QVBoxLayout(dialog)

        title = QLabel("촬영을 시작하기 전에 장비, 위치, 저장 조건을 확인합니다.")
        title.setWordWrap(True)
        layout.addWidget(title)

        table = QTableWidget(len(issues), 3)
        table.setObjectName("preflightTable")
        table.setHorizontalHeaderLabels(["항목", "상태", "내용"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setFocusPolicy(Qt.NoFocus)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

        for row, issue in enumerate(issues):
            values = [issue.item, issue.status, issue.detail]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if column == 1:
                    item.setTextAlignment(Qt.AlignCenter)
                    item.setBackground(_preflight_status_color(issue.status))
                table.setItem(row, column, item)
        layout.addWidget(table, 1)

        notice = QLabel(
            "오류가 있으면 시작할 수 없습니다. 경고는 사용자가 조건을 확인한 뒤 계속 진행할 수 있습니다."
            if has_errors
            else "점검을 통과했습니다. 시작을 누르면 현재 조건으로 run을 시작합니다."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        start_button = buttons.button(QDialogButtonBox.Ok)
        cancel_button = buttons.button(QDialogButtonBox.Cancel)
        start_button.setText("시작")
        cancel_button.setText("취소")
        start_button.setEnabled(not has_errors)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        return dialog

    def show_preflight_dialog(
        self,
        points: list[ScanPoint],
        config: dict[str, Any],
        position_validation: PositionValidationResult,
    ) -> bool:
        dialog = self.build_preflight_dialog(points, config, position_validation)
        return dialog.exec() == QDialog.Accepted

    def start_run(self) -> None:
        try:
            points, validation = self.read_positions_with_validation()
            self.apply_position_validation_feedback(
                validation,
                point_count=len(points),
                capture_total=total_capture_count(points, self.capture_count_spin.value()),
            )
            if validation.errors:
                raise ValueError(format_issue_list("위치 입력을 확인하세요.", validation.errors))
            config = self.build_config(points)
        except Exception as exc:
            QMessageBox.warning(self, "입력 오류", str(exc))
            return

        if not self.show_preflight_dialog(points, config, validation):
            return

        self.captures_table.setRowCount(0)
        self.error_records = []
        self.update_error_summary()
        capture_total = total_capture_count(points, default_capture_count_from_config(config))
        self.progress_bar.setRange(0, capture_total)
        self.progress_bar.setValue(0)
        self.set_run_status("촬영 준비 중")
        self.progress_detail_label.setText(f"0/{capture_total} 완료")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.refresh_button.setEnabled(False)
        self.camera_scan_button.setEnabled(False)
        if self.camera_scan_worker is not None and self.camera_scan_worker.isRunning():
            self.camera_status_label.setText("카메라 검색 종료 대기 중...")
            self.camera_scan_worker.wait(2000)
        self.current_run_dir = None
        self.open_dataset_button.setEnabled(False)
        self.log("촬영 run 시작")

        self.worker = AcquisitionWorker(
            config=config,
            points=points,
            config_path=self.config_path,
            output_root=self.output_root_edit.text(),
            skip_home=self.skip_home_check.isChecked(),
        )
        self.worker.log_message.connect(self.log)
        self.worker.status_changed.connect(self.set_run_status)
        self.worker.capture_done.connect(self.on_capture_done)
        self.worker.progress_changed.connect(self.on_progress_changed)
        self.worker.run_failed.connect(self.on_run_failed)
        self.worker.run_done.connect(self.on_run_done)
        self.worker.start()

    def stop_run(self) -> None:
        if self.worker is not None:
            self.worker.request_stop()
            self.stop_button.setEnabled(False)
            self.set_run_status("중지 요청됨")
            self.log("중지 요청됨")

    def on_progress_changed(self, completed: int, total: int) -> None:
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(completed)
        self.progress_detail_label.setText(f"{completed}/{total} 완료")

    def on_capture_done(self, record: dict[str, Any]) -> None:
        row = self.captures_table.rowCount()
        self.captures_table.insertRow(row)
        values = [
            str(record.get("index", "")),
            _capture_sequence_text(record),
            str(record.get("label", "")),
            _status_text(record.get("status", "")),
            _mm_text(record.get("target_x_mm", "")),
            _mm_text(record.get("target_y_mm", "")),
            _mm_text(record.get("actual_x_mm", "")),
            _mm_text(record.get("actual_y_mm", "")),
            _um_text(record.get("measured_radial_error_um", "")),
            _um_text(record.get("predicted_min_error_um", "")),
            _um_text(record.get("predicted_max_error_um", "")),
            _threshold_text(record),
            str(record.get("image_path", "")),
        ]
        image_path = record.get("absolute_image_path", "")
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            if column != 12:
                item.setTextAlignment(Qt.AlignCenter)
            if image_path:
                item.setData(Qt.UserRole, image_path)
            item.setData(Qt.UserRole + 1, record)
            self.captures_table.setItem(row, column, item)
        self.error_records.append(record)
        self.update_error_summary()
        if image_path:
            self.show_image(Path(image_path))
        self.update_preview_info(record)
        self.set_run_status(
            f"마지막 저장: #{record.get('index', '')} {_capture_sequence_text(record)} {record.get('label', '')}"
        )

    def on_run_failed(self, message: str) -> None:
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.refresh_button.setEnabled(True)
        self.camera_scan_button.setEnabled(True)
        self.set_run_status("오류 발생")
        self.log(f"오류: {message}")
        QMessageBox.critical(self, "실행 실패", message)

    def on_run_done(self, run_dir: str, stopped: bool) -> None:
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.refresh_button.setEnabled(True)
        self.worker = None
        self.camera_scan_button.setEnabled(True)
        if run_dir:
            self.current_run_dir = Path(run_dir)
            self.open_dataset_button.setEnabled(True)
        if self.run_status_label.text() != "오류 발생":
            self.set_run_status("중지됨" if stopped else "완료")
        self.log("촬영 중지됨" if stopped else "촬영 완료")

    def preview_capture_row(self, row: int, column: int) -> None:
        item = self.captures_table.item(row, column) or self.captures_table.item(row, 0)
        if item is None:
            return
        image_path = item.data(Qt.UserRole)
        if image_path:
            self.show_image(Path(image_path))
        record = item.data(Qt.UserRole + 1)
        if record:
            self.update_preview_info(record)

    def show_image(self, path: Path) -> None:
        try:
            self.current_image_path = path
            image = Image.open(path)
            image.thumbnail(
                (
                    max(100, self.preview_label.width() - 24),
                    max(100, self.preview_label.height() - 24),
                ),
                Image.Resampling.LANCZOS,
            )
            image = image.convert("RGB")
            data = image.tobytes("raw", "RGB")
            qimage = QImage(data, image.width, image.height, image.width * 3, QImage.Format_RGB888).copy()
            self.preview_label.setPixmap(QPixmap.fromImage(qimage))
            self.fullscreen_button.setEnabled(True)
        except Exception as exc:
            self.current_image_path = None
            self.fullscreen_button.setEnabled(False)
            self.preview_label.setText(f"미리보기 오류\n{exc}")

    def open_fullscreen_image(self) -> None:
        if self.current_image_path is None or not self.current_image_path.exists():
            QMessageBox.information(self, "이미지 없음", "전체화면으로 볼 이미지가 없습니다.")
            return
        self.image_viewer = FullscreenImageWindow(self.current_image_path)
        self.image_viewer.showFullScreen()

    def open_current_dataset(self) -> None:
        if self.current_run_dir and self.current_run_dir.exists():
            os.startfile(self.current_run_dir)

    def log(self, message: str) -> None:
        self.log_edit.appendPlainText(f"{iso_timestamp()}  {message}")

    def set_run_status(self, message: str) -> None:
        self.run_status_label.setText(message)

    def update_preview_info(self, record: dict[str, Any]) -> None:
        if record.get("status") != "ok":
            self.preview_info_label.setText(f"촬영 오류: {record.get('error_message', '')}")
            self._set_preview_metric_values(
                [
                    str(record.get("index", "")),
                    _capture_sequence_text(record),
                    str(record.get("label", "")),
                    _mm_text(record.get("target_x_mm", "")),
                    _mm_text(record.get("target_y_mm", "")),
                    "-",
                    "-",
                    "-",
                    "-",
                    "-",
                    "오류",
                ]
            )
            return
        label = str(record.get("label", "")).strip()
        self.preview_info_label.setText(
            f"선택된 촬영: #{record.get('index', '')} {_capture_sequence_text(record)} {label}".strip()
        )
        self._set_preview_metric_values(
            [
                str(record.get("index", "")),
                _capture_sequence_text(record),
                label or "-",
                _mm_text(record.get("target_x_mm", "")),
                _mm_text(record.get("target_y_mm", "")),
                _mm_text(record.get("actual_x_mm", "")),
                _mm_text(record.get("actual_y_mm", "")),
                _um_text(record.get("measured_radial_error_um", "")),
                _um_text(record.get("predicted_min_error_um", "")),
                _um_text(record.get("predicted_max_error_um", "")),
                _threshold_text(record),
            ]
        )

    def update_error_summary(self) -> None:
        if not hasattr(self, "error_summary_table"):
            return
        self.error_chart.set_records(self.error_records)
        values = [
            float(record.get("predicted_max_error_um", 0.0))
            for record in self.error_records
            if record.get("status") == "ok" and record.get("predicted_max_error_um") != ""
        ]
        if not values:
            fixed_budget = error_budget_from_config({})
            self._set_error_summary_values(
                [
                    "촬영 전",
                    _um_text(fixed_budget.configured_worst_case_um),
                    _um_text(fixed_budget.max_allowed_um),
                    "-",
                    "-",
                    "0/0",
                ]
            )
            return
        max_value = max(values)
        mean_value = sum(values) / len(values)
        limit = float(self.error_records[-1].get("max_allowed_error_um", error_budget_from_config({}).max_allowed_um))
        failing = sum(1 for value in values if value > limit)
        self._set_error_summary_values(
            [
                "측정 중",
                _um_text(self.error_records[-1].get("configured_error_budget_um", "")),
                _um_text(limit),
                _um_text(max_value),
                _um_text(mean_value),
                f"{failing}/{len(values)}",
            ]
        )

    def _set_preview_metric_values(self, values: list[str]) -> None:
        _set_table_values(self.preview_metrics_table, values)

    def _set_error_summary_values(self, values: list[str]) -> None:
        _set_table_values(self.error_summary_table, values)

    @staticmethod
    def _checked_formats(checks: dict[str, QCheckBox]) -> list[str]:
        return [name for name, check in checks.items() if check.isChecked()]

    @staticmethod
    def _set_format_checks(checks: dict[str, QCheckBox], selected: Any) -> None:
        if isinstance(selected, str):
            selected_set = {item.strip().lower().lstrip(".") for item in selected.split(",") if item.strip()}
        else:
            selected_set = {str(item).strip().lower().lstrip(".") for item in selected or []}
        selected_set = {"yaml" if item == "yml" else item for item in selected_set}
        if not selected_set:
            selected_set = set(checks.keys())
        for name, check in checks.items():
            check.setChecked(name in selected_set)

    @staticmethod
    def _table_text(table: QTableWidget, row: int, column: int) -> str:
        item = table.item(row, column)
        if item is not None and item.data(POSITION_PLACEHOLDER_ROLE):
            return ""
        return item.text().strip() if item else ""

    @staticmethod
    def _set_combo_text(combo: QComboBox, text: str) -> None:
        index = combo.findText(text)
        if index >= 0:
            combo.setCurrentIndex(index)
        elif text:
            combo.addItem(text, text)
            combo.setCurrentIndex(combo.count() - 1)

    @staticmethod
    def _set_combo_data(combo: QComboBox, data: str) -> None:
        for index in range(combo.count()):
            if combo.itemData(index) == data:
                combo.setCurrentIndex(index)
                return


def _optional_float_text(text: str) -> float | None:
    clean = text.strip()
    if not clean:
        return None
    return float(clean)


def _optional_int_text(text: str) -> int | None:
    clean = text.strip()
    if not clean:
        return None
    number = float(clean)
    if not number.is_integer():
        raise ValueError(f"정수로 입력해야 합니다: {text}")
    return int(number)


def _safe_float_text(text: str, default: float) -> float:
    try:
        return float(text.strip())
    except (TypeError, ValueError):
        return default


def _linear_distance(x_start: float, y_start: float, x_stop: float, y_stop: float) -> float:
    return ((x_stop - x_start) ** 2 + (y_stop - y_start) ** 2) ** 0.5


def _linear_spacing_point_count(
    x_start: float,
    y_start: float,
    x_stop: float,
    y_stop: float,
    spacing_mm: float,
) -> int:
    if spacing_mm <= 0:
        raise ValueError("선형 경로 간격은 0보다 커야 합니다.")
    length = math.hypot(x_stop - x_start, y_stop - y_start)
    if length <= 0:
        return 2
    base_count = int(math.floor(length / spacing_mm)) + 1
    endpoint_count = (
        0
        if math.isclose((base_count - 1) * spacing_mm, length, rel_tol=0.0, abs_tol=1e-9)
        else 1
    )
    return base_count + endpoint_count


def _set_placeholder_color(line_edit: QLineEdit) -> None:
    palette = line_edit.palette()
    palette.setColor(QPalette.PlaceholderText, QColor("#9aa5af"))
    line_edit.setPalette(palette)


def _compact_number_text(value: Any, max_decimals: int) -> str:
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number) < 10 ** (-max_decimals):
        number = 0.0
    text = f"{number:.{max_decimals}f}".rstrip("0").rstrip(".")
    return text or "0"


def _mm_text(value: Any) -> str:
    return _compact_number_text(value, 4)


def _um_text(value: Any) -> str:
    return _compact_number_text(value, 2)


def _number_text(value: Any) -> str:
    return _compact_number_text(value, 3)


def _velocity_text(value: Any) -> str:
    if value in (None, ""):
        return "장비 기본값"
    return f"{_number_text(value)} mm/s"


def _capture_sequence_text(record: dict[str, Any]) -> str:
    capture_index = record.get("capture_index", "")
    capture_count = record.get("capture_count", "")
    if capture_index == "" and capture_count == "":
        return "-"
    if capture_index == "":
        return f"-/{capture_count}"
    if capture_count == "":
        return str(capture_index)
    return f"{capture_index}/{capture_count}"


def _position_cell_tooltip(column: int) -> str:
    tooltips = {
        1: "위치 라벨입니다. 비워도 실행은 가능하지만 구분하기 어렵습니다.",
        2: f"X 좌표입니다. 허용 범위: {_mm_text(POSITION_MIN_MM)}-{_mm_text(POSITION_MAX_MM)} mm",
        3: f"Y 좌표입니다. 허용 범위: {_mm_text(POSITION_MIN_MM)}-{_mm_text(POSITION_MAX_MM)} mm",
        4: "위치별 이동속도입니다. 비워 두면 촬영 설정의 이동속도를 사용합니다.",
        5: "위치별 캡쳐 수입니다. 비워 두면 촬영 설정의 기본 캡쳐 수를 사용합니다.",
    }
    return tooltips.get(column, "")


def _point_config_record(point: ScanPoint) -> dict[str, Any]:
    record: dict[str, Any] = {"label": point.label, "x_mm": point.x_mm, "y_mm": point.y_mm}
    if point.move_velocity_mm_s is not None:
        record["move_velocity_mm_s"] = point.move_velocity_mm_s
    if point.capture_count is not None:
        record["capture_count"] = point.capture_count
    return record


def _status_text(value: Any) -> str:
    mapping = {
        "ok": "완료",
        "error": "오류",
        "pending": "대기",
    }
    return mapping.get(str(value), str(value))


def _threshold_text(record: dict[str, Any]) -> str:
    if record.get("status") == "error":
        return "오류"
    if record.get("within_error_threshold") is True:
        return "통과"
    if record.get("within_error_threshold") is False:
        return "초과"
    return "확인"


def _preflight_status_color(status: str) -> QColor:
    if status == "오류":
        return QColor("#ffe1df")
    if status == "경고":
        return QColor("#fff4cc")
    return QColor("#dff2e8")


def _set_table_values(table: QTableWidget, values: list[str]) -> None:
    for column, value in enumerate(values[: table.columnCount()]):
        item = QTableWidgetItem(value)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        item.setTextAlignment(Qt.AlignCenter)
        table.setItem(0, column, item)


def _apply_button_icon(
    button: QPushButton,
    standard_pixmap: QStyle.StandardPixmap,
    tooltip: str,
    icon_size: int = 18,
) -> None:
    button.setIcon(QApplication.style().standardIcon(standard_pixmap))
    button.setIconSize(QSize(icon_size, icon_size))
    button.setToolTip(tooltip)


def _camera_display_name(camera: dict[str, str]) -> str:
    return " | ".join(
        item
        for item in (
            camera.get("model", ""),
            camera.get("serial", ""),
            camera.get("ip", ""),
            camera.get("device_class", ""),
        )
        if item
    )


def _camera_signature(cameras: list[dict[str, str]]) -> tuple[str, ...]:
    return tuple(
        sorted(
            "|".join(
                (
                    camera.get("serial", ""),
                    camera.get("ip", ""),
                    camera.get("model", ""),
                    camera.get("device_class", ""),
                )
            )
            for camera in cameras
        )
    )


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def _apply_default_font() -> None:
    app = QApplication.instance()
    if app is not None:
        font_family = "Malgun Gothic"
        windows_font = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts" / "malgun.ttf"
        if windows_font.exists():
            font_id = QFontDatabase.addApplicationFont(str(windows_font))
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                font_family = families[0]
        app.setFont(QFont(font_family, 9))


def bundled_resource(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", app_base_dir()))
    return base / name


def main() -> int:
    smoke_test = any(arg.lower() in {"--smoke", "--smoke-test"} for arg in sys.argv[1:]) or os.environ.get(
        "LINEAR_STAGE_SMOKE_TEST"
    ) == "1"

    app = QApplication(sys.argv)
    _apply_default_font()
    if smoke_test:
        window = MainWindow(start_device_scan=False)
        window.show()
        app.processEvents()
        window.close()
        app.processEvents()
        os._exit(0)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
