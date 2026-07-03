from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .camera import PYLON_IMPORT_ERROR, enumerate_cameras
from .process_guard import describe_processes, running_pylon_viewer_processes
from .stage import configure_zaber_device_database, list_serial_ports, stage_settings_from_config
from .updater import fetch_latest_update, update_settings_from_config


@dataclass(frozen=True)
class DiagnosticResult:
    item: str
    status: str
    detail: str


def collect_diagnostics(
    config: dict[str, Any],
    *,
    output_root: str | Path | None = None,
    current_version: str = "0.0.0",
    check_camera: bool = True,
    check_updates: bool = True,
) -> list[DiagnosticResult]:
    results = [
        _pylon_result(),
        _pylon_viewer_result(),
        _serial_port_result(config),
        _zaber_database_result(config),
        _output_root_result(output_root or config.get("dataset", {}).get("output_root")),
    ]
    if check_camera:
        results.append(_camera_detection_result())
    if check_updates:
        results.append(_update_access_result(config, current_version))
    return results


def _pylon_result() -> DiagnosticResult:
    if PYLON_IMPORT_ERROR is None:
        return DiagnosticResult("Basler pylon/Python", "통과", "pypylon import 성공")
    return DiagnosticResult("Basler pylon/Python", "오류", f"pypylon import 실패: {PYLON_IMPORT_ERROR}")


def _pylon_viewer_result() -> DiagnosticResult:
    viewers = running_pylon_viewer_processes()
    if viewers:
        return DiagnosticResult("pylon Viewer", "경고", f"실행 중: {describe_processes(viewers)}")
    return DiagnosticResult("pylon Viewer", "통과", "실행 중인 pylon Viewer 없음")


def _camera_detection_result() -> DiagnosticResult:
    try:
        cameras = enumerate_cameras()
    except Exception as exc:
        return DiagnosticResult("Basler 카메라 탐색", "오류", str(exc))
    if not cameras:
        return DiagnosticResult("Basler 카메라 탐색", "경고", "LAN/USB에서 감지된 Basler 카메라 없음")
    names = ", ".join(
        str(camera.get("model") or camera.get("friendly_name") or camera.get("serial") or "Basler")
        for camera in cameras
    )
    return DiagnosticResult("Basler 카메라 탐색", "통과", f"{len(cameras)}대 감지: {names}")


def _serial_port_result(config: dict[str, Any]) -> DiagnosticResult:
    stage = config.get("stage", {})
    selected = str(stage.get("serial_port") or "COM3")
    ports = list_serial_ports()
    if not ports:
        return DiagnosticResult("Zaber COM 포트", "경고", f"감지된 COM 포트 없음 | 설정값 {selected}")
    port_names = [port["device"] for port in ports]
    status = "통과" if selected in port_names else "경고"
    detail = f"감지: {', '.join(port_names)} | 설정값 {selected}"
    return DiagnosticResult("Zaber COM 포트", status, detail)


def _zaber_database_result(config: dict[str, Any]) -> DiagnosticResult:
    try:
        settings = stage_settings_from_config(config)
        path = configure_zaber_device_database(settings)
    except Exception as exc:
        return DiagnosticResult("Zaber Device DB", "오류", str(exc))
    if path is None:
        return DiagnosticResult("Zaber Device DB", "경고", "공식 Device Database 파일을 찾지 못함")
    return DiagnosticResult("Zaber Device DB", "통과", str(path))


def _output_root_result(output_root: str | Path | None) -> DiagnosticResult:
    path = Path(os.path.expandvars(os.path.expanduser(str(output_root or "output/datasets"))))
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".write_test_", dir=path, delete=False) as file:
            file.write(b"ok")
            temp_path = Path(file.name)
        temp_path.unlink(missing_ok=True)
    except Exception as exc:
        return DiagnosticResult("저장 폴더 권한", "오류", f"{path}: {exc}")
    return DiagnosticResult("저장 폴더 권한", "통과", str(path))


def _update_access_result(config: dict[str, Any], current_version: str) -> DiagnosticResult:
    settings = update_settings_from_config(config)
    if not settings.enabled:
        return DiagnosticResult("GitHub 업데이트", "경고", "업데이트 확인이 설정에서 꺼져 있음")
    try:
        update = fetch_latest_update(settings.repo, current_version, timeout_s=5.0)
    except Exception as exc:
        return DiagnosticResult("GitHub 업데이트", "경고", f"Release 접근 실패: {exc}")
    if update is None:
        return DiagnosticResult("GitHub 업데이트", "통과", f"{settings.repo} 접근 가능 | 최신")
    return DiagnosticResult("GitHub 업데이트", "경고", f"{update.version} 설치 가능")
