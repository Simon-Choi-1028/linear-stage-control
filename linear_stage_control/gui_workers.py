from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal

from .camera import BaslerCamera, camera_settings_from_config, enumerate_cameras, iso_timestamp
from .dataset import DatasetRun, base_capture_record, dataset_settings_from_config, safe_timestamp
from .error_model import error_budget_from_config, estimate_position_error_um
from .scan import (
    ScanPoint,
    default_capture_count_from_config,
    effective_capture_count,
    effective_move_velocity_mm_s,
    total_capture_count,
)
from .stage import ZaberXYStage, stage_settings_from_config


class AcquisitionWorker(QThread):
    log_message = Signal(str)
    status_changed = Signal(str)
    capture_done = Signal(dict)
    run_done = Signal(str, bool)
    run_failed = Signal(str)
    progress_changed = Signal(int, int)

    def __init__(
        self,
        config: dict[str, Any],
        points: list[ScanPoint],
        config_path: Path,
        output_root: str,
        skip_home: bool,
    ):
        super().__init__()
        self.config = config
        self.points = points
        self.config_path = config_path
        self.output_root = output_root
        self.skip_home = skip_home
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        run_dir = ""
        stopped = False
        try:
            dataset_settings = dataset_settings_from_config(self.config, self.output_root)
            stage_settings = stage_settings_from_config(self.config)
            camera_settings = camera_settings_from_config(self.config)
            error_budget = error_budget_from_config(self.config)
            self.status_changed.emit("데이터셋, 스테이지, 카메라 연결 중")
            self.log_message.emit("데이터셋, 스테이지, 카메라 연결 중")

            with (
                DatasetRun(dataset_settings, self.config, self.points, self.config_path) as dataset,
                ZaberXYStage(stage_settings) as stage,
                BaslerCamera(camera_settings) as camera,
            ):
                run_dir = str(dataset.run_dir)
                if stage_settings.home_on_start and not self.skip_home:
                    self.status_changed.emit("XY 스테이지 원점 복귀 중")
                    self.log_message.emit("XY 스테이지 원점 복귀 중")
                    stage.home()

                default_capture_count = default_capture_count_from_config(self.config)
                total = total_capture_count(self.points, default_capture_count)
                completed = 0
                for point in self.points:
                    if self._stop_requested:
                        stopped = True
                        break

                    record = base_capture_record(dataset.run_id, point)
                    try:
                        capture_count = effective_capture_count(point, default_capture_count)
                        move_velocity = effective_move_velocity_mm_s(
                            point,
                            stage_settings.move_velocity_mm_s,
                        )
                        record["capture_count"] = capture_count
                        record["point_move_velocity_mm_s"] = point.move_velocity_mm_s or ""
                        record["effective_move_velocity_mm_s"] = move_velocity or ""
                        self.status_changed.emit(
                            f"{completed}/{total} 이동 중 | 위치 #{point.index} "
                            f"X={_mm_text(point.x_mm)}, Y={_mm_text(point.y_mm)}"
                        )
                        self.log_message.emit(
                            f"이동 #{point.index}: X={_mm_text(point.x_mm)}, Y={_mm_text(point.y_mm)}, "
                            f"속도={_velocity_text(move_velocity)}, 캡쳐={capture_count}"
                        )
                        record["move_started_at"] = iso_timestamp()
                        stage.move_absolute_mm(
                            point.x_mm,
                            point.y_mm,
                            velocity_mm_s=move_velocity,
                        )
                        record["move_completed_at"] = iso_timestamp()

                        self.status_changed.emit(f"{completed}/{total} 위치 확인 및 오차 계산 중")
                        actual_x_mm, actual_y_mm = stage.position_mm()
                        record["actual_x_mm"] = actual_x_mm
                        record["actual_y_mm"] = actual_y_mm
                        record["error_x_mm"] = actual_x_mm - point.x_mm
                        record["error_y_mm"] = actual_y_mm - point.y_mm
                        record.update(
                            estimate_position_error_um(
                                float(record["error_x_mm"]),
                                float(record["error_y_mm"]),
                                error_budget,
                            ).as_record()
                        )

                        self.status_changed.emit(f"{completed}/{total} 안정화 대기 중")
                        self.msleep(max(0, int(stage_settings.settle_s * 1000)))
                        record["settle_completed_at"] = iso_timestamp()

                        for capture_index in range(1, capture_count + 1):
                            if self._stop_requested:
                                stopped = True
                                break
                            capture_record = dict(record)
                            capture_record["capture_index"] = capture_index
                            self.status_changed.emit(
                                f"{completed + 1}/{total} 카메라 촬영 중 | "
                                f"위치 #{point.index} {capture_index}/{capture_count}"
                            )
                            image_timestamp = safe_timestamp()
                            image_path = dataset.image_path(point, image_timestamp)
                            npy_path = dataset.npy_path(point, image_timestamp)
                            capture = camera.capture_original_to(image_path, npy_path=npy_path)

                            self.status_changed.emit(f"{completed + 1}/{total} 이미지와 메타데이터 저장 중")
                            capture_record.update(
                                {
                                    "status": "ok",
                                    "capture_commanded_at": capture.captured_at,
                                    "capture_completed_at": capture.completed_at,
                                    "camera_timestamp_ns": capture.camera_timestamp_ns,
                                    "block_id": capture.block_id,
                                    "image_path": str(capture.image_path.relative_to(dataset.run_dir)),
                                    "absolute_image_path": str(capture.image_path),
                                    "npy_path": str(capture.npy_path.relative_to(dataset.run_dir))
                                    if capture.npy_path
                                    else "",
                                    "image_dtype": capture.dtype,
                                    "image_shape": list(capture.shape),
                                    "pixel_type": capture.pixel_type,
                                }
                            )
                            dataset.write_capture(capture_record)
                            self.capture_done.emit(capture_record)
                            completed += 1
                            self.progress_changed.emit(completed, total)
                            self.status_changed.emit(f"{completed}/{total} 저장 완료")
                    except Exception as exc:
                        record["status"] = "error"
                        record["error_message"] = str(exc)
                        dataset.write_capture(record)
                        self.capture_done.emit(record)
                        raise

                for warning in camera.warnings:
                    self.log_message.emit(f"카메라 경고: {warning}")

            self.status_changed.emit("중지됨" if stopped else "완료")
            self.run_done.emit(run_dir, stopped)
        except Exception as exc:
            self.status_changed.emit("오류 발생")
            self.run_failed.emit(str(exc))
            if run_dir:
                self.run_done.emit(run_dir, True)


class CameraDiscoveryWorker(QThread):
    cameras_found = Signal(list, str)
    scan_failed = Signal(str, str)

    def __init__(self, reason: str):
        super().__init__()
        self.reason = reason

    def run(self) -> None:
        try:
            cameras = enumerate_cameras()
            self.cameras_found.emit(cameras, self.reason)
        except Exception as exc:
            self.scan_failed.emit(str(exc), self.reason)


def _mm_text(value: Any) -> str:
    return _compact_number_text(value, 4)


def _velocity_text(value: Any) -> str:
    if value in ("", None):
        return "기본값"
    return f"{_compact_number_text(value, 2)} mm/s"


def _compact_number_text(value: Any, max_decimals: int) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number) < 10 ** (-(max_decimals + 1)):
        number = 0.0
    text = f"{number:.{max_decimals}f}".rstrip("0").rstrip(".")
    return text or "0"
