from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .camera import CaptureResult, save_original_capture


@dataclass(frozen=True)
class CaptureDiskWriteJob:
    image_path: Path
    npy_path: Path | None
    array: np.ndarray
    metadata: dict[str, Any]


class AsyncCaptureDiskWriter:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="capture-disk-writer")

    def __enter__(self) -> AsyncCaptureDiskWriter:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close(wait=True)

    def submit(self, job: CaptureDiskWriteJob) -> Future[CaptureResult]:
        return self._executor.submit(_write_capture, job)

    def close(self, *, wait: bool) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=not wait)


def _write_capture(job: CaptureDiskWriteJob) -> CaptureResult:
    return save_original_capture(
        job.image_path,
        job.array,
        job.metadata,
        npy_path=job.npy_path,
    )
