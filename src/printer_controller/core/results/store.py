"""Everything measured by a program: the table of measurements and the table of average groups."""

import statistics
import threading
import time
from dataclasses import dataclass

DEFINED, COMPUTED = "defined", "computed"      # a position given by the program / computed during a move


@dataclass
class Measurement:
    n: int
    timestamp: float               # wall clock (seconds since 1970)
    elapsed_s: float               # since the start of the program
    x: float
    y: float
    z: float
    position_kind: str             # defined | computed
    kind: str                      # simple | average | path
    raw: float
    value: float                   # raw × multiplier
    unit: str
    group_id: int = None           # set for the readings of an average


@dataclass
class Group:
    id: int
    first_timestamp: float = 0.0
    duration_s: float = 0.0
    count: int = 0
    x: float = None
    y: float = None
    z: float = None
    mean: float = None
    std: float = None
    min: float = None
    max: float = None
    unit: str = ""


class ResultStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._measurements, self._groups = [], []
        self._t0 = time.time()
        self.version = 0                # grows at every change: lets the display refresh only when needed

    def start_run(self):
        """A new program starts: previous results are removed."""
        with self._lock:
            self._measurements, self._groups = [], []
            self._t0 = time.time()
            self.version += 1

    def clear(self):
        self.start_run()

    def new_group(self):
        with self._lock:
            group = Group(id=len(self._groups) + 1)
            self._groups.append(group)
            self.version += 1
            return group.id

    def add(self, timestamp, position, position_kind, kind, raw, value, unit, group_id=None):
        """Record one reading. `position` is {"X", "Y", "Z"} (a value may be None)."""
        with self._lock:
            m = Measurement(len(self._measurements) + 1, timestamp, timestamp - self._t0, position.get("X"),
                            position.get("Y"), position.get("Z"), position_kind, kind, raw, value, unit, group_id)
            self._measurements.append(m)
            self.version += 1
            return m

    def close_group(self, group_id):
        """Compute the statistics of a group once its readings are all recorded."""
        with self._lock:
            group = self._groups[group_id - 1]
            members = [m for m in self._measurements if m.group_id == group_id]
            if members:
                values = [m.value for m in members]
                first = members[0]
                group.first_timestamp = first.timestamp
                group.duration_s = members[-1].timestamp - first.timestamp
                group.count = len(members)
                group.x, group.y, group.z, group.unit = first.x, first.y, first.z, first.unit
                group.mean = statistics.fmean(values)
                group.std = statistics.stdev(values) if len(values) > 1 else 0.0
                group.min, group.max = min(values), max(values)
            self.version += 1

    def snapshot(self):
        """(measurements, groups): copies of the lists, safe to use from another thread."""
        with self._lock:
            return list(self._measurements), list(self._groups)

    def __len__(self):
        return len(self._measurements)
