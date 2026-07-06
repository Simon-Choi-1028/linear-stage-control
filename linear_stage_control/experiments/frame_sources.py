from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from time import perf_counter
from typing import Callable, Optional

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

from ..camera import BaslerCamera, camera_settings_from_config

IMAGE_EXTENSIONS = {".bmp", ".dib", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}
MAX_FRAME_PIXELS = 32_000_000
MAX_FRAME_SOURCE_BYTES = 256 * 1024 * 1024


class FrameSource:
    name = "None"

    def open(self) -> None:
        return

    def read(self) -> Optional[np.ndarray]:
        raise NotImplementedError

    def close(self) -> None:
        return


class SyntheticFrameSource(FrameSource):
    def __init__(self, name: str, factory: Callable[[float], np.ndarray]) -> None:
        self.name = name
        self.factory = factory
        self.start_time = perf_counter()

    def read(self) -> Optional[np.ndarray]:
        phase = (perf_counter() - self.start_time) * 2.0
        return validate_frame_array(self.factory(phase), source_name=self.name)


class FileFrameSource(FrameSource):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.name = self.path.name
        self.image: Optional[np.ndarray] = None
        self.cap: Optional[cv2.VideoCapture] = None

    def open(self) -> None:
        if self.path.suffix.lower() in IMAGE_EXTENSIONS:
            _validate_image_file_size(self.path)
            image = cv2.imread(str(self.path), cv2.IMREAD_COLOR)
            if image is None:
                raise RuntimeError(f"Cannot read image: {self.path}")
            self.image = validate_frame_array(image, source_name=self.name)
            self.image.setflags(write=False)
            return
        self.cap = cv2.VideoCapture(str(self.path))
        if not self.cap.isOpened():
            self.cap.release()
            raise RuntimeError(f"Cannot open video: {self.path}")
        _validate_video_stream_size(self.cap, self.path)

    def read(self) -> Optional[np.ndarray]:
        if self.image is not None:
            return self.image
        if self.cap is None:
            return None
        ok, frame = self.cap.read()
        if ok:
            return validate_frame_array(frame, source_name=self.name)
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ok, frame = self.cap.read()
        return validate_frame_array(frame, source_name=self.name) if ok else None

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None


class BaslerExperimentSource(FrameSource):
    name = "Basler camera"

    def __init__(self, config: dict) -> None:
        config_copy = deepcopy(config)
        camera_config = config_copy.setdefault("camera", {})
        camera_config["use_software_trigger"] = False
        camera_config["trigger_mode"] = "Off"
        camera_config["timeout_ms"] = max(1000, int(camera_config.get("timeout_ms", 1000) or 1000))
        self.settings = camera_settings_from_config(config_copy)
        self.camera: BaslerCamera | None = None

    def open(self) -> None:
        self.camera = BaslerCamera(self.settings).open()
        model = getattr(getattr(self.camera, "camera", None), "GetDeviceInfo", lambda: None)()
        if model is not None:
            try:
                self.name = f"Basler: {model.GetModelName()}"
            except Exception:
                self.name = "Basler camera"

    def read(self) -> Optional[np.ndarray]:
        if self.camera is None:
            return None
        frame = self.camera.grab_array(timeout_ms=self.settings.timeout_ms)
        return validate_frame_array(frame, source_name=self.name)

    def close(self) -> None:
        if self.camera is not None:
            self.camera.close()
            self.camera = None


def ensure_bgr(frame: np.ndarray) -> np.ndarray:
    frame = validate_frame_array(frame)
    if frame.ndim == 2:
        return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    if frame.ndim == 3 and frame.shape[2] == 3:
        return frame
    if frame.ndim == 3 and frame.shape[2] == 4:
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    raise ValueError(f"Unsupported frame shape: {frame.shape}")


def bgr_to_rgb(frame_bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(ensure_bgr(frame_bgr), cv2.COLOR_BGR2RGB)


def validate_frame_array(frame: object, *, source_name: str = "frame") -> np.ndarray:
    arr = np.asarray(frame)
    if arr.ndim not in (2, 3):
        raise ValueError(f"Unsupported {source_name} shape: {arr.shape}")
    if any(int(size) <= 0 for size in arr.shape[:2]):
        raise ValueError(f"{source_name} has an empty dimension: {arr.shape}")
    if arr.ndim == 3 and arr.shape[2] not in (1, 3, 4):
        raise ValueError(f"Unsupported {source_name} channel count: {arr.shape[2]}")
    if arr.dtype == np.dtype("O") or np.issubdtype(arr.dtype, np.complexfloating):
        raise TypeError(f"Unsupported {source_name} dtype: {arr.dtype}")
    pixels = int(arr.shape[0]) * int(arr.shape[1])
    if pixels > MAX_FRAME_PIXELS:
        raise MemoryError(
            f"{source_name} is too large: {arr.shape[1]}x{arr.shape[0]} px "
            f"({pixels:,} px > {MAX_FRAME_PIXELS:,} px)"
        )
    if int(arr.nbytes) > MAX_FRAME_SOURCE_BYTES:
        raise MemoryError(f"{source_name} uses too much source memory: {arr.nbytes / (1024 * 1024):.1f} MiB")
    return arr


def _validate_image_file_size(path: Path) -> None:
    try:
        with Image.open(path) as image:
            width, height = image.size
    except UnidentifiedImageError as exc:
        raise RuntimeError(f"Cannot identify image: {path}") from exc
    except OSError as exc:
        raise RuntimeError(f"Cannot inspect image: {path}") from exc
    _validate_pixel_dimensions(width, height, str(path))


def _validate_video_stream_size(cap: cv2.VideoCapture, path: Path) -> None:
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if width > 0 and height > 0:
        _validate_pixel_dimensions(width, height, str(path))


def _validate_pixel_dimensions(width: int, height: int, label: str) -> None:
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Invalid image dimensions for {label}: {width}x{height}")
    pixels = int(width) * int(height)
    if pixels > MAX_FRAME_PIXELS:
        raise MemoryError(
            f"Frame source is too large: {width}x{height} px ({pixels:,} px > {MAX_FRAME_PIXELS:,} px): {label}"
        )
