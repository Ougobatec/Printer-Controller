"""Reading the sensor: live monitoring (not recorded) and timed samples for the programs."""

import threading
import time
from collections import deque
from contextlib import contextmanager

from .base import SensorError
from .filters import make_filter


class Acquisition:
    def __init__(self, settings, clock=time.monotonic):
        self.settings = settings
        self.sensor = None
        self.error = ""                          # last error of the live monitoring
        self.latest = None                       # (timestamp, filtered value) of the last live reading
        self._clock = clock
        self._lock = threading.RLock()           # one reading at a time
        self._live = deque(maxlen=settings.sensor.live_history)
        self._live_lock = threading.Lock()
        self._live_stop = threading.Event()
        self._live_thread = None
        self._filter = make_filter(settings.sensor.filter, settings.sensor.filter_size)

    # --- connection --------------------------------------------------------------------------
    @property
    def connected(self):
        return self.sensor is not None

    def connect(self, sensor):
        """Open the sensor (blocking) and make it the current one."""
        self.disconnect()
        sensor.open()
        self.sensor = sensor
        self.error = ""

    def disconnect(self):
        self.stop_live()
        sensor, self.sensor = self.sensor, None
        if sensor is not None:
            sensor.close()

    @property
    def max_rate(self):
        return self.sensor.max_rate_hz if self.sensor else 0.0

    # --- values -----------------------------------------------------------------------------------
    def read_raw(self):
        """One raw reading (thread-safe)."""
        with self._lock:
            if self.sensor is None:
                raise SensorError("not connected")
            return self.sensor.read()

    def to_value(self, raw):
        return raw * self.settings.sensor.multiplier

    def apply_settings(self):
        """Filter and history size changed in the settings."""
        s = self.settings.sensor
        self._filter = make_filter(s.filter, s.filter_size)
        with self._live_lock:
            self._live = deque(self._live, maxlen=s.live_history)

    # --- live monitoring ------------------------------------------------------------------------------
    @property
    def live_running(self):
        return self._live_thread is not None and self._live_thread.is_alive()

    def start_live(self):
        if self.live_running or self.sensor is None:
            return
        self._live_stop.clear()
        self._filter.reset()
        self._live_thread = threading.Thread(target=self._live_loop, daemon=True)
        self._live_thread.start()

    def stop_live(self):
        self._live_stop.set()
        thread = self._live_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        self._live_thread = None

    def _live_loop(self):
        next_time = self._clock()
        while not self._live_stop.is_set():
            rate = min(self.settings.sensor.live_rate_hz, self.max_rate) or 1.0
            try:
                value = self._filter.apply(self.to_value(self.read_raw()))
            except SensorError as exc:
                self.error = str(exc)
                break
            now = self._clock()
            with self._live_lock:
                self._live.append((now, value))
            self.latest = (now, value)
            next_time += 1.0 / rate
            delay = next_time - self._clock()
            if delay < -1.0:
                next_time = self._clock()            # far behind: do not try to catch up
            elif delay > 0:
                self._live_stop.wait(delay)

    def live_values(self):
        """[(monotonic time, value)] currently kept (oldest first)."""
        with self._live_lock:
            return list(self._live)

    @contextmanager
    def exclusive(self):
        """Pause the live monitoring while a program takes its own measurements."""
        was_running = self.live_running
        self.stop_live()
        try:
            yield
        finally:
            if was_running:
                self.start_live()

    # --- timed samples (programs) -------------------------------------------------------------------------
    def effective_rate(self, rate):
        """`rate` limited by what the sensor can do."""
        return min(rate, self.max_rate) if self.max_rate else rate

    def sample(self, rate, count, cancel=None, on_sample=None):
        """Take `count` readings, `rate` per second (the first one immediately).

        `on_sample(wall_time, raw)` is called for each; returns the number taken (less if cancelled).
        """
        period = 1.0 / self.effective_rate(rate)
        start = self._clock()
        taken = 0
        for i in range(count):
            if cancel is not None and cancel.is_set():
                break
            wait = start + i * period - self._clock()
            if wait > 0:
                if cancel is not None:
                    if cancel.wait(wait):
                        break
                else:
                    time.sleep(wait)
            raw = self.read_raw()
            taken += 1
            if on_sample:
                on_sample(time.time(), raw)
        return taken
