from __future__ import annotations


class LinearStageControlError(Exception):
    """Base exception with a user-facing message distinct from developer detail."""

    def __init__(self, user_message: str, developer_message: str | None = None):
        super().__init__(developer_message or user_message)
        self.user_message = user_message
        self.developer_message = developer_message or user_message


class CameraConnectionError(LinearStageControlError):
    """Camera discovery, connection, or frame acquisition failed."""


class StageConnectionError(LinearStageControlError):
    """Zaber stage connection or axis resolution failed."""


class PositionValidationError(LinearStageControlError):
    """Position input could not be parsed or validated."""


class DatasetWriteError(LinearStageControlError):
    """Dataset path, image, metadata, or manifest writing failed."""


class UpdateVerificationError(LinearStageControlError):
    """Update manifest, download, or hash verification failed."""


def user_error_message(exc: BaseException) -> str:
    if isinstance(exc, LinearStageControlError):
        return exc.user_message
    return str(exc)
