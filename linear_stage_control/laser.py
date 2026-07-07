from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

import serial

from .exceptions import LaserConnectionError

DEFAULT_LASER_PORT = "COM5"
DEFAULT_LASER_BAUD_RATE = 9600
DEFAULT_LASER_RESPONSE_TIMEOUT_S = 1.0
LASER_PERCENT_MIN = 0
LASER_PERCENT_MAX = 100


@dataclass(frozen=True)
class LaserSettings:
    serial_port: str = DEFAULT_LASER_PORT
    baud_rate: int = DEFAULT_LASER_BAUD_RATE
    expect_response: bool = True
    response_timeout_s: float = DEFAULT_LASER_RESPONSE_TIMEOUT_S


@dataclass(frozen=True)
class LaserCommandResult:
    percent: int
    command: bytes
    response: str | None = None


def laser_settings_from_config(config: dict[str, Any]) -> LaserSettings:
    laser = config.get("laser") or {}
    if not isinstance(laser, dict):
        raise LaserConnectionError(
            "Laser RS485 settings must be a mapping.",
            f"Invalid laser config: {laser!r}",
        )
    return LaserSettings(
        serial_port=_optional_str(laser.get("serial_port")) or DEFAULT_LASER_PORT,
        baud_rate=_positive_int(laser.get("baud_rate", DEFAULT_LASER_BAUD_RATE), "laser.baud_rate"),
        expect_response=_bool_value(laser.get("expect_response", True), True, "laser.expect_response"),
        response_timeout_s=_positive_float(
            laser.get("response_timeout_s", DEFAULT_LASER_RESPONSE_TIMEOUT_S),
            "laser.response_timeout_s",
        ),
    )


def laser_percent_from_config(config: dict[str, Any]) -> int:
    laser = config.get("laser") or {}
    if not isinstance(laser, dict):
        raise LaserConnectionError(
            "Laser RS485 settings must be a mapping.",
            f"Invalid laser config: {laser!r}",
        )
    return parse_laser_percent(laser.get("percent", 0), "laser.percent")


def parse_laser_percent(value: Any, field_name: str = "laser percent") -> int:
    try:
        percent = int(value)
    except (TypeError, ValueError) as exc:
        raise LaserConnectionError(
            f"{field_name} must be an integer from 0 to 100.",
            f"Invalid {field_name}: {value!r}",
        ) from exc
    if percent < LASER_PERCENT_MIN or percent > LASER_PERCENT_MAX:
        raise LaserConnectionError(
            f"{field_name} must be between 0 and 100 percent.",
            f"Out-of-range {field_name}: {percent}",
        )
    return percent


def open_laser_serial(settings: LaserSettings) -> serial.Serial:
    try:
        return serial.Serial(
            port=settings.serial_port,
            baudrate=settings.baud_rate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=settings.response_timeout_s,
            write_timeout=1.0,
        )
    except serial.SerialException as exc:
        raise LaserConnectionError(
            "Laser RS485 port could not be opened.",
            f"Failed to open laser port {settings.serial_port}: {exc}",
        ) from exc


def send_laser_command(ser: Any, percent: int) -> bytes:
    percent = parse_laser_percent(percent)
    command = f"L{percent}\n".encode("ascii")
    try:
        ser.write(command)
        ser.flush()
    except serial.SerialException as exc:
        raise LaserConnectionError(
            "Laser RS485 command could not be sent.",
            f"Failed to send {command!r}: {exc}",
        ) from exc
    return command


def read_response_line(
    ser: Any,
    ignored_lines: set[str] | None = None,
    response_timeout_s: float | None = None,
) -> str | None:
    ignored_lines = ignored_lines or set()
    original_timeout = ser.timeout
    timeout_s = response_timeout_s if response_timeout_s is not None else original_timeout
    deadline = time.monotonic() + float(timeout_s if timeout_s is not None else DEFAULT_LASER_RESPONSE_TIMEOUT_S)
    try:
        while True:
            remaining_s = deadline - time.monotonic()
            if remaining_s <= 0:
                return None
            ser.timeout = remaining_s
            response = ser.readline()
            if not response:
                return None
            response_text = response.decode("ascii", errors="replace").strip()
            if response_text in ignored_lines:
                continue
            return response_text
    except serial.SerialException as exc:
        raise LaserConnectionError(
            "Laser RS485 response could not be read.",
            f"Failed to read laser response: {exc}",
        ) from exc
    finally:
        ser.timeout = original_timeout


def send_laser_percent(settings: LaserSettings, percent: int) -> LaserCommandResult:
    percent = parse_laser_percent(percent)
    with open_laser_serial(settings) as ser:
        command = send_laser_command(ser, percent)
        response = None
        if settings.expect_response:
            response = read_response_line(
                ser,
                {command.decode("ascii").strip()},
                settings.response_timeout_s,
            )
    return LaserCommandResult(percent=percent, command=command, response=response)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _positive_int(value: Any, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise LaserConnectionError(
            f"{field_name} must be an integer.",
            f"Invalid integer for {field_name}: {value!r}",
        ) from exc
    if parsed <= 0:
        raise LaserConnectionError(
            f"{field_name} must be 1 or greater.",
            f"Invalid positive integer for {field_name}: {parsed}",
        )
    return parsed


def _positive_float(value: Any, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise LaserConnectionError(
            f"{field_name} must be a number.",
            f"Invalid float for {field_name}: {value!r}",
        ) from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise LaserConnectionError(
            f"{field_name} must be greater than 0.",
            f"Invalid positive float for {field_name}: {parsed}",
        )
    return parsed


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
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise LaserConnectionError(
        f"{field_name} must be true or false.",
        f"Invalid boolean for {field_name}: {value!r}",
    )
