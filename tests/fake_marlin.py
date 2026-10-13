"""An in-memory Marlin printer with the serial-port interface of pyserial.

It behaves like the real firmware on the points that matter for the app:
* `ok` is sent as soon as a move is queued; M400 answers when the queue is empty;
* M114 reports the END of the queued moves, not the instantaneous position;
* G28 O only homes when the printer has not been homed since power-on;
* M410 stops the moves where the head is.
"""

import queue
import re
import threading
import time


class FakeMarlin:
    def __init__(self, homed=False, boot_delay=0.0, has_m503=True, has_m211=True, home_time=0.3,
                 speed_scale=1.0):
        self.timeout = 0.1
        self.is_open = True
        self.homed = homed
        self.home_count = 0
        self.halted = False
        self.has_m503, self.has_m211 = has_m503, has_m211
        self.home_time, self.speed_scale = home_time, speed_scale
        self.commands = []                      # every command received
        self._out = queue.Queue()
        self._lock = threading.RLock()
        self._moves = []                        # [(t0, t1, start, target)]
        self._rest = {"X": 0.0, "Y": 0.0, "Z": 0.0}
        self._booted_at = time.monotonic() + boot_delay
        if boot_delay:
            threading.Timer(boot_delay, lambda: self._say("start", "echo: Marlin 1.1.9")).start()

    # --- pyserial interface ---------------------------------------------------------------
    def write(self, data):
        for line in data.decode("ascii").splitlines():
            if line.strip():
                self._handle(line.strip())

    def readline(self):
        try:
            return (self._out.get(timeout=self.timeout) + "\n").encode("ascii")
        except queue.Empty:
            return b""

    def close(self):
        self.is_open = False

    # --- helpers -----------------------------------------------------------------------------
    def _say(self, *lines):
        for line in lines:
            self._out.put(line)

    def _later(self, delay, *lines):
        threading.Timer(delay, lambda: self._say(*lines)).start()

    def actual_position(self, now=None):
        """Real position of the head (what a perfect sensor would see)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            position = dict(self._rest)
            for t0, t1, start, target in self._moves:
                if now >= t1:
                    position = dict(target)
                elif now >= t0:
                    fraction = (now - t0) / (t1 - t0)
                    return {a: start[a] + (target[a] - start[a]) * fraction for a in "XYZ"}
            return position

    def planned_end(self):
        with self._lock:
            return dict(self._moves[-1][3]) if self._moves else dict(self._rest)

    def busy_until(self):
        with self._lock:
            return self._moves[-1][1] if self._moves else 0.0

    def queued_seconds(self):
        return max(0.0, self.busy_until() - time.monotonic())

    # --- commands -----------------------------------------------------------------------------------
    def _handle(self, command):
        self.commands.append(command)
        if self.halted or time.monotonic() < self._booted_at:
            return
        text = command.split(";")[0].strip().upper()
        word = text.split(" ")[0]
        if word == "M105":
            self._say("ok T:21.0 /0.0 B:20.0 /0.0 @:0 B@:0")
        elif word == "M115":
            self._say("FIRMWARE_NAME:Marlin 1.1.9 (Github) SOURCE_CODE_URL:https://github.com/MarlinFirmware/Marlin "
                      "PROTOCOL_VERSION:1.0 MACHINE_TYPE:Ender-3 EXTRUDER_COUNT:1 UUID:cede2a2f", "ok")
        elif word == "M503":
            if self.has_m503:
                self._say("echo:  M203 X500.00 Y500.00 Z10.00 E50.00", "echo:  M201 X500 Y500 Z100 E5000",
                          "echo:  M204 P500.00 R500.00 T500.00", "echo:  M92 X80.00 Y80.00 Z400.00 E93.00")
            self._say("ok")
        elif word == "M211":
            if self.has_m211:
                self._say("echo:Soft endstops: ON    Min:  X0.00  Y0.00  Z0.00    Max:  X235.00  Y235.00  Z250.00")
            self._say("ok")
        elif word == "M114":
            p = self.planned_end()
            self._say(f"X:{p['X']:.2f} Y:{p['Y']:.2f} Z:{p['Z']:.2f} E:0.00 Count X:{p['X'] * 80:.0f} "
                      f"Y:{p['Y'] * 80:.0f} Z:{p['Z'] * 400:.0f}", "ok")
        elif word in ("G90", "G91"):
            self._say("ok")
        elif word == "G28":
            if " O" in text and self.homed:
                self._say("ok")
            else:
                self.home_count += 1
                self.homed = True
                with self._lock:
                    self._moves, self._rest = [], {"X": 0.0, "Y": 0.0, "Z": 0.0}
                self._later(self.home_time, "ok")
        elif word == "G1":
            self._queue_move(text)
        elif word == "M400":
            remaining = self.busy_until() - time.monotonic()
            if remaining > 0:
                self._later(remaining, "ok")
            else:
                self._say("ok")
        elif word == "M410":
            with self._lock:
                self._rest = self.actual_position()
                self._moves = []
            self._say("ok")
        elif word == "M112":
            self.halted = True
            self._say("Error:Printer halted. kill() called!")
        else:
            self._say(f"echo:Unknown command: \"{command}\"", "ok")

    def _queue_move(self, text):
        values = {k: float(v) for k, v in re.findall(r"([XYZF])(-?\d+(?:\.\d+)?)", text)}
        start = self.planned_end()
        target = {a: values.get(a, start[a]) for a in "XYZ"}
        feed = values.get("F", 1000.0) / 60.0                         # mm/s
        length = sum((target[a] - start[a]) ** 2 for a in "XYZ") ** 0.5
        duration = (length / feed + 0.05) / self.speed_scale
        with self._lock:
            t0 = max(time.monotonic(), self.busy_until())
            self._moves.append((t0, t0 + duration, start, target))
        self._say("ok")
