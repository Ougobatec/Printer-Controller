"""Runs a program: for each point, move, wait, pause, measure."""

import threading
import time

from ..printer.connection import Aborted, PrinterError
from ..sensor.base import SensorError
from .model import AVERAGE, PATH, SIMPLE

IDLE, RUNNING, PAUSED, STOPPED, FINISHED, ERROR = "idle", "running", "paused", "stopped", "finished", "error"


class _Stop(Exception):
    pass


class ProgramRunner:
    """`on_event(name, **data)` is called from the runner thread: "state", "step", "error"."""

    def __init__(self, printer, recorder, store, on_event=None):
        self.printer, self.recorder, self.store = printer, recorder, store
        self._on_event = on_event or (lambda name, **data: None)
        self.state = IDLE
        self.current = None
        self._cancel = threading.Event()
        self._resume = threading.Event()
        self._resume.set()
        self._thread = None

    @property
    def active(self):
        return self.state in (RUNNING, PAUSED)

    def _set_state(self, state):
        self.state = state
        self._on_event("state", state=state)

    # --- controls ------------------------------------------------------------------------
    def start(self, program):
        if self.active:
            return
        self._cancel.clear()
        self._resume.set()
        self._set_state(RUNNING)
        self._thread = threading.Thread(target=self._run, args=(program,), daemon=True)
        self._thread.start()

    def pause(self):
        """Takes effect at the end of the current step (a move in progress is not interrupted)."""
        if self.state == RUNNING:
            self._resume.clear()
            self._set_state(PAUSED)

    def resume(self):
        if self.state == PAUSED:
            self._resume.set()
            self._set_state(RUNNING)

    def stop(self):
        """Cancel the program; the caller also stops the printer (`Printer.stop_motion`)."""
        self._cancel.set()
        self._resume.set()

    def go_to_start(self, program, speed):
        """Move to the first point of the program (blocking)."""
        index = program.first_move_index()
        if index is not None:
            p = program.points[index]
            self.printer.move_to(p.x, p.y, p.z, speed)

    # --- the program ---------------------------------------------------------------------------
    def _checkpoint(self):
        self._resume.wait()
        if self._cancel.is_set():
            raise _Stop()

    def _sleep(self, seconds):
        """A pause that can be cancelled."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._cancel.wait(min(0.05, max(0.0, end - time.monotonic()))):
                raise _Stop()

    def _run(self, program):
        try:
            with self.recorder.acq.exclusive():
                self.store.start_run()
                for index, point in enumerate(program.points):
                    self._checkpoint()
                    self.current = index
                    self._on_event("step", index=index)
                    self._step(index, point)
            self._set_state(FINISHED)
        except (_Stop, Aborted):
            self._set_state(STOPPED)
        except (PrinterError, SensorError) as exc:
            self._on_event("error", message=str(exc))
            self._set_state(ERROR)
        finally:
            self.current = None

    def _step(self, index, point):
        printer, measure = self.printer, point.measure
        if point.has_coordinates:
            along = measure.kind == PATH and index > 0                  # never on the first point
            plan = printer.move_to(point.x, point.y, point.z, point.speed, wait=not along)
            if along:
                if plan is not None:
                    self.recorder.path(measure, plan.duration, printer.position, self._cancel)
                    self._checkpoint()
                printer.wait_moves()
        self._sleep(point.pause)
        if measure.kind in (SIMPLE, AVERAGE):
            position, _ = printer.position()                              # verified at the end of the move
            where = {a: (v if v is not None else position[a]) for a, v in point.coordinates().items()}
            if measure.kind == SIMPLE:
                self.recorder.simple(where)
            else:
                self.recorder.average(where, measure, self._cancel)
            self._checkpoint()
