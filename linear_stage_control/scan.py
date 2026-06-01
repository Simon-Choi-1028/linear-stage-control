from __future__ import annotations

import csv
import json
import math
import re
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree

import yaml


@dataclass(frozen=True)
class ScanPoint:
    index: int
    x_mm: float
    y_mm: float
    label: str = ""
    move_velocity_mm_s: float | None = None
    capture_count: int | None = None


DEFAULT_CAPTURE_COUNT = 1
LINEAR_PATH_MAX_POINTS = 100_000


def points_from_config(
    config: dict[str, Any],
    base_dir: str | Path | None = None,
) -> list[ScanPoint]:
    scan = config.get("scan", {})
    positions_file = scan.get("positions_file")
    if positions_file:
        return points_from_file(_resolve_path(positions_file, base_dir))

    positions = scan.get("positions")
    if positions:
        return points_from_records(positions)

    linear_path = scan.get("linear_path")
    if linear_path:
        return points_from_linear_path(linear_path)

    return list(
        grid_points(
            x_start=float(scan.get("x_start_mm", 0)),
            x_stop=float(scan.get("x_stop_mm", 0)),
            x_step=float(scan.get("x_step_mm", 1)),
            y_start=float(scan.get("y_start_mm", 0)),
            y_stop=float(scan.get("y_stop_mm", 0)),
            y_step=float(scan.get("y_step_mm", 1)),
            serpentine=bool(scan.get("serpentine", True)),
        )
    )


def default_capture_count_from_config(config: dict[str, Any]) -> int:
    scan = config.get("scan", {})
    value = scan.get("default_capture_count", scan.get("capture_count", DEFAULT_CAPTURE_COUNT))
    return _int_value(value, "default_capture_count", minimum=1)


def effective_capture_count(point: ScanPoint, default_capture_count: int = DEFAULT_CAPTURE_COUNT) -> int:
    if point.capture_count is None:
        return default_capture_count
    return point.capture_count


def effective_move_velocity_mm_s(point: ScanPoint, default_velocity_mm_s: float | None) -> float | None:
    if point.move_velocity_mm_s is None:
        return default_velocity_mm_s
    return point.move_velocity_mm_s


def total_capture_count(points: Iterable[ScanPoint], default_capture_count: int = DEFAULT_CAPTURE_COUNT) -> int:
    return sum(effective_capture_count(point, default_capture_count) for point in points)


def points_from_linear_path(path_config: Mapping[str, Any]) -> list[ScanPoint]:
    spacing_value = _field_value(path_config, LINEAR_SPACING_ALIASES)
    if spacing_value is not None and str(spacing_value).strip() != "":
        return list(
            linear_path_points_by_spacing(
                x_start=_float_value(_field_value(path_config, LINEAR_START_X_ALIASES), "start_x"),
                y_start=_float_value(_field_value(path_config, LINEAR_START_Y_ALIASES), "start_y"),
                x_stop=_float_value(_field_value(path_config, LINEAR_END_X_ALIASES), "end_x"),
                y_stop=_float_value(_field_value(path_config, LINEAR_END_Y_ALIASES), "end_y"),
                spacing_mm=_float_value(spacing_value, "spacing_mm"),
                label_prefix=str(_field_value(path_config, LINEAR_LABEL_ALIASES, default="line") or "line"),
                move_velocity_mm_s=_optional_positive_float(
                    _field_value(path_config, VELOCITY_ALIASES),
                    "move_velocity_mm_s",
                ),
                capture_count=_optional_int_value(
                    _field_value(path_config, CAPTURE_COUNT_ALIASES),
                    "capture_count",
                    minimum=1,
                ),
            )
        )
    return list(
        linear_path_points(
            x_start=_float_value(_field_value(path_config, LINEAR_START_X_ALIASES), "start_x"),
            y_start=_float_value(_field_value(path_config, LINEAR_START_Y_ALIASES), "start_y"),
            x_stop=_float_value(_field_value(path_config, LINEAR_END_X_ALIASES), "end_x"),
            y_stop=_float_value(_field_value(path_config, LINEAR_END_Y_ALIASES), "end_y"),
            count=_int_value(_field_value(path_config, LINEAR_COUNT_ALIASES, default=2), "count", minimum=2),
            label_prefix=str(_field_value(path_config, LINEAR_LABEL_ALIASES, default="line") or "line"),
            move_velocity_mm_s=_optional_positive_float(
                _field_value(path_config, VELOCITY_ALIASES),
                "move_velocity_mm_s",
            ),
            capture_count=_optional_int_value(
                _field_value(path_config, CAPTURE_COUNT_ALIASES),
                "capture_count",
                minimum=1,
            ),
        )
    )


def linear_path_points(
    x_start: float,
    y_start: float,
    x_stop: float,
    y_stop: float,
    count: int,
    label_prefix: str = "line",
    start_index: int = 0,
    move_velocity_mm_s: float | None = None,
    capture_count: int | None = None,
) -> Iterable[ScanPoint]:
    if count < 2:
        raise ValueError("선형 경로는 최소 2개 이상의 위치가 필요합니다.")
    if count > LINEAR_PATH_MAX_POINTS:
        raise ValueError(f"선형 경로 위치 수는 {LINEAR_PATH_MAX_POINTS}개를 넘을 수 없습니다.")
    for offset in range(count):
        ratio = offset / (count - 1)
        yield ScanPoint(
            index=start_index + offset,
            x_mm=round(x_start + (x_stop - x_start) * ratio, 9),
            y_mm=round(y_start + (y_stop - y_start) * ratio, 9),
            label=f"{label_prefix}_{offset + 1:04d}",
            move_velocity_mm_s=move_velocity_mm_s,
            capture_count=capture_count,
        )


def linear_path_points_by_spacing(
    x_start: float,
    y_start: float,
    x_stop: float,
    y_stop: float,
    spacing_mm: float,
    label_prefix: str = "line",
    start_index: int = 0,
    move_velocity_mm_s: float | None = None,
    capture_count: int | None = None,
) -> Iterable[ScanPoint]:
    if spacing_mm <= 0:
        raise ValueError("선형 경로 간격은 0보다 커야 합니다.")
    dx = x_stop - x_start
    dy = y_stop - y_start
    length = math.hypot(dx, dy)
    if length <= 0:
        raise ValueError("선형 경로 시작점과 끝점이 같습니다.")

    base_count = int(math.floor(length / spacing_mm)) + 1
    endpoint_count = 0 if math.isclose((base_count - 1) * spacing_mm, length, rel_tol=0.0, abs_tol=1e-9) else 1
    if base_count + endpoint_count > LINEAR_PATH_MAX_POINTS:
        raise ValueError(f"선형 경로 위치 수는 {LINEAR_PATH_MAX_POINTS}개를 넘을 수 없습니다.")

    distances = [round(index * spacing_mm, 9) for index in range(base_count)]
    if not math.isclose(distances[-1], length, rel_tol=0.0, abs_tol=1e-9):
        distances.append(length)

    for offset, distance in enumerate(distances):
        ratio = distance / length
        yield ScanPoint(
            index=start_index + offset,
            x_mm=round(x_start + dx * ratio, 9),
            y_mm=round(y_start + dy * ratio, 9),
            label=f"{label_prefix}_{offset + 1:04d}",
            move_velocity_mm_s=move_velocity_mm_s,
            capture_count=capture_count,
        )


def points_from_file(path: str | Path) -> list[ScanPoint]:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix in {".csv", ".tsv", ".txt"}:
        return points_from_records(_records_from_text_file(source), source=source)
    if suffix in {".json"}:
        with source.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return points_from_data(data, source=source)
    if suffix in {".jsonl", ".ndjson"}:
        return points_from_records(_records_from_jsonl(source), source=source)
    if suffix in {".yaml", ".yml"}:
        with source.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or []
        return points_from_data(data, source=source)
    if suffix == ".xlsx":
        return points_from_records(_records_from_xlsx(source), source=source)
    raise ValueError(f"Unsupported positions file format: {source}. " "Use CSV, TSV, TXT, JSON, JSONL, YAML, or XLSX.")


def points_from_data(data: Any, source: str | Path | None = None) -> list[ScanPoint]:
    if isinstance(data, Mapping):
        if "positions" in data:
            return points_from_records(data["positions"], source=source)
        if "points" in data:
            return points_from_records(data["points"], source=source)
        scan = data.get("scan")
        if isinstance(scan, Mapping):
            if "positions" in scan:
                return points_from_records(scan["positions"], source=source)
            if "points" in scan:
                return points_from_records(scan["points"], source=source)
        if _mapping_has_xy(data):
            return points_from_records([data], source=source)
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        return points_from_records(data, source=source)
    location = f" in {source}" if source else ""
    raise ValueError(f"Could not find a positions list{location}.")


def points_from_records(
    records: Iterable[Any],
    source: str | Path | None = None,
) -> list[ScanPoint]:
    points: list[ScanPoint] = []
    for index, record in enumerate(records):
        if _is_empty_record(record):
            continue
        try:
            point = _point_from_record(record, len(points))
        except Exception as exc:
            location = f"{source}: " if source else ""
            raise ValueError(f"{location}{index + 1}번째 위치를 해석할 수 없습니다. {exc}") from exc
        points.append(point)
    if not points:
        location = f": {source}" if source else ""
        raise ValueError(f"No scan positions were found{location}.")
    return points


def grid_points(
    x_start: float,
    x_stop: float,
    x_step: float,
    y_start: float,
    y_stop: float,
    y_step: float,
    serpentine: bool = True,
) -> Iterable[ScanPoint]:
    xs = list(_inclusive_range(x_start, x_stop, x_step))
    ys = list(_inclusive_range(y_start, y_stop, y_step))
    index = 0
    for row, y in enumerate(ys):
        row_xs = list(reversed(xs)) if serpentine and row % 2 else xs
        for x in row_xs:
            yield ScanPoint(index=index, x_mm=x, y_mm=y, label=f"grid_{index:04d}")
            index += 1


def _inclusive_range(start: float, stop: float, step: float) -> Iterable[float]:
    if step == 0:
        raise ValueError("Scan step must not be zero.")

    direction = 1 if stop >= start else -1
    step = abs(step) * direction
    epsilon = abs(step) * 1e-9 + 1e-12
    value = start

    def keep_going(current: float) -> bool:
        if direction > 0:
            return current <= stop + epsilon
        return current >= stop - epsilon

    while keep_going(value):
        yield round(value, 9)
        value += step


def _resolve_path(path: str | Path, base_dir: str | Path | None) -> Path:
    source = Path(path)
    if source.is_absolute() or base_dir is None:
        return source
    return Path(base_dir) / source


X_ALIASES = {
    "x",
    "xmm",
    "targetx",
    "targetxmm",
    "stagex",
    "stagexmm",
    "posx",
    "positionx",
}
Y_ALIASES = {
    "y",
    "ymm",
    "targety",
    "targetymm",
    "stagey",
    "stageymm",
    "posy",
    "positiony",
}
LABEL_ALIASES = {
    "label",
    "name",
    "id",
    "sample",
    "samplename",
    "point",
    "pointname",
    "position",
    "positionname",
    "라벨",
    "이름",
    "샘플",
    "시료",
}
VELOCITY_ALIASES = {
    "velocity",
    "velocitymms",
    "speed",
    "speedmms",
    "movevelocity",
    "movevelocitymms",
    "movevelocitymmsec",
    "movevelocitymms",
    "stagevelocity",
    "stagevelocitymms",
    "이동속도",
    "속도",
}
CAPTURE_COUNT_ALIASES = {
    "capture",
    "captures",
    "capturecount",
    "shot",
    "shots",
    "shotcount",
    "frame",
    "frames",
    "framecount",
    "image",
    "images",
    "imagecount",
    "count",
    "n",
    "캡쳐수",
    "캡처수",
    "촬영수",
    "사진수",
}
LINEAR_START_X_ALIASES = {"startx", "startxmm", "xstart", "xstartmm", "fromx", "fromxmm", "시작x"}
LINEAR_START_Y_ALIASES = {"starty", "startymm", "ystart", "ystartmm", "fromy", "fromymm", "시작y"}
LINEAR_END_X_ALIASES = {"endx", "endxmm", "stopx", "stopxmm", "xend", "xstop", "tox", "toxmm", "끝x", "종료x"}
LINEAR_END_Y_ALIASES = {"endy", "endymm", "stopy", "stopymm", "yend", "ystop", "toy", "toymm", "끝y", "종료y"}
LINEAR_COUNT_ALIASES = {"count", "points", "pointcount", "positioncount", "steps", "개수", "위치수"}
LINEAR_SPACING_ALIASES = {
    "spacing",
    "spacingmm",
    "resolution",
    "resolutionmm",
    "interval",
    "intervalmm",
    "mmpercapture",
    "mmperimage",
    "mmperpoint",
    "간격",
    "해상도",
    "분해능",
}
LINEAR_LABEL_ALIASES = {"labelprefix", "prefix", "nameprefix", "label", "라벨", "접두어"}


def _records_from_text_file(path: Path) -> list[Mapping[str, Any] | list[str]]:
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise ValueError(f"Positions file is empty: {path}")
    delimiter = "\t" if path.suffix.lower() == ".tsv" else _sniff_delimiter(text)
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        raise ValueError(f"Positions file has no data rows: {path}")

    if delimiter:
        rows = list(csv.reader(lines, delimiter=delimiter))
    else:
        rows = [re.split(r"\s+", line.strip()) for line in lines]

    if rows and _headers_have_xy(rows[0]):
        headers = rows[0]
        return [dict(zip(headers, row)) for row in rows[1:] if any(str(value).strip() for value in row)]
    records: list[Mapping[str, Any] | list[str]] = [list(row) for row in rows]
    return records


def _records_from_jsonl(path: Path) -> list[Any]:
    records: list[Any] = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}: {line_number}번째 JSONL 줄이 올바르지 않습니다.") from exc
    return records


def _records_from_xlsx(path: Path) -> list[Mapping[str, Any] | list[str]]:
    rows = _xlsx_rows(path)
    if not rows:
        raise ValueError(f"XLSX file has no rows: {path}")
    if _headers_have_xy(rows[0]):
        headers = [str(value) for value in rows[0]]
        return [dict(zip(headers, row)) for row in rows[1:] if any(str(value).strip() for value in row)]
    records: list[Mapping[str, Any] | list[str]] = [list(row) for row in rows]
    return records


def _point_from_record(record: Any, index: int) -> ScanPoint:
    if isinstance(record, Mapping):
        x_value = _field_value(record, X_ALIASES)
        y_value = _field_value(record, Y_ALIASES)
        if x_value is None or y_value is None:
            raise ValueError("X/Y 컬럼을 찾을 수 없습니다. 예: x_mm,y_mm 또는 X mm,Y mm")
        label_value = _field_value(record, LABEL_ALIASES, default="")
        velocity_value = _field_value(record, VELOCITY_ALIASES)
        capture_count_value = _field_value(record, CAPTURE_COUNT_ALIASES)
        return ScanPoint(
            index=index,
            x_mm=_float_value(x_value, "X"),
            y_mm=_float_value(y_value, "Y"),
            label=_normalise_point_label(label_value, index),
            move_velocity_mm_s=_optional_positive_float(velocity_value, "move_velocity_mm_s"),
            capture_count=_optional_int_value(capture_count_value, "capture_count", minimum=1),
        )

    if isinstance(record, Sequence) and not isinstance(record, (str, bytes, bytearray)):
        values = [value for value in record if str(value).strip()]
        if len(values) < 2:
            raise ValueError("행에는 최소 X/Y 값이 필요합니다.")
        if _looks_float(values[0]) and _looks_float(values[1]):
            label, velocity, capture_count = _sequence_optional_fields(values, start=2)
            return ScanPoint(
                index=index,
                x_mm=_float_value(values[0], "X"),
                y_mm=_float_value(values[1], "Y"),
                label=_normalise_point_label(label, index),
                move_velocity_mm_s=velocity,
                capture_count=capture_count,
            )
        if len(values) >= 3 and _looks_float(values[1]) and _looks_float(values[2]):
            label = _normalise_point_label(values[0], index)
            velocity = _optional_positive_float(values[3] if len(values) >= 4 else None, "move_velocity_mm_s")
            capture_count = _optional_int_value(values[4] if len(values) >= 5 else None, "capture_count", minimum=1)
            return ScanPoint(
                index=index,
                label=label,
                x_mm=_float_value(values[1], "X"),
                y_mm=_float_value(values[2], "Y"),
                move_velocity_mm_s=velocity,
                capture_count=capture_count,
            )
    raise ValueError("지원하지 않는 위치 행 형식입니다.")


def _sequence_optional_fields(values: Sequence[Any], start: int) -> tuple[str, float | None, int | None]:
    label = ""
    velocity_index = start
    if len(values) > start and not _looks_float(values[start]):
        label = str(values[start] or "")
        velocity_index = start + 1
    velocity = _optional_positive_float(
        values[velocity_index] if len(values) > velocity_index else None,
        "move_velocity_mm_s",
    )
    capture_count = _optional_int_value(
        values[velocity_index + 1] if len(values) > velocity_index + 1 else None,
        "capture_count",
        minimum=1,
    )
    return label, velocity, capture_count


def _normalise_point_label(value: Any, index: int) -> str:
    text = str(value or "").strip()
    return text or f"point_{index:04d}"


def _field_value(
    record: Mapping[str, Any],
    aliases: set[str],
    default: Any | None = None,
) -> Any:
    for key, value in record.items():
        if _normalise_key(key) in aliases:
            return value
    return default


def _mapping_has_xy(record: Mapping[str, Any]) -> bool:
    return _field_value(record, X_ALIASES) is not None and _field_value(record, Y_ALIASES) is not None


def _headers_have_xy(headers: Sequence[Any]) -> bool:
    normalised = {_normalise_key(header) for header in headers}
    return bool(normalised & X_ALIASES) and bool(normalised & Y_ALIASES)


def _normalise_key(value: Any) -> str:
    text = str(value).strip().lower()
    if text in LABEL_ALIASES:
        return text
    text = text.replace("μ", "u")
    return re.sub(r"[^0-9a-z가-힣]+", "", text)


def _float_value(value: Any, label: str) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 값이 숫자가 아닙니다: {value}") from exc


def _optional_positive_float(value: Any, label: str) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    number = _float_value(value, label)
    if number <= 0:
        raise ValueError(f"{label} 값은 비워 두거나 0보다 커야 합니다: {value}")
    return number


def _int_value(value: Any, label: str, minimum: int | None = None) -> int:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 값이 정수가 아닙니다: {value}") from exc
    if not number.is_integer():
        raise ValueError(f"{label} 값이 정수가 아닙니다: {value}")
    integer = int(number)
    if minimum is not None and integer < minimum:
        raise ValueError(f"{label} 값은 {minimum} 이상이어야 합니다: {value}")
    return integer


def _optional_int_value(value: Any, label: str, minimum: int | None = None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return _int_value(value, label, minimum=minimum)


def _looks_float(value: Any) -> bool:
    try:
        float(str(value).strip())
        return True
    except (TypeError, ValueError):
        return False


def _is_empty_record(record: Any) -> bool:
    if record is None:
        return True
    if isinstance(record, Mapping):
        return not any(str(value).strip() for value in record.values() if value is not None)
    if isinstance(record, Sequence) and not isinstance(record, (str, bytes, bytearray)):
        return not any(str(value).strip() for value in record if value is not None)
    return False


def _sniff_delimiter(text: str) -> str | None:
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;")
        return dialect.delimiter
    except csv.Error:
        return None


def _xlsx_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _xlsx_shared_strings(archive)
        sheet_path = _xlsx_first_sheet_path(archive)
        root = ElementTree.fromstring(archive.read(sheet_path))

    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rows: list[list[str]] = []
    for row_node in root.iter(f"{namespace}row"):
        row_values: list[str] = []
        for cell in row_node.findall(f"{namespace}c"):
            column_index = _xlsx_column_index(cell.attrib.get("r", ""))
            while len(row_values) < column_index - 1:
                row_values.append("")
            row_values.append(_xlsx_cell_value(cell, shared_strings, namespace))
        rows.append(row_values)
    return [row for row in rows if any(str(value).strip() for value in row)]


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for item in root.findall(f"{namespace}si"):
        strings.append("".join(text.text or "" for text in item.iter(f"{namespace}t")))
    return strings


def _xlsx_first_sheet_path(archive: zipfile.ZipFile) -> str:
    if "xl/worksheets/sheet1.xml" in archive.namelist():
        return "xl/worksheets/sheet1.xml"
    for name in archive.namelist():
        if name.startswith("xl/worksheets/") and name.endswith(".xml"):
            return name
    raise ValueError("XLSX worksheet XML을 찾을 수 없습니다.")


def _xlsx_cell_value(
    cell: ElementTree.Element,
    shared_strings: list[str],
    namespace: str,
) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.iter(f"{namespace}t"))
    value_node = cell.find(f"{namespace}v")
    raw_value = value_node.text if value_node is not None and value_node.text is not None else ""
    if cell_type == "s" and raw_value:
        index = int(raw_value)
        return shared_strings[index] if 0 <= index < len(shared_strings) else ""
    return raw_value


def _xlsx_column_index(cell_ref: str) -> int:
    letters = "".join(char for char in cell_ref if char.isalpha()).upper()
    if not letters:
        return 1
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index
