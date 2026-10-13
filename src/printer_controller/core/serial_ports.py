"""Serial ports of the computer, with the name of the connected device."""

from typing import NamedTuple


class PortInfo(NamedTuple):
    device: str
    description: str


def list_ports():
    """Serial ports with the name of the connected device: [PortInfo('COM3', 'USB-SERIAL CH340')]."""
    try:
        from serial.tools import list_ports as pyserial_ports
    except ImportError:
        return []
    ports = []
    for p in sorted(pyserial_ports.comports(), key=lambda p: p.device):
        name = (p.description or "").strip()
        if name.lower() in ("", "n/a"):
            name = (p.product or p.manufacturer or "").strip()
        ports.append(PortInfo(p.device, name.replace(f"({p.device})", "").strip()))
    return ports
