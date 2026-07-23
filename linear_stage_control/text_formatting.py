from __future__ import annotations

import math
from typing import Any

from .position_validation import POSITION_MAX_MM, POSITION_MIN_MM
from .scan import ScanPoint


def optional_float_text(text: str) -> float | None:
    clean = text.strip()
    if not clean:
        return None
    return float(clean)


def optional_int_text(text: str) -> int | None:
    clean = text.strip()
    if not clean:
        return None
    number = float(clean)
    if not number.is_integer():
        raise ValueError(f"정수로 입력해야 합니다: {text}")
    return int(number)


def safe_float_text(text: str, default: float) -> float:
    try:
        return float(text.strip())
    except (TypeError, ValueError):
        return default


def compact_number_text(value: Any, max_decimals: int) -> str:
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number) < 10 ** (-max_decimals):
        number = 0.0
    text = f"{number:.{max_decimals}f}".rstrip("0").rstrip(".")
    return text or "0"


def mm_text(value: Any) -> str:
    return compact_number_text(value, 4)


def um_text(value: Any) -> str:
    return compact_number_text(value, 2)


def number_text(value: Any) -> str:
    return compact_number_text(value, 3)


def settle_display_text(seconds: float) -> str:
    if seconds < 1:
        return f"{number_text(seconds * 1000)} ms"
    return f"{number_text(seconds)} s"


def stage_settle_seconds_from_config(stage: dict[str, Any]) -> float:
    if stage.get("settle_s") not in (None, ""):
        return float(stage.get("settle_s", 0.2))
    if stage.get("settle_ms") not in (None, ""):
        return float(stage["settle_ms"]) / 1000.0
    return 0.2


def velocity_text(value: Any, default_text: str = "기본값") -> str:
    if value in (None, ""):
        return default_text
    return f"{number_text(value)} mm/s"


def capture_sequence_text(record: dict[str, Any]) -> str:
    capture_index = record.get("capture_index", "")
    capture_count = record.get("capture_count", "")
    if capture_index == "" and capture_count == "":
        return "-"
    if capture_index == "":
        return f"-/{capture_count}"
    if capture_count == "":
        return str(capture_index)
    return f"{capture_index}/{capture_count}"


def status_text(value: Any) -> str:
    mapping = {
        "ok": "완료",
        "error": "오류",
        "pending": "대기",
        "stopped": "중지",
    }
    return mapping.get(str(value), str(value))


def threshold_text(record: dict[str, Any]) -> str:
    if record.get("status") == "error":
        return "오류"
    if record.get("within_error_threshold") is True:
        return "통과"
    if record.get("within_error_threshold") is False:
        return "초과"
    return "확인"


def linear_distance(x_start: float, y_start: float, x_stop: float, y_stop: float) -> float:
    return math.hypot(x_stop - x_start, y_stop - y_start)


def position_cell_tooltip(column: int) -> str:
    tooltips = {
        1: "위치 라벨입니다. 비워도 실행은 가능하지만 구분하기 어려울 수 있습니다.",
        2: f"X 좌표입니다. 허용 범위: {mm_text(POSITION_MIN_MM)}-{mm_text(POSITION_MAX_MM)} mm",
        3: f"Y 좌표입니다. 허용 범위: {mm_text(POSITION_MIN_MM)}-{mm_text(POSITION_MAX_MM)} mm",
        4: "위치별 이동속도입니다. 비우면 촬영 설정의 이동속도를 사용합니다.",
        5: "위치별 캡처 수입니다. 비우면 촬영 설정의 기본 캡처 수를 사용합니다.",
    }
    return tooltips.get(column, "")


def point_config_record(point: ScanPoint) -> dict[str, Any]:
    record: dict[str, Any] = {"label": point.label, "x_mm": point.x_mm, "y_mm": point.y_mm}
    if point.move_velocity_mm_s is not None:
        record["move_velocity_mm_s"] = point.move_velocity_mm_s
    if point.capture_count is not None:
        record["capture_count"] = point.capture_count
    return record


def camera_display_name(camera: dict[str, str]) -> str:
    return " | ".join(
        item
        for item in (
            camera.get("model", ""),
            camera.get("serial", ""),
            camera.get("ip", ""),
            camera.get("device_class", ""),
        )
        if item
    )


def camera_signature(cameras: list[dict[str, str]]) -> tuple[str, ...]:
    return tuple(
        sorted(
            "|".join(
                (
                    camera.get("serial", ""),
                    camera.get("ip", ""),
                    camera.get("model", ""),
                    camera.get("device_class", ""),
                )
            )
            for camera in cameras
        )
    )
