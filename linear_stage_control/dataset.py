from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from . import __version__
from .dataset_exports import (
    DEFAULT_METADATA_FORMATS,
    DEFAULT_SUMMARY_FORMATS,
    SUPPORTED_METADATA_FORMATS,
    SUPPORTED_SUMMARY_FORMATS,
    build_run_summary,
    json_ready,
    normalise_formats,
    write_records_json,
    write_records_tsv,
    write_records_xlsx,
    write_records_yaml,
    write_summary_json,
    write_summary_markdown,
    write_summary_yaml,
)
from .exceptions import DatasetWriteError
from .scan import ScanPoint


@dataclass(frozen=True)
class DatasetSettings:
    output_root: Path
    image_format: str = "tiff"
    save_numpy: bool = False
    write_jsonl: bool = True
    metadata_formats: tuple[str, ...] = DEFAULT_METADATA_FORMATS
    summary_formats: tuple[str, ...] = DEFAULT_SUMMARY_FORMATS


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
        image_format=_normalise_image_format(dataset.get("image_format", "tiff")),
        save_numpy=bool(dataset.get("save_numpy", False)),
        write_jsonl="jsonl" in metadata_formats,
        metadata_formats=metadata_formats,
        summary_formats=normalise_formats(
            dataset.get("summary_formats"),
            DEFAULT_SUMMARY_FORMATS,
            SUPPORTED_SUMMARY_FORMATS,
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
        self.records: list[dict[str, Any]] = []
        self._csv_file: Any = None
        self._csv_writer: csv.DictWriter | None = None
        self._jsonl_file: Any = None

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
                yaml.safe_dump(self.config, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            self._write_manifest(status="running")

            self._csv_file = self.csv_path.open("w", newline="", encoding="utf-8")
            self._csv_writer = csv.DictWriter(self._csv_file, fieldnames=CAPTURE_FIELDS)
            self._csv_writer.writeheader()
            if self.settings.write_jsonl:
                self._jsonl_file = self.jsonl_path.open("w", encoding="utf-8")
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
        except DatasetWriteError:
            raise
        except Exception as exc:
            raise DatasetWriteError("데이터셋 종료 처리 또는 manifest 작성에 실패했습니다.", str(exc)) from exc

    def image_path(self, point: ScanPoint, timestamp: str, capture_index: int = 1) -> Path:
        suffix = self.settings.image_format
        return self.images_dir / f"{point_name(point, timestamp, capture_index)}.{suffix}"

    def npy_path(self, point: ScanPoint, timestamp: str, capture_index: int = 1) -> Path | None:
        if not self.settings.save_numpy:
            return None
        return self.arrays_dir / f"{point_name(point, timestamp, capture_index)}.npy"

    def write_capture(self, record: dict[str, Any]) -> None:
        try:
            clean_record = json_ready(record)
            self.records.append(clean_record)
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
        records = list(self.records)
        if "json" in formats:
            write_records_json(self.metadata_paths["json"], records)
        if "tsv" in formats:
            write_records_tsv(self.metadata_paths["tsv"], CAPTURE_FIELDS, records)
        if "yaml" in formats:
            write_records_yaml(self.metadata_paths["yaml"], records)
        if "xlsx" in formats:
            write_records_xlsx(self.metadata_paths["xlsx"], CAPTURE_FIELDS, records)

        summary = build_run_summary(
            run_id=self.run_id,
            status=status,
            point_count=len(self.points),
            records=records,
        )
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
            "record_count": len(self.records),
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
        if status != "running":
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


def point_name(point: ScanPoint, timestamp: str, capture_index: int = 1) -> str:
    label = sanitize_label(point.label) or f"point{point.index:04d}"
    return f"{label}_x{_filename_mm(point.x_mm)}mm_y{_filename_mm(point.y_mm)}mm_" f"{timestamp}_cap{capture_index:03d}"


def sanitize_label(label: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", label.strip())
    return normalized.strip("._")[:80]


def _filename_mm(value: float) -> str:
    return f"{float(value):.3f}"


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _normalise_image_format(value: Any) -> str:
    image_format = str(value or "tiff").strip().lower().lstrip(".")
    aliases = {"tif": "tiff", "jpg": "jpeg"}
    image_format = aliases.get(image_format, image_format)
    allowed = {"tiff", "png", "bmp"}
    if image_format not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise ValueError(f"Unsupported image_format: {value}. Use a lossless format: {allowed_text}.")
    return image_format


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_manifest_path(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()
