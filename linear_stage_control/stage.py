from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from serial.tools import list_ports
from zaber_motion import Units
from zaber_motion.ascii import Axis, Connection


@dataclass(frozen=True)
class AxisAddress:
    device_index: int
    axis_number: int = 1


@dataclass
class StageSettings:
    serial_port: str
    baud_rate: int = 115200
    identify_devices: bool = True
    home_on_start: bool = True
    settle_s: float = 0.2
    move_velocity_mm_s: float | None = None
    x: AxisAddress = AxisAddress(device_index=0, axis_number=1)
    y: AxisAddress = AxisAddress(device_index=1, axis_number=1)


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
        x=_axis_address(axes, "x", default_device_index=0),
        y=_axis_address(axes, "y", default_device_index=1),
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
    ) -> None:
        x_axis, y_axis = self._require_axes()
        velocity = float(velocity_mm_s or 0)
        x_axis.move_absolute(
            x_mm,
            Units.LENGTH_MILLIMETRES,
            wait_until_idle=False,
            velocity=velocity,
            velocity_unit=Units.VELOCITY_MILLIMETRES_PER_SECOND,
        )
        y_axis.move_absolute(
            y_mm,
            Units.LENGTH_MILLIMETRES,
            wait_until_idle=False,
            velocity=velocity,
            velocity_unit=Units.VELOCITY_MILLIMETRES_PER_SECOND,
        )
        x_axis.wait_until_idle()
        y_axis.wait_until_idle()

    def position_mm(self) -> tuple[float, float]:
        x_axis, y_axis = self._require_axes()
        return (
            x_axis.get_position(Units.LENGTH_MILLIMETRES),
            y_axis.get_position(Units.LENGTH_MILLIMETRES),
        )

    def stop(self) -> None:
        for axis in self._axes():
            axis.stop()

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

    def _resolve_axis(self, address: AxisAddress) -> Axis:
        if address.device_index < 0 or address.device_index >= len(self.devices):
            raise RuntimeError(
                f"Zaber device index {address.device_index} was requested, "
                f"but only {len(self.devices)} device(s) were detected."
            )
        return self.devices[address.device_index].get_axis(address.axis_number)

    def _require_axes(self) -> tuple[Axis, Axis]:
        if self.x_axis is None or self.y_axis is None:
            raise RuntimeError("Stage is not open.")
        return self.x_axis, self.y_axis

    def _axes(self) -> tuple[Axis, Axis]:
        return self._require_axes()


def _axis_address(
    axes: dict[str, Any],
    name: str,
    default_device_index: int,
) -> AxisAddress:
    axis = axes.get(name, {})
    return AxisAddress(
        device_index=int(axis.get("device_index", default_device_index)),
        axis_number=int(axis.get("axis_number", 1)),
    )


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
