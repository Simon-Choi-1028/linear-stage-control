from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QStyle, QTableWidget, QTableWidgetItem


def set_placeholder_color(line_edit: QLineEdit) -> None:
    palette = line_edit.palette()
    palette.setColor(QPalette.PlaceholderText, QColor("#9aa5af"))
    line_edit.setPalette(palette)


def preflight_status_color(status: str) -> QColor:
    if status == "오류":
        return QColor("#ffe1df")
    if status == "경고":
        return QColor("#fff4cc")
    return QColor("#dff2e8")


def set_table_values(table: QTableWidget, values: list[str]) -> None:
    for column, value in enumerate(values[: table.columnCount()]):
        item = QTableWidgetItem(value)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        item.setTextAlignment(Qt.AlignCenter)
        table.setItem(0, column, item)


def apply_button_icon(
    button: QPushButton,
    standard_pixmap: QStyle.StandardPixmap,
    tooltip: str,
    icon_size: int = 18,
) -> None:
    button.setIcon(QApplication.style().standardIcon(standard_pixmap))
    button.setIconSize(QSize(icon_size, icon_size))
    button.setToolTip(tooltip)


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def apply_default_font() -> None:
    app = QApplication.instance()
    if app is None:
        return
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
