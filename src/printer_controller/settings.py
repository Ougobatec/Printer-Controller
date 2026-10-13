"""Every user-configurable parameter, in ONE place.

* The defaults are the values written in the dataclasses below.
* ``settings.json`` only stores what differs from the defaults ("reset" = delete the key).
* Code reads ``settings.printer.command_timeout`` etc.; nothing else holds a copy.

Units used everywhere: speeds in mm/min, accelerations in mm/s², positions in mm,
durations in s, frequencies in Hz.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

VERSION = 2


def _data_dir() -> str:
    return str(Path.home() / "PrinterController")


@dataclass
class AppSettings:
    language: str = "fr"                 # "fr" or "en" (applied at the next start)
    console_visible: bool = True
    console_height: int = 170


@dataclass
class PrinterSettings:
    port: str = ""                       # last connection
    baudrate: int = 115200
    auto_home: bool = True               # check the homing at every connection (G28 O)
    startup_timeout: float = 10.0        # s : maximum wait for the printer to boot
    command_timeout: float = 30.0        # s : maximum wait for one command
    home_timeout: float = 120.0          # s : maximum wait for a homing
    move_margin: float = 10.0            # s : added to the estimated duration of a move
    speed_cap: float = 0.0               # mm/min, 0 = no cap (the printer's own maximum)
    quick_speeds: list = field(default_factory=lambda: [100.0, 300.0, 500.0, 1000.0, 1500.0, 3000.0])
    overrides: dict = field(default_factory=dict)   # manual values of the printer profile


@dataclass
class SensorSettings:
    kind: str = "serial"                 # "serial" or "nidaq"
    serial: dict = field(default_factory=dict)
    nidaq: dict = field(default_factory=dict)
    multiplier: float = 1.0
    unit: str = "V"
    filter: str = "none"                 # none | moving_average | median | exponential
    filter_size: int = 5
    live_rate_hz: float = 10.0
    live_history: int = 2000             # values kept for the live graph (never recorded)


@dataclass
class ProgramSettings:
    speed: float = 300.0                 # mm/min : default of every new line
    pause: float = 0.0                   # s
    rate_hz: float = 10.0
    duration_s: float = 2.0
    count: int = 5
    folder: str = field(default_factory=lambda: os.path.join(_data_dir(), "programs"))


@dataclass
class ResultsSettings:
    export_dir: str = field(default_factory=lambda: os.path.join(_data_dir(), "results"))
    csv_delimiter: str = ";"
    csv_decimal: str = ","


SECTIONS = {"app": AppSettings, "printer": PrinterSettings, "sensor": SensorSettings,
            "program": ProgramSettings, "results": ResultsSettings}

# Values that must stay strictly positive (otherwise the default is restored)
_POSITIVE = {"printer": ("baudrate", "startup_timeout", "command_timeout", "home_timeout"),
             "sensor": ("live_rate_hz", "filter_size", "live_history"),
             "program": ("speed", "rate_hz", "duration_s", "count"),
             "app": ("console_height",)}


def default_path() -> Path:
    """settings.json at the root of the repository (or PRINTER_CONTROLLER_SETTINGS)."""
    env = os.environ.get("PRINTER_CONTROLLER_SETTINGS")
    return Path(env) if env else Path(__file__).resolve().parents[2] / "settings.json"


def _coerce(value, default):
    """`value` converted to the type of `default`; ValueError if impossible."""
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ValueError("bool expected")
        return value
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("number expected")
        return int(value)
    if isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("number expected")
        return float(value)
    if isinstance(default, str):
        if not isinstance(value, str):
            raise ValueError("text expected")
        return value
    if isinstance(default, list):
        if not isinstance(value, list):
            raise ValueError("list expected")
        kind = type(default[0]) if default else None
        return [kind(v) for v in value] if kind in (int, float) else list(value)
    if isinstance(default, dict):
        if not isinstance(value, dict):
            raise ValueError("object expected")
        return dict(value)
    return value


def _diff(current, default) -> dict:
    """Fields of the dataclass `current` that differ from `default`."""
    out = {}
    for f in fields(current):
        a, b = getattr(current, f.name), getattr(default, f.name)
        if a != b:
            out[f.name] = a
    return out


class Settings:
    """All the sections (``app``, ``printer``, ``sensor``, ``program``, ``results``)."""

    def __init__(self, path=None):
        self.path = Path(path) if path else default_path()
        for name, cls in SECTIONS.items():
            setattr(self, name, cls())

    # --- reading / writing -----------------------------------------------------------
    @classmethod
    def load(cls, path=None) -> "Settings":
        settings = cls(path)
        try:
            data = json.loads(settings.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return settings
        if not isinstance(data, dict):
            return settings
        if data.get("version") != VERSION:
            data = migrate_legacy(data)
        for name in SECTIONS:
            settings._apply(name, data.get(name))
        return settings

    def _apply(self, name, values):
        if not isinstance(values, dict):
            return
        section, default = getattr(self, name), SECTIONS[name]()
        for f in fields(section):
            if f.name not in values:
                continue
            try:
                value = _coerce(values[f.name], getattr(default, f.name))
            except (ValueError, TypeError):
                continue                                  # invalid value: keep the default
            if f.name in _POSITIVE.get(name, ()) and value <= 0:
                continue
            setattr(section, f.name, value)

    def save(self):
        data = {"version": VERSION}
        for name, cls in SECTIONS.items():
            changed = _diff(getattr(self, name), cls())
            if changed:
                data[name] = changed
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)                            # never leave a half-written file

    # --- generic access (used by the settings dialogs) -----------------------------------
    def get(self, path: str):
        section, key = path.split(".")
        return getattr(getattr(self, section), key)

    def set(self, path: str, value):
        section, key = path.split(".")
        default = getattr(SECTIONS[section](), key)
        value = _coerce(value, default)
        if key in _POSITIVE.get(section, ()) and value <= 0:
            raise ValueError(f"{path} must be > 0")
        setattr(getattr(self, section), key, value)

    def reset(self, path: str):
        """Back to the default of one value ("printer.baudrate") or of a whole section."""
        if "." in path:
            section, key = path.split(".")
            setattr(getattr(self, section), key, getattr(SECTIONS[section](), key))
        else:
            setattr(self, path, SECTIONS[path]())


def migrate_legacy(old: dict) -> dict:
    """Settings of the first version of the app (no "version" key) -> current layout."""
    out = {"version": VERSION}

    def put(section, key, value):
        out.setdefault(section, {})[key] = value

    printer = old.get("printer") if isinstance(old.get("printer"), dict) else {}
    for key in ("port", "baudrate"):
        if key in printer:
            put("printer", key, printer[key])
    mapping = {
        "DEFAULT_SPEED": ("program", "speed"), "PROGRAM_FREQUENCY": ("program", "rate_hz"),
        "PROGRAM_DURATION": ("program", "duration_s"), "ACQ_AVERAGE_SAMPLES": ("program", "count"),
        "ACQ_SAMPLE_RATE": ("sensor", "live_rate_hz"), "SPEED_SIZES": ("printer", "quick_speeds"),
        "MAX_SPEED": ("printer", "speed_cap"), "STARTUP_DELAY": ("printer", "startup_timeout"),
        "COMMAND_TIMEOUT": ("printer", "command_timeout"), "HOME_TIMEOUT": ("printer", "home_timeout"),
        "PROGRAMS_DIR": ("program", "folder"), "CSV_DELIMITER": ("results", "csv_delimiter"),
        "CSV_DECIMAL": ("results", "csv_decimal"),
    }
    defaults = old.get("defaults") if isinstance(old.get("defaults"), dict) else {}
    for key, (section, name) in mapping.items():
        if key in defaults:
            put(section, name, defaults[key])
    if "PROGRAMS_DIR" in defaults:
        put("results", "export_dir", defaults["PROGRAMS_DIR"])
    signal = old.get("measure", {}).get("signal") if isinstance(old.get("measure"), dict) else None
    if isinstance(signal, dict):
        for key in ("multiplier", "unit"):
            if key in signal:
                put("sensor", key, signal[key])
    return out
