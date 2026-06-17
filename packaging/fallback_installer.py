from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "Linear Stage Control"
APP_DIR_NAME = "Linear Stage Control"
EXE_NAME = "LinearStageControl.exe"


def main() -> int:
    source = resource_path("LinearStageControl")
    target = Path(os.environ["LOCALAPPDATA"]) / "Programs" / APP_DIR_NAME
    exe_path = target / EXE_NAME
    try:
        if not source.exists():
            raise RuntimeError(f"Installer payload is missing: {source}")
        subprocess.run(
            ["taskkill", "/IM", EXE_NAME, "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        replace_tree(source, target)
        create_shortcut(start_menu_shortcut_path(), exe_path, target)
        message_box(f"{APP_NAME} has been installed.", APP_NAME)
        return 0
    except Exception as exc:
        message_box(f"Installation failed:\n{exc}", APP_NAME)
        return 1


def resource_path(relative_path: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative_path


def replace_tree(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.with_name(f"{target.name}.old")
    if backup.exists():
        shutil.rmtree(backup)
    if target.exists():
        target.replace(backup)
    try:
        shutil.copytree(source, target)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        if backup.exists():
            backup.replace(target)
        raise


def start_menu_shortcut_path() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk"


def create_shortcut(shortcut_path: Path, target_path: Path, working_dir: Path) -> None:
    shortcut_path.parent.mkdir(parents=True, exist_ok=True)
    command = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$shortcut = $shell.CreateShortcut('{ps_quote(shortcut_path)}'); "
        f"$shortcut.TargetPath = '{ps_quote(target_path)}'; "
        f"$shortcut.WorkingDirectory = '{ps_quote(working_dir)}'; "
        "$shortcut.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def ps_quote(path: Path) -> str:
    return str(path).replace("'", "''")


def message_box(message: str, title: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, message, title, 0x40)


if __name__ == "__main__":
    raise SystemExit(main())
