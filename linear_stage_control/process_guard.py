from __future__ import annotations

import csv
import subprocess
from dataclasses import dataclass
from io import StringIO

PYLON_VIEWER_USER_MESSAGE = (
    "Basler pylon Viewer가 실행 중입니다. 같은 카메라를 점유할 수 있으니 Viewer를 닫은 뒤 다시 시도하세요."
)

_PYLON_VIEWER_EXACT_NAMES = {"pylonviewer.exe", "pylonviewerapp.exe"}


@dataclass(frozen=True)
class RunningProcess:
    name: str
    pid: int | None = None


def is_pylon_viewer_process_name(name: str) -> bool:
    normalized = name.strip().strip('"').lower()
    return normalized in _PYLON_VIEWER_EXACT_NAMES or ("pylon" in normalized and "viewer" in normalized)


def parse_tasklist_csv(output: str) -> list[RunningProcess]:
    processes: list[RunningProcess] = []
    reader = csv.reader(StringIO(output))
    for row in reader:
        if len(row) < 2:
            continue
        name = row[0].strip()
        if not name:
            continue
        try:
            pid = int(row[1])
        except ValueError:
            pid = None
        processes.append(RunningProcess(name=name, pid=pid))
    return processes


def running_pylon_viewer_processes(tasklist_output: str | None = None) -> list[RunningProcess]:
    if tasklist_output is None:
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            completed = subprocess.run(
                ["tasklist", "/fo", "csv", "/nh"],
                capture_output=True,
                check=True,
                creationflags=creationflags,
                text=True,
            )
        except Exception:
            return []
        tasklist_output = completed.stdout
    return [process for process in parse_tasklist_csv(tasklist_output) if is_pylon_viewer_process_name(process.name)]


def describe_processes(processes: list[RunningProcess]) -> str:
    return ", ".join(
        f"{process.name}({process.pid})" if process.pid is not None else process.name for process in processes
    )


def pylon_viewer_block_message(processes: list[RunningProcess]) -> str:
    detail = describe_processes(processes)
    return f"{PYLON_VIEWER_USER_MESSAGE} 감지: {detail}" if detail else PYLON_VIEWER_USER_MESSAGE
