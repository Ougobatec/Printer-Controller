"""A program: a list of points, each with a speed, a pause and a measurement."""

from dataclasses import dataclass, field, replace

from ..printer import gcode
from ..printer.motion import plan_move

NONE, SIMPLE, AVERAGE, PATH = "none", "simple", "average", "path"
KINDS = (NONE, SIMPLE, AVERAGE, PATH)
DURATION, COUNT, RATE = "duration", "count", "rate"      # how an average / a path is defined
BY = {AVERAGE: (DURATION, COUNT), PATH: (RATE, COUNT)}


@dataclass
class Measure:
    kind: str = NONE
    by: str = DURATION            # average: duration | count ; path: rate | count
    duration_s: float = 2.0       # average defined by a duration
    count: int = 5                # average or path defined by a number of measurements
    rate_hz: float = 10.0         # frequency of the measurements

    def samples(self, move_duration=0.0, max_rate=None):
        """(number of readings, frequency in Hz) for this measurement.

        For a path, `move_duration` is the estimated duration of the move."""
        rate = self.rate_hz
        if self.kind == AVERAGE:
            count = self.count if self.by == COUNT else max(1, round(self.duration_s * rate))
        elif self.kind == PATH:
            if self.by == COUNT:
                count = self.count
                rate = count / move_duration if move_duration > 0 else self.rate_hz
            else:
                count = max(1, int(move_duration * rate) + 1)
        else:
            return (1, rate) if self.kind == SIMPLE else (0, rate)
        return count, min(rate, max_rate) if max_rate else rate

    def duration(self, move_duration=0.0):
        """Time spent measuring at the point (an average only; a path happens during the move)."""
        if self.kind != AVERAGE:
            return 0.0
        count, rate = self.samples()
        return self.duration_s if self.by == DURATION else count / rate


@dataclass
class Point:
    x: float = None               # an axis left to None is not moved
    y: float = None
    z: float = None
    speed: float = 300.0          # mm/min
    pause: float = 0.0            # s, between the arrival and the measurement
    measure: Measure = field(default_factory=Measure)

    def coordinates(self):
        return {"X": self.x, "Y": self.y, "Z": self.z}

    @property
    def has_coordinates(self):
        return any(v is not None for v in (self.x, self.y, self.z))


@dataclass
class Program:
    name: str = ""
    points: list = field(default_factory=list)

    def first_move_index(self):
        for i, point in enumerate(self.points):
            if point.has_coordinates:
                return i
        return None

    def normalize(self):
        """No path measurement on the first point (nothing to travel from). True if changed."""
        if self.points and self.points[0].measure.kind == PATH:
            self.points[0] = replace(self.points[0], measure=replace(self.points[0].measure, kind=NONE))
            return True
        return False

    def validate(self, limits, speed_limit, max_rate):
        """Problems found, as [(code, details)] (translated by the interface)."""
        issues = []
        if not any(p.has_coordinates for p in self.points):
            issues.append(("empty", {}))
        for i, p in enumerate(self.points, start=1):
            for axis, (low, high) in limits.items():
                value = p.coordinates()[axis]
                if value is not None and not low <= value <= high:
                    issues.append(("out_of_limits", {"line": i, "axis": axis, "value": value, "low": low, "high": high}))
            if p.speed <= 0 or (speed_limit and p.speed > speed_limit):
                issues.append(("speed", {"line": i, "speed": p.speed, "limit": speed_limit}))
            if p.pause < 0:
                issues.append(("pause", {"line": i}))
            m = p.measure
            if m.kind in (AVERAGE, PATH):
                if m.rate_hz <= 0 or (max_rate and m.rate_hz > max_rate):
                    issues.append(("rate", {"line": i, "rate": m.rate_hz, "limit": max_rate}))
                if m.count < 1 or m.duration_s <= 0:
                    issues.append(("measure", {"line": i}))
            if m.kind == PATH and i == 1:
                issues.append(("path_first", {"line": i}))
        return issues

    def estimate(self, profile, start=None):
        """(duration in s, path length in mm) of the whole program, from the motion model."""
        position = dict(start or {a: 0.0 for a in gcode.AXES})
        seconds = length = 0.0
        for p in self.points:
            target = {a: position[a] if v is None else v for a, v in p.coordinates().items()}
            speed = min(p.speed, profile.fastest_speed())
            plan = plan_move(profile, position, target, speed)
            if plan:
                seconds += plan.duration
                length += plan.length
            seconds += p.pause + p.measure.duration()
            position = target
        return seconds, length
