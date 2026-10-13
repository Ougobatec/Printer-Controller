import sys
import threading
import time
import types
from dataclasses import dataclass

import pytest

from printer_controller.core.sensor import nidaq_sensor
from printer_controller.core.sensor.acquisition import Acquisition
from printer_controller.core.sensor.base import Sensor, SensorError
from printer_controller.core.sensor.filters import make_filter
from printer_controller.core.sensor.line_parser import ParseError, ParserConfig, extract
from printer_controller.core.sensor.serial_sensor import SerialConfig, SerialSensor
from printer_controller.settings import Settings


def cfg(**kw):
    return ParserConfig(**kw)


@pytest.mark.parametrize("line, config, expected", [
    ("MEAS:VOLT 12.534 V", cfg(mode="number", position=1), 12.534),
    ("T=21.5 H=40.0 P=1013", cfg(mode="number", position=2), 40.0),
    ("-3.2e-2", cfg(mode="number"), -0.032),
    ("12,534 V", cfg(mode="number", decimal=","), 12.534),
    ("1;2;3.5;4", cfg(mode="field", separator="semicolon", position=3), 3.5),
    ("a:7:b", cfg(mode="field", separator="colon", position=2), 7.0),
    ("10    20   30", cfg(mode="field", separator="space", position=3), 30.0),
    ("5\t6\t7", cfg(mode="field", separator="tab", position=2), 6.0),
    ("1|2|3", cfg(mode="field", separator="custom", custom_separator="|", position=3), 3.0),
    ("1,5;2,5", cfg(mode="field", separator="semicolon", position=2, decimal=","), 2.5),
    ("VAL=0.75mV", cfg(mode="after_text", text="VAL="), 0.75),
    ("ch1: 4.2 ch2: 9.9", cfg(mode="after_text", text="ch2:"), 9.9),
    ("temp 23.4 C", cfg(mode="regex", regex=r"temp\s+([\d.]+)"), 23.4),
    ("x=5", cfg(mode="regex", regex=r"\d+"), 5.0),
])
def test_extract(line, config, expected):
    assert extract(line, config) == pytest.approx(expected)


@pytest.mark.parametrize("line, config, code", [
    ("", cfg(), "empty"),
    ("no digits here", cfg(), "no_number"),
    ("1 2", cfg(position=3), "position"),
    ("1;2", cfg(mode="field", separator="semicolon", position=5), "position"),
    ("abc;def", cfg(mode="field", separator="semicolon", position=1), "no_number"),
    ("X=1", cfg(mode="after_text", text="Y="), "text_not_found"),
    ("X=1", cfg(mode="after_text", text=""), "no_text"),
    ("X=1", cfg(mode="regex", regex="("), "bad_regex"),
    ("X=1", cfg(mode="regex", regex="zzz"), "no_match"),
])
def test_extract_errors(line, config, code):
    with pytest.raises(ParseError) as error:
        extract(line, config)
    assert error.value.code == code


def test_filters():
    mean = make_filter("moving_average", 3)
    assert [mean.apply(v) for v in (3, 6, 9, 12)] == [3, 4.5, 6, 9]
    median = make_filter("median", 3)
    assert [median.apply(v) for v in (1, 100, 2, 3)] == [1, 50.5, 2, 3]
    expo = make_filter("exponential", 3)                  # alpha = 0.5
    assert [expo.apply(v) for v in (10, 20, 20)] == [10, 15, 17.5]
    assert make_filter("none", 5).apply(7) == 7


class FakeSerial:
    """A device that sends lines by itself, or answers a command."""

    def __init__(self, lines, answers=None):
        self.lines, self.answers, self.written = list(lines), answers or {}, []
        self.in_waiting = 0
        self.closed = False

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self.written.append(data)
        command = data.decode().strip()
        if command in self.answers:
            self.lines.append(self.answers[command])

    def flush(self):
        pass

    def readline(self):
        return (self.lines.pop(0) + "\r\n").encode() if self.lines else b""

    def close(self):
        self.closed = True


def serial_sensor(fake, **kw):
    config = SerialConfig(port="COM9", **kw)
    return SerialSensor(config, serial_factory=lambda port, baud, timeout: fake), fake


def test_serial_sensor_command_mode():
    sensor, fake = serial_sensor(FakeSerial([], {"MEAS:VOLT?": "MEAS:VOLT 12.534 V"}),
                                 command="MEAS:VOLT?", eol="CRLF")
    sensor.open()
    assert sensor.read() == pytest.approx(12.534)
    assert fake.written == [b"MEAS:VOLT?\r\n"]
    sensor.close()
    assert fake.closed


def test_serial_sensor_streaming_takes_a_line_and_times_out():
    sensor, fake = serial_sensor(FakeSerial(["5.5", "6.5"]))
    sensor.open()
    assert sensor.read() == 5.5 and sensor.read() == 6.5
    with pytest.raises(SensorError, match="timeout"):
        sensor.read()


def test_serial_sensor_unreadable_line_is_an_error():
    sensor, _ = serial_sensor(FakeSerial(["hello"]))
    sensor.open()
    with pytest.raises(SensorError, match="no_number"):
        sensor.read()


def test_serial_config_round_trip():
    config = SerialConfig(port="COM3", baudrate=9600, command="X?", parser=ParserConfig(mode="field", position=2))
    again = SerialConfig.from_dict(config.to_dict())
    assert again.to_dict() == config.to_dict()
    assert SerialConfig.from_dict(None).baudrate == 115200


class CountingSensor(Sensor):
    max_rate_hz = 200.0

    def __init__(self, fail_after=None):
        self.n, self.fail_after, self.opened = 0, fail_after, False

    def open(self):
        self.opened = True

    def read(self):
        self.n += 1
        if self.fail_after and self.n > self.fail_after:
            raise SensorError("unplugged")
        return float(self.n)


def make_acquisition(tmp_path, sensor=None):
    settings = Settings.load(tmp_path / "s.json")
    settings.sensor.multiplier = 2.0
    settings.sensor.live_rate_hz = 100.0
    settings.sensor.live_history = 10
    acquisition = Acquisition(settings)
    acquisition.connect(sensor or CountingSensor())
    return acquisition


def test_live_monitoring_keeps_a_limited_history(tmp_path):
    acquisition = make_acquisition(tmp_path)
    acquisition.start_live()
    time.sleep(0.4)
    acquisition.stop_live()
    values = acquisition.live_values()
    assert len(values) == 10                               # history limit
    assert values[-1][1] == values[-1][1] // 1 and values[-1][1] % 2 == 0   # multiplier 2 applied
    assert not acquisition.live_running


def test_live_rate_is_limited_by_the_sensor(tmp_path):
    acquisition = make_acquisition(tmp_path)
    acquisition.settings.sensor.live_rate_hz = 5000.0     # above the sensor's 200 Hz
    acquisition.settings.sensor.live_history = 1000
    acquisition.apply_settings()
    acquisition.start_live()
    time.sleep(0.5)
    acquisition.stop_live()
    assert len(acquisition.live_values()) < 150            # about 200 Hz * 0.5 s = 100


def test_live_error_stops_the_loop_and_reports(tmp_path):
    acquisition = make_acquisition(tmp_path, CountingSensor(fail_after=3))
    acquisition.start_live()
    time.sleep(0.3)
    assert acquisition.error == "unplugged" and not acquisition.live_running


def test_sample_count_rate_and_cancel(tmp_path):
    acquisition = make_acquisition(tmp_path)
    got = []
    start = time.monotonic()
    taken = acquisition.sample(50.0, 6, on_sample=lambda t, raw: got.append(raw))
    assert taken == 6 and got == [1, 2, 3, 4, 5, 6]
    assert 0.08 < time.monotonic() - start < 0.4            # 5 intervals of 20 ms
    cancel = threading.Event()
    cancel.set()
    assert acquisition.sample(50.0, 6, cancel=cancel) == 0


def test_exclusive_pauses_and_restarts_live(tmp_path):
    acquisition = make_acquisition(tmp_path)
    acquisition.start_live()
    with acquisition.exclusive():
        assert not acquisition.live_running
    assert acquisition.live_running
    acquisition.disconnect()
    assert not acquisition.connected


def fake_nidaqmx(monkeypatch, fail_read=False):
    @dataclass
    class Cfg:
        name: str

    class Device:
        name = "Dev1"
        product_type = "USB-6211"
        ai_resolution = 16.0
        ai_max_single_chan_rate = 250000.0
        ai_term_cfgs = [Cfg("RSE"), Cfg("DIFF")]
        ai_voltage_rngs = [-10.0, 10.0, -5.0, 5.0]
        ai_physical_chans = types.SimpleNamespace(channel_names=["Dev1/ai0", "Dev1/ai1"])

    class Task:
        def __init__(self):
            self.added = []
            self.ai_channels = types.SimpleNamespace(add_ai_voltage_chan=lambda *a, **k: self.added.append((a, k)))

        def read(self, timeout=None):
            if fail_read:
                raise RuntimeError("device disconnected")
            return 1.25

        def close(self):
            pass

    terminal = types.SimpleNamespace(RSE="rse", DIFF="diff", DEFAULT="default")
    module = types.ModuleType("nidaqmx")
    module.Task = Task
    module.constants = types.SimpleNamespace(TerminalConfiguration=terminal)
    module.system = types.SimpleNamespace(System=types.SimpleNamespace(
        local=lambda: types.SimpleNamespace(devices=[Device()])))
    monkeypatch.setitem(sys.modules, "nidaqmx", module)
    monkeypatch.setitem(sys.modules, "nidaqmx.constants", module.constants)
    monkeypatch.setitem(sys.modules, "nidaqmx.system", module.system)


def test_nidaq_detection_and_reading(monkeypatch):
    fake_nidaqmx(monkeypatch)
    [device] = nidaq_sensor.detect_devices()
    assert device.label == "Dev1 - USB-6211" and device.channels == ["ai0", "ai1"]
    assert device.terminals == ["RSE", "DIFF"] and device.ranges == [(-10.0, 10.0), (-5.0, 5.0)]
    assert device.resolution == 16.0 and device.max_rate_hz == 250000.0
    sensor = nidaq_sensor.NidaqSensor(nidaq_sensor.NidaqConfig(device="Dev1", channel="ai0", terminal="DIFF"), device)
    sensor.open()
    assert sensor.max_rate_hz == 250000.0 and sensor.read() == 1.25
    assert sensor.info()["resolution"] == "16"
    sensor.close()


def test_nidaq_errors(monkeypatch):
    monkeypatch.setitem(sys.modules, "nidaqmx", None)         # not installed
    with pytest.raises(SensorError, match="nidaqmx_missing"):
        nidaq_sensor.detect_devices()
    fake_nidaqmx(monkeypatch, fail_read=True)
    sensor = nidaq_sensor.NidaqSensor(nidaq_sensor.NidaqConfig(device="Dev1"))
    sensor.open()
    with pytest.raises(SensorError, match="disconnected"):
        sensor.read()
