"""Any device with a serial port that sends a value continuously or answers a command."""

from dataclasses import dataclass, field

from .base import Sensor, SensorError
from .line_parser import ParseError, ParserConfig, extract

EOL = {"none": "", "LF": "\n", "CR": "\r", "CRLF": "\r\n"}
ENCODINGS = ("ascii", "utf-8")


@dataclass
class SerialConfig:
    port: str = ""
    baudrate: int = 115200
    command: str = ""                    # empty: the device sends by itself
    eol: str = "LF"                      # end of the command: none | LF | CR | CRLF
    encoding: str = "ascii"
    parser: ParserConfig = field(default_factory=ParserConfig)
    example: str = ""                    # a line sent by the device, to check the extraction
    timeout_s: float = 1.0
    max_rate_hz: float = 10000.0

    @classmethod
    def from_dict(cls, data):
        data = data or {}
        base = cls()
        for key in ("port", "command", "eol", "encoding", "example"):
            setattr(base, key, str(data.get(key, getattr(base, key))))
        for key in ("timeout_s", "max_rate_hz"):
            setattr(base, key, float(data.get(key, getattr(base, key))))
        base.baudrate = int(data.get("baudrate", base.baudrate))
        base.parser = ParserConfig.from_dict(data.get("parser"))
        return base

    def to_dict(self):
        out = dict(self.__dict__)
        out["parser"] = self.parser.to_dict()
        return out


def _open_serial(port, baudrate, timeout):
    import serial
    return serial.Serial(port, baudrate, timeout=timeout)


class SerialSensor(Sensor):
    def __init__(self, config, serial_factory=_open_serial):
        self.config = config
        self.max_rate_hz = config.max_rate_hz
        self.timeout_s = config.timeout_s
        self._factory = serial_factory
        self._serial = None

    def open(self):
        try:
            self._serial = self._factory(self.config.port, self.config.baudrate, self.config.timeout_s)
        except Exception as exc:
            raise SensorError(f"{self.config.port}: {exc}") from None

    def close(self):
        port, self._serial = self._serial, None
        if port is not None:
            try:
                port.close()
            except Exception:
                pass

    def read_line(self):
        """The next line of text from the device (after sending the command, if there is one)."""
        port, c = self._serial, self.config
        if port is None:
            raise SensorError("not connected")
        try:
            if c.command:
                port.reset_input_buffer()
                port.write((c.command + EOL.get(c.eol, "")).encode(c.encoding, errors="replace"))
                port.flush()
                raw = port.readline()
            else:
                raw = port.readline()
                while getattr(port, "in_waiting", 0):          # the device talks on its own: take the newest
                    newer = port.readline()
                    if newer:
                        raw = newer
        except Exception as exc:
            raise SensorError(str(exc)) from None
        if not raw:
            raise SensorError("timeout")
        return raw.decode(c.encoding, errors="replace").strip()

    def read(self):
        line = self.read_line()
        try:
            return extract(line, self.config.parser)
        except ParseError as exc:
            raise SensorError(f"{exc.code}: {line!r}") from None

    def info(self):
        return {"port": self.config.port, "baudrate": str(self.config.baudrate)}
