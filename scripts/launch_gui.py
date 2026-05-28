from __future__ import annotations

import sys
import os
from pathlib import Path


def _trace(message: str) -> None:
    trace_path = os.environ.get("LINEAR_STAGE_SMOKE_TRACE")
    if not trace_path:
        return
    try:
        with Path(trace_path).open("a", encoding="utf-8") as trace_file:
            trace_file.write(f"{message}\n")
    except OSError:
        pass


if not getattr(sys, "frozen", False):
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))


_trace("launcher_start")
from linear_stage_control.gui_app import main
_trace("gui_app_imported")


if __name__ == "__main__":
    raise SystemExit(main())
