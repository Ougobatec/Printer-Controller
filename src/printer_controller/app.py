"""Main window: builds the three columns and wires the panels to the printer, sensor and program."""

import tkinter as tk
from tkinter import messagebox

from .core.printer.connection import PrinterError
from .core.printer.printer import Printer
from .core.program.model import NONE
from .core.program.recorder import Recorder
from .core.program.runner import ERROR, FINISHED, STOPPED, ProgramRunner
from .core.results.store import ResultStore
from .core.sensor.acquisition import Acquisition
from .core.sensor.nidaq_sensor import detect_devices
from .core.sensor.serial_sensor import SerialSensor
from .core.serial_ports import list_ports
from .i18n import set_language, t
from .settings import Settings
from .threads import UiDispatcher, run_in_background
from .ui.app_settings_dialog import open_app_settings
from .ui.console import Console
from .ui.header import Header
from .ui.point_panel import PointPanel
from .ui.printer_dialog import PrinterDialog
from .ui.printer_panel import PrinterPanel
from .ui.printer_popover import PrinterPopover
from .ui.program_tab import ProgramTab
from .ui.results_tab import ResultsTab
from .ui.sensor_live import SensorLive
from .ui.sensor_popover import SensorPopover
from .ui.theme import C, STATUS_COLORS, install_theme
from .ui.view3d import MachineView
from .ui.widgets import Popovers, ScrollColumn, TabBar, install_wheel_router

LEFT_WIDTH, RIGHT_WIDTH = 330, 400
TICK_MS = 100


class App:
    def __init__(self, settings_path=None, serial_factory=None):
        self.settings = Settings.load(settings_path)
        set_language(self.settings.app.language)

        self.root = tk.Tk()
        self.root.title(t("app.title"))
        self.root.configure(bg=C["bg"])
        self.dispatcher = UiDispatcher(self.root)
        self.fonts = install_theme(self.root)
        install_wheel_router(self.root)
        self._place_window()

        options = {"serial_factory": serial_factory} if serial_factory else {}
        self.printer = Printer(self.settings, on_traffic=self._on_traffic, **options)
        self.acquisition = Acquisition(self.settings)
        self.store = ResultStore()
        self.runner = ProgramRunner(self.printer, Recorder(self.acquisition, self.store, self.settings), self.store,
                                    on_event=lambda name, **data: self.dispatcher.post(self._runner_event, name, data))
        self.busy, self.busy_text = False, ""
        self.sensor_busy, self.sensor_error = False, ""
        self._printer_dialog = None
        self._last_row = None
        self._ticks = 0
        self._build()
        self.log(t("console.started"))
        self.root.bind("<Escape>", lambda _e: self.stop())
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.update_state()
        self.update_sensor_state()
        self.root.after(TICK_MS, self._tick)

    # --- window ------------------------------------------------------------------------
    def _place_window(self):
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        width, height = min(1440, screen_w - 60), min(900, screen_h - 90)
        self.root.geometry(f"{width}x{height}+{(screen_w - width) // 2}+{max(0, (screen_h - height) // 3)}")
        self.root.minsize(min(1100, width), min(640, height))
        self._small_screen = screen_h < 800

    def _build(self):
        s = self.settings
        self.header = Header(self.root, self.fonts, on_printer=lambda: self.popovers.toggle("printer"),
                             on_sensor=lambda: self.popovers.toggle("sensor"),
                             on_settings=lambda: open_app_settings(self.root, self.fonts, self),
                             on_stop=self.stop, on_emergency=self.emergency_stop)
        self.header.pack(fill="x")
        tk.Frame(self.root, bg=C["line"], height=1).pack(fill="x")
        self.console = Console(
            self.root, self.fonts, height=s.app.console_height,
            visible=s.app.console_visible and not self._small_screen, on_send=self.send_command)
        self.console.pack(side="bottom", fill="x")          # packed before the body: keeps its height

        outer = tk.Frame(self.root, bg=C["bg"])
        outer.pack(fill="both", expand=True, padx=12, pady=12)
        outer.grid_rowconfigure(0, weight=1)
        outer.grid_columnconfigure(0, minsize=LEFT_WIDTH)
        outer.grid_columnconfigure(1, weight=1)
        outer.grid_columnconfigure(2, minsize=RIGHT_WIDTH)

        # Left: printer
        left = ScrollColumn(outer)
        left.grid(row=0, column=0, sticky="nsew")
        self.printer_panel = PrinterPanel(
            left.body, self.fonts, s, on_jog_start=self._jog_start, on_jog_stop=self._jog_stop,
            on_home_origin=self.go_origin, on_open_settings=self.open_printer_dialog)
        self.printer_panel.configure(highlightthickness=1, highlightbackground=C["line"])
        self.printer_panel.pack(fill="x", anchor="n")

        # Centre: tabs
        center = tk.Frame(outer, bg=C["bg"])
        center.grid(row=0, column=1, sticky="nsew", padx=12)
        self.tabs = TabBar(center, self.fonts["bold"])
        self.tabs.pack(fill="both", expand=True)
        program_column = ScrollColumn(self.tabs.add("program", t("tabs.program")))
        program_column.pack(fill="both", expand=True)
        self.program_tab = ProgramTab(program_column.body, self.fonts, self)
        self.program_tab.pack(fill="x", anchor="n")
        self.sensor_live = SensorLive(self.tabs.add("measure", t("tabs.measure")), self.fonts, s, self.acquisition)
        self.sensor_live.pack(fill="both", expand=True)
        self.results_tab = ResultsTab(self.tabs.add("results", t("tabs.results")), self.fonts, self)
        self.results_tab.pack(fill="both", expand=True)

        # Right: 3D view and point entry
        right = ScrollColumn(outer)
        right.grid(row=0, column=2, sticky="nsew")
        right.body.grid_columnconfigure(0, weight=1)
        card = tk.Frame(right.body, bg=C["surface"], highlightthickness=1, highlightbackground=C["line"])
        card.grid(row=0, column=0, sticky="ew")
        self.view = MachineView(card, self.fonts)
        self.view.pack(fill="both", expand=True)
        self.point_panel = PointPanel(right.body, self.fonts, self.program_changed, self.copy_current_position,
                                      self.go_to_point, self.add_point_to_program)
        self.point_panel.grid(row=1, column=0, sticky="ew", pady=(12, 0))

        # Header pop-overs
        self.printer_popover = PrinterPopover(self.fonts, s, list_ports, self.connect_printer, self.disconnect_printer)
        self.popovers = Popovers(self.root)
        self.popovers.add("printer", self.header.printer_button, self.printer_popover.build, width=380)
        self.sensor_popover = SensorPopover(
            self.fonts, s, list_ports, self.connect_sensor, self.disconnect_sensor, self._run_sensor_task,
            self._read_example_line, detect_devices)
        self.popovers.add("sensor", self.header.sensor_button, self.sensor_popover.build, width=470)

    # --- background work and console -------------------------------------------------------------------
    def log(self, message, kind="info"):
        self.console.append(message, kind)

    def run_task(self, work, done=None, text=""):
        """Run `work()` in the background; the printer buttons are locked meanwhile."""
        if self.busy:
            return False
        self.busy, self.busy_text = True, text
        self.update_state()

        def finished(result):
            self.busy = False
            if done:
                done(result)
            self.update_state()

        def failed(exc):
            self.busy = False
            self.log(str(exc), "error")
            self.update_state()
            if isinstance(exc, PrinterError):
                messagebox.showerror(t("printer.connect_error"), str(exc), parent=self.root)

        run_in_background(self.dispatcher, work, finished, failed)
        return True

    def _on_traffic(self, direction, text):
        """Called from the serial threads: shown in the console on the Tk thread."""
        if direction == "tx":
            self.dispatcher.post(self.console.append, f"> {text}", "tx")
        else:
            self.dispatcher.post(self.console.append, f"< {text}", "error" if text.startswith("Error") else "rx")

    def _log_async(self, message):
        self.dispatcher.post(self.console.append, message, "info")

    # --- printer ----------------------------------------------------------------------------------------
    def connect_printer(self, port, baud_text):
        try:
            baud = int(baud_text)
            if baud <= 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning(t("printer.connect_error"), t("printer.bad_baud"), parent=self.root)
            return
        if not port:
            messagebox.showwarning(t("printer.connect_error"), t("printer.no_port"), parent=self.root)
            return

        def done(_):
            self.settings.printer.port, self.settings.printer.baudrate = port, baud
            self.settings.save()
            self.log(t("printer.connected_log", port=port, baud=baud))
            self.profile_changed()

        self.run_task(lambda: self.printer.connect(port, baud, log=self._log_async), done, t("state.connecting"))

    def disconnect_printer(self):
        self.printer.disconnect()
        self.log(t("printer.disconnected_log"))
        self.update_state()

    def home_printer(self):
        self.run_task(lambda: self.printer.home(), lambda _: self.profile_changed(), t("state.homing"))

    def reread_printer(self):
        self.run_task(self.printer.read_printer, lambda _: self.profile_changed())

    def go_origin(self):
        speed = self.printer_panel.speed()
        self.run_task(lambda: self.printer.move_to(0, 0, 0, speed), None, t("state.busy"))

    def _jog_start(self, axis, direction):
        if not self.busy and not self.runner.active:
            self.printer.jogger.start(axis, direction, self.printer_panel.speed())

    def _jog_stop(self):
        self.printer.jogger.stop()

    def stop(self):
        """STOP: stops the program and the current move (never blocked by a running task)."""
        self.runner.stop()
        if self.printer.connected:
            run_in_background(self.dispatcher, self.printer.stop_motion, lambda _: self.update_state(),
                              lambda exc: self.log(str(exc), "error"))

    def emergency_stop(self):
        self.runner.stop()
        if self.printer.connected:
            self.printer.emergency_stop()
            self.log(t("button.emergency"), "error")
            self.update_state()

    def send_command(self, command):
        if not self.printer.connected:
            self.log(t("console.not_connected"), "error")
        elif self.runner.active:
            self.log(t("console.locked"), "error")
        else:
            run_in_background(self.dispatcher, lambda: self.printer.send_raw(command), None,
                              lambda exc: self.log(str(exc), "error"))

    def open_printer_dialog(self):
        if self._printer_dialog is not None and self._printer_dialog.win.winfo_exists():
            self._printer_dialog.win.lift()
            return
        self._printer_dialog = PrinterDialog(self.root, self.fonts, self)

    def profile_changed(self):
        """The printer profile or the printer settings changed."""
        self.printer_panel.set_quick_speeds(self.settings.printer.quick_speeds, self.settings.printer.speed_cap)
        self.update_state()
        self.program_changed()

    def speed_limit(self):
        """Highest speed accepted by the app (mm/min): the printer's own, or the cap if lower."""
        return self.printer.effective_speed(1e12)

    def rate_limit(self):
        """Highest acquisition frequency (Hz) of the connected sensor, 0 if none is connected."""
        return self.acquisition.max_rate

    # --- sensor ---------------------------------------------------------------------------------------------
    def _run_sensor_task(self, work, done=None, failed=None):
        run_in_background(self.dispatcher, work, done, failed or (lambda exc: self.sensor_popover.show_error(str(exc))))

    def connect_sensor(self, sensor):
        if self.sensor_busy:
            return
        self.sensor_busy, self.sensor_error = True, ""
        self.update_sensor_state()

        def done(_):
            self.sensor_busy = False
            info = ", ".join(f"{k} {v}" for k, v in sensor.info().items() if v)
            self.log(t("sensor.connected_log", name=info))
            self.update_sensor_state()

        def failed(exc):
            self.sensor_busy, self.sensor_error = False, str(exc)
            self.sensor_popover.show_error(str(exc))
            self.update_sensor_state()

        run_in_background(self.dispatcher, lambda: self.acquisition.connect(sensor), done, failed)

    def disconnect_sensor(self):
        self.acquisition.disconnect()
        self.log(t("sensor.disconnected_log"))
        self.update_sensor_state()

    def _read_example_line(self, config):
        """One line from the device, to try the extraction (the connected sensor if it is the same port)."""
        sensor = self.acquisition.sensor
        if isinstance(sensor, SerialSensor) and sensor.config.port == config.port:
            return sensor.read_line()
        probe = SerialSensor(config)
        probe.open()
        try:
            return probe.read_line()
        finally:
            probe.close()

    # --- program ---------------------------------------------------------------------------------------------
    def run_program(self, program):
        if self.busy or self.runner.active:
            return
        if not self.printer.ready:
            messagebox.showwarning(t("program.title"), t("run.printer_needed"), parent=self.root)
            return
        if any(p.measure.kind != NONE for p in program.points) and not self.acquisition.connected:
            messagebox.showwarning(t("program.title"), t("run.sensor_needed"), parent=self.root)
            return
        self.runner.start(program)
        self.tabs.select("results")

    def pause_program(self):
        self.runner.pause()

    def resume_program(self):
        self.runner.resume()

    def stop_program(self):
        self.stop()

    def go_to_start(self, program):
        speed = self.settings.program.speed
        self.run_task(lambda: self.runner.go_to_start(program, speed), None, t("state.busy"))

    def _runner_event(self, name, data):
        if name == "error":
            self.log(data["message"], "error")
            self._run_error = data["message"]
            messagebox.showerror(t("program.title"), data["message"], parent=self.root)
        else:
            self.program_tab.set_run_state(self.runner.state, self.runner.current, getattr(self, "_run_error", ""))
            if name == "state" and self.runner.state in (FINISHED, STOPPED, ERROR):
                self.update_state()

    def program_changed(self):
        """The program, its selection or the target point changed: refresh the 3D view."""
        if not hasattr(self, "point_panel"):
            return                                   # interface still being built
        tab = self.program_tab
        rows = tab.table.selected_rows()
        if len(rows) == 1 and rows[0] != self._last_row and rows[0] < len(tab.program.points):
            p = tab.program.points[rows[0]]
            self._last_row = rows[0]
            self.point_panel.vars["X"].set("" if p.x is None else f"{p.x:g}")
            self.point_panel.vars["Y"].set("" if p.y is None else f"{p.y:g}")
            self.point_panel.vars["Z"].set("" if p.z is None else f"{p.z:g}")
        elif len(rows) != 1:
            self._last_row = None
        self.view.set_scene(limits=self.printer.profile.limits(), points=list(tab.program.points),
                            selected=set(rows), target=self.point_panel.values())

    # --- point panel -----------------------------------------------------------------------------------------------
    def copy_current_position(self):
        position, _ = self.printer.position()
        self.point_panel.set_values(*(round(position[a], 3) for a in "XYZ"))

    def go_to_point(self):
        values = self.point_panel.values()
        if values is None:
            messagebox.showwarning(t("point.title"), t("point.invalid"), parent=self.root)
            return
        speed = self.printer_panel.speed()
        self.run_task(lambda: self.printer.move_to(*values, speed), None, t("state.busy"))

    def add_point_to_program(self):
        values = self.point_panel.values()
        if values is None:
            messagebox.showwarning(t("point.title"), t("point.invalid"), parent=self.root)
        elif self.runner.active:
            messagebox.showinfo(t("program.title"), t("program.running.locked"), parent=self.root)
        else:
            self.program_tab.add_point(*values)
            self.tabs.select("program")

    # --- state shown to the user ----------------------------------------------------------------------------------------
    def update_state(self):
        printer = self.printer
        if self.runner.active:
            kind, text = "busy", t(f"run.state.{self.runner.state}", n=(self.runner.current or 0) + 1,
                                   total=len(self.program_tab.program.points))
        elif self.busy and self.busy_text:
            kind, text = "busy", self.busy_text
        elif not printer.connected:
            kind, text = "idle", t("state.printer.disconnected")
        elif not printer.homed:
            kind, text = "busy", t("state.needs_homing")
        else:
            kind, text = "ok", t("state.connected", port=self.settings.printer.port)
        self.header.set_printer_state(STATUS_COLORS[kind], text)
        self.header.set_stop_enabled(printer.connected or self.runner.active)
        free = printer.ready and not self.busy and not self.runner.active
        self.printer_panel.set_enabled(free)
        self.point_panel.set_printer_ready(free)
        self.printer_popover.set_state(printer.connected, self.busy or self.runner.active)
        self.program_tab.set_run_state(self.runner.state, self.runner.current, getattr(self, "_run_error", ""))
        if self._printer_dialog is not None:
            self._printer_dialog.refresh()

    def update_sensor_state(self):
        acq = self.acquisition
        if self.sensor_busy:
            kind, text = "busy", t("state.sensor.connecting")
        elif not acq.connected:
            kind, text = ("error", t("state.sensor.error")) if self.sensor_error else ("idle", t("state.sensor.disconnected"))
        elif acq.live_running and acq.latest:
            kind, text = "ok", f"{acq.latest[1]:.5g} {self.settings.sensor.unit}"
        else:
            kind, text = "ok", t("state.sensor.connected")
        self.header.set_sensor_state(STATUS_COLORS[kind], text)
        self.sensor_popover.set_state(acq.connected, self.sensor_busy)
        self.sensor_live.refresh()

    def _tick(self):
        """Position of the head (computed while it moves), 3D view, live value, results."""
        position = None
        if self.printer.connected:
            position, kind = self.printer.position()
            self.printer_panel.set_position(position["X"], position["Y"], position["Z"], t(f"printer.position.{kind}"))
        else:
            self.printer_panel.set_position(None, None, None)
        scene = {"machine": position, "current": self.runner.current if self.runner.active else None}
        scene["done"] = set(range(self.runner.current)) if self.runner.active and self.runner.current else set()
        if self.view.show_measures:
            measurements, _ = self.store.snapshot()
            scene["measures"] = [(m.x, m.y, m.z, m.value) for m in measurements if None not in (m.x, m.y, m.z)]
        self.view.set_scene(**scene)
        self._ticks += 1
        if self._ticks % 3 == 0:
            if self.acquisition.error and not self.acquisition.live_running and self.acquisition.connected:
                self.sensor_error = self.acquisition.error
            self.update_sensor_state()
            self.results_tab.refresh()
        self.root.after(TICK_MS, self._tick)

    # --- life cycle ---------------------------------------------------------------------------------------------------------
    def run(self):
        self.root.mainloop()

    def close(self):
        self.settings.app.console_visible = self.console.visible
        self.settings.app.console_height = self.console.height
        self.settings.save()
        try:
            self.runner.stop()
            self.printer.disconnect()
            self.acquisition.disconnect()
        finally:
            self.root.destroy()
