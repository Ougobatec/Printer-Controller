import json
import time

import pytest

from fake_marlin import FakeMarlin
from printer_controller.core.printer.printer import Printer
from printer_controller.core.printer.profile import PrinterProfile
from printer_controller.core.program import io as program_io
from printer_controller.core.program.model import AVERAGE, COUNT, DURATION, NONE, PATH, RATE, SIMPLE, Measure, Point, Program
from printer_controller.core.program.recorder import Recorder
from printer_controller.core.program.runner import ERROR, FINISHED, PAUSED, STOPPED, ProgramRunner
from printer_controller.core.results.store import ResultStore
from printer_controller.core.sensor.acquisition import Acquisition
from printer_controller.core.sensor.base import Sensor
from printer_controller.settings import Settings


def test_measure_sample_counts():
    assert Measure(AVERAGE, DURATION, duration_s=2).samples(program_rate=10) == (20, 10)
    assert Measure(AVERAGE, COUNT, count=7).samples(program_rate=10) == (7, 10)
    assert Measure(AVERAGE, DURATION, duration_s=2).samples(max_rate=100, program_rate=1000) == (2000, 100)
    assert Measure(SIMPLE).samples() == (1, 10.0) and Measure(NONE).samples()[0] == 0
    count, rate = Measure(PATH).samples(move_duration=2.0, program_rate=1000)
    assert (count, rate) == (2001, 1000)                  # path count is derived from the global rate
    assert Measure(AVERAGE, COUNT, count=5).duration(program_rate=10) == 0.5


def test_first_point_cannot_be_a_path():
    program = Program("p", [Point(0, 0, 0, measure=Measure(PATH)), Point(10, 0, 0, measure=Measure(PATH))])
    assert program.normalize()
    assert program.points[0].measure.kind == NONE and program.points[1].measure.kind == PATH


def test_validate():
    limits = {"X": (0, 100), "Y": (0, 100), "Z": (0, 100)}
    program = Program("p", [Point(500, 0, 0, speed=300), Point(10, 10, 10, speed=99999),
                            Point(1, 1, 1, measure=Measure(AVERAGE))])
    codes = [code for code, _ in program.validate(limits, 6000, 1000)]
    assert codes == ["out_of_limits", "speed"]  # per-point acquisition rates are no longer validated/used
    assert [c for c, _ in Program("e", []).validate(limits, 6000, 1000)] == ["empty"]


def test_estimate_includes_moves_pauses_and_measures():
    profile = PrinterProfile()
    program = Program("p", [Point(0, 0, 0), Point(100, 0, 0, speed=3000, pause=2.0, measure=Measure(AVERAGE, COUNT, count=10))])
    seconds, length = program.estimate(profile)
    assert length == pytest.approx(100.0)
    assert 2.0 + 1.0 + 2.0 < seconds < 8.0                  # ~2 s of move + 2 s pause + 1 s of average


def test_json_round_trip(tmp_path):
    program = Program("test", [
        Point(0, 0, 0, 600, 0.5),
        Point(10, None, 5, 300, 1.0, Measure(AVERAGE, DURATION, duration_s=3)),
        Point(20, 20, 5, 900, 0.0, Measure(PATH)),
        Point(30, 0, 0, 300, 0.0, Measure(SIMPLE))])
    path = tmp_path / "p.json"
    program_io.save(program, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 2 and data["points"][1]["measure"]["by"] == "duration"
    assert data["points"][2]["measure"] == {"kind": PATH}  # no per-path count or frequency
    again = program_io.load(path)
    assert again == program


def test_legacy_files_are_converted():
    old = {"nom": "old", "positions": [
        {"x": 0, "y": 0, "z": 0, "vitesse": 600, "mesure": "simple"},
        {"x": 10, "y": 0, "z": 0, "mesure": "point", "mesure_n": 5, "mesure_frequence": 20},
        {"x": 20, "y": 0, "z": 0, "mesure": "continu", "mesure_duree": 3, "mesure_frequence": 10, "attente": 1.5},
        {"x": 30, "y": 0, "z": 0, "mesure": "trajet", "mesure_frequence": 25}]}
    program = program_io.from_dict(old)
    kinds = [p.measure.kind for p in program.points]
    assert kinds == [SIMPLE, AVERAGE, AVERAGE, PATH] and program.name == "old"
    assert program.points[0].speed == 600 and program.points[2].pause == 1.5
    assert program.points[1].measure.by == COUNT and program.points[1].measure.count == 5
    assert program.points[2].measure.by == DURATION and program.points[3].measure.kind == PATH
    route = {"nom": "r", "mode": "parcours", "parcours": {"vitesse": 450, "frequence": 15, "mesure": True},
             "positions": [{"x": 0, "y": 0, "z": 0}, {"x": 5, "y": 0, "z": 0}, {"x": 9, "y": 0, "z": 0}]}
    converted = program_io.from_dict(route)
    assert [p.measure.kind for p in converted.points] == [NONE, PATH, PATH]
    assert converted.points[1].speed == 450 and converted.points[1].measure.kind == PATH


def test_bad_files(tmp_path):
    with pytest.raises(program_io.ProgramFileError):
        program_io.from_dict({"version": 2, "points": [{"measure": {"kind": "weird"}}]})
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(program_io.ProgramFileError):
        program_io.load(bad)


def test_store_groups_and_statistics():
    store = ResultStore()
    store.start_run()
    group = store.new_group()
    for i, v in enumerate((1.0, 2.0, 3.0, 4.0)):
        store.add(100.0 + i * 0.1, {"X": 1, "Y": 2, "Z": 3}, "defined", "average", v / 1000, v, "V", group)
    store.add(101.0, {"X": 5, "Y": 5, "Z": 5}, "computed", "path", 0.0, 0.0, "V")
    store.close_group(group)
    measurements, groups = store.snapshot()
    assert [m.n for m in measurements] == [1, 2, 3, 4, 5]
    g = groups[0]
    assert g.count == 4 and g.mean == 2.5 and (g.min, g.max) == (1.0, 4.0)
    assert g.std == pytest.approx(1.2909944)
    assert g.duration_s == pytest.approx(0.3) and (g.x, g.y, g.z) == (1, 2, 3)


class RampSensor(Sensor):
    max_rate_hz = 500.0

    def __init__(self):
        self.n = 0

    def open(self):
        pass

    def read(self):
        self.n += 1
        return self.n / 1000.0


def make_runner(tmp_path, sensor=None):
    settings = Settings.load(tmp_path / "s.json")
    settings.printer.startup_timeout = settings.printer.command_timeout = 3.0
    settings.sensor.multiplier, settings.sensor.unit = 1000.0, "mV"
    fake = FakeMarlin(homed=True)
    printer = Printer(settings, serial_factory=lambda p, b: fake)
    printer.connect("COM_FAKE", 115200)
    acquisition = Acquisition(settings)
    acquisition.connect(sensor or RampSensor())
    store = ResultStore()
    events = []
    runner = ProgramRunner(printer, Recorder(acquisition, store, settings), store,
                           on_event=lambda name, **data: events.append((name, data)))
    return runner, printer, fake, store, events


def wait_for(runner, states, timeout=20):
    end = time.monotonic() + timeout
    while runner.state not in states and time.monotonic() < end:
        time.sleep(0.02)
    return runner.state


def test_program_run_records_every_kind_of_measure(tmp_path):
    runner, printer, fake, store, events = make_runner(tmp_path)
    program = Program("t", [
        Point(10, 0, 0, 6000, 0.0, Measure(PATH)),                                   # first point: ignored
        Point(30, 0, 0, 6000, 0.1, Measure(SIMPLE)),
        Point(30, 20, 0, 6000, 0.1, Measure(AVERAGE, COUNT, count=5)),
        Point(60, 20, 0, 6000, 0.0, Measure(PATH))])
    program.normalize()
    runner.start(program)
    assert wait_for(runner, (FINISHED, ERROR, STOPPED)) == FINISHED
    measurements, groups = store.snapshot()
    kinds = [m.kind for m in measurements]
    assert kinds.count("simple") == 1 and kinds.count("average") == 5 and kinds.count("path") >= 5
    assert len(groups) == 1 and groups[0].count == 5 and groups[0].unit == "mV"
    simple = next(m for m in measurements if m.kind == "simple")
    assert (simple.x, simple.y, simple.position_kind) == (pytest.approx(30), pytest.approx(0), "defined")
    assert simple.value == pytest.approx(simple.raw * 1000)
    path = [m for m in measurements if m.kind == "path"]
    assert all(m.position_kind == "computed" for m in path)
    assert path[0].x < path[-1].x and 29 < path[0].x < 61              # the head was moving
    assert path == sorted(path, key=lambda m: m.elapsed_s)
    assert [e for e in events if e[0] == "step"][-1][1]["index"] == 3
    assert fake.actual_position()["X"] == pytest.approx(60.0)


def test_pause_resume_and_stop(tmp_path):
    runner, printer, fake, store, events = make_runner(tmp_path)
    program = Program("t", [Point(20, 0, 0, 6000, 0.0), Point(40, 0, 0, 6000, 0.0), Point(60, 0, 0, 6000, 0.0)])
    runner.start(program)
    time.sleep(0.15)
    runner.pause()
    assert runner.state == PAUSED
    time.sleep(1.0)
    assert runner.state == PAUSED and runner.current is not None          # nothing advances while paused
    runner.resume()
    assert wait_for(runner, (FINISHED, ERROR, STOPPED)) == FINISHED

    runner.start(Program("t2", [Point(10, 0, 0, 600), Point(100, 0, 0, 600)]))          # slow program
    time.sleep(0.5)
    runner.stop()
    printer.stop_motion()
    assert wait_for(runner, (FINISHED, ERROR, STOPPED)) == STOPPED
    assert 5 < fake.actual_position()["X"] < 100


def test_sensor_failure_ends_the_program_with_an_error(tmp_path):
    class Failing(RampSensor):
        def read(self):
            from printer_controller.core.sensor.base import SensorError
            raise SensorError("unplugged")

    runner, printer, fake, store, events = make_runner(tmp_path, Failing())
    runner.start(Program("t", [Point(10, 0, 0, 6000, 0.0, Measure(SIMPLE))]))
    assert wait_for(runner, (FINISHED, ERROR, STOPPED)) == ERROR
    assert any(name == "error" for name, _ in events)


def test_limits_stop_the_program(tmp_path):
    runner, printer, fake, store, events = make_runner(tmp_path)
    runner.start(Program("t", [Point(9999, 0, 0, 6000)]))
    assert wait_for(runner, (FINISHED, ERROR, STOPPED)) == ERROR
    assert not any(c.startswith("G1 X9999") for c in fake.commands)
