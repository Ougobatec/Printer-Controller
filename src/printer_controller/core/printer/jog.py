"""Manual moves: the printer moves while a button is held and stops when it is released.

While a button is held, short pieces (about 0.25 s of travel each) are queued one after the
other, never more than 0.3 s ahead. So the printer never has a long queue to cancel, and
repeated presses cannot flood it: a new press is ignored until the previous one has fully
stopped (state machine IDLE -> MOVING -> STOPPING -> IDLE).
"""

import threading
import time

from .connection import PrinterError

IDLE, MOVING, STOPPING = "idle", "moving", "stopping"
SEGMENT_S = 0.25          # travel time of each queued piece
AHEAD_S = 0.30            # maximum time of moves waiting in the printer


class Jogger:
    def __init__(self, printer):
        self._printer = printer
        self._lock = threading.Lock()
        self.state = IDLE
        self._hold = False
        self._thread = None

    def start(self, axis, direction, speed):
        """Begin moving `axis` (+1 / -1) at `speed` mm/min. Returns False if ignored."""
        with self._lock:
            if self.state != IDLE or not self._printer.ready:
                return False
            self.state, self._hold = MOVING, True
        self._thread = threading.Thread(target=self._run, args=(axis, direction, speed), daemon=True)
        self._thread.start()
        return True

    def stop(self):
        """Button released."""
        self._hold = False

    def _run(self, axis, direction, speed):
        printer = self._printer
        released = True
        try:
            speed = printer.effective_speed(speed, axis)
            step = max(0.05, speed / 60.0 * SEGMENT_S)
            low, high = printer.profile.limits()[axis]
            while self._hold:
                if printer.tracker.remaining() > AHEAD_S:
                    time.sleep(0.02)
                    continue
                current = printer.tracker.end_position()[axis]
                target = max(low, min(high, current + direction * step))
                if abs(target - current) < 1e-3:         # at the end of the axis
                    released = False
                    break
                printer.queue_move({axis: target}, speed)
        except PrinterError:
            released = True
        finally:
            with self._lock:
                self.state = STOPPING
            try:
                if released:
                    printer.stop_motion()                # released: stop right now
                else:
                    printer.wait_moves()                 # limit reached: let the last piece finish
            except PrinterError:
                pass
            with self._lock:
                self.state, self._hold = IDLE, False
