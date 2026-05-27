from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console

from linear_stage_control.camera import BaslerCamera, camera_settings_from_config
from linear_stage_control.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Grab one frame from a Basler camera.")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config.")
    parser.add_argument("--output", default="output/frame.png", help="Output image path.")
    args = parser.parse_args()

    console = Console()
    config = load_config(args.config)
    settings = camera_settings_from_config(config)
    with BaslerCamera(settings) as camera:
        path = camera.capture_original_to(Path(args.output)).image_path
        for warning in camera.warnings:
            console.print(f"[yellow]Camera warning:[/yellow] {warning}")
    console.print(f"Saved {path}")


if __name__ == "__main__":
    main()
