from __future__ import annotations

import argparse
from pathlib import Path
from time import sleep

from rich.console import Console
from rich.table import Table

from linear_stage_control.camera import BaslerCamera, camera_settings_from_config
from linear_stage_control.config import load_config
from linear_stage_control.dataset import DatasetRun, base_capture_record, dataset_settings_from_config, safe_timestamp
from linear_stage_control.error_model import error_budget_from_config, estimate_position_error_um
from linear_stage_control.position_validation import (
    disabled_axis_variation_errors,
    format_issue_list,
    validate_scan_points,
)
from linear_stage_control.scan import (
    ScanPoint,
    default_capture_count_from_config,
    effective_capture_count,
    effective_move_velocity_mm_s,
    points_from_config,
    total_capture_count,
)
from linear_stage_control.stage import ZaberXYStage, stage_settings_from_config
from linear_stage_control.text_formatting import mm_text as _mm_text


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Move a Zaber XY stage over a grid and capture a Basler image at each point."
    )
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config.")
    parser.add_argument("--output", help="Override dataset output root.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned points only.")
    parser.add_argument("--skip-home", action="store_true", help="Do not home before scanning.")
    args = parser.parse_args()

    console = Console()
    config_path = Path(args.config)
    config = load_config(config_path)
    points = points_from_config(config, base_dir=config_path.parent)
    validation = validate_scan_points(points)
    for warning in validation.warnings:
        console.print(f"[yellow]Position warning:[/yellow] {warning}")
    if validation.errors:
        raise SystemExit(format_issue_list("Position list has errors.", validation.errors))

    stage_settings = stage_settings_from_config(config)
    axis_errors = disabled_axis_variation_errors(
        points,
        x_active=stage_settings.x.enabled,
        y_active=stage_settings.y.enabled,
    )
    if axis_errors:
        raise SystemExit(
            format_issue_list("Stage axis settings conflict with the position list.", axis_errors)
        )

    if args.dry_run:
        _print_points(console, points)
        return

    dataset_settings = dataset_settings_from_config(config, args.output)
    camera_settings = camera_settings_from_config(config)
    error_budget = error_budget_from_config(config)
    default_capture_count = default_capture_count_from_config(config)
    x_active = stage_settings.x.enabled
    y_active = stage_settings.y.enabled

    with (
        DatasetRun(dataset_settings, config, points, config_path) as dataset,
        ZaberXYStage(stage_settings) as stage,
        BaslerCamera(camera_settings) as camera,
    ):
        if stage_settings.home_on_start and not args.skip_home:
            stage.home()

        completed = 0
        total = total_capture_count(points, default_capture_count)
        for point in points:
            record = base_capture_record(dataset.run_id, point)
            record["axis_x_active"] = x_active
            record["axis_y_active"] = y_active
            try:
                capture_count = effective_capture_count(point, default_capture_count)
                move_velocity = effective_move_velocity_mm_s(point, stage_settings.move_velocity_mm_s)
                record["capture_count"] = capture_count
                record["point_move_velocity_mm_s"] = point.move_velocity_mm_s or ""
                record["effective_move_velocity_mm_s"] = move_velocity or ""
                record["move_started_at"] = _now()
                stage.move_absolute_mm(
                    point.x_mm,
                    point.y_mm,
                    velocity_mm_s=move_velocity,
                )
                record["move_completed_at"] = _now()
                actual_x_mm, actual_y_mm = stage.position_mm()
                error_x_mm = (
                    actual_x_mm - point.x_mm if x_active and actual_x_mm is not None else None
                )
                error_y_mm = (
                    actual_y_mm - point.y_mm if y_active and actual_y_mm is not None else None
                )
                record["actual_x_mm"] = actual_x_mm if actual_x_mm is not None else ""
                record["actual_y_mm"] = actual_y_mm if actual_y_mm is not None else ""
                record["error_x_mm"] = error_x_mm if error_x_mm is not None else ""
                record["error_y_mm"] = error_y_mm if error_y_mm is not None else ""
                record.update(
                    estimate_position_error_um(
                        error_x_mm,
                        error_y_mm,
                        error_budget,
                        x_active=x_active,
                        y_active=y_active,
                    ).as_record()
                )

                sleep(stage_settings.settle_s)
                record["settle_completed_at"] = _now()

                for capture_index in range(1, capture_count + 1):
                    capture_record = dict(record)
                    capture_record["capture_index"] = capture_index
                    image_timestamp = safe_timestamp()
                    image_path = dataset.image_path(point, image_timestamp, capture_index)
                    npy_path = dataset.npy_path(point, image_timestamp, capture_index)
                    capture = camera.capture_original_to(image_path, npy_path=npy_path)

                    capture_record.update(
                        {
                            "status": "ok",
                            "capture_commanded_at": capture.captured_at,
                            "capture_completed_at": capture.completed_at,
                            "camera_timestamp_ns": capture.camera_timestamp_ns,
                            "block_id": capture.block_id,
                            "image_path": str(capture.image_path.relative_to(dataset.run_dir)),
                            "image_filename": capture.image_path.name,
                            "npy_path": str(capture.npy_path.relative_to(dataset.run_dir))
                            if capture.npy_path
                            else "",
                            "image_dtype": capture.dtype,
                            "image_shape": list(capture.shape),
                            "pixel_type": capture.pixel_type,
                        }
                    )
                    dataset.write_capture(capture_record)
                    completed += 1
                    console.print(
                        f"[{completed}/{total}] "
                        f"#{point.index} capture {capture_index}/{capture_count} "
                        f"X={_mm_text(actual_x_mm)} mm Y={_mm_text(actual_y_mm)} mm -> "
                        f"{capture_record['image_path']}"
                    )
            except Exception as exc:
                record["status"] = "error"
                record["error_message"] = str(exc)
                dataset.write_capture(record)
                raise

        for warning in camera.warnings:
            console.print(f"[yellow]Camera warning:[/yellow] {warning}")

        console.print(f"Dataset complete: {dataset.run_dir}")


def _print_points(console: Console, points: list[ScanPoint]) -> None:
    table = Table("Index", "Label", "X mm", "Y mm", "Velocity mm/s", "Captures")
    for point in points:
        table.add_row(
            str(point.index),
            point.label,
            _mm_text(point.x_mm),
            _mm_text(point.y_mm),
            "" if point.move_velocity_mm_s is None else _mm_text(point.move_velocity_mm_s),
            "" if point.capture_count is None else str(point.capture_count),
        )
    console.print(table)
    console.print(f"{len(points)} point(s)")

def _now() -> str:
    from linear_stage_control.camera import iso_timestamp

    return iso_timestamp()

if __name__ == "__main__":
    main()
