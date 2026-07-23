from __future__ import annotations

import os
import sys
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..gui_app import MainWindow
from ..gui_style import APP_STYLESHEET
from ..gui_support import apply_button_icon, apply_default_font
from .alignment_window import AlignmentWindow
from .fwhm_window import FwhmWindow
from .vp_window import VPWindow


def _trace(message: str) -> None:
    trace_path = os.environ.get("LINEAR_STAGE_SMOKE_TRACE")
    if not trace_path:
        return
    try:
        with open(trace_path, "a", encoding="utf-8") as trace_file:
            trace_file.write(f"{message}\n")
    except OSError:
        pass


class ExperimentLauncherWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        apply_default_font()
        self.active_window: QMainWindow | None = None
        self.card_buttons: list[QPushButton] = []
        self.setWindowTitle("Linear Stage Experiment Platform")
        self.resize(980, 620)
        self.setMinimumSize(820, 520)
        self._build_ui()
        self.setStyleSheet(APP_STYLESHEET + LAUNCHER_STYLESHEET)

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(18)

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        title_box = QWidget()
        title_layout = QVBoxLayout(title_box)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Integrated Experiment Platform")
        title.setObjectName("launcherTitle")
        subtitle = QLabel("Choose one experiment workspace. Hardware-backed windows run one at a time.")
        subtitle.setObjectName("launcherSubtitle")
        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)
        version = QLabel(f"v{__version__}")
        version.setObjectName("updateStatus")
        header_layout.addWidget(title_box, 1)
        header_layout.addWidget(version)
        layout.addWidget(header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)
        cards = [
            (
                "XY Stage Capture",
                "Move Zaber XY positions and capture Basler datasets.",
                "Dataset capture, live preview, preflight, diagnostics",
                self.open_xy_capture,
                QStyle.SP_DriveHDIcon,
            ),
            (
                "FWHM Monitor",
                "Measure laser/profile width at selected image columns.",
                "Gaussian FWHM, ROI, overlay, CSV",
                lambda: self.open_experiment(FwhmWindow),
                QStyle.SP_FileDialogDetailedView,
            ),
            (
                "Laser Alignment",
                "Fit a laser line and monitor screen-horizontal angle.",
                "Angle, RMS, coverage, ALIGNED state",
                lambda: self.open_experiment(AlignmentWindow),
                QStyle.SP_ArrowRight,
            ),
            (
                "Laser VP Detection",
                "Detect the virtual point from a triangular V target.",
                "Arm fitting, VP coordinate, RMS checks",
                lambda: self.open_experiment(VPWindow),
                QStyle.SP_ComputerIcon,
            ),
        ]
        for index, card in enumerate(cards):
            grid.addWidget(self._card(*card), index // 2, index % 2)
        layout.addLayout(grid, 1)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("launcherStatus")
        layout.addWidget(self.status_label)
        self.setCentralWidget(root)

    def _card(
        self,
        title: str,
        description: str,
        metrics: str,
        callback: Callable[[], None],
        icon: QStyle.StandardPixmap,
    ) -> QFrame:
        frame = QFrame()
        frame.setObjectName("launcherCard")
        frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        title_label = QLabel(title)
        title_label.setObjectName("launcherCardTitle")
        description_label = QLabel(description)
        description_label.setWordWrap(True)
        description_label.setObjectName("launcherCardDescription")
        metrics_label = QLabel(metrics)
        metrics_label.setWordWrap(True)
        metrics_label.setObjectName("launcherCardMetrics")
        button = QPushButton("Open")
        button.setProperty("variant", "primary")
        apply_button_icon(button, icon, f"Open {title}")
        button.clicked.connect(callback)
        self.card_buttons.append(button)
        layout.addWidget(title_label)
        layout.addWidget(description_label)
        layout.addStretch(1)
        layout.addWidget(metrics_label)
        layout.addWidget(button)
        return frame

    def open_xy_capture(self) -> None:
        self.open_experiment(MainWindow, start_device_scan=True)

    def open_experiment(self, window_class: type[QMainWindow], **kwargs: object) -> None:
        if self.active_window is not None:
            self.status_label.setText("Close the active experiment window before opening another one.")
            return
        window = window_class(**kwargs)
        window.setAttribute(Qt.WA_DeleteOnClose, True)
        window.destroyed.connect(self._on_active_window_destroyed)
        self.active_window = window
        self._set_cards_enabled(False)
        self.status_label.setText(f"Active: {window.windowTitle()}")
        window.show()
        window.raise_()
        window.activateWindow()

    def _on_active_window_destroyed(self) -> None:
        self.active_window = None
        self._set_cards_enabled(True)
        self.status_label.setText("Ready")

    def _set_cards_enabled(self, enabled: bool) -> None:
        for button in self.card_buttons:
            button.setEnabled(enabled)

    def closeEvent(self, event: object) -> None:
        if self.active_window is not None:
            self.active_window.close()
            if self.active_window is not None and self.active_window.isVisible():
                self.status_label.setText("Waiting for the active experiment window to stop hardware workers.")
                ignore = getattr(event, "ignore", None)
                if callable(ignore):
                    ignore()
                return
        super().closeEvent(event)


def main() -> int:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    smoke_test = bool(os.environ.get("LINEAR_STAGE_SMOKE_TRACE")) or any(
        arg.lower() in {"--smoke", "--smoke-test"} for arg in sys.argv[1:]
    )
    _trace(f"launcher_main_start smoke={int(smoke_test)}")
    if smoke_test:
        _trace("launcher_smoke_import_cv2_start")
        import cv2  # noqa: F401

        _trace("launcher_smoke_import_cv2_done")
        _trace("launcher_smoke_import_scipy_start")
        import numpy as np
        from scipy.optimize import curve_fit

        def _line(x: np.ndarray, slope: float, intercept: float) -> np.ndarray:
            return slope * x + intercept

        x_data = np.asarray([0.0, 1.0, 2.0, 3.0], dtype=np.float64)
        y_data = np.asarray([1.0, 3.0, 5.0, 7.0], dtype=np.float64)
        params, _ = curve_fit(_line, x_data, y_data, p0=(1.0, 0.0), maxfev=1000)
        if not np.allclose(params, (2.0, 1.0), atol=1e-6):
            raise RuntimeError(f"scipy curve_fit smoke check failed: {params!r}")
        _trace("launcher_smoke_import_scipy_done")
        return 0

    _trace("launcher_qapplication_start")
    app = QApplication.instance() or QApplication(sys.argv)
    _trace("launcher_window_start")
    window = ExperimentLauncherWindow()
    window.show()
    return int(app.exec())


LAUNCHER_STYLESHEET = """
QLabel#launcherTitle {
    font-size: 24px;
    font-weight: 700;
    color: #111827;
}
QLabel#launcherSubtitle {
    color: #4b5563;
}
QFrame#launcherCard {
    background: #ffffff;
    border: 1px solid #d0d7de;
    border-radius: 8px;
}
QLabel#launcherCardTitle {
    font-size: 17px;
    font-weight: 700;
    color: #111827;
}
QLabel#launcherCardDescription {
    color: #344054;
}
QLabel#launcherCardMetrics {
    color: #667085;
}
QLabel#launcherStatus {
    color: #344054;
}
"""
