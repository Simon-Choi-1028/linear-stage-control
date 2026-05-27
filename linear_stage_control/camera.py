from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from pypylon import pylon

from .config import none_if_blank


PIXEL_TYPES: dict[str, int] = {
    "Mono8": pylon.PixelType_Mono8,
    "BGR8": pylon.PixelType_BGR8packed,
    "RGB8": pylon.PixelType_RGB8packed,
}


@dataclass
class CameraSettings:
    serial_number: str | None = None
    user_defined_name: str | None = None
    pixel_format: str | None = "Mono8"
    exposure_us: float | None = None
    gain: float | None = None
    trigger_mode: str | None = "Off"
    trigger_selector: str = "FrameStart"
    trigger_source: str = "Software"
    use_software_trigger: bool = False
    output_pixel_format: str = "Mono8"
    timeout_ms: int = 5000


def camera_settings_from_config(config: dict[str, Any]) -> CameraSettings:
    camera = config.get("camera", {})
    return CameraSettings(
        serial_number=none_if_blank(camera.get("serial_number")),
        user_defined_name=none_if_blank(camera.get("user_defined_name")),
        pixel_format=none_if_blank(camera.get("pixel_format", "Mono8")),
        exposure_us=camera.get("exposure_us"),
        gain=camera.get("gain"),
        trigger_mode=none_if_blank(camera.get("trigger_mode", "Off")),
        trigger_selector=str(camera.get("trigger_selector", "FrameStart")),
        trigger_source=str(camera.get("trigger_source", "Software")),
        use_software_trigger=bool(camera.get("use_software_trigger", False)),
        output_pixel_format=camera.get("output_pixel_format", "Mono8"),
        timeout_ms=int(camera.get("timeout_ms", 5000)),
    )


@dataclass(frozen=True)
class CaptureResult:
    image_path: Path
    npy_path: Path | None
    captured_at: str
    completed_at: str
    dtype: str
    shape: tuple[int, ...]
    pixel_type: str | None
    width: int | None
    height: int | None
    camera_timestamp_ns: int | None
    block_id: int | None


def enumerate_cameras() -> list[dict[str, str]]:
    devices = pylon.TlFactory.GetInstance().EnumerateDevices()
    return [_device_info_to_dict(device) for device in devices]


def _device_info_to_dict(device_info: Any) -> dict[str, str]:
    fields = {
        "model": "GetModelName",
        "serial": "GetSerialNumber",
        "user_name": "GetUserDefinedName",
        "device_class": "GetDeviceClass",
        "friendly_name": "GetFriendlyName",
        "full_name": "GetFullName",
        "ip": "GetIpAddress",
        "mac": "GetMacAddress",
    }
    result: dict[str, str] = {}
    for key, method_name in fields.items():
        method = getattr(device_info, method_name, None)
        if method is None:
            continue
        try:
            value = method()
        except Exception:
            continue
        if value:
            result[key] = str(value)
    return result


@dataclass
class BaslerCamera:
    settings: CameraSettings
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.camera: pylon.InstantCamera | None = None
        self.converter: pylon.ImageFormatConverter | None = None

    def __enter__(self) -> BaslerCamera:
        return self.open()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def open(self) -> BaslerCamera:
        device_info = self._select_device()
        self.camera = pylon.InstantCamera(
            pylon.TlFactory.GetInstance().CreateDevice(device_info)
        )
        self.camera.Open()
        self._apply_settings()
        self.converter = pylon.ImageFormatConverter()
        self.converter.OutputPixelFormat = self._output_pixel_type()
        self.converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned
        return self

    def close(self) -> None:
        if self.camera is not None and self.camera.IsOpen():
            self.camera.Close()
        self.camera = None
        self.converter = None

    def grab_array(self, timeout_ms: int | None = None) -> np.ndarray:
        if self.camera is None or self.converter is None:
            raise RuntimeError("Camera is not open.")

        timeout = timeout_ms or self.settings.timeout_ms
        self.camera.StartGrabbingMax(1)
        grab_result = self.camera.RetrieveResult(
            timeout, pylon.TimeoutHandling_ThrowException
        )
        try:
            if not grab_result.GrabSucceeded():
                raise RuntimeError(
                    f"Grab failed: {grab_result.ErrorCode} {grab_result.ErrorDescription}"
                )
            image = self.converter.Convert(grab_result)
            return image.GetArray().copy()
        finally:
            grab_result.Release()

    def capture_to(self, output_path: str | Path) -> Path:
        array = self.grab_array()
        path = Path(output_path)
        save_array(path, array, self.settings.output_pixel_format)
        return path

    def capture_original_to(
        self,
        output_path: str | Path,
        npy_path: str | Path | None = None,
        timeout_ms: int | None = None,
    ) -> CaptureResult:
        """Capture one frame and save the unconverted camera array losslessly."""
        array, metadata = self.grab_original_array(timeout_ms=timeout_ms)
        image_path = Path(output_path)
        save_original_array(image_path, array)

        saved_npy_path: Path | None = None
        if npy_path is not None:
            saved_npy_path = Path(npy_path)
            saved_npy_path.parent.mkdir(parents=True, exist_ok=True)
            np.save(saved_npy_path, array)

        return CaptureResult(
            image_path=image_path,
            npy_path=saved_npy_path,
            captured_at=metadata["captured_at"],
            completed_at=metadata["completed_at"],
            dtype=str(array.dtype),
            shape=tuple(int(item) for item in array.shape),
            pixel_type=metadata.get("pixel_type"),
            width=metadata.get("width"),
            height=metadata.get("height"),
            camera_timestamp_ns=metadata.get("camera_timestamp_ns"),
            block_id=metadata.get("block_id"),
        )

    def grab_original_array(
        self,
        timeout_ms: int | None = None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        if self.camera is None:
            raise RuntimeError("Camera is not open.")

        timeout = timeout_ms or self.settings.timeout_ms
        self.camera.StartGrabbingMax(1)
        captured_at = iso_timestamp()
        try:
            if self.settings.use_software_trigger:
                self.camera.WaitForFrameTriggerReady(
                    timeout, pylon.TimeoutHandling_ThrowException
                )
                captured_at = iso_timestamp()
                self.camera.ExecuteSoftwareTrigger()

            grab_result = self.camera.RetrieveResult(
                timeout, pylon.TimeoutHandling_ThrowException
            )
            completed_at = iso_timestamp()
            try:
                if not grab_result.GrabSucceeded():
                    raise RuntimeError(
                        f"Grab failed: {grab_result.ErrorCode} "
                        f"{grab_result.ErrorDescription}"
                    )
                array = grab_result.GetArray().copy()
                metadata = {
                    "captured_at": captured_at,
                    "completed_at": completed_at,
                    "pixel_type": _grab_value(grab_result, "PixelType"),
                    "width": _grab_int(grab_result, "Width"),
                    "height": _grab_int(grab_result, "Height"),
                    "camera_timestamp_ns": _grab_int(grab_result, "TimeStamp"),
                    "block_id": _grab_int(grab_result, "BlockID"),
                }
                return array, metadata
            finally:
                grab_result.Release()
        finally:
            if self.camera is not None and self.camera.IsGrabbing():
                self.camera.StopGrabbing()

    def _select_device(self) -> Any:
        devices = pylon.TlFactory.GetInstance().EnumerateDevices()
        if not devices:
            raise RuntimeError("No Basler camera was detected.")

        if self.settings.serial_number:
            for device in devices:
                if _safe_device_value(device, "GetSerialNumber") == self.settings.serial_number:
                    return device
            raise RuntimeError(f"Basler camera serial not found: {self.settings.serial_number}")

        if self.settings.user_defined_name:
            for device in devices:
                if (
                    _safe_device_value(device, "GetUserDefinedName")
                    == self.settings.user_defined_name
                ):
                    return device
            raise RuntimeError(
                f"Basler camera user-defined name not found: {self.settings.user_defined_name}"
            )

        return devices[0]

    def _apply_settings(self) -> None:
        if self.camera is None:
            raise RuntimeError("Camera is not open.")

        if self.settings.pixel_format:
            self._set_feature("PixelFormat", self.settings.pixel_format)
        if self.settings.use_software_trigger:
            self._set_feature("TriggerSelector", self.settings.trigger_selector)
            self._set_feature("TriggerMode", "On")
            self._set_feature("TriggerSource", self.settings.trigger_source)
        elif self.settings.trigger_mode:
            self._set_feature("TriggerMode", self.settings.trigger_mode)
        if self.settings.exposure_us is not None:
            self._set_feature("ExposureAuto", "Off")
            self._set_feature("ExposureTime", float(self.settings.exposure_us))
        if self.settings.gain is not None:
            self._set_feature("GainAuto", "Off")
            self._set_feature("Gain", float(self.settings.gain))

    def _set_feature(self, feature_name: str, value: Any) -> None:
        if self.camera is None:
            raise RuntimeError("Camera is not open.")
        try:
            feature = getattr(self.camera, feature_name)
            feature.SetValue(value)
        except Exception as exc:
            self.warnings.append(f"{feature_name}: {exc}")

    def _output_pixel_type(self) -> int:
        try:
            return PIXEL_TYPES[self.settings.output_pixel_format]
        except KeyError as exc:
            supported = ", ".join(sorted(PIXEL_TYPES))
            raise ValueError(
                f"Unsupported output_pixel_format {self.settings.output_pixel_format!r}. "
                f"Supported values: {supported}"
            ) from exc


def _safe_device_value(device_info: Any, method_name: str) -> str | None:
    method = getattr(device_info, method_name, None)
    if method is None:
        return None
    try:
        value = method()
    except Exception:
        return None
    return str(value) if value else None


def save_array(path: Path, array: np.ndarray, pixel_format: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if array.ndim == 2:
        image = Image.fromarray(array)
    elif array.ndim == 3 and array.shape[2] == 3 and pixel_format == "BGR8":
        image = Image.fromarray(np.ascontiguousarray(array[:, :, ::-1]), "RGB")
    elif array.ndim == 3 and array.shape[2] == 3:
        image = Image.fromarray(np.ascontiguousarray(array), "RGB")
    else:
        raise ValueError(f"Unsupported image array shape: {array.shape}")
    image.save(path)


def save_original_array(path: Path, array: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if array.ndim == 2:
        image = Image.fromarray(np.ascontiguousarray(array))
    elif array.ndim == 3 and array.shape[2] in (3, 4):
        image = Image.fromarray(np.ascontiguousarray(array))
    else:
        raise ValueError(f"Unsupported original image array shape: {array.shape}")
    image.save(path)


def iso_timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _grab_value(grab_result: Any, name: str) -> str | None:
    value = getattr(grab_result, name, None)
    if callable(value):
        try:
            value = value()
        except Exception:
            return None
    return str(value) if value is not None else None


def _grab_int(grab_result: Any, name: str) -> int | None:
    value = _grab_value(grab_result, name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
