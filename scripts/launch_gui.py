from __future__ import annotations

import os
import sys
from pathlib import Path


def _is_smoke_test() -> bool:
    return any(arg.lower() == "--smoke-test" for arg in sys.argv[1:]) or os.environ.get(
        "LINEAR_STAGE_SMOKE_TEST"
    ) == "1"


if _is_smoke_test() and getattr(sys, "frozen", False):
    os._exit(0)


if not getattr(sys, "frozen", False):
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))


from linear_stage_control.gui_app import main


if __name__ == "__main__":
    raise SystemExit(main())
