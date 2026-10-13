"""Reading what the printer says about itself (M115, M503, M211, M114).

Everything here is parsing of text answers: no serial access, easy to test.
Speeds are returned as the firmware gives them (mm/s); `profile.py` converts to mm/min.
"""

import re
from dataclasses import dataclass, field

from . import gcode

_PARAM = re.compile(r"([A-Za-z])\s*(-?\d+(?:\.\d+)?)")


@dataclass
class Detected:
    """Whatever could be read from the printer; a missing item stays empty."""
    firmware: str = ""
    model: str = ""
    max_speed: dict = field(default_factory=dict)      # mm/s per axis      (M203)
    max_accel: dict = field(default_factory=dict)      # mm/s² per axis     (M201)
    travel_accel: float = None                         # mm/s²              (M204)
    steps_per_mm: dict = field(default_factory=dict)   # steps/mm per axis  (M92)
    limits: dict = field(default_factory=dict)         # {axis: (min, max)} (M211)
    position: dict = field(default_factory=dict)       # {axis: mm}         (M114)


def parse_m115(lines):
    text = " ".join(lines)
    firmware = re.search(r"FIRMWARE_NAME:(.*?)(?=\s+[A-Z_]{4,}:|$)", text)
    model = re.search(r"MACHINE_TYPE:(.*?)(?=\s+[A-Z_]{4,}:|$)", text)
    return (firmware.group(1).strip() if firmware else "", model.group(1).strip() if model else "")


def _axis_values(line, command):
    """{'X': 500.0, ...} from « M203 X500 Y500 ... »."""
    if command not in line:
        return None
    rest = line.split(command, 1)[1].split(";")[0]
    return {k.upper(): float(v) for k, v in _PARAM.findall(rest) if k.upper() in "XYZ"}


def parse_m503(lines):
    """Returns (max_speed, max_accel, travel_accel, steps_per_mm) found in an M503 report."""
    speed, accel, steps, travel = {}, {}, {}, None
    for line in lines:
        for command, target in (("M203", speed), ("M201", accel), ("M92", steps)):
            values = _axis_values(line, command)
            if values:
                target.update(values)
        if "M204" in line:
            params = {k.upper(): float(v) for k, v in _PARAM.findall(line.split("M204", 1)[1].split(";")[0])}
            travel = params.get("T", params.get("S", travel))     # T = travel, S = old syntax
    return speed, accel, travel, steps


def parse_m211(lines):
    """Soft endstops: « Min: X0.00 Y0.00 Z0.00  Max: X235.00 Y235.00 Z250.00 »."""
    for line in lines:
        match = re.search(r"Min:?(.*?)Max:?(.*)", line, re.I)
        if not match:
            continue
        low = {k.upper(): float(v) for k, v in _PARAM.findall(match.group(1))}
        high = {k.upper(): float(v) for k, v in _PARAM.findall(match.group(2))}
        zone = {a: (low[a], high[a]) for a in gcode.AXES if a in low and a in high and low[a] < high[a]}
        if len(zone) == len(gcode.AXES):
            return zone
    return {}


def read_printer(connection, timeout=10.0):
    """Ask the printer who it is and what it can do. Each query is independent: a failing
    one only leaves its part of the result empty."""
    from .connection import PrinterError
    info = Detected()

    def ask(command):
        try:
            return connection.send(command, timeout=timeout)
        except PrinterError:
            return []

    info.firmware, info.model = parse_m115(ask(gcode.FIRMWARE_INFO))
    info.max_speed, info.max_accel, info.travel_accel, info.steps_per_mm = parse_m503(ask(gcode.SETTINGS))
    info.limits = parse_m211(ask(gcode.SOFT_ENDSTOPS))
    for line in ask(gcode.POSITION):
        position = gcode.parse_position(line)
        if position:
            info.position = position
    return info
