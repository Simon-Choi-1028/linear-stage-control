from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

try:
    from pypylon import pylon
except Exception as exc:  # pragma: no cover - exercised on runtime-missing PCs.
    pylon = None
    PYLON_IMPORT_ERROR: Exception | None = exc
else:
    PYLON_IMPORT_ERROR = None

from .config import none_if_blank
from .exceptions import CameraConnectionError, DatasetWriteError

DEFAULT_PIXEL_FORMAT_CANDIDATES = (
    "Mono8",
    "Mono10",
    "Mono12",
    "Mono16",
    "BayerRG8",
    "BayerGB8",
    "BayerGR8",
    "BayerBG8",
    "RGB8",
    "BGR8",
)

OUTPUT_PIXEL_TYPE_ALIASES = {
    "Mono8": "Mono8",
    "Mono10": "Mono16",
    "Mono12": "Mono16",
    "Mono16": "Mono16",
    "RGB8": "RGB8packed",
    "BGR8": "BGR8packed",
    "RGBA8": "RGBA8packed",
    "BGRA8": "BGRA8packed",
}


@dataclass
class CameraSettings:
    serial_number: str | None = None
    user_defined_name: str | None = None
    model_name: str | None = None
    device_class: str | None = None
    pixel_format: str | None = "Mono8"
    pixel_format_candidates: tuple[str, ...] = DEFAULT_PIXEL_FORMAT_CANDIDATES
    exposure_us: float | None = None
    gain: float | None = None
    acquisition_frame_rate: float | None = None
    width: int | None = None
    height: int | None = None
    offset_x: int | None = None
    offset_y: int | None = None
    gamma: float | None = None
    black_level: float | None = None
    binning_x: int | None = None
    binning_y: int | None = None
    decimation_x: int | None = None
    decimation_y: int | None = None
    trigger_mode: str | None = "Off"
    trigger_selector: str = "FrameStart"
    trigger_source: str = "Software"
    use_software_trigger: bool = False
    output_pixel_format: str = "Mono8"
    timeout_ms: int = 5000
    rotate_180: bool = True
    flip_horizontal: bool = False
    flip_vertical: bool = False


def camera_settings_from_config(config: dict[str, Any]) -> CameraSettings:
    camera = config.get("camera", {})
    return CameraSettings(
        serial_number=none_if_blank(camera.get("serial_number")),
        user_defined_name=none_if_blank(camera.get("user_defined_name")),
        model_name=none_if_blank(camera.get("model_name")),
        device_class=none_if_blank(camera.get("device_class")),
        pixel_format=none_if_blank(camera.get("pixel_format", "Mono8")),
        pixel_format_candidates=_string_tuple(
            camera.get("pixel_format_candidates"),
            DEFAULT_PIXEL_FORMAT_CANDIDATES,
        ),
        exposure_us=_optional_float(camera.get("exposure_us")),
        gain=_optional_float(camera.get("gain")),
        acquisition_frame_rate=_optional_float(camera.get("acquisition_frame_rate")),
        width=_optional_int(camera.get("width")),
        height=_optional_int(camera.get("height")),
        offset_x=_optional_int(camera.get("offset_x")),
        offset_y=_optional_int(camera.get("offset_y")),
        gamma=_optional_float(camera.get("gamma")),
        black_level=_optional_float(camera.get("black_level")),
        binning_x=_optional_int(camera.get("binning_x")),
        binning_y=_optional_int(camera.get("binning_y")),
        decimation_x=_optional_int(camera.get("decimation_x")),
        decimation_y=_optional_int(camera.get("decimation_y")),
        trigger_mode=none_if_blank(camera.get("trigger_mode", "Off")),
        trigger_selector=str(camera.get("trigger_selector", "FrameStart")),
        trigger_source=str(camera.get("trigger_source", "Software")),
        use_software_trigger=_bool_value(camera.get("use_software_trigger", False), False, "camera.use_software_trigger"),
        output_pixel_format=camera.get("output_pixel_format", "Mono8"),
        timeout_ms=int(camera.get("timeout_ms", 5000)),
        rotate_180=_bool_value(camera.get("rotate_180", True), True, "camera.rotate_180"),
        flip_horizontal=_bool_value(camera.get("flip_horizontal", False), False, "camera.flip_horizontal"),
        flip_vertical=_bool_value(camera.get("flip_vertical", False), False, "camera.flip_vertical"),
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
    require_pylon()
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
        self.camera: Any | None = None
        self.converter: Any | None = None

    def __enter__(self) -> BaslerCamera:
        return self.open()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def open(self) -> BaslerCamera:
        try:
            require_pylon()
            device_info = self._select_device()
            self.camera = pylon.InstantCamera(pylon.TlFactory.GetInstance().CreateDevice(device_info))
            self.camera.Open()
            self._apply_settings()
            self.converter = pylon.ImageFormatConverter()
            self.converter.OutputPixelFormat = self._output_pixel_type()
            self.converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned
        except CameraConnectionError:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise CameraConnectionError("Basler 카메라 연결 또는 설정 적용에 실패했습니다.", str(exc)) from exc
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
        grab_result = self.camera.RetrieveResult(timeout, pylon.TimeoutHandling_ThrowException)
        try:
            if not grab_result.GrabSucceeded():
                raise CameraConnectionError(
                    "Basler 카메라 프레임 수신에 실패했습니다.",
                    f"Grab failed: {grab_result.ErrorCode} {grab_result.ErrorDescription}",
                )
            image = self.converter.Convert(grab_result)
            return apply_camera_orientation(
                image.GetArray(),
                rotate_180=self.settings.rotate_180,
                flip_horizontal=self.settings.flip_horizontal,
                flip_vertical=self.settings.flip_vertical,
            )
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
                self.camera.WaitForFrameTriggerReady(timeout, pylon.TimeoutHandling_ThrowException)
                captured_at = iso_timestamp()
                self.camera.ExecuteSoftwareTrigger()

            grab_result = self.camera.RetrieveResult(timeout, pylon.TimeoutHandling_ThrowException)
            completed_at = iso_timestamp()
            try:
                if not grab_result.GrabSucceeded():
                    raise CameraConnectionError(
                        "Basler 카메라 캡처에 실패했습니다.",
                        f"Grab failed: {grab_result.ErrorCode} {grab_result.ErrorDescription}",
                    )
                array = apply_camera_orientation(
                    grab_result.GetArray(),
                    rotate_180=self.settings.rotate_180,
                    flip_horizontal=self.settings.flip_horizontal,
                    flip_vertical=self.settings.flip_vertical,
                )
                metadata = _grab_metadata(grab_result, captured_at, completed_at)
                return array, metadata
            finally:
                grab_result.Release()
        finally:
            if self.camera is not None and self.camera.IsGrabbing():
                self.camera.StopGrabbing()

    def live_original_arrays(
        self,
        timeout_ms: int | None = None,
        stop_requested: Callable[[], bool] | None = None,
        max_consecutive_failures: int = 5,
    ) -> Any:
        if self.camera is None:
            raise RuntimeError("Camera is not open.")

        timeout = timeout_ms or self.settings.timeout_ms
        failure_threshold = max(1, int(max_consecutive_failures))
        consecutive_failures = 0
        strategy = getattr(pylon, "GrabStrategy_LatestImageOnly", None)
        if strategy is None:
            self.camera.StartGrabbing()
        else:
            self.camera.StartGrabbing(strategy)
        try:
            while self.camera.IsGrabbing():
                if stop_requested is not None and stop_requested():
                    break
                captured_at = iso_timestamp()
                try:
                    grab_result = self.camera.RetrieveResult(timeout, pylon.TimeoutHandling_ThrowException)
                except Exception as exc:
                    consecutive_failures += 1
                    if consecutive_failures >= failure_threshold:
                        raise CameraConnectionError(
                            "Basler live preview frame receive failed repeatedly.",
                            f"Live RetrieveResult failed {consecutive_failures} times: {exc}",
                        ) from exc
                    continue
                completed_at = iso_timestamp()
                try:
                    if not grab_result.GrabSucceeded():
                        consecutive_failures += 1
                        if consecutive_failures >= failure_threshold:
                            raise CameraConnectionError(
                                "Basler live preview frame receive failed repeatedly.",
                                f"Live grab failed: {grab_result.ErrorCode} {grab_result.ErrorDescription}",
                            )
                        continue
                    consecutive_failures = 0
                    yield (
                        apply_camera_orientation(
                            grab_result.GetArray(),
                            rotate_180=self.settings.rotate_180,
                            flip_horizontal=self.settings.flip_horizontal,
                            flip_vertical=self.settings.flip_vertical,
                        ),
                        _grab_metadata(
                            grab_result,
                            captured_at,
                            completed_at,
                        ),
                    )
                finally:
                    grab_result.Release()
        finally:
            if self.camera is not None and self.camera.IsGrabbing():
                self.camera.StopGrabbing()

    def apply_live_settings(self, settings: CameraSettings) -> list[str]:
        if self.camera is None:
            raise RuntimeError("Camera is not open.")
        warning_start = len(self.warnings)
        self.settings = settings
        self._apply_live_safe_camera_parameters(settings)
        return self.warnings[warning_start:]

    def _apply_live_safe_camera_parameters(self, settings: CameraSettings) -> None:
        if settings.exposure_us is not None:
            self._set_first_available_feature(("ExposureAuto",), "Off", warn_if_missing=False)
            self._set_first_available_feature(
                ("ExposureTime", "ExposureTimeAbs"),
                float(settings.exposure_us),
                label="ExposureTime",
            )
        if settings.gain is not None:
            self._set_first_available_feature(("GainAuto",), "Off", warn_if_missing=False)
            self._set_first_available_feature(
                ("Gain", "GainRaw"),
                float(settings.gain),
                label="Gain",
            )
        if settings.acquisition_frame_rate is not None:
            self._set_first_available_feature(
                ("AcquisitionFrameRateEnable",),
                True,
                warn_if_missing=False,
            )
            self._set_first_available_feature(
                ("AcquisitionFrameRate", "AcquisitionFrameRateAbs"),
                float(settings.acquisition_frame_rate),
                label="AcquisitionFrameRate",
            )
        if settings.gamma is not None:
            self._set_first_available_feature(("Gamma",), float(settings.gamma), label="Gamma")
        if settings.black_level is not None:
            self._set_first_available_feature(
                ("BlackLevel", "BlackLevelRaw"),
                float(settings.black_level),
                label="BlackLevel",
            )

    def _select_device(self) -> Any:
        require_pylon()
        devices = list(pylon.TlFactory.GetInstance().EnumerateDevices())
        if not devices:
            raise CameraConnectionError("Basler 카메라가 감지되지 않았습니다.")

        if self.settings.serial_number:
            for device in devices:
                if _safe_device_value(device, "GetSerialNumber") == self.settings.serial_number:
                    return device
            raise CameraConnectionError(
                "선택한 Basler 카메라 serial을 찾을 수 없습니다.",
                f"Basler camera serial not found: {self.settings.serial_number}",
            )

        if self.settings.user_defined_name:
            for device in devices:
                if _safe_device_value(device, "GetUserDefinedName") == self.settings.user_defined_name:
                    return device
            raise CameraConnectionError(
                "선택한 Basler 카메라 사용자 이름을 찾을 수 없습니다.",
                f"Basler camera user-defined name not found: {self.settings.user_defined_name}",
            )

        candidates = devices
        if self.settings.model_name:
            expected = self.settings.model_name.lower()
            candidates = [
                device
                for device in candidates
                if expected in (_safe_device_value(device, "GetModelName") or "").lower()
            ]
            if not candidates:
                raise CameraConnectionError(
                    "설정한 Basler 카메라 모델을 찾을 수 없습니다.",
                    f"Basler camera model not found: {self.settings.model_name}",
                )
        if self.settings.device_class:
            expected = self.settings.device_class.lower()
            candidates = [
                device
                for device in candidates
                if expected == (_safe_device_value(device, "GetDeviceClass") or "").lower()
            ]
            if not candidates:
                raise CameraConnectionError(
                    "설정한 Basler device class를 찾을 수 없습니다.",
                    f"Basler camera device class not found: {self.settings.device_class}",
                )

        return candidates[0]

    def _apply_settings(self) -> None:
        if self.camera is None:
            raise RuntimeError("Camera is not open.")

        self._apply_pixel_format()
        if self.settings.use_software_trigger:
            self._set_feature("TriggerSelector", self.settings.trigger_selector)
            self._set_feature("TriggerMode", "On")
            self._set_feature("TriggerSource", self.settings.trigger_source)
        elif self.settings.trigger_mode:
            self._set_feature("TriggerMode", self.settings.trigger_mode)
        if self.settings.exposure_us is not None:
            self._set_first_available_feature(("ExposureAuto",), "Off", warn_if_missing=False)
            self._set_first_available_feature(
                ("ExposureTime", "ExposureTimeAbs"),
                float(self.settings.exposure_us),
                label="ExposureTime",
            )
        if self.settings.gain is not None:
            self._set_first_available_feature(("GainAuto",), "Off", warn_if_missing=False)
            self._set_first_available_feature(
                ("Gain", "GainRaw"),
                float(self.settings.gain),
                label="Gain",
            )
        self._apply_optional_camera_parameters()

    def _apply_optional_camera_parameters(self) -> None:
        if self.settings.acquisition_frame_rate is not None:
            self._set_first_available_feature(
                ("AcquisitionFrameRateEnable",),
                True,
                warn_if_missing=False,
            )
            self._set_first_available_feature(
                ("AcquisitionFrameRate", "AcquisitionFrameRateAbs"),
                float(self.settings.acquisition_frame_rate),
                label="AcquisitionFrameRate",
            )
        for feature_names, value, label in (
            (("Width",), self.settings.width, "Width"),
            (("Height",), self.settings.height, "Height"),
            (("OffsetX",), self.settings.offset_x, "OffsetX"),
            (("OffsetY",), self.settings.offset_y, "OffsetY"),
            (("Gamma",), self.settings.gamma, "Gamma"),
            (("BlackLevel", "BlackLevelRaw"), self.settings.black_level, "BlackLevel"),
            (("BinningHorizontal", "BinningX"), self.settings.binning_x, "BinningX"),
            (("BinningVertical", "BinningY"), self.settings.binning_y, "BinningY"),
            (("DecimationHorizontal", "DecimationX"), self.settings.decimation_x, "DecimationX"),
            (("DecimationVertical", "DecimationY"), self.settings.decimation_y, "DecimationY"),
        ):
            if value is None:
                continue
            self._set_first_available_feature(feature_names, value, label=label)

    def _apply_pixel_format(self) -> None:
        if self.camera is None:
            raise RuntimeError("Camera is not open.")
        if str(self.settings.pixel_format or "").strip().lower() in {"auto", "default"}:
            return
        requested = _dedupe_strings([self.settings.pixel_format or ""] + list(self.settings.pixel_format_candidates))
        requested = [item for item in requested if item.lower() not in {"auto", "default"}]
        if not requested:
            return

        available = set(self._enum_symbolics("PixelFormat"))
        skipped: list[str] = []
        for pixel_format in requested:
            if available and pixel_format not in available:
                skipped.append(pixel_format)
                continue
            if self._set_feature("PixelFormat", pixel_format, warn=False):
                if pixel_format != self.settings.pixel_format:
                    self.warnings.append(
                        f"PixelFormat: requested {self.settings.pixel_format!r}, using fallback {pixel_format!r}"
                    )
                return
            skipped.append(pixel_format)

        supported = ", ".join(sorted(available)) if available else "unknown"
        tried = ", ".join(skipped) if skipped else ", ".join(requested)
        self.warnings.append(f"PixelFormat: no requested format was accepted. Tried: {tried}. Supported: {supported}")

    def _set_first_available_feature(
        self,
        feature_names: tuple[str, ...],
        value: Any,
        label: str | None = None,
        warn_if_missing: bool = True,
    ) -> bool:
        for feature_name in feature_names:
            if not self._feature_exists(feature_name):
                continue
            if self._set_feature(feature_name, value):
                return True
        if warn_if_missing:
            feature_text = "/".join(feature_names)
            self.warnings.append(f"{label or feature_text}: camera does not expose a writable {feature_text} feature")
        return False

    def _set_feature(self, feature_name: str, value: Any, warn: bool = True) -> bool:
        if self.camera is None:
            raise RuntimeError("Camera is not open.")
        try:
            feature = getattr(self.camera, feature_name)
            feature.SetValue(value)
            return True
        except Exception as exc:
            if warn:
                self.warnings.append(f"{feature_name}: {exc}")
            return False

    def _feature_exists(self, feature_name: str) -> bool:
        if self.camera is None:
            return False
        try:
            getattr(self.camera, feature_name)
            return True
        except Exception:
            return False

    def _enum_symbolics(self, feature_name: str) -> list[str]:
        if self.camera is None:
            return []
        try:
            feature = getattr(self.camera, feature_name)
            symbolics = feature.GetSymbolics()
        except Exception:
            return []
        return [str(item) for item in symbolics]

    def _output_pixel_type(self) -> int:
        require_pylon()
        output_format = _normalise_pixel_format_name(self.settings.output_pixel_format)
        pylon_name = OUTPUT_PIXEL_TYPE_ALIASES.get(output_format, output_format)
        pixel_type = getattr(pylon, f"PixelType_{pylon_name}", None)
        if pixel_type is None:
            supported = ", ".join(sorted(OUTPUT_PIXEL_TYPE_ALIASES))
            raise ValueError(
                f"Unsupported output_pixel_format {self.settings.output_pixel_format!r}. "
                f"Supported values: {supported}"
            )
        return pixel_type


def require_pylon() -> None:
    if pylon is not None:
        return
    detail = f" ({PYLON_IMPORT_ERROR})" if PYLON_IMPORT_ERROR else ""
    raise CameraConnectionError(
        "Basler pylon Runtime 또는 pypylon 로딩에 실패했습니다. "
        "Basler pylon Runtime을 설치한 뒤 앱을 다시 실행하세요."
        f"{detail}",
    )


def _safe_device_value(device_info: Any, method_name: str) -> str | None:
    method = getattr(device_info, method_name, None)
    if method is None:
        return None
    try:
        value = method()
    except Exception:
        return None
    return str(value) if value else None


def _string_tuple(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        items = value.split(",")
    else:
        try:
            items = list(value)
        except TypeError:
            items = [value]
    normalised = _dedupe_strings(items)
    return tuple(normalised) if normalised else default


def _dedupe_strings(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _optional_float(value: Any) -> float | None:
    text = none_if_blank(value)
    if text is None:
        return None
    return float(text)


def _optional_int(value: Any) -> int | None:
    text = none_if_blank(value)
    if text is None:
        return None
    number = float(text)
    if not number.is_integer():
        raise ValueError(f"Expected an integer camera parameter, got {value!r}.")
    return int(number)


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
    raise CameraConnectionError(
        f"{field_name} must be true or false.",
        f"Invalid boolean for {field_name}: {value!r}",
    )


def _normalise_pixel_format_name(value: Any) -> str:
    return str(value or "").strip().replace(" ", "")


def apply_camera_orientation(
    array: np.ndarray,
    *,
    rotate_180: bool,
    flip_horizontal: bool = False,
    flip_vertical: bool = False,
) -> np.ndarray:
    arr = np.asarray(array)
    if arr.ndim < 2:
        return np.ascontiguousarray(arr)
    if rotate_180:
        arr = np.flip(arr, axis=(0, 1))
    if flip_vertical:
        arr = np.flip(arr, axis=0)
    if flip_horizontal:
        arr = np.flip(arr, axis=1)
    return np.ascontiguousarray(arr)


def save_array(path: Path, array: np.ndarray, pixel_format: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        output_format = _normalise_pixel_format_name(pixel_format)
        if array.ndim == 2:
            image = Image.fromarray(array)
        elif array.ndim == 3 and array.shape[2] == 3 and output_format in {"BGR8", "BGR8packed"}:
            image = Image.fromarray(np.ascontiguousarray(array[:, :, ::-1]), "RGB")
        elif array.ndim == 3 and array.shape[2] == 3:
            image = Image.fromarray(np.ascontiguousarray(array), "RGB")
        else:
            raise ValueError(f"Unsupported image array shape: {array.shape}")
        image.save(path)
    except DatasetWriteError:
        raise
    except Exception as exc:
        raise DatasetWriteError("이미지 파일 저장에 실패했습니다.", str(exc)) from exc


def save_original_array(path: Path, array: np.ndarray) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if array.ndim == 2:
            image = Image.fromarray(np.ascontiguousarray(array))
        elif array.ndim == 3 and array.shape[2] in (3, 4):
            image = Image.fromarray(np.ascontiguousarray(array))
        else:
            raise ValueError(f"Unsupported original image array shape: {array.shape}")
        image.save(path)
    except DatasetWriteError:
        raise
    except Exception as exc:
        raise DatasetWriteError("원본 이미지 파일 저장에 실패했습니다.", str(exc)) from exc


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


def _grab_metadata(grab_result: Any, captured_at: str, completed_at: str) -> dict[str, Any]:
    return {
        "captured_at": captured_at,
        "completed_at": completed_at,
        "pixel_type": _grab_value(grab_result, "PixelType"),
        "width": _grab_int(grab_result, "Width"),
        "height": _grab_int(grab_result, "Height"),
        "camera_timestamp_ns": _grab_int(grab_result, "TimeStamp"),
        "block_id": _grab_int(grab_result, "BlockID"),
    }
