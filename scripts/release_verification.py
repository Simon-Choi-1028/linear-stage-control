from __future__ import annotations

import argparse
import json
import math
import os
import random
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import numpy as np
from PIL import Image, ImageDraw
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import linear_stage_control.stage as stage_module
from linear_stage_control.gui_app import MainWindow
from linear_stage_control.position_validation import validate_scan_points
from linear_stage_control.scan import points_from_records
from linear_stage_control.stage import StageSettings, ZaberXYStage


class VirtualAxis:
    def __init__(self, name: str) -> None:
        self.name = name
        self.position_mm = 0.0
        self.homed = False
        self.move_calls = 0
        self.home_calls = 0
        self.stop_calls = 0

    def is_homed(self) -> bool:
        return self.homed

    def home(self) -> None:
        self.home_calls += 1
        self.position_mm = 0.0
        self.homed = True

    def move_absolute(self, position: float, *_args: object, **_kwargs: object) -> None:
        self.move_calls += 1
        self.position_mm = float(position)

    def is_busy(self) -> bool:
        return False

    def stop(self, *, wait_until_idle: bool = False) -> None:
        _ = wait_until_idle
        self.stop_calls += 1

    def get_position(self, *_args: object, **_kwargs: object) -> float:
        return self.position_mm


class VirtualDevice:
    axis_count = 1
    name = "Virtual X-LDM210C-AE54"
    serial_number = 100001

    def __init__(self, address: int, axis: VirtualAxis) -> None:
        self.device_address = address
        self.device_id = 50812
        self.axis = axis

    def get_axis(self, axis_number: int) -> VirtualAxis:
        if axis_number != 1:
            raise ValueError(f"virtual device has only axis 1, requested {axis_number}")
        return self.axis


class VirtualConnection:
    def __init__(self, port_name: str, axes: tuple[VirtualAxis, VirtualAxis]) -> None:
        self.port_name = port_name
        self.axes = axes
        self.closed = False

    def enable_alerts(self) -> None:
        return

    def detect_devices(self, identify_devices: bool = True) -> list[VirtualDevice]:
        _ = identify_devices
        return [VirtualDevice(1, self.axes[0]), VirtualDevice(2, self.axes[1])]

    def close(self) -> None:
        self.closed = True


@contextmanager
def patched_virtual_serial(axes: tuple[VirtualAxis, VirtualAxis]) -> Iterator[None]:
    original = stage_module.Connection.open_serial_port

    def open_virtual_serial(port_name: str, baud_rate: int = 115200) -> VirtualConnection:
        _ = baud_rate
        return VirtualConnection(port_name, axes)

    stage_module.Connection.open_serial_port = open_virtual_serial  # type: ignore[method-assign]
    try:
        yield
    finally:
        stage_module.Connection.open_serial_port = original  # type: ignore[method-assign]


def run_zaber_experiments(iterations: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index in range(iterations):
        axes = (VirtualAxis("x"), VirtualAxis("y"))
        settings = StageSettings(serial_port=f"COM_VIRTUAL_{index + 1:02d}")
        target_x = round(1.25 + index * 0.37, 4)
        target_y = round(2.5 + index * 0.41, 4)
        with patched_virtual_serial(axes):
            with ZaberXYStage(settings) as stage:
                summary = stage.device_summary()
                stage.home()
                stage.move_absolute_mm(target_x, target_y, velocity_mm_s=12.5)
                actual_x, actual_y = stage.position_mm()
        passed = (
            axes[0].home_calls == 1
            and axes[1].home_calls == 1
            and math.isclose(actual_x or 0.0, target_x)
            and math.isclose(actual_y or 0.0, target_y)
            and summary[0]["axis_count"] == 1
            and summary[1]["axis_count"] == 1
        )
        results.append(
            {
                "iteration": index + 1,
                "port": settings.serial_port,
                "target": {"x_mm": target_x, "y_mm": target_y},
                "actual": {"x_mm": actual_x, "y_mm": actual_y},
                "x_home_calls": axes[0].home_calls,
                "y_home_calls": axes[1].home_calls,
                "x_move_calls": axes[0].move_calls,
                "y_move_calls": axes[1].move_calls,
                "device_summary": summary,
                "passed": passed,
            }
        )
    return results


def sampled_color_count(path: Path) -> int:
    image = Image.open(path).convert("RGB")
    step_x = max(1, image.width // 64)
    step_y = max(1, image.height // 64)
    colors = {
        image.getpixel((x, y))
        for x in range(0, image.width, step_x)
        for y in range(0, image.height, step_y)
    }
    return len(colors)


def run_gui_experiments(iterations: int, output_dir: Path) -> list[dict[str, Any]]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    screenshot_dir = output_dir / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    rng = random.Random(1616)
    sizes = [(1320, 820), (980, 760), (1440, 640), (1180, 900), (1040, 780)]
    for index in range(iterations):
        window = MainWindow(start_device_scan=False)
        width, height = sizes[index % len(sizes)]
        window.resize(width + index * 3, height + index * 2)
        window.show()
        app.processEvents()
        QTest.qWait(40)
        window.update_responsive_layout()

        window.preview_mode = "live"
        frame_h = 48 + index * 4
        frame_w = 64 + index * 5
        frame = np.arange(frame_h * frame_w, dtype=np.uint16).reshape(frame_h, frame_w)
        window.live_first_frame_pending = True
        window.on_live_frame(frame, {"live_fps": 8.0 + index / 3, "completed_at": f"iter-{index + 1:02d}"})
        window.preview_zoom_slider.setValue(100 + (index % 5) * 100)
        window.preview_grid_check.setChecked(index % 2 == 0)
        window.preview_cross_check.setChecked(index % 3 == 0)
        window.resize_preview_by_drag(rng.randint(20, 90), rng.randint(15, 70))
        window.set_preview_center_from_label(window.preview_label.width() * 0.55, window.preview_label.height() * 0.45)
        window.preview_tabs.setCurrentIndex(index % window.preview_tabs.count())

        points = points_from_records(
            [
                {"label": "origin", "x_mm": 0, "y_mm": 0},
                {"label": f"p{index}", "x_mm": index % 5, "y_mm": (index * 2) % 5},
            ]
        )
        config = window.build_config(points)
        config.setdefault("dataset", {})["output_root"] = str(output_dir / "datasets")
        issues = window.collect_preflight_issues(points, config, validate_scan_points(points))
        error_count = sum(1 for issue in issues if issue.status == "오류")

        app.processEvents()
        screenshot_path = screenshot_dir / f"gui_iter_{index + 1:02d}.png"
        window.grab().save(str(screenshot_path))
        colors = sampled_color_count(screenshot_path)
        preview_size = window.preview_label.size()
        preview_gap_px = window.preview_command_bar.geometry().top() - window.preview_frame.geometry().bottom()
        passed = (
            screenshot_path.exists()
            and screenshot_path.stat().st_size > 10_000
            and colors > 12
            and preview_size.width() >= 240
            and preview_size.height() >= 180
            and preview_gap_px >= 0
        )
        results.append(
            {
                "iteration": index + 1,
                "window_size": [window.width(), window.height()],
                "preview_size": [preview_size.width(), preview_size.height()],
                "crop_rect": list(window.preview_crop_rect or ()),
                "zoom": window.preview_zoom_slider.value(),
                "grid": window.preview_grid_check.isChecked(),
                "cross": window.preview_cross_check.isChecked(),
                "tab": window.preview_tabs.tabText(window.preview_tabs.currentIndex()),
                "preflight_error_count": error_count,
                "preview_to_controls_gap_px": preview_gap_px,
                "screenshot": str(screenshot_path),
                "sampled_color_count": colors,
                "passed": passed,
            }
        )
        window.close()
        app.processEvents()
    return results


def make_contact_sheet(screenshots: list[Path], output_path: Path) -> None:
    thumbs: list[Image.Image] = []
    for path in screenshots:
        image = Image.open(path).convert("RGB")
        image.thumbnail((320, 220))
        tile = Image.new("RGB", (340, 250), "white")
        tile.paste(image, ((340 - image.width) // 2, 10))
        draw = ImageDraw.Draw(tile)
        draw.text((12, 228), path.name, fill=(20, 24, 30))
        thumbs.append(tile)
    if not thumbs:
        return
    columns = 2
    rows = math.ceil(len(thumbs) / columns)
    sheet = Image.new("RGB", (columns * 340, rows * 250), (245, 247, 250))
    for index, thumb in enumerate(thumbs):
        sheet.paste(thumb, ((index % columns) * 340, (index // columns) * 250))
    sheet.save(output_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run release verification experiments.")
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()

    iterations = max(10, int(args.iterations))
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path("output") / "release_verification" / stamp
    output_dir.mkdir(parents=True, exist_ok=True)

    zaber_results = run_zaber_experiments(iterations)
    gui_results = run_gui_experiments(iterations, output_dir)
    screenshots = [Path(item["screenshot"]) for item in gui_results]
    contact_sheet = output_dir / "gui_contact_sheet.png"
    make_contact_sheet(screenshots, contact_sheet)

    report = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "iterations": iterations,
        "notes": [
            "No OS-level virtual COM driver is installed in this environment.",
            "Zaber experiments use an application-level COM_VIRTUAL_* simulator through the production ZaberXYStage API.",
        ],
        "zaber": zaber_results,
        "gui": gui_results,
        "contact_sheet": str(contact_sheet),
    }
    report_path = output_dir / "release_verification_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    zaber_passed = all(item["passed"] for item in zaber_results)
    gui_passed = all(item["passed"] for item in gui_results)
    print(f"report={report_path}")
    print(f"contact_sheet={contact_sheet}")
    print(f"zaber_passed={zaber_passed} gui_passed={gui_passed}")
    return 0 if zaber_passed and gui_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
