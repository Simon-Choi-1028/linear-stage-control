from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGroupBox, QLabel, QPushButton, QScrollArea, QWidget

if not getattr(sys, "frozen", False):
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from linear_stage_control.experiments.alignment_window import AlignmentWindow  # noqa: E402
from linear_stage_control.experiments.fwhm_window import FwhmWindow  # noqa: E402
from linear_stage_control.experiments.launcher import ExperimentLauncherWindow  # noqa: E402
from linear_stage_control.experiments.vp_window import VPWindow  # noqa: E402

SIZES = [(1366, 768), (1600, 900), (1920, 1080), (1100, 760), (900, 900)]
REQUIRED_GROUPS = ("Source", "Manual Stage", "ROI", "Processing", "Save")


def default_output_dir() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Downloads" / "LinearStageControl_Experiment_QA"


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    failures: list[str] = []
    warnings: list[str] = []
    screenshots: list[Path] = []

    launcher = ExperimentLauncherWindow()
    screenshots.extend(
        _capture_window(app, launcher, output_dir, "launcher", failures, warnings, check_experiment=False)
    )
    launcher.close()

    for key, window_class in (("fwhm", FwhmWindow), ("alignment", AlignmentWindow), ("vp", VPWindow)):
        window = window_class()
        screenshots.extend(_capture_window(app, window, output_dir, key, failures, warnings, check_experiment=True))
        window.close()
        app.processEvents()

    contact_sheet = output_dir / "contact_sheet.png"
    _make_contact_sheet(screenshots, contact_sheet)
    report_path = output_dir / "qa_report.txt"
    report_path.write_text(_report_text(failures, warnings, screenshots, contact_sheet), encoding="utf-8")

    print(f"Saved experiment QA screenshots: {output_dir}")
    if warnings:
        print(f"Warnings: {len(warnings)}")
    if failures:
        print(f"Failures: {len(failures)}")
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    return 0


def _capture_window(
    app: QApplication,
    window: QWidget,
    output_dir: Path,
    key: str,
    failures: list[str],
    warnings: list[str],
    *,
    check_experiment: bool,
) -> list[Path]:
    paths: list[Path] = []
    for width, height in SIZES:
        window.resize(width, height)
        window.show()
        window.raise_()
        for _ in range(15):
            app.processEvents()
            QTest.qWait(60)
        if check_experiment:
            _check_experiment_window(window, key, f"{width}x{height}", failures, warnings)
        path = output_dir / f"{key}_{width}x{height}.png"
        pixmap = window.grab()
        pixmap.save(str(path))
        paths.append(path)
    return paths


def _check_experiment_window(
    window: QWidget, key: str, size_label: str, failures: list[str], warnings: list[str]
) -> None:
    measurement = getattr(window, "latest_measurement", None)
    overlay = measurement.overlay_bgr if measurement is not None else None
    if overlay is None:
        failures.append(f"{key} {size_label}: preview overlay was not produced")
    else:
        arr = np.asarray(overlay)
        if arr.size == 0 or float(np.var(arr)) < 5.0:
            failures.append(f"{key} {size_label}: preview appears blank or near-solid")
        colored = _colored_overlay_pixels(arr)
        if colored < 20:
            failures.append(f"{key} {size_label}: overlay colors were not detected")

    for name in REQUIRED_GROUPS:
        group = window.findChild(QGroupBox, name)
        if group is None:
            failures.append(f"{key} {size_label}: missing group {name}")
            continue
        if not group.isVisible() or group.width() <= 20 or group.height() <= 20:
            failures.append(f"{key} {size_label}: group {name} is not visibly laid out")

    result_table = window.findChild(QWidget, "Result")
    if result_table is None or not result_table.isVisible():
        failures.append(f"{key} {size_label}: result table is not visible")

    control_scroll = window.findChild(QScrollArea, "controlScroll")
    if control_scroll is not None and control_scroll.horizontalScrollBar().maximum() != 0:
        failures.append(f"{key} {size_label}: control panel needs a horizontal scrollbar")

    control_panel = getattr(window, "control_panel", None)
    if isinstance(control_panel, QWidget):
        _check_group_overlap(control_panel, key, size_label, failures)
    _check_text_clipping(window, key, size_label, warnings)


def _colored_overlay_pixels(arr: np.ndarray) -> int:
    if arr.ndim != 3 or arr.shape[2] < 3:
        return 0
    b = arr[:, :, 0].astype(np.int16)
    g = arr[:, :, 1].astype(np.int16)
    r = arr[:, :, 2].astype(np.int16)
    saturated_color = (np.maximum.reduce([b, g, r]) > 160) & (
        (np.maximum.reduce([b, g, r]) - np.minimum.reduce([b, g, r])) > 50
    )
    return int(np.count_nonzero(saturated_color))


def _check_group_overlap(control_panel: QWidget, key: str, size_label: str, failures: list[str]) -> None:
    groups = [child for child in control_panel.findChildren(QGroupBox) if child.parentWidget() is control_panel]
    for index, first in enumerate(groups):
        first_rect = first.geometry()
        for second in groups[index + 1 :]:
            overlap = first_rect.intersected(second.geometry())
            if overlap.isValid() and overlap.width() * overlap.height() > 16:
                failures.append(f"{key} {size_label}: groups overlap ({first.title()} / {second.title()})")


def _check_text_clipping(window: QWidget, key: str, size_label: str, warnings: list[str]) -> None:
    for widget in list(window.findChildren(QLabel)) + list(window.findChildren(QPushButton)):
        if not widget.isVisible():
            continue
        text = widget.text().replace("\n", " ")
        if not text:
            continue
        if isinstance(widget, QLabel) and widget.wordWrap():
            continue
        text_width = widget.fontMetrics().horizontalAdvance(text.replace("&", ""))
        available_width = widget.contentsRect().width()
        if isinstance(widget, QPushButton):
            icon = widget.icon()
            if not icon.isNull():
                available_width -= widget.iconSize().width() + 8
            available_width -= 16
        if text_width > max(1, available_width):
            warnings.append(f"{key} {size_label}: possible clipped text: {text[:60]}")


def _make_contact_sheet(paths: Iterable[Path], output_path: Path) -> None:
    images = []
    for path in paths:
        with Image.open(path) as image:
            thumb = image.convert("RGB")
            thumb.thumbnail((360, 220))
            images.append((path.name, thumb.copy()))
    if not images:
        return
    cell_w, cell_h = 400, 260
    columns = 3
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new("RGB", (cell_w * columns, cell_h * rows), "white")
    draw = ImageDraw.Draw(sheet)
    for index, (name, image) in enumerate(images):
        x = (index % columns) * cell_w
        y = (index // columns) * cell_h
        sheet.paste(image, (x + 20, y + 28))
        draw.text((x + 20, y + 8), name, fill=(20, 20, 20))
    sheet.save(output_path)


def _report_text(failures: list[str], warnings: list[str], screenshots: list[Path], contact_sheet: Path) -> str:
    lines = [
        f"failures={len(failures)}",
        f"warnings={len(warnings)}",
        f"contact_sheet={contact_sheet}",
        "",
        "[failures]",
        *failures,
        "",
        "[warnings]",
        *warnings,
        "",
        "[screenshots]",
        *(str(path) for path in screenshots),
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
