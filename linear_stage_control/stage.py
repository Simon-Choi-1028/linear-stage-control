from __future__ import annotations

import time
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from serial.tools import list_ports
from zaber_motion import DeviceDbSourceType, Library, Units
from zaber_motion.ascii import Axis, Connection


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
    y: AxisAddress = AxisAddress(device_index=0, axis_number=2)


class StageMoveCancelled(RuntimeError):
    """Raised when a stage move is stopped through the owning worker."""


def stage_settings_from_config(config: dict[str, Any]) -> StageSettings:
    stage = config.get("stage", {})
    axes = stage.get("axes", {})
    return StageSettings(
        serial_port=str(stage.get("serial_port", "COM3")),
        baud_rate=int(stage.get("baud_rate", 115200)),
        identify_devices=bool(stage.get("identify_devices", True)),
        home_on_start=bool(stage.get("home_on_start", True)),
        settle_s=float(stage.get("settle_s", 0.2)),
        move_velocity_mm_s=_optional_float(stage.get("move_velocity_mm_s")),
        device_db_path=_optional_str(stage.get("device_db_path")),
        use_bundled_device_db=bool(stage.get("use_bundled_device_db", True)),
        x=_axis_address(axes, "x", default_device_index=0),
        y=_axis_address(axes, "y", default_device_index=0, default_axis_number=2),
    )


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
        configure_zaber_device_database(self.settings)
        self.connection = Connection.open_serial_port(
            self.settings.serial_port,
            baud_rate=self.settings.baud_rate,
        )
        self.connection.enable_alerts()
        self.devices = self.connection.detect_devices(
            identify_devices=self.settings.identify_devices
        )
        self.x_axis = self._resolve_axis(self.settings.x)
        self.y_axis = self._resolve_axis(self.settings.y)
        if not self._axes():
            raise RuntimeError("At least one Zaber axis must be enabled.")
        return self

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
        self.connection = None
        self.devices = []
        self.x_axis = None
        self.y_axis = None

    def home(self) -> None:
        for axis in self._axes():
            if not axis.is_homed():
                axis.home()

    def move_absolute_mm(
        self,
        x_mm: float,
        y_mm: float,
        velocity_mm_s: float | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> None:
        active_moves: list[Axis] = []
        move_kwargs = _move_kwargs(velocity_mm_s)
        if self.settings.x.enabled:
            x_axis = self._require_axis(self.x_axis, "X")
            x_axis.move_absolute(x_mm, Units.LENGTH_MILLIMETRES, **move_kwargs)
            active_moves.append(x_axis)
        if self.settings.y.enabled:
            y_axis = self._require_axis(self.y_axis, "Y")
            y_axis.move_absolute(y_mm, Units.LENGTH_MILLIMETRES, **move_kwargs)
            active_moves.append(y_axis)
        if not active_moves:
            raise RuntimeError("At least one Zaber axis must be enabled before moving.")
        self._wait_until_idle(active_moves, cancel_requested)

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
        for axis in self._axes():
            axis.stop(wait_until_idle=wait_until_idle)

    def device_summary(self) -> list[dict[str, Any]]:
        return [
            {
                "index": index,
                "address": getattr(device, "device_address", None),
                "serial_number": getattr(device, "serial_number", None),
                "name": getattr(device, "name", None),
            }
            for index, device in enumerate(self.devices)
        ]

    def _resolve_axis(self, address: AxisAddress) -> Axis | None:
        if not address.enabled:
            return None
        if address.device_index < 0 or address.device_index >= len(self.devices):
            raise RuntimeError(
                f"Zaber device index {address.device_index} was requested, "
                f"but only {len(self.devices)} device(s) were detected."
            )
        return self.devices[address.device_index].get_axis(address.axis_number)

    @staticmethod
    def _require_axis(axis: Axis | None, name: str) -> Axis:
        if axis is None:
            raise RuntimeError(f"{name} axis is not available.")
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
                self.stop(wait_until_idle=False)
                while any(axis.is_busy() for axis in axes):
                    time.sleep(0.05)
                raise StageMoveCancelled("Stage move was cancelled.")
            time.sleep(0.05)


def _axis_address(
    axes: dict[str, Any],
    name: str,
    default_device_index: int,
    default_axis_number: int = 1,
) -> AxisAddress:
    axis = axes.get(name, {})
    return AxisAddress(
        device_index=int(axis.get("device_index", default_device_index)),
        axis_number=int(axis.get("axis_number", default_axis_number)),
        enabled=bool(axis.get("enabled", True)),
    )


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


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


def configure_zaber_device_database(settings: StageSettings) -> Path | None:
    device_db_path = _resolve_device_db_path(settings)
    if device_db_path is None:
        return None
    Library.set_device_db_source(DeviceDbSourceType.FILE, str(device_db_path))
    return device_db_path


def _resolve_device_db_path(settings: StageSettings) -> Path | None:
    candidates: list[Path] = []
    if settings.device_db_path:
        candidates.append(Path(os.path.expandvars(os.path.expanduser(settings.device_db_path))))
    if settings.use_bundled_device_db:
        relative_path = Path("sdk_downloads") / "zaber" / "devices-public-v2.sqlite.lzma"
        candidates.extend(
            [
                Path.cwd() / relative_path,
                Path(getattr(sys, "_MEIPASS", Path.cwd())) / relative_path,
                Path(sys.executable).resolve().parent / "_internal" / relative_path,
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None
