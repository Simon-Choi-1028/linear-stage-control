from __future__ import annotations

import csv
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from time import monotonic
from typing import Any

import yaml

from . import __version__
from .camera import CAMERA_CAPTURE_PARAMETER_FIELDS
from .exceptions import DatasetWriteError
from .scan import ScanPoint


@dataclass(frozen=True)
class DatasetSettings:
    output_root: Path
    image_format: str = "png"
    save_numpy: bool = False
    manifest_detail: str = "summary"
    metadata_flush_records: int = 100
    metadata_flush_interval_s: float = 1.0


def dataset_settings_from_config(
    config: dict[str, Any],
    output_override: str | Path | None = None,
) -> DatasetSettings:
    dataset = config.get("dataset", {})
    scan = config.get("scan", {})
    output_root = output_override or dataset.get("output_root") or scan.get("output_dir")
    return DatasetSettings(
        output_root=Path(os.path.expandvars(os.path.expanduser(str(output_root or "output/datasets")))),
        image_format=_normalise_image_format(dataset.get("image_format", "png")),
        save_numpy=bool(dataset.get("save_numpy", False)),
        manifest_detail=_normalise_manifest_detail(dataset.get("manifest_detail", "summary")),
        metadata_flush_records=_positive_int(dataset.get("metadata_flush_records", 100), "metadata_flush_records"),
        metadata_flush_interval_s=_non_negative_float(
            dataset.get("metadata_flush_interval_s", 1.0),
            "metadata_flush_interval_s",
        ),
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
        self.image_name_stems = planned_image_name_stems(points)
        self.csv_path = self.run_dir / "captures.csv"
        self.manifest_path = self.run_dir / "dataset_manifest.json"
        self.legacy_manifest_path = self.run_dir / "manifest.json"
        self.config_snapshot_path = self.run_dir / "config.yaml"
        self.record_count = 0
        self._csv_file: Any = None
        self._csv_writer: csv.DictWriter | None = None
        self._metadata_dirty_records = 0
        self._last_metadata_flush_monotonic = monotonic()

    def __enter__(self) -> DatasetRun:
        self.open()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close(status="failed" if exc_type else "complete")

    def open(self) -> None:
        try:
            self.images_dir.mkdir(parents=True, exist_ok=False)
            if self.settings.save_numpy:
                self.arrays_dir.mkdir(parents=True, exist_ok=True)

            self.config_snapshot_path.write_text(
                yaml.safe_dump(_csv_only_config_snapshot(self.config), sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            self._write_manifest(status="running")

            self._csv_file = self.csv_path.open("w", newline="", encoding="utf-8")
            self._csv_writer = csv.DictWriter(self._csv_file, fieldnames=CAPTURE_FIELDS)
            self._csv_writer.writeheader()
            self._metadata_dirty_records = 1
            self._last_metadata_flush_monotonic = monotonic()
            self.flush_metadata(force=True)
        except DatasetWriteError:
            raise
        except Exception as exc:
            raise DatasetWriteError("데이터셋 폴더 또는 metadata 파일을 열 수 없습니다.", str(exc)) from exc

    def close(self, status: str = "complete") -> None:
        try:
            self.flush_metadata(force=True)
            if self._csv_file is not None:
                self._csv_file.close()
                self._csv_file = None
            if self.run_dir.exists():
                self._write_manifest(status=status)
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
        base_stem = self.image_name_stems.get(point.index)
        if base_stem is None:
            return point_name(point, capture_index=capture_index)
        capture_suffix = f"_C{capture_index:02d}" if capture_index > 1 else ""
        return f"{base_stem}{capture_suffix}"

    def write_capture(self, record: dict[str, Any]) -> None:
        try:
            row = {field: _csv_value(record.get(field, "")) for field in CAPTURE_FIELDS}
            if self._csv_writer is None:
                raise RuntimeError("Dataset CSV writer is not open.")
            self._csv_writer.writerow(row)
            self.record_count += 1
            self._metadata_dirty_records += 1
            self.flush_metadata()
        except DatasetWriteError:
            raise
        except Exception as exc:
            raise DatasetWriteError("캡처 metadata 저장에 실패했습니다.", str(exc)) from exc

    def flush_metadata(self, *, force: bool = False) -> None:
        if self._metadata_dirty_records <= 0:
            return
        elapsed_s = monotonic() - self._last_metadata_flush_monotonic
        if (
            not force
            and self._metadata_dirty_records < self.settings.metadata_flush_records
            and elapsed_s < self.settings.metadata_flush_interval_s
        ):
            return
        if self._csv_file is not None:
            self._csv_file.flush()
        self._metadata_dirty_records = 0
        self._last_metadata_flush_monotonic = monotonic()

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
            "record_count": self.record_count,
            "metadata_format": "csv",
            "metadata_files": (
                {"csv": _relative_manifest_path(self.csv_path, self.run_dir)} if self.csv_path.exists() else {}
            ),
            # Retained as an empty compatibility key for existing manifest readers.
            "summary_files": {},
            "config_snapshot": _relative_manifest_path(self.config_snapshot_path, self.run_dir),
        }
        if status != "running" and self.settings.manifest_detail == "full":
            manifest["files"] = self._file_manifest_entries()
        manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2)
        self.manifest_path.write_text(manifest_text, encoding="utf-8")
        self.legacy_manifest_path.write_text(manifest_text, encoding="utf-8")

    def _file_manifest_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        candidates: list[tuple[str, Path]] = [("config", self.config_snapshot_path)]
        candidates.extend(("image", path) for path in sorted(self.images_dir.glob("*")) if path.is_file())
        if self.arrays_dir.exists():
            candidates.extend(("array", path) for path in sorted(self.arrays_dir.glob("*")) if path.is_file())
        if self.csv_path.exists():
            candidates.append(("metadata", self.csv_path))

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
    "move_duration_ms",
    "settle_duration_ms",
    "capture_duration_ms",
    "disk_write_duration_ms",
] + list(CAMERA_CAPTURE_PARAMETER_FIELDS)


def base_capture_record(run_id: str, point: ScanPoint) -> dict[str, Any]:
    record = {
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
        "move_duration_ms": "",
        "settle_duration_ms": "",
        "capture_duration_ms": "",
        "disk_write_duration_ms": "",
    }
    record.update({field: "" for field in CAMERA_CAPTURE_PARAMETER_FIELDS})
    return record


def _csv_only_config_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    snapshot = dict(config)
    dataset = config.get("dataset")
    if isinstance(dataset, dict):
        dataset_snapshot = dict(dataset)
        dataset_snapshot.pop("metadata_formats", None)
        dataset_snapshot.pop("summary_formats", None)
        dataset_snapshot.pop("write_jsonl", None)
        snapshot["dataset"] = dataset_snapshot
    scan = config.get("scan")
    if isinstance(scan, dict):
        scan_snapshot = dict(scan)
        scan_snapshot.pop("estimated_export_overhead_s", None)
        scan_snapshot.pop("estimated_export_per_capture_s", None)
        snapshot["scan"] = scan_snapshot
    return snapshot


def safe_timestamp(value: datetime | None = None) -> str:
    dt = value or datetime.now().astimezone()
    return dt.strftime("%Y%m%dT%H%M%S_%f%z")


def validate_image_output_plan(points: list[ScanPoint], default_capture_count: int = 1) -> list[str]:
    del default_capture_count
    try:
        planned_image_name_stems(points)
    except ValueError as exc:
        return [str(exc)]
    return []


def planned_image_name_stems(points: list[ScanPoint]) -> dict[int, str]:
    stems: dict[int, str] = {}
    coordinate_stem_counts: dict[str, int] = {}
    for point in points:
        if point.index in stems:
            raise ValueError(f"Duplicate point index in image output plan: {point.index}.")
        base_stem = _coordinate_stem(point)
        coordinate_stem_counts[base_stem] = coordinate_stem_counts.get(base_stem, 0) + 1
        occurrence_index = coordinate_stem_counts[base_stem]
        point_suffix = f"_P{occurrence_index:04d}" if occurrence_index > 1 else ""
        stems[point.index] = f"{base_stem}{point_suffix}"
    return stems


def point_name(point: ScanPoint, timestamp: str | None = None, capture_index: int = 1) -> str:
    del timestamp
    capture_suffix = f"_C{capture_index:02d}" if capture_index > 1 else ""
    return f"{_coordinate_stem(point)}{capture_suffix}"


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


def _positive_int(value: Any, field_name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"dataset.{field_name} must be a positive integer.") from exc
    if number <= 0:
        raise ValueError(f"dataset.{field_name} must be a positive integer.")
    return number


def _non_negative_float(value: Any, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"dataset.{field_name} must be zero or greater.") from exc
    if number < 0:
        raise ValueError(f"dataset.{field_name} must be zero or greater.")
    return number


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_manifest_path(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()
