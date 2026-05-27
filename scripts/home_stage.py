from __future__ import annotations

import argparse

from rich.console import Console

from linear_stage_control.config import load_config
from linear_stage_control.stage import ZaberXYStage, stage_settings_from_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Home the configured Zaber XY stage.")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config.")
    args = parser.parse_args()

    console = Console()
    config = load_config(args.config)
    settings = stage_settings_from_config(config)

    with ZaberXYStage(settings) as stage:
        console.print(stage.device_summary())
        stage.home()
        x_mm, y_mm = stage.position_mm()
    console.print(f"Homed. Position: X={x_mm:.6f} mm, Y={y_mm:.6f} mm")


if __name__ == "__main__":
    main()
