from __future__ import annotations

import logging
import lzma
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from serial.tools import list_ports
from zaber_motion import DeviceDbSourceType, Library, Units
from zaber_motion.ascii import Axis, Connection
from zaber_motion.exceptions import (
    DeviceBusyException,
    DeviceDbFailedException,
    InvalidDataException,
    SerialPortBusyException,
)

from .exceptions import LinearStageControlError, StageConnectionError

DEVICE_DB_SQLITE_NAME = "devices-public-v2.sqlite"
DEVICE_DB_LZMA_NAME = "devices-public-v2.sqlite.lzma"
DEVICE_DB_RELATIVE_DIR = Path("sdk_downloads") / "zaber"
SQLITE_HEADER = b"SQLite format 3\x00"
TRUE_CONFIG_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_CONFIG_VALUES = {"0", "false", "no", "n", "off"}


@dataclass(frozen=True)
class AxisAddress:
    device_index: int
    axis_number: int = 1
    enabled: bool = True


@dataclass
class StageSettings:
    serial_port: str
    baud_rate: int = 115200
    identify_devices: bool = True
    home_on_start: bool = True
    settle_s: float = 0.2
    move_velocity_mm_s: float | None = None
    device_db_path: str | None = None
    use_bundled_device_db: bool = True
    x: AxisAddress = AxisAddress(device_index=0, axis_number=1)
    y: AxisAddress = AxisAddress(device_index=1, axis_number=1)


class StageMoveCancelled(LinearStageControlError):
    """Raised when a stage move is stopped through the owning worker."""


def stage_settings_from_config(config: dict[str, Any]) -> StageSettings:
    stage = config.get("stage") or {}
    if not isinstance(stage, dict):
        raise StageConnectionError(
            "Zaber stage 설정은 객체 형태여야 합니다.",
            f"Invalid stage config: {stage!r}",
        )
    axes = stage.get("axes") or {}
    settle_s = _settle_seconds(stage)
    move_velocity_mm_s = _optional_float(stage.get("move_velocity_mm_s"), "stage.move_velocity_mm_s")
    if settle_s < 0:
        raise StageConnectionError(
            "Zaber 안정화 시간은 0 이상이어야 합니다.",
            f"Invalid stage.settle_s: {settle_s}",
        )
    if move_velocity_mm_s is not None and move_velocity_mm_s <= 0:
        raise StageConnectionError(
            "Zaber 이동속도는 비워두거나 0보다 큰 값이어야 합니다.",
            f"Invalid stage.move_velocity_mm_s: {move_velocity_mm_s}",
        )
    baud_rate = _positive_int(stage.get("baud_rate", 115200), "stage.baud_rate")
    return StageSettings(
        serial_port=str(stage.get("serial_port") or "COM3"),
        baud_rate=baud_rate,
        identify_devices=_bool_value(stage.get("identify_devices", True), True, "stage.identify_devices"),
        home_on_start=_bool_value(stage.get("home_on_start", True), True, "stage.home_on_start"),
        settle_s=settle_s,
        move_velocity_mm_s=move_velocity_mm_s,
        device_db_path=_optional_str(stage.get("device_db_path")),
        use_bundled_device_db=_bool_value(
            stage.get("use_bundled_device_db", True),
            True,
            "stage.use_bundled_device_db",
        ),
        x=_axis_address(axes, "x", default_device_index=0),
        y=_axis_address(axes, "y", default_device_index=1, default_axis_number=1),
    )


def _settle_seconds(stage: dict[str, Any]) -> float:
    try:
        if stage.get("settle_s") not in (None, ""):
            return float(stage.get("settle_s", 0.2))
        if stage.get("settle_ms") not in (None, ""):
            return float(stage["settle_ms"]) / 1000.0
        return 0.2
    except (TypeError, ValueError) as exc:
        raise StageConnectionError(
            "Zaber 안정화 시간은 숫자로 입력해야 합니다.",
            f"Invalid stage settle value: {exc}",
        ) from exc


def list_serial_ports() -> list[dict[str, str]]:
    return [
        {
            "device": port.device,
            "description": port.description,
            "hwid": port.hwid,
        }
        for port in list_ports.comports()
    ]


class ZaberXYStage:
    def __init__(self, settings: StageSettings):
        self.settings = settings
        self.connection: Connection | None = None
        self.devices: list[Any] = []
        self.x_axis: Axis | None = None
        self.y_axis: Axis | None = None

    def __enter__(self) -> ZaberXYStage:
        return self.open()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def open(self) -> ZaberXYStage:
        try:
            configure_zaber_device_database(self.settings)
            try:
                self._open_connection_and_detect_devices()
            except DeviceDbFailedException as exc:
                self.close()
                _configure_zaber_web_service_fallback(f"device detection failed with local Device DB: {exc}")
                self._open_connection_and_detect_devices()
        except StageConnectionError:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise StageConnectionError(
                "Zaber 스테이지 연결 또는 장치 탐색에 실패했습니다.",
                str(exc),
            ) from exc
        return self

    def _open_connection_and_detect_devices(self) -> None:
        self.connection = Connection.open_serial_port(
            self.settings.serial_port,
            baud_rate=self.settings.baud_rate,
        )
        self.connection.enable_alerts()
        self.devices = self.connection.detect_devices(identify_devices=self.settings.identify_devices)
        self.x_axis = self._resolve_axis(self.settings.x, "X")
        self.y_axis = self._resolve_axis(self.settings.y, "Y")
        if not self._axes():
            raise StageConnectionError("최소 하나의 Zaber 축은 활성화되어야 합니다.")

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
        self.connection = None
        self.devices = []
        self.x_axis = None
        self.y_axis = None

    def home(self) -> None:
        try:
            for axis in self._axes():
                if not axis.is_homed():
                    axis.home()
        except StageConnectionError:
            raise
        except Exception as exc:
            raise _stage_command_error(exc, "stage home command failed") from exc

    def move_absolute_mm(
        self,
        x_mm: float,
        y_mm: float,
        velocity_mm_s: float | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> None:
        active_moves: list[Axis] = []
        move_kwargs = _move_kwargs(velocity_mm_s)
        try:
            if self.settings.x.enabled:
                x_axis = self._require_axis(self.x_axis, "X")
                x_axis.move_absolute(x_mm, Units.LENGTH_MILLIMETRES, **move_kwargs)
                active_moves.append(x_axis)
            if self.settings.y.enabled:
                y_axis = self._require_axis(self.y_axis, "Y")
                y_axis.move_absolute(y_mm, Units.LENGTH_MILLIMETRES, **move_kwargs)
                active_moves.append(y_axis)
            if not active_moves:
                raise StageConnectionError("이동하려면 최소 하나의 Zaber 축이 활성화되어야 합니다.")
        except StageConnectionError:
            self._stop_axes(active_moves, wait_until_idle=False, suppress_errors=True)
            raise
        except Exception as exc:
            self._stop_axes(active_moves, wait_until_idle=False, suppress_errors=True)
            raise _stage_command_error(exc, "stage move command failed") from exc
        try:
            self._wait_until_idle(active_moves, cancel_requested)
        except StageMoveCancelled:
            raise
        except Exception as exc:
            self._stop_axes(active_moves, wait_until_idle=False, suppress_errors=True)
            raise _stage_command_error(exc, "stage move wait failed") from exc

    def position_mm(self) -> tuple[float | None, float | None]:
        x_position = (
            self._require_axis(self.x_axis, "X").get_position(Units.LENGTH_MILLIMETRES)
            if self.settings.x.enabled
            else None
        )
        y_position = (
            self._require_axis(self.y_axis, "Y").get_position(Units.LENGTH_MILLIMETRES)
            if self.settings.y.enabled
            else None
        )
        return x_position, y_position

    def stop(self, wait_until_idle: bool = False) -> None:
        self._stop_axes(self._axes(), wait_until_idle=wait_until_idle, suppress_errors=False)

    def device_summary(self) -> list[dict[str, Any]]:
        return [
            {
                "index": index,
                "address": getattr(device, "device_address", None),
                "serial_number": getattr(device, "serial_number", None),
                "name": getattr(device, "name", None),
                "axis_count": _device_axis_count(device),
            }
            for index, device in enumerate(self.devices)
        ]

    def _resolve_axis(self, address: AxisAddress, axis_name: str) -> Axis | None:
        if not address.enabled:
            return None
        address = self._auto_remap_axis(address, axis_name)
        if address.device_index < 0 or address.device_index >= len(self.devices):
            fallback = self._single_device_y_fallback(address, axis_name)
            if fallback is not None:
                address = fallback
        if address.device_index < 0 or address.device_index >= len(self.devices):
            raise StageConnectionError(
                "요청한 Zaber 장치 index를 찾을 수 없습니다. 축 사용 설정과 device index를 확인하세요.",
                (
                    f"Zaber device index {address.device_index} was requested, "
                    f"but only {len(self.devices)} device(s) were detected."
                ),
            )
        device = self.devices[address.device_index]
        axis_count = _device_axis_count(device)
        if axis_count is not None and address.axis_number > axis_count:
            fallback = self._auto_remap_axis(address, axis_name, force=True)
            if fallback != address:
                address = fallback
                device = self.devices[address.device_index]
                axis_count = _device_axis_count(device)
            if axis_count is not None and address.axis_number > axis_count:
                raise StageConnectionError(
                    "Zaber 축 매핑이 실제 장치 구성과 맞지 않습니다. 체인 연결된 단축 X/Y 스테이지는 "
                    "X=device 0 axis 1, Y=device 1 axis 1로 설정하세요.",
                    (
                        f"Zaber {axis_name} axis requested device index {address.device_index} "
                        f"axis {address.axis_number}, but detected device has {axis_count} axis(es). "
                        "For a single two-axis controller use Y=(device 0, axis 2); "
                        "for chained single-axis stages use Y=(device 1, axis 1)."
                    ),
                )
        return device.get_axis(address.axis_number)

    def _auto_remap_axis(self, address: AxisAddress, axis_name: str, *, force: bool = False) -> AxisAddress:
        if axis_name != "Y":
            return address
        if _same_axis(address, device_index=0, axis_number=2):
            fallback = self._chained_y_fallback(address)
            if fallback is not None:
                return fallback
        if force and _same_axis(address, device_index=1, axis_number=1):
            fallback = self._single_device_y_fallback(address, axis_name)
            if fallback is not None:
                return fallback
        return address

    def _chained_y_fallback(self, address: AxisAddress) -> AxisAddress | None:
        if len(self.devices) < 2:
            return None
        first_axis_count = _device_axis_count(self.devices[0])
        second_axis_count = _device_axis_count(self.devices[1])
        if first_axis_count == 1 and second_axis_count is not None and second_axis_count >= 1:
            logging.getLogger("linear_stage_control.stage").warning(
                "remapping legacy Y axis device 0 axis 2 to chained device 1 axis 1"
            )
            return AxisAddress(device_index=1, axis_number=1, enabled=address.enabled)
        return None

    def _single_device_y_fallback(self, address: AxisAddress, axis_name: str) -> AxisAddress | None:
        if axis_name != "Y" or len(self.devices) != 1:
            return None
        first_axis_count = _device_axis_count(self.devices[0])
        if first_axis_count is not None and first_axis_count >= 2:
            logging.getLogger("linear_stage_control.stage").warning(
                "remapping default chained Y axis device 1 axis 1 to single-controller device 0 axis 2"
            )
            return AxisAddress(device_index=0, axis_number=2, enabled=address.enabled)
        return None

    @staticmethod
    def _require_axis(axis: Axis | None, name: str) -> Axis:
        if axis is None:
            raise StageConnectionError(f"{name}축을 사용할 수 없습니다. 축 활성화와 Zaber 연결을 확인하세요.")
        return axis

    def _axes(self) -> tuple[Axis, ...]:
        axes: list[Axis] = []
        if self.settings.x.enabled and self.x_axis is not None:
            axes.append(self.x_axis)
        if self.settings.y.enabled and self.y_axis is not None:
            axes.append(self.y_axis)
        return tuple(axes)

    def _wait_until_idle(
        self,
        axes: list[Axis],
        cancel_requested: Callable[[], bool] | None,
    ) -> None:
        while True:
            busy_axes = [axis for axis in axes if axis.is_busy()]
            if not busy_axes:
                return
            if cancel_requested is not None and cancel_requested():
                self._stop_axes(axes, wait_until_idle=False, suppress_errors=False)
                while any(axis.is_busy() for axis in axes):
                    time.sleep(0.05)
                raise StageMoveCancelled("사용자 중지 요청으로 스테이지 이동을 취소했습니다.")
            time.sleep(0.05)

    def _stop_axes(
        self,
        axes: tuple[Axis, ...] | list[Axis],
        *,
        wait_until_idle: bool,
        suppress_errors: bool,
    ) -> None:
        errors: list[str] = []
        for axis in axes:
            try:
                axis.stop(wait_until_idle=wait_until_idle)
            except Exception as exc:
                errors.append(str(exc))
                logging.getLogger("linear_stage_control.stage").warning(
                    "zaber axis stop failed: %s",
                    exc,
                )
                if not suppress_errors:
                    break
        if errors and not suppress_errors:
            raise StageConnectionError(
                "Zaber 정지 명령을 완료하지 못했습니다. 장비 전원과 COM 포트 점유 상태를 확인하세요.",
                "; ".join(errors),
            )


def _axis_address(
    axes: dict[str, Any],
    name: str,
    default_device_index: int,
    default_axis_number: int = 1,
) -> AxisAddress:
    if not isinstance(axes, dict):
        raise StageConnectionError(
            "Zaber axes 설정은 x/y 항목을 가진 객체여야 합니다.",
            f"Invalid stage.axes: {axes!r}",
        )
    axis = axes.get(name, {})
    if axis is None:
        axis = {}
    if not isinstance(axis, dict):
        raise StageConnectionError(
            f"Zaber {name.upper()}축 설정은 객체여야 합니다.",
            f"Invalid stage.axes.{name}: {axis!r}",
        )
    device_index = _non_negative_int(axis.get("device_index", default_device_index), f"stage.axes.{name}.device_index")
    axis_number = _positive_int(axis.get("axis_number", default_axis_number), f"stage.axes.{name}.axis_number")
    return AxisAddress(
        device_index=device_index,
        axis_number=axis_number,
        enabled=_bool_value(axis.get("enabled", True), True, f"stage.axes.{name}.enabled"),
    )


def _optional_float(value: Any, field_name: str = "value") -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise StageConnectionError(
            f"{field_name} 값은 숫자로 입력해야 합니다.",
            f"Invalid float for {field_name}: {value!r}",
        ) from exc


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _move_kwargs(velocity_mm_s: float | None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"wait_until_idle": False}
    if velocity_mm_s is not None:
        kwargs["velocity"] = float(velocity_mm_s)
        kwargs["velocity_unit"] = Units.VELOCITY_MILLIMETRES_PER_SECOND
    return kwargs


def _same_axis(address: AxisAddress, *, device_index: int, axis_number: int) -> bool:
    return address.device_index == device_index and address.axis_number == axis_number


def _device_axis_count(device: Any) -> int | None:
    try:
        value = getattr(device, "axis_count")
    except Exception:
        return None
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_value(value: Any, default: bool, field_name: str) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, float) and value in (0.0, 1.0):
        return bool(value)
    text = str(value).strip().lower()
    if text in TRUE_CONFIG_VALUES:
        return True
    if text in FALSE_CONFIG_VALUES:
        return False
    raise StageConnectionError(
        f"{field_name} 설정은 true 또는 false로 입력해야 합니다.",
        f"Invalid boolean for {field_name}: {value!r}",
    )


def _positive_int(value: Any, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise StageConnectionError(
            f"{field_name} 값은 정수로 입력해야 합니다.",
            f"Invalid integer for {field_name}: {value!r}",
        ) from exc
    if parsed <= 0:
        raise StageConnectionError(
            f"{field_name} 값은 1 이상이어야 합니다.",
            f"Invalid positive integer for {field_name}: {parsed}",
        )
    return parsed


def _non_negative_int(value: Any, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise StageConnectionError(
            f"{field_name} 값은 정수로 입력해야 합니다.",
            f"Invalid integer for {field_name}: {value!r}",
        ) from exc
    if parsed < 0:
        raise StageConnectionError(
            f"{field_name} 값은 0 이상이어야 합니다.",
            f"Invalid non-negative integer for {field_name}: {parsed}",
        )
    return parsed


def _stage_command_error(exc: Exception, developer_context: str) -> StageConnectionError:
    if isinstance(exc, InvalidDataException) and "Response device or axis does not match" in str(exc):
        return StageConnectionError(
            "Zaber 응답 축이 요청 축과 다릅니다. 체인 연결된 단축 X/Y 스테이지는 "
            "X=device 0 axis 1, Y=device 1 axis 1로 설정하세요.",
            f"{developer_context}: {exc}",
        )
    if isinstance(exc, SerialPortBusyException):
        return StageConnectionError(
            "Zaber COM 포트가 사용 중입니다. 실행 중인 run은 앱의 중지 버튼으로 정지하고, "
            "Zaber Launcher 등 같은 포트를 쓰는 프로그램을 닫은 뒤 다시 시도하세요.",
            f"{developer_context}: {exc}",
        )
    if isinstance(exc, DeviceBusyException):
        return StageConnectionError(
            "Zaber 장치가 이전 명령을 처리 중입니다. 잠시 기다리거나 중지 버튼으로 현재 이동을 멈춘 뒤 다시 시도하세요.",
            f"{developer_context}: {exc}",
        )
    return StageConnectionError(
        "Zaber 스테이지 명령 처리 중 오류가 발생했습니다. 연결, 전원, 축 활성화 설정을 확인하세요.",
        f"{developer_context}: {exc}",
    )


def configure_zaber_device_database(settings: StageSettings) -> Path | None:
    device_db_path = _resolve_device_db_path(settings)
    if device_db_path is None:
        return None
    try:
        Library.set_device_db_source(DeviceDbSourceType.FILE, str(device_db_path))
    except Exception as exc:
        _configure_zaber_web_service_fallback(f"local Device DB failed: {exc}")
        return None
    return device_db_path


def _resolve_device_db_path(settings: StageSettings) -> Path | None:
    candidates: list[Path] = []
    if settings.device_db_path:
        candidates.append(Path(os.path.expandvars(os.path.expanduser(settings.device_db_path))))
    if settings.use_bundled_device_db:
        sqlite_relative_path = DEVICE_DB_RELATIVE_DIR / DEVICE_DB_SQLITE_NAME
        lzma_relative_path = DEVICE_DB_RELATIVE_DIR / DEVICE_DB_LZMA_NAME
        base_dirs = [
            Path.cwd(),
            Path(getattr(sys, "_MEIPASS", Path.cwd())),
            Path(sys.executable).resolve().parent / "_internal",
        ]
        candidates.extend(
            [base_dir / sqlite_relative_path for base_dir in base_dirs]
            + [base_dir / lzma_relative_path for base_dir in base_dirs]
        )
    invalid_reasons: list[str] = []
    logger = logging.getLogger("linear_stage_control.stage")
    for candidate in _dedupe_paths(candidates):
        if candidate.exists():
            try:
                return prepare_zaber_device_db_path(candidate)
            except StageConnectionError as exc:
                invalid_reasons.append(exc.developer_message)
                logger.warning("skipping invalid zaber device database candidate: %s", exc.developer_message)
                continue
    if invalid_reasons:
        _configure_zaber_web_service_fallback("; ".join(invalid_reasons))
    return None


def prepare_zaber_device_db_path(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.exists():
        raise StageConnectionError(
            "Zaber Device Database 파일을 찾을 수 없습니다.",
            f"Missing Zaber Device Database: {candidate}",
        )
    if candidate.name.lower().endswith(".sqlite.lzma"):
        return _decompress_zaber_device_db(candidate)
    if candidate.suffix.lower() == ".sqlite":
        resolved = candidate.resolve()
        if _validate_sqlite_device_db(resolved):
            return resolved
        raise StageConnectionError(
            "Zaber Device Database 파일이 올바른 SQLite DB가 아닙니다.",
            f"Invalid Zaber Device Database header: {resolved}",
        )
    raise StageConnectionError(
        "Zaber Device Database는 .sqlite 또는 .sqlite.lzma 파일이어야 합니다.",
        f"Unsupported Zaber Device Database path: {candidate}",
    )


def _validate_sqlite_device_db(path: str | Path) -> bool:
    try:
        candidate = Path(path)
        if not candidate.is_file():
            return False
        with candidate.open("rb") as file:
            return file.read(len(SQLITE_HEADER)) == SQLITE_HEADER
    except OSError:
        return False


def _decompress_zaber_device_db(path: Path) -> Path:
    source = path.resolve()
    cache_path = _zaber_device_db_cache_path()
    if _validate_sqlite_device_db(cache_path) and cache_path.stat().st_mtime >= source.stat().st_mtime:
        return cache_path
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = cache_path.with_name(cache_path.name + ".tmp")
    try:
        with lzma.open(source, "rb") as source_file, temp_path.open("wb") as target_file:
            while True:
                chunk = source_file.read(1024 * 1024)
                if not chunk:
                    break
                target_file.write(chunk)
        if not _validate_sqlite_device_db(temp_path):
            raise StageConnectionError(
                "Zaber Device Database 압축 해제 결과가 올바른 SQLite DB가 아닙니다.",
                f"Invalid SQLite header after decompressing {source}",
            )
        temp_path.replace(cache_path)
    except StageConnectionError:
        temp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        raise StageConnectionError(
            "Zaber Device Database 압축 해제에 실패했습니다.",
            f"Failed to decompress {source}: {exc}",
        ) from exc
    return cache_path


def _zaber_device_db_cache_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / "LinearStageControl" / "zaber" / DEVICE_DB_SQLITE_NAME


def _configure_zaber_web_service_fallback(reason: str) -> None:
    logger = logging.getLogger("linear_stage_control.stage")
    try:
        Library.set_device_db_source(DeviceDbSourceType.WEB_SERVICE)
    except Exception:
        logger.exception("zaber device database web service fallback failed")
        return
    logger.warning("zaber device database web service fallback enabled: %s", reason)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result
