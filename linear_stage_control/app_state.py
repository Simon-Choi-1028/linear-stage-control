from __future__ import annotations

from enum import Enum


class AppRunState(Enum):
    IDLE = "idle"
    DISCOVERING_CAMERA = "discovering_camera"
    LIVE_PREVIEW = "live_preview"
    ACQUIRING = "acquiring"
    CANCELLING = "cancelling"
    DIAGNOSTICS = "diagnostics"
    MANUAL_STAGE = "manual_stage"
    UPDATE_CHECKING = "update_checking"
    UPDATE_DOWNLOADING = "update_downloading"
    ERROR = "error"
