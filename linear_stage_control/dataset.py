from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from . import __version__
from .dataset_exports import (
    DEFAULT_METADATA_FORMATS,
    DEFAULT_SUMMARY_FORMATS,
    POST_RUN_METADATA_FORMATS,
    SUPPORTED_METADATA_FORMATS,
    SUPPORTED_SUMMARY_FORMATS,
    RunStatsAccumulator,
    iter_jsonl_records,
    json_ready,
    normalise_formats,
    write_records_json_stream,
    write_records_tsv_stream,
    write_records_xlsx_stream,
    write_records_yaml_stream,
    write_summary_json,
    write_summary_markdown,
    write_summary_yaml,
)
from .exceptions import DatasetWriteError
from .scan import ScanPoint, default_capture_count_from_config, effective_capture_count


@dataclass(frozen=True)
class DatasetSettings:
    output_root: Path
    image_format: str = "png"
    save_numpy: bool = False
    write_jsonl: bool = True
    metadata_formats: tuple[str, ...] = DEFAULT_METADATA_FORMATS
    summary_formats: tuple[str, ...] = DEFAULT_SUMMARY_FORMATS
    manifest_detail: str = "summary"


def dataset_settings_from_config(
    config: dict[str, Any],
    output_override: str | Path | None = None,
) -> DatasetSettings:
    dataset = config.get("dataset", {})
    scan = config.get("scan", {})
    output_root = output_override or dataset.get("output_root") or scan.get("output_dir")
    write_jsonl = bool(dataset.get("write_jsonl", True))
    metadata_formats = normalise_formats(
        dataset.get("metadata_formats"),
        DEFAULT_METADATA_FORMATS
        if write_jsonl
        else tuple(item for item in DEFAULT_METADATA_FORMATS if item != "jsonl"),
        SUPPORTED_METADATA_FORMATS,
    )
    if "csv" not in metadata_formats:
        metadata_formats = ("csv",) + metadata_formats
    if write_jsonl and "jsonl" not in metadata_formats:
        metadata_formats = metadata_formats + ("jsonl",)
    return DatasetSettings(
        output_root=Path(os.path.expandvars(os.path.expanduser(str(output_root or "output/datasets")))),
        image_format=_normalise_image_format(dataset.get("image_format", "png")),
        save_numpy=bool(dataset.get("save_numpy", False)),
        write_jsonl="jsonl" in metadata_formats,
        metadata_formats=metadata_formats,
        summary_formats=normalise_formats(
            dataset.get("summary_formats"),
            DEFAULT_SUMMARY_FORMATS,
            SUPPORTED_SUMMARY_FORMATS,
        ),
        manifest_detail=_normalise_manifest_detail(dataset.get("manifest_detail", "summary")),
    )


class DatasetRun:
    def __init__(
        self,
        settings: DatasetSettings,
        config: dict[str, Any],
        points: list[ScanPoint],
        config_path: str | Path,
    ):
        self.settings = settings
        self.config = config
        self.points = points
        self.config_path = Path(config_path)
        self.run_id = safe_timestamp(datetime.now().astimezone())
        self.run_dir = self.settings.output_root / self.run_id
        self.images_dir = self.run_dir / "images"
        self.arrays_dir = self.run_dir / "arrays"
        self.default_capture_count = default_capture_count_from_config(config)
        self.image_name_stems = planned_image_name_stems(points, self.default_capture_count)
        self.csv_path = self.run_dir / "captures.csv"
        self.jsonl_path = self.run_dir / "captures.jsonl"
        self.metadata_paths = {
            "csv": self.csv_path,
            "jsonl": self.jsonl_path,
            "json": self.run_dir / "captures.json",
            "tsv": self.run_dir / "captures.tsv",
            "yaml": self.run_dir / "captures.yaml",
            "xlsx": self.run_dir / "captures.xlsx",
        }
        self.summary_paths = {
            "json": self.run_dir / "summary.json",
            "yaml": self.run_dir / "summary.yaml",
            "md": self.run_dir / "summary.md",
        }
        self.manifest_path = self.run_dir / "dataset_manifest.json"
        self.legacy_manifest_path = self.run_dir / "manifest.json"
        self.config_snapshot_path = self.run_dir / "config.yaml"
        self.export_source_jsonl_path = self.run_dir / ".captures_export_source.jsonl"
        self.records: list[dict[str, Any]] = []
        self.stats = RunStatsAccumulator()
        self._csv_file: Any = None
        self._csv_writer: csv.DictWriter | None = None
        self._jsonl_file: Any = None
        self._jsonl_is_export_source = False

    def __enter__(self) -> DatasetRun:
        self.open()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close(status="failed" if exc_type else "complete")

    def open(self) -> None:
        try:
            image_plan_errors = validate_image_output_plan(
                self.points,
                self.default_capture_count,
            )
            if image_plan_errors:
                raise DatasetWriteError(
                    "이미지 파일명 계획을 확인하세요.",
                    "; ".join(image_plan_errors),
                )
            self.images_dir.mkdir(parents=True, exist_ok=False)
            if self.settings.save_numpy:
                self.arrays_dir.mkdir(parents=True, exist_ok=True)

            self.config_snapshot_path.write_text(
                yaml.safe_dump(self.config, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            self._write_manifest(status="running")

            self._csv_file = self.csv_path.open("w", newline="", encoding="utf-8")
            self._csv_writer = csv.DictWriter(self._csv_file, fieldnames=CAPTURE_FIELDS)
            self._csv_writer.writeheader()
            if self._needs_jsonl_stream():
                self._jsonl_is_export_source = not self.settings.write_jsonl
                jsonl_path = self.export_source_jsonl_path if self._jsonl_is_export_source else self.jsonl_path
                self._jsonl_file = jsonl_path.open("w", encoding="utf-8")
        except DatasetWriteError:
            raise
        except Exception as exc:
            raise DatasetWriteError("데이터셋 폴더 또는 metadata 파일을 열 수 없습니다.", str(exc)) from exc

    def close(self, status: str = "complete") -> None:
        try:
            if self._jsonl_file is not None:
                self._jsonl_file.close()
                self._jsonl_file = None
            if self._csv_file is not None:
                self._csv_file.close()
                self._csv_file = None
            if self.run_dir.exists():
                self._write_post_run_exports(status=status)
                self._write_manifest(status=status)
                if self._jsonl_is_export_source and self.export_source_jsonl_path.exists():
                    self.export_source_jsonl_path.unlink()
        except DatasetWriteError:
            raise
        except Exception as exc:
            raise DatasetWriteError("데이터셋 종료 처리 또는 manifest 작성에 실패했습니다.", str(exc)) from exc

    def image_path(self, point: ScanPoint, timestamp: str, capture_index: int = 1) -> Path:
        del timestamp
        suffix = self.settings.image_format
        return self.images_dir / f"{self._image_name_stem(point, capture_index)}.{suffix}"

    def npy_path(self, point: ScanPoint, timestamp: str, capture_index: int = 1) -> Path | None:
        del timestamp
        if not self.settings.save_numpy:
            return None
        return self.arrays_dir / f"{self._image_name_stem(point, capture_index)}.npy"

    def _image_name_stem(self, point: ScanPoint, capture_index: int) -> str:
        return self.image_name_stems.get((point.index, capture_index), point_name(point, capture_index=capture_index))

    def write_capture(self, record: dict[str, Any]) -> None:
        try:
            clean_record = json_ready(record)
            self.stats.add_record(clean_record)
            row = {field: _csv_value(record.get(field, "")) for field in CAPTURE_FIELDS}
            if self._csv_writer is None:
                raise RuntimeError("Dataset CSV writer is not open.")
            self._csv_writer.writerow(row)
            self._csv_file.flush()
            if self._jsonl_file is not None:
                self._jsonl_file.write(json.dumps(clean_record, ensure_ascii=False) + "\n")
                self._jsonl_file.flush()
        except DatasetWriteError:
            raise
        except Exception as exc:
            raise DatasetWriteError("캡처 metadata 저장에 실패했습니다.", str(exc)) from exc

    def _write_post_run_exports(self, status: str) -> None:
        formats = set(self.settings.metadata_formats)
        post_run_formats = formats & POST_RUN_METADATA_FORMATS
        if post_run_formats:
            source_path = self._post_run_jsonl_source()
            if source_path is None:
                raise DatasetWriteError("Post-run metadata export requires a JSONL stream source.")
            if "json" in post_run_formats:
                write_records_json_stream(self.metadata_paths["json"], iter_jsonl_records(source_path))
            if "tsv" in post_run_formats:
                write_records_tsv_stream(self.metadata_paths["tsv"], CAPTURE_FIELDS, iter_jsonl_records(source_path))
            if "yaml" in post_run_formats:
                write_records_yaml_stream(self.metadata_paths["yaml"], iter_jsonl_records(source_path))
            if "xlsx" in post_run_formats:
                write_records_xlsx_stream(self.metadata_paths["xlsx"], CAPTURE_FIELDS, iter_jsonl_records(source_path))

        summary = self.stats.as_summary(run_id=self.run_id, status=status, point_count=len(self.points))
        summary_formats = set(self.settings.summary_formats)
        if "json" in summary_formats:
            write_summary_json(self.summary_paths["json"], summary)
        if "yaml" in summary_formats:
            write_summary_yaml(self.summary_paths["yaml"], summary)
        if "md" in summary_formats:
            write_summary_markdown(self.summary_paths["md"], summary)

    def _write_manifest(self, status: str) -> None:
        manifest = {
            "run_id": self.run_id,
            "status": status,
            "app_version": __version__,
            "created_at": self.run_id,
            "updated_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "config_path": str(self.config_path),
            "dataset": asdict(self.settings) | {"output_root": str(self.settings.output_root)},
            "point_count": len(self.points),
            "record_count": self.stats.record_count,
            "metadata_files": {
                key: _relative_manifest_path(path, self.run_dir)
                for key, path in self.metadata_paths.items()
                if path.exists()
            },
            "summary_files": {
                key: _relative_manifest_path(path, self.run_dir)
                for key, path in self.summary_paths.items()
                if path.exists()
            },
            "config_snapshot": _relative_manifest_path(self.config_snapshot_path, self.run_dir),
        }
        if status != "running" and self.settings.manifest_detail == "full":
            manifest["files"] = self._file_manifest_entries()
        manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2)
        self.manifest_path.write_text(manifest_text, encoding="utf-8")
        self.legacy_manifest_path.write_text(manifest_text, encoding="utf-8")

    def _needs_jsonl_stream(self) -> bool:
        return self.settings.write_jsonl or bool(set(self.settings.metadata_formats) & POST_RUN_METADATA_FORMATS)

    def _post_run_jsonl_source(self) -> Path | None:
        if self._jsonl_is_export_source:
            return self.export_source_jsonl_path if self.export_source_jsonl_path.exists() else None
        return self.jsonl_path if self.jsonl_path.exists() else None

    def _file_manifest_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        candidates: list[tuple[str, Path]] = [("config", self.config_snapshot_path)]
        candidates.extend(("image", path) for path in sorted(self.images_dir.glob("*")) if path.is_file())
        if self.arrays_dir.exists():
            candidates.extend(("array", path) for path in sorted(self.arrays_dir.glob("*")) if path.is_file())
        candidates.extend(("metadata", path) for path in self.metadata_paths.values() if path.exists())
        candidates.extend(("summary", path) for path in self.summary_paths.values() if path.exists())

        seen: set[Path] = set()
        for role, path in candidates:
            resolved = path.resolve()
            if resolved in seen or not path.is_file():
                continue
            seen.add(resolved)
            entries.append(
                {
                    "path": _relative_manifest_path(path, self.run_dir),
                    "role": role,
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
            )
        return entries


CAPTURE_FIELDS = [
    "run_id",
    "index",
    "label",
    "status",
    "capture_index",
    "capture_count",
    "point_move_velocity_mm_s",
    "effective_move_velocity_mm_s",
    "axis_x_active",
    "axis_y_active",
    "target_x_mm",
    "target_y_mm",
    "actual_x_mm",
    "actual_y_mm",
    "error_x_mm",
    "error_y_mm",
    "measured_error_x_um",
    "measured_error_y_um",
    "measured_radial_error_um",
    "predicted_min_error_um",
    "predicted_max_error_um",
    "predicted_x_min_um",
    "predicted_x_max_um",
    "predicted_y_min_um",
    "predicted_y_max_um",
    "configured_error_budget_um",
    "max_allowed_error_um",
    "within_error_threshold",
    "move_started_at",
    "move_completed_at",
    "settle_completed_at",
    "capture_commanded_at",
    "capture_completed_at",
    "camera_timestamp_ns",
    "block_id",
    "image_path",
    "image_filename",
    "npy_path",
    "image_dtype",
    "image_shape",
    "pixel_type",
    "error_message",
]


def base_capture_record(run_id: str, point: ScanPoint) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "index": point.index,
        "label": point.label,
        "status": "pending",
        "capture_index": "",
        "capture_count": point.capture_count or "",
        "point_move_velocity_mm_s": point.move_velocity_mm_s or "",
        "effective_move_velocity_mm_s": "",
        "axis_x_active": "",
        "axis_y_active": "",
        "target_x_mm": point.x_mm,
        "target_y_mm": point.y_mm,
        "actual_x_mm": "",
        "actual_y_mm": "",
        "error_x_mm": "",
        "error_y_mm": "",
        "measured_error_x_um": "",
        "measured_error_y_um": "",
        "measured_radial_error_um": "",
        "predicted_min_error_um": "",
        "predicted_max_error_um": "",
        "predicted_x_min_um": "",
        "predicted_x_max_um": "",
        "predicted_y_min_um": "",
        "predicted_y_max_um": "",
        "configured_error_budget_um": "",
        "max_allowed_error_um": "",
        "within_error_threshold": "",
        "move_started_at": "",
        "move_completed_at": "",
        "settle_completed_at": "",
        "capture_commanded_at": "",
        "capture_completed_at": "",
        "camera_timestamp_ns": "",
        "block_id": "",
        "image_path": "",
        "image_filename": "",
        "npy_path": "",
        "image_dtype": "",
        "image_shape": "",
        "pixel_type": "",
        "error_message": "",
    }


def safe_timestamp(value: datetime | None = None) -> str:
    dt = value or datetime.now().astimezone()
    return dt.strftime("%Y%m%dT%H%M%S_%f%z")


def validate_image_output_plan(points: list[ScanPoint], default_capture_count: int = 1) -> list[str]:
    errors: list[str] = []
    try:
        stems = planned_image_name_stems(points, default_capture_count)
    except ValueError as exc:
        return [str(exc)]
    seen_names: dict[str, tuple[int, int]] = {}
    for key, stem in stems.items():
        filename = f"{stem}.png"
        previous = seen_names.get(filename)
        if previous is not None:
            previous_point, previous_capture = previous
            point_index, capture_index = key
            errors.append(
                f"위치 #{previous_point}({previous_capture})와 #{point_index}({capture_index})가 "
                f"같은 이미지 파일명 {filename}을 사용합니다."
            )
        else:
            seen_names[filename] = key
    return errors


def planned_image_name_stems(points: list[ScanPoint], default_capture_count: int = 1) -> dict[tuple[int, int], str]:
    stems: dict[tuple[int, int], str] = {}
    coordinate_stem_counts: dict[str, int] = {}
    used_stems: set[str] = set()
    for point in points:
        base_stem = _coordinate_stem(point)
        coordinate_stem_counts[base_stem] = coordinate_stem_counts.get(base_stem, 0) + 1
        occurrence_index = coordinate_stem_counts[base_stem]
        point_suffix = f"_P{occurrence_index:04d}" if occurrence_index > 1 else ""
        for capture_index in range(1, effective_capture_count(point, default_capture_count) + 1):
            capture_suffix = f"_C{capture_index:02d}" if capture_index > 1 else ""
            stem = f"{base_stem}{point_suffix}{capture_suffix}"
            if stem in used_stems:
                stem = f"{base_stem}_P{occurrence_index:04d}{capture_suffix}"
            duplicate_index = 2
            while stem in used_stems:
                stem = f"{base_stem}_P{occurrence_index:04d}_D{duplicate_index:02d}{capture_suffix}"
                duplicate_index += 1
            stems[(point.index, capture_index)] = stem
            used_stems.add(stem)
    return stems


def point_name(point: ScanPoint, timestamp: str | None = None, capture_index: int = 1) -> str:
    del timestamp
    capture_suffix = f"_C{capture_index:02d}" if capture_index > 1 else ""
    return f"{_coordinate_stem(point)}{capture_suffix}"


def sanitize_label(label: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", label.strip())
    return normalized.strip("._")[:80]


def _coordinate_stem(point: ScanPoint) -> str:
    return f"X{_filename_coordinate(point.x_mm)}_Y{_filename_coordinate(point.y_mm)}"


def _filename_coordinate(value: float) -> str:
    try:
        coordinate = Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid image coordinate: {value}") from exc
    if coordinate < 0:
        raise ValueError(f"Image coordinate cannot be negative: {value}")
    return f"{coordinate:07.3f}"


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _normalise_image_format(value: Any) -> str:
    image_format = str(value or "png").strip().lower().lstrip(".")
    aliases = {"tif": "tiff", "jpg": "jpeg"}
    image_format = aliases.get(image_format, image_format)
    allowed = {"tiff", "png", "bmp"}
    if image_format not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise ValueError(f"Unsupported image_format: {value}. Use a lossless format: {allowed_text}.")
    return image_format


def _normalise_manifest_detail(value: Any) -> str:
    detail = str(value or "summary").strip().lower()
    if detail not in {"summary", "full"}:
        raise ValueError(f"Unsupported manifest_detail: {value}. Use summary or full.")
    return detail


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_manifest_path(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()
