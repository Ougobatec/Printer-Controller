"""National Instruments NI-DAQmx cards (needs the NI-DAQmx driver and `pip install nidaqmx`)."""

from dataclasses import dataclass, field

from .base import Sensor, SensorError

DEFAULT_MAX_RATE = 1000.0


def _nidaqmx():
    try:
        import nidaqmx
        import nidaqmx.constants
        import nidaqmx.system
    except ImportError:
        raise SensorError("nidaqmx_missing") from None
    return nidaqmx


@dataclass
class NiDevice:
    """What a detected card says about itself (None = the card did not tell)."""
    name: str = ""                                   # "Dev1"
    product: str = ""                                # "USB-6211"
    channels: list = field(default_factory=list)     # ["ai0", "ai1", ...]
    terminals: list = field(default_factory=list)    # ["DEFAULT", "RSE", "DIFF", ...]
    ranges: list = field(default_factory=list)       # [(-10.0, 10.0), (-5.0, 5.0), ...]
    resolution: float = None                         # bits
    max_rate_hz: float = None

    @property
    def label(self):
        return f"{self.name} - {self.product}" if self.product else self.name


@dataclass
class NidaqConfig:
    device: str = ""
    channel: str = "ai0"
    terminal: str = "DEFAULT"
    range_lo: float = -10.0
    range_hi: float = 10.0
    timeout_s: float = 2.0

    @classmethod
    def from_dict(cls, data):
        base = cls()
        for key, default in base.__dict__.items():
            value = (data or {}).get(key, default)
            setattr(base, key, float(value) if isinstance(default, float) else str(value))
        return base

    def to_dict(self):
        return dict(self.__dict__)


def _safe(getter, default=None):
    try:
        return getter()
    except Exception:
        return default


def detect_devices():
    """Cards currently plugged in, with the options they accept. Raises SensorError if NI-DAQmx is unusable."""
    nidaqmx = _nidaqmx()
    try:
        devices = list(nidaqmx.system.System.local().devices)
    except Exception as exc:
        raise SensorError(f"driver: {exc}") from None
    found = []
    for device in devices:
        names = _safe(lambda: list(device.ai_physical_chans.channel_names), [])
        flat = _safe(lambda: list(device.ai_voltage_rngs), [])
        found.append(NiDevice(
            name=device.name,
            product=_safe(lambda: device.product_type, "") or "",
            channels=[n.split("/")[-1] for n in names],
            terminals=[getattr(c, "name", str(c)) for c in _safe(lambda: list(device.ai_term_cfgs), [])],
            ranges=[(flat[i], flat[i + 1]) for i in range(0, len(flat) - 1, 2)],
            resolution=_safe(lambda: float(device.ai_resolution)),
            max_rate_hz=_safe(lambda: float(device.ai_max_single_chan_rate))))
    return found


class NidaqSensor(Sensor):
    def __init__(self, config, device=None):
        self.config = config
        self.device = device
        self.timeout_s = config.timeout_s
        self.max_rate_hz = (device.max_rate_hz if device and device.max_rate_hz else DEFAULT_MAX_RATE)
        self._task = None

    def open(self):
        nidaqmx = _nidaqmx()
        c = self.config
        try:
            terminal = getattr(nidaqmx.constants.TerminalConfiguration, c.terminal)
            task = nidaqmx.Task()
            task.ai_channels.add_ai_voltage_chan(f"{c.device}/{c.channel}", terminal_config=terminal,
                                                 min_val=c.range_lo, max_val=c.range_hi)
        except Exception as exc:
            raise SensorError(str(exc)) from None
        self._task = task

    def close(self):
        task, self._task = self._task, None
        if task is not None:
            try:
                task.close()
            except Exception:
                pass

    def read(self):
        if self._task is None:
            raise SensorError("not connected")
        try:
            return float(self._task.read(timeout=self.timeout_s))
        except Exception as exc:
            raise SensorError(str(exc)) from None

    def info(self):
        d = self.device
        return {"device": d.label if d else self.config.device,
                "resolution": f"{d.resolution:g}" if d and d.resolution else "",
                "max_rate_hz": f"{self.max_rate_hz:g}"}
