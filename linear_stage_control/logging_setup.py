from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

LOGGER_NAME = "linear_stage_control"
_RESERVED_RECORD_KEYS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "message",
    "module",
    "msecs",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_KEYS or key.startswith("_"):
                continue
            payload[key] = _json_ready(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def default_log_dir() -> Path:
    configured = os.environ.get("LINEAR_STAGE_LOG_DIR")
    if configured:
        return Path(os.path.expandvars(os.path.expanduser(configured)))
    return Path.home() / "Documents" / "LinearStageControl" / "logs"


def configure_logging(log_dir: str | Path | None = None) -> Path:
    target_dir = Path(log_dir) if log_dir is not None else default_log_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    log_path = target_dir / f"app_{datetime.now().strftime('%Y%m%d')}.log"

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(getattr(handler, "_linear_stage_app_log", False) for handler in logger.handlers):
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(JsonLineFormatter())
        handler.setLevel(logging.INFO)
        handler._linear_stage_app_log = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    return log_path


def get_logger(name: str | None = None) -> logging.Logger:
    if name:
        return logging.getLogger(f"{LOGGER_NAME}.{name}")
    return logging.getLogger(LOGGER_NAME)


def add_run_file_handler(run_id: str, log_dir: str | Path | None = None) -> logging.Handler:
    target_dir = Path(log_dir) if log_dir is not None else default_log_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(target_dir / f"run_{run_id}.log", encoding="utf-8")
    handler.setFormatter(JsonLineFormatter())
    handler.setLevel(logging.INFO)
    handler._linear_stage_run_log = True  # type: ignore[attr-defined]
    logging.getLogger(LOGGER_NAME).addHandler(handler)
    return handler


def remove_log_handler(handler: logging.Handler | None) -> None:
    if handler is None:
        return
    logger = logging.getLogger(LOGGER_NAME)
    logger.removeHandler(handler)
    handler.close()


def _json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(item) for item in value]
    return str(value)
