import time

import pytest

from fake_marlin import FakeMarlin
from printer_controller.core.printer import gcode
from printer_controller.core.printer.connection import PrinterError
from printer_controller.core.printer.identify import parse_m115, parse_m211, parse_m503
from printer_controller.core.printer.jog import IDLE
from printer_controller.core.printer.printer import Printer
from printer_controller.settings import Settings


def make_printer(tmp_path, **fake_options):
    settings = Settings.load(tmp_path / "s.json")
    settings.printer.startup_timeout = 3.0
    settings.printer.command_timeout = 3.0
    settings.printer.home_timeout = 5.0
    fake = FakeMarlin(**fake_options)
    printer = Printer(settings, serial_factory=lambda port, baud: fake)
    return printer, fake


def test_gcode_helpers():
    assert gcode.move(10, None, 5.5, 1200) == "G1 X10.000 Z5.500 F1200.0"
    assert gcode.parse_position("X:12.50 Y:3.00 Z:0.20 E:0.00 Count X:1000 Y:240 Z:80") == \
        {"X": 12.5, "Y": 3.0, "Z": 0.2}
    assert gcode.parse_position("ok") is None


def test_parsers():
    name, model = parse_m115(["FIRMWARE_NAME:Marlin 2.0.9.3 (Github) SOURCE_CODE_URL:https://x PROTOCOL_VERSION:1.0 "
                              "MACHINE_TYPE:Creality Ender-3 EXTRUDER_COUNT:1"])
    assert name == "Marlin 2.0.9.3 (Github)" and model == "Creality Ender-3"
    speed, accel, travel, steps = parse_m503(["echo:  M203 X500.00 Y500.00 Z5.00 E25.00",
                                              "echo:  M201 X500 Y500 Z100 E5000",
                                              "echo:  M204 P500.00 R500.00 T400.00", "echo:  M92 X80 Y80 Z400 E93"])
    assert speed["Z"] == 5.0 and accel["X"] == 500 and travel == 400.0 and steps["Z"] == 400
    assert parse_m503(["echo:  M204 S600.00 T300.00"])[2] == 300.0
    assert parse_m211(["echo:Soft endstops: ON Min: X0.00 Y0.00 Z0.00 Max: X235.00 Y235.00 Z250.00"])["Z"] == (0.0, 250.0)
    assert parse_m211(["echo:  M211 S1 ; ON"]) == {}


def test_connect_identifies_and_homes_when_needed(tmp_path):
    printer, fake = make_printer(tmp_path, homed=False)
    printer.connect("COM_FAKE", 115200)
    assert printer.connected and printer.homed
    assert fake.home_count == 1 and "G28 O" in fake.commands
    p = printer.profile
    assert p.firmware.startswith("Marlin") and p.model == "Ender-3"
    assert p.limits()["X"] == (0.0, 235.0) and p.source("limits.X.max") == "detected"
    assert p.max_speed("X") == 30000.0 and p.source("max_speed.X") == "detected"   # 500 mm/s in mm/min
    assert p.max_accel("Z") == 100 and p.travel_accel == 500


def test_no_homing_when_already_homed_since_power_on(tmp_path):
    printer, fake = make_printer(tmp_path, homed=True)
    printer.connect("COM_FAKE", 115200)
    assert printer.homed and fake.home_count == 0


def test_auto_home_off_blocks_moves_until_homed(tmp_path):
    printer, fake = make_printer(tmp_path, homed=False)
    printer.settings.printer.auto_home = False
    printer.connect("COM_FAKE", 115200)
    assert not printer.homed
    with pytest.raises(PrinterError):
        printer.move_to(10, 10, 10, 1000, wait=False)
    printer.home()
    assert printer.homed


def test_defaults_when_printer_gives_nothing(tmp_path):
    printer, _ = make_printer(tmp_path, homed=True, has_m503=False, has_m211=False)
    printer.connect("COM_FAKE", 115200)
    p = printer.profile
    assert p.source("limits.X.max") == "default" and p.value("limits.X.max") == 200.0
    assert p.source("max_speed.Z") == "default"


def test_manual_values_and_reset_to_detected(tmp_path):
    printer, _ = make_printer(tmp_path, homed=True)
    printer.connect("COM_FAKE", 115200)
    p = printer.profile
    p.set_manual("limits.X.max", 180)
    assert p.value("limits.X.max") == 180 and p.source("limits.X.max") == "manual"
    assert p.overrides() == {"limits.X.max": 180.0}
    p.set_manual("limits.X.max", 235)                     # same as detected: no longer manual
    assert p.source("limits.X.max") == "detected"
    p.set_manual("max_speed.X", 6000)
    p.reset_to_detected()
    assert p.value("max_speed.X") == 30000.0 and p.overrides() == {}
    with pytest.raises(ValueError):
        p.set_manual("limits.X.min", 999)                 # min above max


def test_limits_are_always_enforced(tmp_path):
    printer, fake = make_printer(tmp_path, homed=True)
    printer.connect("COM_FAKE", 115200)
    before = len(fake.commands)
    with pytest.raises(PrinterError):
        printer.move_to(500, 0, 0, 1000, wait=False)
    assert len(fake.commands) == before                   # nothing was sent
    printer.profile.set_manual("limits.X.max", 600)       # limits can be changed by hand
    printer.profile.set_manual("limits.X.min", 0)
    printer.move_to(500, 0, 0, 30000, wait=False)


def test_position_is_computed_during_a_move_and_verified_after(tmp_path):
    printer, fake = make_printer(tmp_path, homed=True)
    printer.connect("COM_FAKE", 115200)
    plan = printer.move_to(60, 0, 0, 6000, wait=False)    # 100 mm/s -> about 0.7 s
    assert plan.duration > 0.5
    time.sleep(plan.duration / 2)
    estimated, kind = printer.position()
    real = fake.actual_position()
    assert kind == "computed"
    assert abs(estimated["X"] - real["X"]) < 8            # the model is close to the real motion
    assert 5 < estimated["X"] < 55
    printer.wait_moves()
    final, kind = printer.position()
    assert kind == "verified" and final["X"] == pytest.approx(60.0)


def test_quick_stop_stops_where_the_head_is(tmp_path):
    printer, fake = make_printer(tmp_path, homed=True)
    printer.connect("COM_FAKE", 115200)
    printer.move_to(200, 0, 0, 3000, wait=False)          # 50 mm/s: 4 s
    time.sleep(0.5)
    printer.stop_motion()
    stopped = fake.actual_position()
    assert 5 < stopped["X"] < 60
    position, kind = printer.position()
    assert abs(position["X"] - stopped["X"]) < 1.0
    assert fake.queued_seconds() == 0


def test_emergency_stop_closes_the_connection(tmp_path):
    printer, fake = make_printer(tmp_path, homed=True)
    printer.connect("COM_FAKE", 115200)
    printer.emergency_stop()
    assert "M112" in fake.commands and not printer.connected and not printer.homed


def test_printer_error_is_reported(tmp_path):
    printer, fake = make_printer(tmp_path, homed=True)
    printer.connect("COM_FAKE", 115200)
    fake._handle = lambda command: fake._say("Error:Heating failed")
    with pytest.raises(PrinterError, match="Heating failed"):
        printer.connection.send("M104 S200", timeout=1)


def test_jog_moves_while_held_and_stops_on_release(tmp_path):
    printer, fake = make_printer(tmp_path, homed=True)
    printer.connect("COM_FAKE", 115200)
    assert printer.jogger.start("X", 1, 3000)
    peak = 0.0
    t_end = time.monotonic() + 1.0
    while time.monotonic() < t_end:
        peak = max(peak, fake.queued_seconds())
        time.sleep(0.01)
    printer.jogger.stop()
    deadline = time.monotonic() + 3
    while printer.jogger.state != IDLE and time.monotonic() < deadline:
        time.sleep(0.02)
    assert printer.jogger.state == IDLE
    assert peak < 0.8                                     # never a long queue in the printer
    real = fake.actual_position()
    assert 20 < real["X"] < 80                            # it moved about 1 s at 50 mm/s
    position, kind = printer.position()
    assert kind == "verified" and abs(position["X"] - real["X"]) < 0.5
    assert fake.queued_seconds() == 0


def test_rapid_presses_do_not_flood_the_printer(tmp_path):
    printer, fake = make_printer(tmp_path, homed=True)
    printer.connect("COM_FAKE", 115200)
    started = 0
    t_end = time.monotonic() + 1.5
    while time.monotonic() < t_end:
        if printer.jogger.start("Y", 1, 3000):
            started += 1
        time.sleep(0.01)
        printer.jogger.stop()
        time.sleep(0.01)
    deadline = time.monotonic() + 3
    while printer.jogger.state != IDLE and time.monotonic() < deadline:
        time.sleep(0.02)
    assert printer.jogger.state == IDLE and started >= 1
    assert started < 40                                   # presses during a stop are ignored
    assert max(fake.queued_seconds(), 0) == 0
    low, high = printer.profile.limits()["Y"]
    assert low <= fake.actual_position()["Y"] <= high


def test_jog_stops_at_the_limit(tmp_path):
    printer, fake = make_printer(tmp_path, homed=True)
    printer.connect("COM_FAKE", 115200)
    printer.profile.set_manual("limits.X.max", 5.0)
    printer.jogger.start("X", 1, 6000)
    deadline = time.monotonic() + 4
    while printer.jogger.state != IDLE and time.monotonic() < deadline:
        time.sleep(0.02)
    assert printer.jogger.state == IDLE
    assert fake.actual_position()["X"] == pytest.approx(5.0, abs=0.01)
