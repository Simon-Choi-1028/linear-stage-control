from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .exceptions import LinearStageControlError


class ConfigError(LinearStageControlError):
    """Raised when a configuration file is missing or malformed."""


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Configuration file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    if not isinstance(data, dict):
        raise ConfigError(f"Configuration root must be a mapping: {config_path}")
    return data


def none_if_blank(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value
