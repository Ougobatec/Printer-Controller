"""Motion model: how long a move takes and where the head is at any instant.

Marlin answers `ok` as soon as a move is queued, and M114 reports the END of the queued moves,
so the real position during a move must be computed. Each move is modelled as a trapezoid
(accelerate, cruise, decelerate) limited by the speed and acceleration of every axis.
Moves are treated one by one (rest to rest), which is slightly conservative.
"""

import math
import threading
import time

from . import gcode

COMPUTED, VERIFIED = "computed", "verified"


class MovePlan:
    """One straight move from `start` to `target` ({'X','Y','Z'} in mm)."""

    def __init__(self, start, target, speed, accel):
        self.start, self.target = dict(start), dict(target)
        self.speed, self.accel = speed, accel                   # mm/s along the path, mm/s²
        self.length = math.sqrt(sum((target[a] - start[a]) ** 2 for a in gcode.AXES))
        accel_distance = speed * speed / (2 * accel)
        if 2 * accel_distance >= self.length:                   # triangle: no cruise phase
            self.peak = math.sqrt(accel * self.length)
            self.t_accel = self.peak / accel
            self.t_cruise = 0.0
        else:
            self.peak = speed
            self.t_accel = speed / accel
            self.t_cruise = (self.length - 2 * accel_distance) / speed
        self.duration = 2 * self.t_accel + self.t_cruise

    def distance_at(self, t):
        t = max(0.0, min(self.duration, t))
        a, ta, tc = self.accel, self.t_accel, self.t_cruise
        if t <= ta:
            return 0.5 * a * t * t
        if t <= ta + tc:
            return 0.5 * a * ta * ta + self.peak * (t - ta)
        remaining = self.duration - t
        return self.length - 0.5 * a * remaining * remaining

    def position_at(self, t):
        fraction = self.distance_at(t) / self.length if self.length else 1.0
        return {a: self.start[a] + (self.target[a] - self.start[a]) * fraction for a in gcode.AXES}


def plan_move(profile, start, target, speed):
    """MovePlan for a move at `speed` (mm/min), or None if it is shorter than a micron.

    The path speed is lowered so that no axis exceeds its own maximum; the same for the
    acceleration (and the travel acceleration of the printer).
    """
    delta = {a: target[a] - start[a] for a in gcode.AXES}
    length = math.sqrt(sum(d * d for d in delta.values()))
    if length < 1e-3:
        return None
    path_speed = speed / 60.0
    path_accel = profile.travel_accel
    for axis, d in delta.items():
        if abs(d) < 1e-9:
            continue
        share = abs(d) / length                       # part of the path travelled by this axis
        path_speed = min(path_speed, profile.max_speed(axis) / 60.0 / share)
        path_accel = min(path_accel, profile.max_accel(axis) / share)
    return MovePlan(start, target, path_speed, path_accel)


class PositionTracker:
    """Position of the head at any time, from the moves sent and the last verified position."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._base = {a: 0.0 for a in gcode.AXES}
        self._plans = []                                  # [(t_start, t_end, plan)]
        self._kind = VERIFIED

    def sync(self, position):
        """A position read from the printer (after M400): it is the truth."""
        with self._lock:
            self._base, self._plans, self._kind = dict(position), [], VERIFIED

    def queue(self, plan, submitted_at=None):
        """Register a move sent to Marlin. submitted_at is captured just before serial send."""
        with self._lock:
            now = self._clock() if submitted_at is None else submitted_at
            t0 = max(now, self._plans[-1][1]) if self._plans else now
            self._plans.append((t0, t0 + plan.duration, plan))
            self._kind = COMPUTED
            return t0, t0 + plan.duration

    def end_position(self):
        """Where the head will be once everything queued is done."""
        with self._lock:
            return dict(self._plans[-1][2].target) if self._plans else dict(self._base)

    def remaining(self):
        """Seconds until the queued moves are finished."""
        with self._lock:
            return max(0.0, self._plans[-1][1] - self._clock()) if self._plans else 0.0

    def position(self, now=None):
        """(position, kind) at `now`: interpolated while moving."""
        with self._lock:
            now = self._clock() if now is None else now
            return self._position_locked(now), self._kind

    def _position_locked(self, now):
        position = dict(self._base)
        for t0, t1, plan in self._plans:
            if now >= t1:
                position = dict(plan.target)
            elif now >= t0:
                return plan.position_at(now - t0)
            else:
                break
        return position

    def stop(self):
        """Quick stop (M410): the head stays where it is now; the rest is cancelled."""
        with self._lock:
            self._base = self._position_locked(self._clock())
            self._plans = []
            self._kind = COMPUTED
