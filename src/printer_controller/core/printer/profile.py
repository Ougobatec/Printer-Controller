"""What the app knows about the printer, and where each value comes from.

Every number is a `Field(value, source)` with source "detected" (read from the printer),
"manual" (changed by the user) or "default" (the printer did not provide it).
Speeds are in mm/min here, accelerations in mm/s².
"""

from dataclasses import dataclass

from . import gcode

DETECTED, MANUAL, DEFAULT = "detected", "manual", "default"
MM_S_TO_MM_MIN = 60.0

# Values used when the printer does not provide them (marked "default")
DEFAULT_LIMIT_MAX = 200.0
DEFAULT_SPEED_XY = 6000.0     # mm/min
DEFAULT_SPEED_Z = 300.0
DEFAULT_ACCEL = 500.0

KEYS = ([f"limits.{a}.{end}" for a in gcode.AXES for end in ("min", "max")]
        + [f"max_speed.{a}" for a in gcode.AXES] + [f"max_accel.{a}" for a in gcode.AXES]
        + ["travel_accel"])


def _default(key):
    if key.startswith("limits."):
        return 0.0 if key.endswith("min") else DEFAULT_LIMIT_MAX
    if key.startswith("max_speed."):
        return DEFAULT_SPEED_Z if key.endswith("Z") else DEFAULT_SPEED_XY
    return DEFAULT_ACCEL


@dataclass
class Field:
    value: float
    source: str


class PrinterProfile:
    def __init__(self):
        self.model = ""
        self.firmware = ""
        self.steps_per_mm = {}                                     # information only
        self._base = {k: Field(_default(k), DEFAULT) for k in KEYS}
        self._manual = {}                                          # key -> value

    # --- loading -----------------------------------------------------------------
    def apply_detected(self, info):
        """Take what the printer gave; keep the defaults for the rest (manual values stay)."""
        self.model, self.firmware = info.model, info.firmware
        self.steps_per_mm = dict(info.steps_per_mm)
        self._base = {k: Field(_default(k), DEFAULT) for k in KEYS}
        for axis, (low, high) in info.limits.items():
            self._base[f"limits.{axis}.min"] = Field(low, DETECTED)
            self._base[f"limits.{axis}.max"] = Field(high, DETECTED)
        for axis, value in info.max_speed.items():
            self._base[f"max_speed.{axis}"] = Field(value * MM_S_TO_MM_MIN, DETECTED)
        for axis, value in info.max_accel.items():
            self._base[f"max_accel.{axis}"] = Field(value, DETECTED)
        if info.travel_accel:
            self._base["travel_accel"] = Field(info.travel_accel, DETECTED)

    def load_overrides(self, overrides):
        self._manual = {k: float(v) for k, v in overrides.items() if k in KEYS}

    # --- reading --------------------------------------------------------------------
    def field(self, key):
        if key in self._manual:
            return Field(self._manual[key], MANUAL)
        return self._base[key]

    def value(self, key):
        return self.field(key).value

    def source(self, key):
        return self.field(key).source

    def limits(self):
        return {a: (self.value(f"limits.{a}.min"), self.value(f"limits.{a}.max")) for a in gcode.AXES}

    def max_speed(self, axis):
        return self.value(f"max_speed.{axis}")

    def max_accel(self, axis):
        return self.value(f"max_accel.{axis}")

    @property
    def travel_accel(self):
        return self.value("travel_accel")

    def fastest_speed(self):
        """Highest speed of the printer (X or Y), mm/min: the natural cap of the app."""
        return max(self.max_speed("X"), self.max_speed("Y"))

    # --- editing ------------------------------------------------------------------------
    def set_manual(self, key, value):
        if key not in KEYS:
            raise KeyError(key)
        value = float(value)
        if key.startswith("limits."):
            axis = key.split(".")[1]
            low = value if key.endswith("min") else self.value(f"limits.{axis}.min")
            high = value if key.endswith("max") else self.value(f"limits.{axis}.max")
            if low >= high:
                raise ValueError(f"{axis}: min must be lower than max")
        elif value <= 0:
            raise ValueError(f"{key} must be > 0")
        if value == self._base[key].value:
            self._manual.pop(key, None)                  # same as the detected one: not "manual"
        else:
            self._manual[key] = value

    def reset_to_detected(self):
        """Back to what the printer provides (and to the defaults for what it does not)."""
        self._manual.clear()

    def overrides(self):
        return dict(self._manual)
