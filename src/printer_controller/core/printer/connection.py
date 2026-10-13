"""Serial link with a Marlin printer.

* One reader thread turns the incoming bytes into lines (also shown in the console).
* `send()` writes ONE command and waits for its `ok`; commands are sent one at a time.
* `quick_stop()` bypasses the queue: it interrupts the command being waited for.
"""

import queue
import threading
import time
from . import gcode


class PrinterError(Exception):
    pass


class PrinterTimeout(PrinterError):
    pass


class Aborted(PrinterError):
    pass


def _open_serial(port, baudrate):
    import serial
    return serial.Serial(port, baudrate, timeout=0.1, write_timeout=2)


class Connection:
    def __init__(self, on_traffic=None, serial_factory=_open_serial):
        self._on_traffic = on_traffic or (lambda direction, text: None)
        self._factory = serial_factory
        self._serial = None
        self._rx = queue.Queue()
        self._lock = threading.Lock()              # one command at a time
        self._abort = threading.Event()
        self._stopping = threading.Event()         # M410 sent: swallow the oks that follow
        self._stop_ok = threading.Event()
        self._closing = False

    @property
    def connected(self):
        return self._serial is not None

    # --- open / close ----------------------------------------------------------------
    def open(self, port, baudrate, startup_timeout=10.0):
        """Open the port and wait until the printer answers (it may reboot when the port opens)."""
        try:
            self._serial = self._factory(port, baudrate)
        except Exception as exc:
            self._serial = None
            raise PrinterError(f"{port}: {exc}") from None
        self._closing = False
        self._abort.clear()
        threading.Thread(target=self._read_loop, args=(self._serial,), daemon=True).start()
        deadline = time.monotonic() + startup_timeout
        while True:
            try:
                self.send(gcode.TEMPERATURE, timeout=1.5)
                return
            except PrinterTimeout:
                if time.monotonic() > deadline:
                    self.close()
                    raise PrinterTimeout(f"{port}: no answer from the printer after "
                                         f"{startup_timeout:g} s") from None

    def close(self):
        self._closing = True
        serial_port, self._serial = self._serial, None
        if serial_port is not None:
            try:
                serial_port.close()
            except Exception:
                pass

    # --- reading ---------------------------------------------------------------------------
    def _read_loop(self, port):
        while not self._closing:
            try:
                raw = port.readline()
            except Exception:
                break
            if not raw:
                continue
            line = raw.decode("ascii", errors="replace").strip()
            if not line:
                continue
            self._on_traffic("rx", line)
            if self._stopping.is_set() and line.startswith("ok"):
                self._stop_ok.set()                  # answer of M410 (or of a cancelled command)
                continue
            self._rx.put(line)
        if not self._closing:
            self._rx.put("Error:connection lost")

    # --- sending ---------------------------------------------------------------------------
    def send(self, command, timeout=30.0):
        """Send a command, return the lines printed before its `ok`."""
        with self._lock:
            if self._serial is None:
                raise PrinterError("printer not connected")
            if self._abort.is_set():
                raise Aborted("aborted")
            while not self._rx.empty():              # leftovers of an earlier command
                self._rx.get_nowait()
            self._write(command)
            lines = []
            deadline = time.monotonic() + timeout
            while True:
                if self._abort.is_set():
                    raise Aborted("aborted")
                try:
                    line = self._rx.get(timeout=0.05)
                except queue.Empty:
                    if time.monotonic() > deadline:
                        raise PrinterTimeout(f"no answer to {command!r} after {timeout:g} s") from None
                    continue
                if line.startswith("ok"):
                    return lines
                if line.startswith("Error:"):
                    raise PrinterError(line)
                if "busy" in line:
                    deadline = time.monotonic() + timeout        # still working: wait longer
                lines.append(line)

    def _write(self, command):
        self._on_traffic("tx", command)
        try:
            self._serial.write((command + "\n").encode("ascii"))
        except Exception as exc:
            raise PrinterError(f"write error: {exc}") from None

    # --- stops --------------------------------------------------------------------------------
    def quick_stop(self):
        """M410: interrupt the move in progress and whatever command waits for it."""
        if self._serial is None:
            return
        self._abort.set()
        self._stopping.set()
        self._stop_ok.clear()
        try:
            self._write(gcode.QUICK_STOP)
        except PrinterError:
            pass
        self._stop_ok.wait(2.0)
        time.sleep(0.25)                              # other oks (cancelled M400...) may follow
        with self._lock:                              # the interrupted command has left
            while not self._rx.empty():
                self._rx.get_nowait()
            self._stopping.clear()
            self._abort.clear()

    def emergency_stop(self):
        """M112: the printer halts and must be restarted; the connection is closed."""
        if self._serial is not None:
            self._abort.set()
            try:
                self._write(gcode.EMERGENCY)
            except PrinterError:
                pass
        self.close()
