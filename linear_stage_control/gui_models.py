from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QTableView

from .position_validation import PositionInputRow, PositionValidationResult
from .scan import ScanPoint
from .text_formatting import position_cell_tooltip

CAPTURE_RESULT_HEADERS = (
    "#",
    "Capture",
    "Label",
    "Status",
    "Target X",
    "Target Y",
    "Actual X",
    "Actual Y",
    "Measured",
    "Pred Min",
    "Pred Max",
    "Threshold",
    "Image",
)

POSITION_HEADERS = ("#", "Label", "X mm", "Y mm", "Velocity\nmm/s", "Captures")
CAPTURE_DISPLAY_LIMIT = 1000
POSITION_HIGHLIGHT_LIMIT = 2000


@dataclass(frozen=True)
class CaptureResultRow:
    values: tuple[str, ...]
    image_path: str
    record: dict[str, Any]


class CaptureResultsModel(QAbstractTableModel):
    def __init__(self, max_rows: int = CAPTURE_DISPLAY_LIMIT) -> None:
        super().__init__()
        self.max_rows = max(1, int(max_rows))
        self._rows: list[CaptureResultRow] = []
        self.total_seen = 0

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(CAPTURE_RESULT_HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        if role == Qt.DisplayRole:
            return row.values[index.column()]
        if role == Qt.TextAlignmentRole and index.column() != 12:
            return Qt.AlignCenter
        if role == Qt.UserRole:
            return row.image_path
        if role == Qt.UserRole + 1:
            return row.record
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole) -> Any:
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal and 0 <= section < len(CAPTURE_RESULT_HEADERS):
            return CAPTURE_RESULT_HEADERS[section]
        return None

    def clear(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self.total_seen = 0
        self.endResetModel()

    def append_capture(self, values: Sequence[str], image_path: str, record: dict[str, Any]) -> None:
        compact_values = tuple(str(value) for value in values)
        if len(compact_values) != len(CAPTURE_RESULT_HEADERS):
            raise ValueError("Capture result row has the wrong column count.")
        self.append_captures([(compact_values, image_path, record)])

    def append_captures(
        self,
        rows: Sequence[tuple[Sequence[str], str, dict[str, Any]]],
        *,
        seen_count: int | None = None,
    ) -> None:
        if not rows:
            if seen_count:
                self.total_seen += max(0, int(seen_count))
            return
        new_rows: list[CaptureResultRow] = []
        for values, image_path, record in rows:
            compact_values = tuple(str(value) for value in values)
            if len(compact_values) != len(CAPTURE_RESULT_HEADERS):
                raise ValueError("Capture result row has the wrong column count.")
            new_rows.append(CaptureResultRow(compact_values, image_path, dict(record)))
        total_increment = len(new_rows) if seen_count is None else int(seen_count)
        if total_increment < len(new_rows):
            raise ValueError("seen_count cannot be smaller than the number of retained capture rows.")
        self.total_seen += total_increment
        if len(new_rows) >= self.max_rows:
            self.beginResetModel()
            self._rows = new_rows[-self.max_rows :]
            self.endResetModel()
            return
        if len(self._rows) + len(new_rows) > self.max_rows:
            remove_count = len(self._rows) + len(new_rows) - self.max_rows
            self.beginRemoveRows(QModelIndex(), 0, remove_count - 1)
            del self._rows[:remove_count]
            self.endRemoveRows()
        insert_at = len(self._rows)
        self.beginInsertRows(QModelIndex(), insert_at, insert_at + len(new_rows) - 1)
        self._rows.extend(new_rows)
        self.endInsertRows()

    def record_at(self, row: int) -> dict[str, Any] | None:
        if 0 <= row < len(self._rows):
            return self._rows[row].record
        return None

    def records(self) -> list[dict[str, Any]]:
        return [row.record for row in self._rows]

    def image_path_at(self, row: int) -> str:
        if 0 <= row < len(self._rows):
            return self._rows[row].image_path
        return ""


class CaptureResultsView(QTableView):
    def rowCount(self) -> int:
        model = self.model()
        return model.rowCount() if model is not None else 0


@dataclass
class PositionRow:
    label: str
    x_text: str
    y_text: str
    velocity_text: str = ""
    capture_count_text: str = ""


class PositionTableModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self._rows: list[PositionRow] = []
        self._cell_errors: dict[tuple[int, int], str] = {}
        self._cell_warnings: dict[tuple[int, int], str] = {}
        self._velocity_placeholder = ""
        self._capture_placeholder = "(1)"

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(POSITION_HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row_index = index.row()
        column = index.column()
        row = self._rows[row_index]
        raw_value = self._raw_value(row, row_index, column)
        if role in (Qt.DisplayRole, Qt.EditRole):
            if role == Qt.EditRole:
                return raw_value
            if column == 4 and raw_value == "":
                return self._velocity_placeholder
            if column == 5 and raw_value == "":
                return self._capture_placeholder
            return raw_value
        if role == Qt.TextAlignmentRole and column in (0, 2, 3, 4, 5):
            return Qt.AlignCenter
        if role == Qt.ForegroundRole and column in (4, 5) and raw_value == "":
            return QBrush(QColor("#8c96a0"))
        if role == Qt.BackgroundRole:
            if (row_index, column) in self._cell_errors:
                return QColor("#ffe1df")
            if (row_index, column) in self._cell_warnings:
                return QColor("#fff4cc")
            return QColor("#ffffff")
        if role == Qt.ToolTipRole:
            return self._cell_errors.get((row_index, column)) or self._cell_warnings.get(
                (row_index, column),
                position_cell_tooltip(column),
            )
        return None

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.EditRole) -> bool:
        if role != Qt.EditRole or not index.isValid():
            return False
        row = self._rows[index.row()]
        text = str(value).strip()
        column = index.column()
        if column == 1:
            row.label = text
        elif column == 2:
            row.x_text = text
        elif column == 3:
            row.y_text = text
        elif column == 4:
            row.velocity_text = text
        elif column == 5:
            row.capture_count_text = text
        else:
            return False
        self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
        return True

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.NoItemFlags
        flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        if index.column() != 0:
            flags |= Qt.ItemIsEditable
        return flags

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole) -> Any:
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal and 0 <= section < len(POSITION_HEADERS):
            return POSITION_HEADERS[section]
        return None

    def clear(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self._cell_errors.clear()
        self._cell_warnings.clear()
        self.endResetModel()

    def set_points(self, points: Sequence[ScanPoint]) -> None:
        self.beginResetModel()
        self._rows = [
            PositionRow(
                label=point.label,
                x_text=_number_text(point.x_mm),
                y_text=_number_text(point.y_mm),
                velocity_text="" if point.move_velocity_mm_s is None else _number_text(point.move_velocity_mm_s),
                capture_count_text="" if point.capture_count is None else str(point.capture_count),
            )
            for point in points
        ]
        self._cell_errors.clear()
        self._cell_warnings.clear()
        self.endResetModel()

    def add_point(self, point: ScanPoint | None = None) -> None:
        insert_at = len(self._rows)
        point = point or ScanPoint(insert_at, 0.0, 0.0, "")
        self.beginInsertRows(QModelIndex(), insert_at, insert_at)
        self._rows.append(
            PositionRow(
                label=point.label,
                x_text=_number_text(point.x_mm),
                y_text=_number_text(point.y_mm),
                velocity_text="" if point.move_velocity_mm_s is None else _number_text(point.move_velocity_mm_s),
                capture_count_text="" if point.capture_count is None else str(point.capture_count),
            )
        )
        self.endInsertRows()

    def remove_rows(self, rows: Sequence[int]) -> None:
        for row in sorted(set(rows), reverse=True):
            if 0 <= row < len(self._rows):
                self.beginRemoveRows(QModelIndex(), row, row)
                self._rows.pop(row)
                self.endRemoveRows()

    def set_validation(
        self, validation: PositionValidationResult, highlight_limit: int = POSITION_HIGHLIGHT_LIMIT
    ) -> None:
        self._cell_errors = {key: value for key, value in validation.cell_errors.items() if key[0] < highlight_limit}
        self._cell_warnings = {
            key: value for key, value in validation.cell_warnings.items() if key[0] < highlight_limit
        }
        if self._rows:
            bottom_right = self.index(min(len(self._rows), highlight_limit) - 1, len(POSITION_HEADERS) - 1)
            self.dataChanged.emit(self.index(0, 0), bottom_right, [Qt.BackgroundRole, Qt.ToolTipRole])

    def set_placeholders(self, velocity: str, captures: str) -> None:
        if self._velocity_placeholder == velocity and self._capture_placeholder == captures:
            return
        self._velocity_placeholder = velocity
        self._capture_placeholder = captures
        if self._rows:
            self.dataChanged.emit(self.index(0, 4), self.index(len(self._rows) - 1, 5), [Qt.DisplayRole])

    def input_rows(self) -> list[PositionInputRow]:
        return [
            PositionInputRow(
                index=index,
                label=row.label,
                x_text=row.x_text,
                y_text=row.y_text,
                velocity_text=row.velocity_text,
                capture_count_text=row.capture_count_text,
            )
            for index, row in enumerate(self._rows)
        ]

    def text(self, row: int, column: int) -> str:
        if 0 <= row < len(self._rows):
            return str(self._raw_value(self._rows[row], row, column)).strip()
        return ""

    def _raw_value(self, row: PositionRow, row_index: int, column: int) -> str:
        if column == 0:
            return str(row_index)
        if column == 1:
            return row.label
        if column == 2:
            return row.x_text
        if column == 3:
            return row.y_text
        if column == 4:
            return row.velocity_text
        if column == 5:
            return row.capture_count_text
        return ""


class PositionTableView(QTableView):
    def rowCount(self) -> int:
        model = self.model()
        return model.rowCount() if model is not None else 0


def _number_text(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = f"{number:.9f}".rstrip("0").rstrip(".")
    return text or "0"
