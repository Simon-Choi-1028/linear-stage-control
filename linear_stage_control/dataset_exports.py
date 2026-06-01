from __future__ import annotations

import csv
import json
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Sequence
from xml.sax.saxutils import escape

import yaml

DEFAULT_METADATA_FORMATS = ("csv", "jsonl", "json", "tsv", "yaml", "xlsx")
DEFAULT_SUMMARY_FORMATS = ("json", "yaml", "md")
SUPPORTED_METADATA_FORMATS = {"csv", "jsonl", "json", "tsv", "yaml", "xlsx"}
SUPPORTED_SUMMARY_FORMATS = {"json", "yaml", "md"}


def normalise_formats(
    value: Any,
    default: Sequence[str],
    supported: set[str],
) -> tuple[str, ...]:
    if value is None:
        values = list(default)
    elif isinstance(value, str):
        values = [item.strip() for item in value.split(",")]
    elif isinstance(value, Iterable):
        values = [str(item).strip() for item in value]
    else:
        raise ValueError(f"Format list must be a string or list, got {type(value).__name__}.")

    formats: list[str] = []
    for item in values:
        if not item:
            continue
        normalised = item.lower().lstrip(".")
        if normalised == "yml":
            normalised = "yaml"
        if normalised not in supported:
            supported_text = ", ".join(sorted(supported))
            raise ValueError(f"Unsupported export format: {item}. Supported: {supported_text}.")
        if normalised not in formats:
            formats.append(normalised)
    return tuple(formats)


def json_ready(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(key): json_ready(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [json_ready(item) for item in value]
        return str(value)


def write_records_json(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def write_records_yaml(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(yaml.safe_dump(records, sort_keys=False, allow_unicode=True), encoding="utf-8")


def write_records_tsv(
    path: Path,
    fields: Sequence[str],
    records: list[dict[str, Any]],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(fields), delimiter="\t")
        writer.writeheader()
        for record in records:
            writer.writerow({field: _text_value(record.get(field, "")) for field in fields})


def write_records_xlsx(
    path: Path,
    fields: Sequence[str],
    records: list[dict[str, Any]],
    sheet_name: str = "captures",
) -> None:
    rows = [list(fields)]
    for record in records:
        rows.append([record.get(field, "") for field in fields])
    _write_xlsx(path, rows, sheet_name=sheet_name)


def build_run_summary(
    run_id: str,
    status: str,
    point_count: int,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    ok_records = [record for record in records if record.get("status") == "ok"]
    error_records = [record for record in records if record.get("status") == "error"]
    predicted_values = [
        float(record["predicted_max_error_um"])
        for record in ok_records
        if record.get("predicted_max_error_um") not in ("", None)
    ]
    measured_values = [
        float(record["measured_radial_error_um"])
        for record in ok_records
        if record.get("measured_radial_error_um") not in ("", None)
    ]
    threshold_failures = sum(1 for record in ok_records if record.get("within_error_threshold") is False)
    return {
        "run_id": run_id,
        "status": status,
        "point_count": point_count,
        "record_count": len(records),
        "capture_ok_count": len(ok_records),
        "capture_error_count": len(error_records),
        "threshold_failure_count": threshold_failures,
        "predicted_max_error_um_max": max(predicted_values) if predicted_values else None,
        "predicted_max_error_um_mean": _mean(predicted_values),
        "measured_radial_error_um_max": max(measured_values) if measured_values else None,
        "measured_radial_error_um_mean": _mean(measured_values),
        "error_messages": [
            {
                "index": record.get("index"),
                "label": record.get("label"),
                "message": record.get("error_message", ""),
            }
            for record in error_records
        ],
    }


def write_summary_json(path: Path, summary: dict[str, Any]) -> None:
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def write_summary_yaml(path: Path, summary: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(summary, sort_keys=False, allow_unicode=True), encoding="utf-8")


def write_summary_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        f"# Run Summary: {summary['run_id']}",
        "",
        f"- Status: {summary['status']}",
        f"- Planned points: {summary['point_count']}",
        f"- Records: {summary['record_count']}",
        f"- Successful captures: {summary['capture_ok_count']}",
        f"- Capture errors: {summary['capture_error_count']}",
        f"- Threshold failures: {summary['threshold_failure_count']}",
        f"- Max predicted error (um): {_summary_number(summary['predicted_max_error_um_max'])}",
        f"- Mean predicted error (um): {_summary_number(summary['predicted_max_error_um_mean'])}",
        f"- Max measured radial error (um): {_summary_number(summary['measured_radial_error_um_max'])}",
        f"- Mean measured radial error (um): {_summary_number(summary['measured_radial_error_um_mean'])}",
    ]
    if summary["error_messages"]:
        lines.extend(["", "## Capture Errors", ""])
        for item in summary["error_messages"]:
            lines.append(f"- #{item['index']} {item['label']}: {item['message']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_xlsx(path: Path, rows: list[list[Any]], sheet_name: str) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _xlsx_content_types())
        archive.writestr("_rels/.rels", _xlsx_root_rels())
        archive.writestr("xl/workbook.xml", _xlsx_workbook(sheet_name))
        archive.writestr("xl/_rels/workbook.xml.rels", _xlsx_workbook_rels())
        archive.writestr("xl/styles.xml", _xlsx_styles())
        archive.writestr("xl/worksheets/sheet1.xml", _xlsx_sheet(rows))


def _xlsx_content_types() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""


def _xlsx_root_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""


def _xlsx_workbook(sheet_name: str) -> str:
    safe_name = escape(sheet_name[:31] or "Sheet1")
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="{safe_name}" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>"""


def _xlsx_workbook_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""


def _xlsx_styles() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
</styleSheet>"""


def _xlsx_sheet(rows: list[list[Any]]) -> str:
    row_xml: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            cell_ref = f"{_column_letters(column_index)}{row_index}"
            cells.append(_xlsx_cell(cell_ref, value))
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>" + "".join(row_xml) + "</sheetData></worksheet>"
    )


def _xlsx_cell(cell_ref: str, value: Any) -> str:
    if value in ("", None):
        return f'<c r="{cell_ref}"/>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{cell_ref}"><v>{value}</v></c>'
    text = escape(str(value))
    return f'<c r="{cell_ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def _column_letters(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _summary_number(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.4f}".rstrip("0").rstrip(".")
