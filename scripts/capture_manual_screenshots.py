from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from linear_stage_control.gui_app import MainWindow  # noqa: E402
from linear_stage_control.linear_path_dialog import show_linear_path_dialog  # noqa: E402
from linear_stage_control.scan import points_from_records  # noqa: E402


def default_output_dir() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Downloads" / "LinearStageControl_UI_Screenshots"


def save_widget(widget, output_dir: Path, name: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    QApplication.processEvents()
    path = output_dir / f"{name}.png"
    pixmap = widget.grab()
    if pixmap.isNull():
        raise RuntimeError(f"Could not capture screenshot: {name}")
    pixmap.save(str(path), "PNG")
    return path


def prepare_window() -> MainWindow:
    window = MainWindow(start_device_scan=False)
    window.resize(1320, 860)
    window.show()
    window.set_positions(
        points_from_records(
            [
                {"label": "origin", "x_mm": 0, "y_mm": 0, "capture_count": 1},
                {"label": "sample_a", "x_mm": 0.5, "y_mm": -1.25, "move_velocity_mm_s": 25, "capture_count": 3},
                {"label": "sample_b", "x_mm": 1.0, "y_mm": -1.25},
            ]
        )
    )
    frame = np.tile(np.linspace(0, 255, 640, dtype=np.uint8), (420, 1))
    window.preview_mode = "live"
    window.preview_grid_check.setChecked(True)
    window.preview_cross_check.setChecked(True)
    window.on_live_frame(frame, {"completed_at": "2026-05-28T15:30:12+09:00"})
    QApplication.processEvents()
    return window


def capture_preflight(window: MainWindow, output_dir: Path) -> None:
    points, validation = window.read_positions_with_validation()
    dialog = window.build_preflight_dialog(points, window.build_config(points), validation)
    dialog.show()
    save_widget(dialog, output_dir, "06_preflight_check")
    dialog.close()


def capture_linear_path_dialog(window: MainWindow, output_dir: Path) -> None:
    def grab_and_close() -> None:
        dialog = QApplication.activeModalWidget()
        if dialog is not None:
            save_widget(dialog, output_dir, "05_linear_path_dialog")
            dialog.close()

    QTimer.singleShot(300, grab_and_close)
    show_linear_path_dialog(
        window,
        (0.0, 0.0, 5.0, 3.0),
        default_capture_count=window.capture_count_spin.value(),
        append_start_index=window.positions_table.rowCount(),
    )


def capture_all(output_dir: Path) -> list[Path]:
    app = QApplication.instance() or QApplication([])
    window = prepare_window()
    saved: list[Path] = []

    saved.append(save_widget(window, output_dir, "01_main_live_preview"))
    window.preview_tabs.setCurrentIndex(0)
    saved.append(save_widget(window, output_dir, "02_capture_list_tab"))
    window.preview_tabs.setCurrentIndex(1)
    saved.append(save_widget(window, output_dir, "03_error_tab"))
    window.preview_tabs.setCurrentIndex(2)
    saved.append(save_widget(window, output_dir, "04_diagnostics_tab"))
    capture_linear_path_dialog(window, output_dir)
    saved.append(output_dir / "05_linear_path_dialog.png")
    capture_preflight(window, output_dir)
    saved.append(output_dir / "06_preflight_check.png")
    window.preview_tabs.setCurrentIndex(3)
    saved.append(save_widget(window, output_dir, "07_log_tab"))
    saved.append(save_widget(window.control_panel, output_dir, "08_left_control_panel"))

    window.close()
    app.processEvents()
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture LinearStageControl manual UI screenshots.")
    parser.add_argument("--output-dir", type=Path, default=default_output_dir())
    args = parser.parse_args()
    paths = capture_all(args.output_dir)
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
