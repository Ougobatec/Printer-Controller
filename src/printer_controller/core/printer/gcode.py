"""G-code commands (Marlin) and the parser of the position report."""

import re

AXES = "XYZ"

ABSOLUTE = "G90"
HOME = "G28"
HOME_IF_NEEDED = "G28 O"        # homes only if the position is unknown (not homed since power-on)
TEMPERATURE = "M105"
POSITION = "M114"
WAIT_MOVES = "M400"
QUICK_STOP = "M410"
EMERGENCY = "M112"
FIRMWARE_INFO = "M115"
SETTINGS = "M503"
SOFT_ENDSTOPS = "M211"

_COORD = re.compile(r"([XYZ]):\s*(-?\d+(?:\.\d+)?)")


def move(x=None, y=None, z=None, speed=None):
    """Absolute linear move; an axis left to None is not moved. Speed in mm/min."""
    parts = ["G1"]
    for axis, value in zip(AXES, (x, y, z)):
        if value is not None:
            parts.append(f"{axis}{value:.3f}")
    if speed is not None:
        parts.append(f"F{speed:.1f}")
    return " ".join(parts)


def parse_position(text):
    """{'X': .., 'Y': .., 'Z': ..} from an M114 answer, or None."""
    head = text.split("Count")[0]
    found = {axis: float(value) for axis, value in _COORD.findall(head)}
    return found if all(a in found for a in AXES) else None
