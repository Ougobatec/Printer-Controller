"""The printer as the rest of the app sees it: connect, identify, home, move, stop."""

from . import gcode
from .connection import Connection, PrinterError, _open_serial
from .identify import read_printer
from .jog import Jogger
from .motion import PositionTracker, plan_move
from .profile import PrinterProfile


class Printer:
    def __init__(self, settings, on_traffic=None, serial_factory=_open_serial):
        self.settings = settings
        self.connection = Connection(on_traffic, serial_factory)
        self.profile = PrinterProfile()
        self.tracker = PositionTracker()
        self.jogger = Jogger(self)
        self.homed = False

    # --- state ------------------------------------------------------------------------
    @property
    def connected(self):
        return self.connection.connected

    @property
    def ready(self):
        """Connected and homed: moves are allowed."""
        return self.connected and self.homed

    def position(self):
        """(position, kind) with kind "computed" while moving, "verified" after a reading."""
        return self.tracker.position()

    # --- connection ---------------------------------------------------------------------
    def connect(self, port, baudrate, log=lambda message: None):
        """Open, identify the printer, check the homing. Blocking (run it in the background)."""
        s = self.settings.printer
        self.connection.open(port, baudrate, s.startup_timeout)
        try:
            self.connection.send(gcode.ABSOLUTE, timeout=s.command_timeout)
            self.read_printer()
            log(f"{self.profile.firmware or '?'} · {self.profile.model or '?'}")
            self.homed = False
            if s.auto_home:
                log("homing check (G28 O)…")
                self.home(only_if_needed=True)
            self.sync_position()
        except PrinterError:
            self.connection.close()
            raise

    def disconnect(self):
        self.jogger.stop()
        self.connection.close()
        self.homed = False

    def read_printer(self):
        """(Re)read the printer's own values into the profile."""
        info = read_printer(self.connection, self.settings.printer.command_timeout)
        self.profile.apply_detected(info)
        self.profile.load_overrides(self.settings.printer.overrides)
        return info

    # --- homing -----------------------------------------------------------------------------
    def home(self, only_if_needed=False):
        """G28 (or G28 O: only if the printer has not been homed since power-on)."""
        s = self.settings.printer
        command = gcode.HOME_IF_NEEDED if only_if_needed else gcode.HOME
        self.connection.send(command, timeout=s.home_timeout)
        self.homed = True
        self.read_printer()                     # the soft limits can depend on the homing
        self.sync_position()

    # --- position ------------------------------------------------------------------------------
    def sync_position(self):
        """Read M114 and make it the verified position (only valid when nothing is moving)."""
        for line in self.connection.send(gcode.POSITION, timeout=self.settings.printer.command_timeout):
            position = gcode.parse_position(line)
            if position:
                self.tracker.sync(position)
                return position
        return None

    def wait_moves(self):
        """Wait for the queued moves (M400), then verify the position."""
        timeout = self.tracker.remaining() + self.settings.printer.move_margin
        self.connection.send(gcode.WAIT_MOVES, timeout=timeout)
        self.sync_position()

    # --- moves -----------------------------------------------------------------------------------
    def effective_speed(self, speed, axis=None):
        """`speed` (mm/min) limited by the app cap and by the printer."""
        cap = self.settings.printer.speed_cap
        limit = self.profile.max_speed(axis) if axis else self.profile.fastest_speed()
        return min(speed, limit, cap) if cap > 0 else min(speed, limit)

    def check_target(self, target):
        """Moves outside the limits are always refused."""
        for axis, (low, high) in self.profile.limits().items():
            if not low - 1e-6 <= target[axis] <= high + 1e-6:
                raise PrinterError(f"{axis}={target[axis]:g} is outside the limits ({low:g} to {high:g} mm)")

    def queue_move(self, partial, speed):
        """Send one move (axes missing from `partial` stay where they are); returns the MovePlan.

        Does not wait for the end of the move: the tracker computes the position meanwhile.
        """
        if not self.connected:
            raise PrinterError("printer not connected")
        if not self.homed:
            raise PrinterError("homing required")
        start = self.tracker.end_position()
        target = {a: start[a] if partial.get(a) is None else partial[a] for a in gcode.AXES}
        self.check_target(target)
        speed = self.effective_speed(speed)
        plan = plan_move(self.profile, start, target, speed)
        if plan is None:
            return None
        submitted_at = __import__("time").monotonic()
        self.connection.send(gcode.move(*(target[a] for a in gcode.AXES), speed),
                             timeout=self.settings.printer.command_timeout)
        self.tracker.queue(plan, submitted_at=submitted_at)
        return plan

    def move_to(self, x=None, y=None, z=None, speed=300.0, wait=True):
        plan = self.queue_move({"X": x, "Y": y, "Z": z}, speed)
        if wait:
            self.wait_moves()
        return plan

    # --- stops ---------------------------------------------------------------------------------------
    def stop_motion(self):
        """Quick stop (M410), then read where the head really is."""
        self.jogger.stop()
        self.connection.quick_stop()
        self.tracker.stop()
        try:
            self.sync_position()
        except PrinterError:
            pass

    def emergency_stop(self):
        self.jogger.stop()
        self.connection.emergency_stop()
        self.tracker.stop()
        self.homed = False

    # --- free G-code (console) ------------------------------------------------------------------------
    def send_raw(self, command):
        """Send a command typed by the user; the position is re-read after a move or a homing."""
        s = self.settings.printer
        head = command.strip().split(" ")[0].upper()
        lines = self.connection.send(command, timeout=s.home_timeout if head == "G28" else s.command_timeout)
        if head in ("G0", "G1", "G2", "G3", "G28", "G92"):
            self.connection.send(gcode.WAIT_MOVES, timeout=s.home_timeout)
            self.sync_position()
            if head == "G28":
                self.homed = True
        return lines
