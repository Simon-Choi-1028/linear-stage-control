from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Callable

import numpy as np
from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import ConfigError, load_config
from ..gui_style import APP_STYLESHEET
from ..gui_support import apply_button_icon, apply_default_font
from ..gui_widgets import ImagePreviewLabel
from ..preview_rendering import qimage_from_array, render_preview_qimage
from .frame_sources import (
    BaslerExperimentSource,
    FileFrameSource,
    FrameSource,
    SyntheticFrameSource,
    bgr_to_rgb,
    ensure_bgr,
)
from .manual_stage_panel import ManualStagePanel
from .result_io import default_output_dir, save_csv, save_overlay, timestamp_for_filename
from .roi_controls import Rect, RoiControls


@dataclass
class ProcessedFrame:
    overlay_bgr: np.ndarray
    result: object
    roi: Rect


@dataclass
class MeasurementSnapshot:
    overlay_bgr: np.ndarray
    result: object
    source_name: str
    roi: Rect
    timestamp: str
    frame_id: int
    state_version: int


class ExperimentLiveWorker(QThread):
    frame_ready = Signal(object, object, str, object, int, int, str)
    status_changed = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        source_factory: Callable[[], FrameSource],
        processor: Callable[[np.ndarray, dict[str, Any]], ProcessedFrame],
        processing_state: dict[str, Any],
        interval_ms: int = 40,
    ) -> None:
        super().__init__()
        self.source_factory = source_factory
        self.processor = processor
        self.interval_ms = max(10, int(interval_ms))
        self._stop_requested = False
        self._state_lock = Lock()
        self._frame_lock = Lock()
        self._frame_in_flight = False
        self._processing_state = deepcopy(processing_state)
        self._frame_id = 0

    def request_stop(self) -> None:
        self._stop_requested = True

    def request_processing_state(self, state: dict[str, Any]) -> None:
        with self._state_lock:
            self._processing_state = deepcopy(state)

    def _state_snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return deepcopy(self._processing_state)

    def mark_frame_consumed(self) -> None:
        with self._frame_lock:
            self._frame_in_flight = False

    def _frame_delivery_pending(self) -> bool:
        with self._frame_lock:
            return self._frame_in_flight

    def _mark_frame_in_flight(self) -> bool:
        with self._frame_lock:
            if self._frame_in_flight:
                return False
            self._frame_in_flight = True
            return True

    def run(self) -> None:
        source: FrameSource | None = None
        try:
            source = self.source_factory()
            source.open()
            self.status_changed.emit(f"Source running: {source.name}")
            while not self._stop_requested:
                if self._frame_delivery_pending():
                    self.msleep(self.interval_ms)
                    continue
                frame = source.read()
                if frame is None:
                    self.msleep(self.interval_ms)
                    continue
                if not self._mark_frame_in_flight():
                    self.msleep(self.interval_ms)
                    continue
                frame_bgr = ensure_bgr(frame)
                state = self._state_snapshot()
                state_version = int(state.get("_state_version", 0) or 0)
                processed = self.processor(frame_bgr, state)
                overlay_bgr = ensure_bgr(np.asarray(processed.overlay_bgr))
                _validate_processed_frame(frame_bgr, overlay_bgr, processed.roi)
                self._frame_id += 1
                timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
                self.frame_ready.emit(
                    overlay_bgr,
                    processed.result,
                    source.name,
                    processed.roi,
                    self._frame_id,
                    state_version,
                    timestamp,
                )
                self.msleep(self.interval_ms)
        except Exception as exc:
            self.mark_frame_consumed()
            if not self._stop_requested:
                self.failed.emit(str(exc))
        finally:
            if source is not None:
                try:
                    source.close()
                except Exception as exc:
                    if not self._stop_requested:
                        self.failed.emit(f"Source close error: {exc}")


class ExperimentWindowBase(QMainWindow):
    feature_key = "experiment"
    feature_title = "Experiment"
    synthetic_source_name = "Synthetic"
    default_roi = (0.0, 100.0, 0.0, 100.0)
    csv_fieldnames: list[str] = []

    def __init__(self, *, config_path: Path | None = None) -> None:
        super().__init__()
        apply_default_font()
        self.config_path = config_path or Path("config.yaml")
        self.config = self._load_config()
        self.worker: ExperimentLiveWorker | None = None
        self.latest_measurement: MeasurementSnapshot | None = None
        self.preview_source_qimage: QImage | None = None
        self.preview_crop_rect: tuple[int, int, int, int] | None = None
        self.preview_center_x = 0.5
        self.preview_center_y = 0.5
        self._layout_is_narrow: bool | None = None
        self._close_requested = False
        self._released_worker_ids: set[int] = set()
        self._processing_state_version = 0
        self._processing_update_timer = QTimer(self)
        self._processing_update_timer.setSingleShot(True)
        self._processing_update_timer.setInterval(40)
        self._processing_update_timer.timeout.connect(self._apply_processing_update)
        self.setWindowTitle(self.feature_title)
        self.resize(1320, 840)
        self.setMinimumSize(900, 680)
        self._build_ui()
        self._set_save_buttons_enabled(False)
        self.setStyleSheet(APP_STYLESHEET + EXPERIMENT_STYLESHEET)
        self.start_synthetic_source()

    def closeEvent(self, event: object) -> None:
        self._close_requested = True
        source_stopped = self.stop_source(wait_ms=1500)
        stage_stopped = self.manual_stage_panel.stop_worker(wait_ms=1500, closing=True)
        if not source_stopped or not stage_stopped:
            self.set_status("Stopping hardware workers before closing...")
            ignore = getattr(event, "ignore", None)
            if callable(ignore):
                ignore()
            return
        self._close_requested = False
        super().closeEvent(event)

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)
        self.update_responsive_layout()
        self.render_preview_source()

    def config_provider(self) -> dict[str, Any]:
        config = deepcopy(self.config)
        return config

    def synthetic_factory(self, phase: float) -> np.ndarray:
        raise NotImplementedError

    def build_processing_controls(self) -> QWidget:
        raise NotImplementedError

    def processing_state(self) -> dict[str, Any]:
        raise NotImplementedError

    def process_frame(self, frame_bgr: np.ndarray, state: dict[str, Any]) -> ProcessedFrame:
        raise NotImplementedError

    def result_pairs(self, result: object) -> list[tuple[str, str]]:
        raise NotImplementedError

    def csv_rows(self, result: object) -> list[dict[str, Any]]:
        raise NotImplementedError

    def _load_config(self) -> dict[str, Any]:
        try:
            return load_config(self.config_path) if self.config_path.exists() else {}
        except ConfigError:
            return {}

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("topToolbar")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 7, 14, 7)
        self.title_label = QLabel(self.feature_title)
        self.title_label.setObjectName("topTitle")
        self.source_status_label = QLabel("Source: --")
        self.source_status_label.setObjectName("topSubtitle")
        header_layout.addWidget(self.title_label)
        header_layout.addSpacing(12)
        header_layout.addWidget(self.source_status_label, 1)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.control_scroll = QScrollArea()
        self.control_scroll.setObjectName("controlScroll")
        self.control_scroll.setWidgetResizable(True)
        self.control_scroll.setFrameShape(QScrollArea.NoFrame)
        self.control_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.control_scroll.setMinimumWidth(450)
        self.control_panel = self._build_control_panel()
        self.control_scroll.setWidget(self.control_panel)
        self.preview_panel = self._build_preview_panel()
        self.splitter.addWidget(self.control_scroll)
        self.splitter.addWidget(self.preview_panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([450, 870])

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("experimentStatus")
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(34)

        root_layout.addWidget(header)
        root_layout.addWidget(self.splitter, 1)
        root_layout.addWidget(self.status_label)
        self.setCentralWidget(root)

    def _build_control_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("experimentControlPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 12, 14)
        layout.setSpacing(12)
        layout.addWidget(self._source_group())
        self.manual_stage_panel = ManualStagePanel(self.config_provider)
        self.manual_stage_panel.log_message.connect(self.set_status)
        self.manual_stage_panel.busy_changed.connect(lambda busy=False: self._maybe_finish_pending_close())
        layout.addWidget(self._wrap_group("Manual Stage", self.manual_stage_panel, "Manual Stage"))
        left, right, top, bottom = self.default_roi
        self.roi_controls = RoiControls(left=left, right=right, top=top, bottom=bottom)
        self.roi_controls.roi_changed.connect(self.request_processing_update)
        layout.addWidget(self._wrap_group("ROI", self.roi_controls, "ROI"))
        self.processing_controls = self.build_processing_controls()
        layout.addWidget(self._wrap_group("Processing", self.processing_controls, "Processing"))
        layout.addWidget(self._save_group())
        layout.addStretch(1)
        return panel

    def _build_preview_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("experimentPreviewPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 14, 14)
        layout.setSpacing(9)

        self.preview_label = ImagePreviewLabel("No frame")
        self.preview_label.setObjectName("preview")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(QSize(520, 360))
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview_label.clicked.connect(self.set_preview_center_from_label)
        layout.addWidget(self.preview_label, 1)

        toolbar = QWidget()
        toolbar.setObjectName("previewToolBar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(9, 7, 9, 7)
        self.preview_zoom_slider = QSlider(Qt.Horizontal)
        self.preview_zoom_slider.setRange(100, 800)
        self.preview_zoom_slider.setValue(100)
        self.preview_zoom_slider.setTracking(False)
        self.preview_zoom_slider.setFixedWidth(150)
        self.preview_zoom_label = QLabel("100%")
        self.preview_grid_check = QCheckBox("Grid")
        self.preview_cross_check = QCheckBox("Cross")
        self.preview_reset_button = QPushButton("100%")
        apply_button_icon(self.preview_reset_button, QStyle.SP_LineEditClearButton, "Reset preview zoom")
        self.preview_zoom_slider.valueChanged.connect(self.set_preview_zoom)
        self.preview_reset_button.clicked.connect(self.reset_preview_zoom)
        self.preview_grid_check.toggled.connect(lambda _checked=False: self.render_preview_source())
        self.preview_cross_check.toggled.connect(lambda _checked=False: self.render_preview_source())
        toolbar_layout.addWidget(QLabel("Inspect"))
        toolbar_layout.addStretch(1)
        toolbar_layout.addWidget(QLabel("Zoom"))
        toolbar_layout.addWidget(self.preview_zoom_slider)
        toolbar_layout.addWidget(self.preview_zoom_label)
        toolbar_layout.addWidget(self.preview_reset_button)
        toolbar_layout.addWidget(self.preview_grid_check)
        toolbar_layout.addWidget(self.preview_cross_check)
        layout.addWidget(toolbar)

        self.result_table = QTableWidget(0, 2)
        self.result_table.setObjectName("Result")
        self.result_table.setHorizontalHeaderLabels(["Metric", "Value"])
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.result_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.result_table.setMinimumHeight(170)
        self.result_table.setMaximumHeight(230)
        layout.addWidget(self.result_table)
        return panel

    def _source_group(self) -> QGroupBox:
        group = QGroupBox("Source")
        group.setObjectName("Source")
        layout = QVBoxLayout(group)
        row = QHBoxLayout()
        self.basler_button = QPushButton("Basler")
        self.file_button = QPushButton("Open file")
        self.synthetic_button = QPushButton("Synthetic")
        self.stop_button = QPushButton("Stop")
        apply_button_icon(self.basler_button, QStyle.SP_ComputerIcon, "Open selected Basler camera")
        apply_button_icon(self.file_button, QStyle.SP_DialogOpenButton, "Open image or video file")
        apply_button_icon(self.synthetic_button, QStyle.SP_MediaPlay, "Use synthetic test source")
        apply_button_icon(self.stop_button, QStyle.SP_MediaStop, "Stop current source")
        self.basler_button.clicked.connect(self.start_basler_source)
        self.file_button.clicked.connect(self.open_file_source)
        self.synthetic_button.clicked.connect(self.start_synthetic_source)
        self.stop_button.clicked.connect(lambda: self.stop_source(wait_ms=1500))
        for button in (self.basler_button, self.file_button, self.synthetic_button, self.stop_button):
            row.addWidget(button)
        self.source_label = QLabel("Source: --")
        self.source_label.setWordWrap(True)
        layout.addLayout(row)
        layout.addWidget(self.source_label)
        return group

    def _save_group(self) -> QGroupBox:
        group = QGroupBox("Save")
        group.setObjectName("Save")
        layout = QHBoxLayout(group)
        self.save_overlay_button = QPushButton("Save overlay")
        self.save_csv_button = QPushButton("Save CSV")
        apply_button_icon(self.save_overlay_button, QStyle.SP_DialogSaveButton, "Save current overlay image")
        apply_button_icon(self.save_csv_button, QStyle.SP_FileDialogDetailedView, "Save current measurement CSV")
        self.save_overlay_button.clicked.connect(self.save_overlay_dialog)
        self.save_csv_button.clicked.connect(self.save_csv_dialog)
        layout.addWidget(self.save_overlay_button)
        layout.addWidget(self.save_csv_button)
        return group

    def _wrap_group(self, title: str, widget: QWidget, object_name: str) -> QGroupBox:
        group = QGroupBox(title)
        group.setObjectName(object_name)
        layout = QVBoxLayout(group)
        layout.addWidget(widget)
        return group

    def start_synthetic_source(self) -> None:
        if not self.stop_source(wait_ms=1000):
            return
        self._render_synthetic_snapshot()
        self._start_source(
            lambda: SyntheticFrameSource(self.synthetic_source_name, self.synthetic_factory),
            stop_existing=False,
            invalidate_existing=False,
        )

    def start_basler_source(self) -> None:
        self._start_source(lambda: BaslerExperimentSource(self.config_provider()))

    def open_file_source(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self,
            "Open image or video",
            str(Path.home()),
            "Image/video (*.bmp *.jpg *.jpeg *.png *.tif *.tiff *.avi *.mp4 *.mov *.mkv);;All files (*.*)",
        )
        if path:
            self._start_source(lambda path=path: FileFrameSource(path))

    def _start_source(
        self,
        source_factory: Callable[[], FrameSource],
        *,
        stop_existing: bool = True,
        invalidate_existing: bool = True,
    ) -> None:
        if stop_existing and not self.stop_source(wait_ms=1000):
            return
        if self.worker is not None:
            self.set_status("Source is still stopping; wait for cleanup before starting another source.")
            return
        try:
            processing_state = self._combined_processing_state()
        except Exception as exc:
            self._invalidate_latest_measurement(f"Processing input error: {exc}")
            QMessageBox.warning(self, self.feature_title, str(exc))
            return
        if invalidate_existing:
            self._invalidate_latest_measurement("Source changed; waiting for a fresh measurement.")
        worker = ExperimentLiveWorker(source_factory, self.process_frame, processing_state)
        worker.frame_ready.connect(
            lambda overlay,
            result,
            source_name,
            roi,
            frame_id,
            state_version,
            timestamp,
            worker=worker: self._on_frame_ready_from_worker(
                worker,
                overlay,
                result,
                source_name,
                roi,
                frame_id,
                state_version,
                timestamp,
            )
        )
        worker.status_changed.connect(lambda message, worker=worker: self._on_worker_status(worker, message))
        worker.failed.connect(lambda message, worker=worker: self._on_worker_failed(worker, message))
        worker.finished.connect(lambda worker=worker: self._on_worker_finished(worker))
        self.worker = worker
        self._set_source_buttons_enabled(False, stop_enabled=True)
        worker.start()
        self.set_status("Source starting")

    def _render_synthetic_snapshot(self) -> None:
        try:
            frame = self.synthetic_factory(0.0)
            processed = self.process_frame(ensure_bgr(frame), self._combined_processing_state())
        except Exception as exc:
            self._invalidate_latest_measurement(f"Synthetic preview error: {exc}")
            return
        self._on_frame_ready(
            processed.overlay_bgr,
            processed.result,
            self.synthetic_source_name,
            processed.roi,
            frame_id=0,
            state_version=self._processing_state_version,
            timestamp=datetime.now().astimezone().isoformat(timespec="milliseconds"),
        )

    def stop_source(self, wait_ms: int = 0) -> bool:
        worker = self.worker
        if worker is None:
            self._set_source_buttons_enabled(True)
            return True
        worker.request_stop()
        if wait_ms and not worker.wait(wait_ms):
            self._set_source_buttons_enabled(False, stop_enabled=False)
            self.set_status("Source stopping; waiting for camera/file read to return...")
            return False
        self._release_worker(worker)
        self.set_status("Source stopped")
        self._set_source_buttons_enabled(True)
        return True

    def request_processing_update(self) -> None:
        self._processing_state_version += 1
        self._invalidate_latest_measurement("Processing settings changed; waiting for a fresh measurement.")
        if self.worker is None:
            return
        if not self._processing_update_timer.isActive():
            self._processing_update_timer.start()

    def _apply_processing_update(self) -> None:
        worker = self.worker
        if worker is None:
            return
        try:
            state = self._combined_processing_state()
        except Exception as exc:
            self._invalidate_latest_measurement(f"Processing input error: {exc}")
            return
        if worker is self.worker:
            worker.request_processing_state(state)

    def _combined_processing_state(self) -> dict[str, Any]:
        return {
            "roi_percentages": self.roi_controls.percentages() if hasattr(self, "roi_controls") else self.default_roi,
            "_state_version": self._processing_state_version,
            **self.processing_state(),
        }

    def roi_from_state(self, shape: tuple[int, ...], state: dict[str, Any]) -> Rect:
        height, width = shape[:2]
        left, right, top, bottom = state.get("roi_percentages", self.default_roi)
        x1 = int(round(width * float(left) / 100.0))
        x2 = int(round(width * float(right) / 100.0))
        y1 = int(round(height * float(top) / 100.0))
        y2 = int(round(height * float(bottom) / 100.0))
        x1 = max(0, min(width - 1, x1))
        y1 = max(0, min(height - 1, y1))
        x2 = min(width, max(x1 + 2, x2))
        y2 = min(height, max(y1 + 2, y2))
        return x1, y1, x2, y2

    def _on_frame_ready_from_worker(
        self,
        worker: ExperimentLiveWorker,
        overlay_bgr: object,
        result: object,
        source_name: str,
        roi: object,
        frame_id: int,
        state_version: int,
        timestamp: str,
    ) -> None:
        try:
            if worker is self.worker and not self._close_requested:
                if int(state_version) < self._processing_state_version:
                    self.set_status("Dropped stale processed frame; waiting for current settings.")
                    return
                self._on_frame_ready(overlay_bgr, result, source_name, roi, frame_id, state_version, timestamp)
        finally:
            worker.mark_frame_consumed()

    def _on_frame_ready(
        self,
        overlay_bgr: object,
        result: object,
        source_name: str,
        roi: object,
        frame_id: int = 0,
        state_version: int = 0,
        timestamp: str | None = None,
    ) -> None:
        try:
            overlay = ensure_bgr(np.asarray(overlay_bgr))
            roi_tuple = _coerce_roi(roi, overlay.shape)
            qimage = qimage_from_array(bgr_to_rgb(overlay))
            pairs = self.result_pairs(result)
        except Exception as exc:
            self._invalidate_latest_measurement(f"Result rendering error: {exc}")
            return
        measurement = MeasurementSnapshot(
            overlay_bgr=overlay,
            result=result,
            source_name=source_name,
            roi=roi_tuple,
            timestamp=timestamp or datetime.now().astimezone().isoformat(timespec="milliseconds"),
            frame_id=int(frame_id),
            state_version=int(state_version),
        )
        self.latest_measurement = measurement
        self.source_label.setText(f"Source: {source_name}")
        self.source_status_label.setText(f"Source: {source_name}")
        self.roi_controls.update_summary(roi_tuple)
        self.set_preview_source(qimage, reset_center=False)
        self.set_result_pairs(pairs)
        self._set_save_buttons_enabled(True)

    def _on_worker_status(self, worker: ExperimentLiveWorker, message: str) -> None:
        if worker is self.worker:
            self.set_status(message)
            self._set_source_buttons_enabled(True)

    def _on_worker_failed(self, worker: ExperimentLiveWorker, message: str) -> None:
        if worker is not self.worker:
            return
        self._invalidate_latest_measurement(f"Source error: {message}")
        if not self._close_requested and self.isVisible():
            QMessageBox.warning(self, self.feature_title, message)

    def _on_worker_finished(self, worker: ExperimentLiveWorker) -> None:
        self._release_worker(worker)
        self._set_source_buttons_enabled(True)
        self._maybe_finish_pending_close()

    def _release_worker(self, worker: ExperimentLiveWorker) -> None:
        if self.worker is worker:
            self.worker = None
        worker_id = id(worker)
        if worker_id in self._released_worker_ids:
            return
        self._released_worker_ids.add(worker_id)
        worker.destroyed.connect(lambda *_args, worker_id=worker_id: self._released_worker_ids.discard(worker_id))
        worker.deleteLater()

    def _set_source_buttons_enabled(self, enabled: bool, *, stop_enabled: bool | None = None) -> None:
        for button_name in ("basler_button", "file_button", "synthetic_button"):
            button = getattr(self, button_name, None)
            if button is not None:
                button.setEnabled(enabled)
        stop_button = getattr(self, "stop_button", None)
        if stop_button is not None:
            stop_button.setEnabled(enabled if stop_enabled is None else stop_enabled)

    def _set_save_buttons_enabled(self, enabled: bool) -> None:
        for button_name in ("save_overlay_button", "save_csv_button"):
            button = getattr(self, button_name, None)
            if button is not None:
                button.setEnabled(enabled)

    def _invalidate_latest_measurement(self, message: str) -> None:
        self.latest_measurement = None
        self._set_save_buttons_enabled(False)
        if hasattr(self, "result_table"):
            self.set_result_pairs([("Status", message)])
        if hasattr(self, "status_label"):
            self.set_status(message)

    def _maybe_finish_pending_close(self) -> None:
        if not self._close_requested:
            return
        source_running = self.worker is not None and self.worker.isRunning()
        stage_running = self.manual_stage_panel.has_running_worker()
        if not source_running and not stage_running:
            self.close()

    def set_result_pairs(self, pairs: list[tuple[str, str]]) -> None:
        self.result_table.setRowCount(len(pairs))
        for row, (metric, value) in enumerate(pairs):
            metric_item = QTableWidgetItem(metric)
            value_item = QTableWidgetItem(value)
            metric_item.setTextAlignment(Qt.AlignCenter)
            value_item.setTextAlignment(Qt.AlignCenter)
            self.result_table.setItem(row, 0, metric_item)
            self.result_table.setItem(row, 1, value_item)

    def set_preview_zoom(self, value: int) -> None:
        self.preview_zoom_label.setText(f"{int(value)}%")
        self.render_preview_source()

    def reset_preview_zoom(self) -> None:
        self.preview_center_x = 0.5
        self.preview_center_y = 0.5
        self.preview_zoom_slider.setValue(100)
        self.render_preview_source()

    def set_preview_center_from_label(self, x: float, y: float) -> None:
        if self.preview_source_qimage is None or self.preview_crop_rect is None:
            return
        pixmap = self.preview_label.pixmap()
        if pixmap is None or pixmap.isNull():
            return
        x_offset = max(0.0, (self.preview_label.width() - pixmap.width()) / 2)
        y_offset = max(0.0, (self.preview_label.height() - pixmap.height()) / 2)
        rel_x = (x - x_offset) / max(1, pixmap.width())
        rel_y = (y - y_offset) / max(1, pixmap.height())
        if rel_x < 0 or rel_x > 1 or rel_y < 0 or rel_y > 1:
            return
        crop_x, crop_y, crop_w, crop_h = self.preview_crop_rect
        self.preview_center_x = min(
            1.0, max(0.0, (crop_x + rel_x * crop_w) / max(1, self.preview_source_qimage.width()))
        )
        self.preview_center_y = min(
            1.0, max(0.0, (crop_y + rel_y * crop_h) / max(1, self.preview_source_qimage.height()))
        )
        self.render_preview_source()

    def set_preview_source(self, qimage: QImage, reset_center: bool = False) -> None:
        self.preview_source_qimage = qimage
        if reset_center:
            self.preview_center_x = 0.5
            self.preview_center_y = 0.5
        self.render_preview_source()

    def render_preview_source(self) -> None:
        if self.preview_source_qimage is None:
            return
        target = QSize(max(240, self.preview_label.width() - 24), max(180, self.preview_label.height() - 24))
        try:
            pixmap, crop_rect = render_preview_qimage(
                self.preview_source_qimage,
                target,
                self.preview_zoom_slider.value(),
                self.preview_center_x,
                self.preview_center_y,
                self.preview_grid_check.isChecked(),
                self.preview_cross_check.isChecked(),
            )
        except Exception as exc:
            self.preview_label.setText(f"Preview render error\n{exc}")
            self.preview_crop_rect = None
            return
        self.preview_crop_rect = crop_rect
        self.preview_label.setPixmap(pixmap)

    def update_responsive_layout(self) -> None:
        is_narrow = self.width() < 1180
        if self._layout_is_narrow == is_narrow:
            return
        self._layout_is_narrow = is_narrow
        self.splitter.setOrientation(Qt.Vertical if is_narrow else Qt.Horizontal)
        self.splitter.setSizes(
            [330, max(420, self.height() - 330)] if is_narrow else [450, max(720, self.width() - 450)]
        )

    def save_overlay_dialog(self) -> None:
        measurement = self.latest_measurement
        if measurement is None:
            QMessageBox.information(self, "Save overlay", "No overlay image is available yet.")
            return
        directory = default_output_dir(self.feature_key)
        default_path = directory / f"{self.feature_key}_overlay_{timestamp_for_filename()}.png"
        path, _selected = QFileDialog.getSaveFileName(
            self, "Save overlay", str(default_path), "PNG image (*.png);;JPEG image (*.jpg);;All files (*.*)"
        )
        if not path:
            return
        try:
            saved = save_overlay(path, measurement.overlay_bgr)
        except Exception as exc:
            QMessageBox.warning(self, "Save overlay", str(exc))
            return
        self.set_status(f"Saved overlay: {saved.name}")

    def save_csv_dialog(self) -> None:
        measurement = self.latest_measurement
        if measurement is None:
            QMessageBox.information(self, "Save CSV", "No measurement result is available yet.")
            return
        directory = default_output_dir(self.feature_key)
        default_path = directory / f"{self.feature_key}_result_{timestamp_for_filename()}.csv"
        path, _selected = QFileDialog.getSaveFileName(
            self, "Save CSV", str(default_path), "CSV file (*.csv);;All files (*.*)"
        )
        if not path:
            return
        try:
            rows = self.csv_rows(measurement.result)
            saved = save_csv(path, self.csv_fieldnames, rows)
        except Exception as exc:
            QMessageBox.warning(self, "Save CSV", str(exc))
            return
        self.set_status(f"Saved CSV: {saved.name}")

    def set_status(self, message: str) -> None:
        self.status_label.setText(str(message))

    def measurement_context(self) -> dict[str, Any]:
        measurement = self.latest_measurement
        if measurement is None:
            raise RuntimeError("No current measurement snapshot is available.")
        x1, y1, x2, y2 = measurement.roi
        return {
            "timestamp": measurement.timestamp,
            "source": measurement.source_name,
            "roi_left": x1,
            "roi_right": x2 - 1,
            "roi_top": y1,
            "roi_bottom": y2 - 1,
            "frame_id": measurement.frame_id,
            "state_version": measurement.state_version,
        }


def _coerce_roi(roi: object, frame_shape: tuple[int, ...]) -> Rect:
    height, width = frame_shape[:2]
    if not isinstance(roi, (tuple, list)) or len(roi) != 4:
        raise ValueError("Processed ROI must be a 4-value rectangle.")
    x1, y1, x2, y2 = (int(value) for value in roi)
    if x1 < 0 or y1 < 0 or x2 > width or y2 > height or x2 <= x1 or y2 <= y1:
        raise ValueError(f"Processed ROI is outside the frame: {roi}")
    return x1, y1, x2, y2


def _validate_processed_frame(frame_bgr: np.ndarray, overlay_bgr: np.ndarray, roi: object) -> None:
    if overlay_bgr.shape[:2] != frame_bgr.shape[:2]:
        raise ValueError(
            f"Processed overlay shape {overlay_bgr.shape[:2]} does not match frame shape {frame_bgr.shape[:2]}."
        )
    _coerce_roi(roi, frame_bgr.shape)


EXPERIMENT_STYLESHEET = """
QWidget#experimentControlPanel {
    background: #f5f7fa;
}
QWidget#experimentPreviewPanel {
    background: #ffffff;
}
QLabel#experimentStatus {
    background: #101828;
    color: #f8fafc;
    padding: 8px 14px;
}
QLabel#roiSummary {
    color: #46515f;
}
QGroupBox {
    font-weight: 600;
}
QTableWidget#Result {
    background: #ffffff;
    border: 1px solid #d0d7de;
}
QLabel#preview {
    background: #050607;
    color: #d0d7de;
    border: 1px solid #232b33;
}
"""
