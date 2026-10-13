"""Takes the measurements of a program and records them in the result store.

This is the only link between the program and the sensor.
"""

import time

from ..results.store import COMPUTED, DEFINED
from .model import AVERAGE, PATH, SIMPLE


class Recorder:
    def __init__(self, acquisition, store, settings):
        self.acq, self.store, self.settings = acquisition, store, settings

    def _record(self, timestamp, raw, position, position_kind, kind, group_id=None):
        return self.store.add(timestamp, position, position_kind, kind, raw, self.acq.to_value(raw),
                              self.settings.sensor.unit, group_id)

    def simple(self, position):
        """One reading at a point (position defined by the program)."""
        raw = self.acq.read_raw()
        return self._record(time.time(), raw, position, DEFINED, SIMPLE)

    def average(self, position, measure, cancel=None):
        """Several readings at a point; they form a group (statistics in the second table)."""
        count, rate = measure.samples(max_rate=self.acq.max_rate)
        group = self.store.new_group()
        try:
            self.acq.sample(rate, count, cancel,
                            lambda t, raw: self._record(t, raw, position, DEFINED, AVERAGE, group))
        finally:
            self.store.close_group(group)
        return group

    def path(self, measure, move_duration, position_now, cancel=None):
        """Readings taken WHILE the head moves; each has a computed position.

        `position_now()` returns ({"X","Y","Z"}, kind) for the current instant.
        """
        count, rate = measure.samples(move_duration, self.acq.max_rate)

        def on_sample(t, raw):
            position, _ = position_now()
            self._record(t, raw, position, COMPUTED, PATH)

        return self.acq.sample(rate, count, cancel, on_sample)
