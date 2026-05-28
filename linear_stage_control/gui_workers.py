from __future__ import annotations

from copy import deepcopy
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
from .stage import StageMoveCancelled, ZaberXYStage, stage_settings_from_config
from .text_formatting import mm_text as _mm_text, velocity_text as _velocity_text
from .updater import UpdateInfo, download_file, fetch_latest_update, verify_file_sha256


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
            x_active = stage_settings.x.enabled
            y_active = stage_settings.y.enabled
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
                        record["axis_x_active"] = x_active
                        record["axis_y_active"] = y_active
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
                            cancel_requested=lambda: self._stop_requested,
                        )
                        record["move_completed_at"] = iso_timestamp()

                        self.status_changed.emit(f"{completed}/{total} 위치 확인 및 오차 계산 중")
                        actual_x_mm, actual_y_mm = stage.position_mm()
                        error_x_mm = actual_x_mm - point.x_mm if x_active and actual_x_mm is not None else None
                        error_y_mm = actual_y_mm - point.y_mm if y_active and actual_y_mm is not None else None
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

                        self.status_changed.emit(f"{completed}/{total} 안정화 대기 중")
                        self._sleep_interruptible(max(0, int(stage_settings.settle_s * 1000)))
                        if self._stop_requested:
                            stopped = True
                            break
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
                            image_path = dataset.image_path(point, image_timestamp, capture_index)
                            npy_path = dataset.npy_path(point, image_timestamp, capture_index)
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
                                    "image_filename": capture.image_path.name,
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
                    except StageMoveCancelled:
                        stopped = True
                        record["status"] = "stopped"
                        record["error_message"] = "사용자 중지 요청으로 이동을 취소했습니다."
                        dataset.write_capture(record)
                        self.capture_done.emit(record)
                        break
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


    def _sleep_interruptible(self, duration_ms: int) -> None:
        remaining_ms = max(0, duration_ms)
        while remaining_ms > 0 and not self._stop_requested:
            step_ms = min(remaining_ms, 50)
            self.msleep(step_ms)
            remaining_ms -= step_ms


class LivePreviewWorker(QThread):
    frame_ready = Signal(object, dict)
    status_changed = Signal(str)
    live_failed = Signal(str)

    def __init__(self, config: dict[str, Any], fps: int = 10):
        super().__init__()
        self.config = deepcopy(config)
        self.fps = max(1, min(60, int(fps or 10)))
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        try:
            preview_config = deepcopy(self.config)
            camera_config = preview_config.setdefault("camera", {})
            camera_config["use_software_trigger"] = False
            camera_config["trigger_mode"] = "Off"
            camera_config["timeout_ms"] = int(camera_config.get("timeout_ms", 1000) or 1000)
            settings = camera_settings_from_config(preview_config)
            frame_delay_ms = max(1, int(1000 / self.fps))
            with BaslerCamera(settings) as camera:
                self.status_changed.emit("Live 첫 프레임 대기")
                first_frame = True
                for array, metadata in camera.live_original_arrays(
                    timeout_ms=settings.timeout_ms,
                    stop_requested=lambda: self._stop_requested,
                ):
                    if self._stop_requested:
                        break
                    if first_frame:
                        self.status_changed.emit(f"Live 수신 중 ({self.fps} FPS)")
                        first_frame = False
                    self.frame_ready.emit(array, metadata)
                    self.msleep(frame_delay_ms)
        except Exception as exc:
            if not self._stop_requested:
                self.live_failed.emit(str(exc))


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


class UpdateCheckWorker(QThread):
    update_available = Signal(object)
    update_not_available = Signal(str)
    update_failed = Signal(str)

    def __init__(self, repo: str, current_version: str):
        super().__init__()
        self.repo = repo
        self.current_version = current_version

    def run(self) -> None:
        try:
            update = fetch_latest_update(self.repo, self.current_version)
            if update is None:
                self.update_not_available.emit(self.current_version)
            else:
                self.update_available.emit(update)
        except Exception as exc:
            self.update_failed.emit(str(exc))


class UpdateDownloadWorker(QThread):
    progress_changed = Signal(int, int)
    download_done = Signal(str)
    download_failed = Signal(str)

    def __init__(self, update: UpdateInfo, output_path: Path):
        super().__init__()
        self.update = update
        self.output_path = output_path

    def run(self) -> None:
        try:
            def progress(received: int, total: int | None) -> None:
                self.progress_changed.emit(received, total or 0)

            path = download_file(self.update.setup_url, self.output_path, progress_callback=progress)
            if not self.update.sha256 or not verify_file_sha256(path, self.update.sha256):
                raise RuntimeError("다운로드한 설치 파일의 SHA256 검증에 실패했습니다.")
            self.download_done.emit(str(path))
        except Exception as exc:
            self.download_failed.emit(str(exc))
