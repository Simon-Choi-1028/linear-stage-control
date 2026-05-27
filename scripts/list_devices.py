from __future__ import annotations

from rich.console import Console
from rich.table import Table

from linear_stage_control.camera import enumerate_cameras
from linear_stage_control.stage import list_serial_ports


def main() -> None:
    console = Console()
    console.print("[bold]Serial ports[/bold]")
    port_table = Table("Port", "Description", "HWID")
    for port in list_serial_ports():
        port_table.add_row(port["device"], port["description"], port["hwid"])
    console.print(port_table)

    console.print("[bold]Basler cameras[/bold]")
    camera_table = Table("Model", "Serial", "User name", "Class", "IP")
    for camera in enumerate_cameras():
        camera_table.add_row(
            camera.get("model", ""),
            camera.get("serial", ""),
            camera.get("user_name", ""),
            camera.get("device_class", ""),
            camera.get("ip", ""),
        )
    console.print(camera_table)


if __name__ == "__main__":
    main()
