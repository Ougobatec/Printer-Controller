"""End-to-end test of the whole window with a fake printer and a fake sensor (needs a display)."""

import time

import pytest

from fake_marlin import FakeMarlin
from printer_controller.core.program.model import AVERAGE, COUNT, PATH, RATE, SIMPLE, Measure, Point, Program
from printer_controller.core.sensor.base import Sensor

tk = pytest.importorskip("tkinter")


class Ramp(Sensor):
    max_rate_hz = 200.0

    def __init__(self):
        self.n = 0

    def open(self):
        pass

    def read(self):
        self.n += 1
        return self.n / 1000.0


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("PRINTER_CONTROLLER_SETTINGS", str(tmp_path / "settings.json"))
    from printer_controller.app import App
    try:
        application = App(serial_factory=lambda port, baud: FakeMarlin(homed=False, home_time=0.2))
    except tk.TclError:
        pytest.skip("no display available")
    yield application
    application.root.destroy()


def pump(app, condition, timeout=15.0):
    end = time.monotonic() + timeout
    while not condition() and time.monotonic() < end:
        app.root.update()
        time.sleep(0.01)
    return condition()


def test_connect_run_a_program_and_export(app, tmp_path):
    app.connect_printer("COM_FAKE", "115200")
    app.connect_sensor(Ramp())
    assert pump(app, lambda: app.printer.ready and app.acquisition.connected and not app.busy and not app.sensor_busy)
    assert "G28 O" in [c for c in app.printer.connection._serial.commands]
    program = Program("smoke", [
        Point(10, 10, 5, 6000, 0.0, Measure(SIMPLE)),
        Point(30, 10, 5, 6000, 0.0, Measure(AVERAGE, COUNT, count=4, rate_hz=100)),
        Point(30, 30, 5, 6000, 0.0, Measure(PATH, RATE, rate_hz=30))])
    app.program_tab.program = program
    app.program_tab.refresh(keep=[])
    assert app.program_tab.issues() == []
    app.program_tab._start()
    assert pump(app, lambda: app.runner.state in ("finished", "error", "stopped"))
    assert app.runner.state == "finished"
    measurements, groups = app.store.snapshot()
    assert {m.kind for m in measurements} == {"simple", "average", "path"} and len(groups) == 1
    pump(app, lambda: False, 0.5)
    assert app.results_tab.count_var.get().startswith(f"{len(measurements)} ")
    from printer_controller.core.results.export_xlsx import export_xlsx
    assert export_xlsx(app.store, tmp_path / "out.xlsx") == len(measurements)


def test_windows_open_without_error(app):
    from printer_controller.ui.app_settings_dialog import open_app_settings
    app.connect_printer("COM_FAKE", "115200")
    assert pump(app, lambda: app.printer.ready and not app.busy)
    open_app_settings(app.root, app.fonts, app)
    app.open_printer_dialog()
    app.program_tab.add_point(1, 2, 3)
    app.program_tab._edit_measure(0)
    app.popovers.show("sensor")
    app.popovers.show("printer")
    app.root.update()


def test_table_edit_changes_one_line_only(app):
    tab = app.program_tab
    for i in range(3):
        tab.add_point(i, i, i)
    tab.table.select([0, 1, 2])
    tab._edit_cell(1, "speed", "777")
    assert [p.speed for p in tab.program.points] == [300.0, 777.0, 300.0]
    assert tab.table.selected_rows() == [0, 1, 2]                    # the ticks are kept


def test_form_edits_all_ticked_lines_and_trajet_rules(app):
    tab = app.program_tab
    for i in range(3):
        tab.add_point(i, 0, 0)
    tab.table.select([1, 2])
    tab.form.vars["pause"].set("1.5")
    m = tab.form.measure
    m.kind_var.set(m.kinds["path"])
    tab._apply_form()
    assert [p.pause for p in tab.program.points] == [0.0, 1.5, 1.5]
    assert [p.measure.kind for p in tab.program.points] == ["none", "path", "path"]
    tab.table.select([0, 1, 2])                                          # first line ticked with others
    m = tab.form.measure
    m.kind_var.set(m.kinds["path"])
    tab._apply_form()
    assert [p.measure.kind for p in tab.program.points] == ["none", "path", "path"]   # the first one stays empty
    tab.table.select([0])
    app.root.update()                                                    # delivers the selection event
    assert t_path_label(tab) not in tab.form.measure.kind_combo["values"]


def t_path_label(tab):
    return tab.form.measure.kinds["path"]
