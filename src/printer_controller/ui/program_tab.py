"""Program tab: file buttons, run controls, table of lines, edit form."""

import os
import tkinter as tk
from dataclasses import replace
from tkinter import filedialog, messagebox, ttk

from ..core.program import io as program_io
from ..core.program.model import Measure, Point, Program
from ..core.program.runner import FINISHED, IDLE, PAUSED, RUNNING
from ..i18n import t
from .measure_editor import MeasureEditor
from .program_form import ProgramForm
from .program_table import ProgramTable
from .theme import C
from .widgets import ThinProgress, Tooltip


def format_duration(seconds):
    seconds = int(round(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min {seconds:02d} s" if minutes else f"{seconds} s"


class ProgramTab(tk.Frame):
    """`app` provides: settings, printer, acquisition, run_program(program), pause_program(),
    resume_program(), stop_program(), go_to_start(program), program_changed()."""

    def __init__(self, parent, fonts, app):
        super().__init__(parent, bg=C["surface"], highlightthickness=1, highlightbackground=C["line"])
        self.f, self.app = fonts, app
        self.program = Program(t("program.untitled"))
        self.state, self.step = IDLE, None
        self.name_var = tk.StringVar()
        self.status_var, self.estimate_var = tk.StringVar(value=t("run.state.idle")), tk.StringVar()
        pad = tk.Frame(self, bg=C["surface"])
        pad.pack(fill="both", expand=True, padx=16, pady=14)

        # --- name and files ---------------------------------------------------------------------------
        tk.Label(pad, text=t("program.title"), bg=C["surface"], fg=C["muted"], font=fonts["section"]).pack(anchor="w")
        name = ttk.Entry(pad, textvariable=self.name_var, font=fonts["bold"])
        name.pack(fill="x", pady=(6, 8), ipady=3)
        name.bind("<KeyRelease>", lambda _e: setattr(self.program, "name", self.name_var.get()))
        files = tk.Frame(pad, bg=C["surface"])
        files.pack(fill="x")
        for i, (text, command) in enumerate(((t("program.new"), self.new), (t("program.open"), self.open),
                                             (t("program.save"), self.save))):
            files.grid_columnconfigure(i, weight=1, uniform="file")
            ttk.Button(files, text=text, style="Soft.TButton", command=command).grid(
                row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0))

        # --- run controls -------------------------------------------------------------------------------------
        run = tk.Frame(pad, bg=C["surface"])
        run.pack(fill="x", pady=(12, 0))
        self.buttons = {}
        for i, (key, text, command, tip) in enumerate((
                ("goto", t("run.goto"), self._goto, t("run.goto.tip")), ("start", t("run.start"), self._start, ""),
                ("pause", t("run.pause"), self._pause_resume, ""), ("stop", t("run.stop"), app.stop_program, ""))):
            run.grid_columnconfigure(i, weight=1, uniform="run")
            button = ttk.Button(run, text=text, style="Accent.TButton" if key == "start" else "Soft.TButton",
                                command=command)
            button.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0))
            self.buttons[key] = button
            if tip:
                Tooltip(button, tip)
        tk.Label(pad, textvariable=self.status_var, bg=C["surface"], fg=C["text"], font=fonts["small"], anchor="w").pack(
            fill="x", pady=(8, 2))
        self.progress = ThinProgress(pad)
        self.progress.pack(fill="x")
        tk.Label(pad, textvariable=self.estimate_var, bg=C["surface"], fg=C["muted"], font=fonts["small"],
                 anchor="w").pack(fill="x", pady=(4, 0))

        # --- table ----------------------------------------------------------------------------------------------------
        self.table = ProgramTable(pad, fonts, self._selection_changed, self._delete, self._edit_cell, self._edit_measure)
        self.table.pack(fill="x", pady=(12, 0))
        tools = tk.Frame(pad, bg=C["surface"])
        tools.pack(fill="x", pady=(6, 0))
        tools.grid_columnconfigure(0, weight=1)
        add = ttk.Button(tools, text=t("program.add_line"), style="Soft.TButton", command=lambda: self.add_point())
        add.grid(row=0, column=0, sticky="ew")
        Tooltip(add, t("program.add_line.tip"))
        for i, (text, direction, tip) in enumerate((("▲", -1, t("program.up.tip")), ("▼", 1, t("program.down.tip")))):
            button = ttk.Button(tools, text=text, width=3, style="Soft.TButton", command=lambda d=direction: self._move(d))
            button.grid(row=0, column=i + 1, padx=(6, 0))
            Tooltip(button, tip)
        tk.Label(pad, text=t("program.hint"), bg=C["surface"], fg=C["muted"], font=fonts["small"], anchor="w",
                 justify="left", wraplength=420).pack(fill="x", pady=(6, 0))

        # --- edit form ----------------------------------------------------------------------------------------------------
        self.form = ProgramForm(pad, fonts, self._apply_form)
        self.form.pack(fill="x", pady=(12, 0))
        self.refresh()
        self.set_run_state(IDLE, None)

    # --- showing the program ----------------------------------------------------------------------------------------------
    def refresh(self, keep=None):
        self.name_var.set(self.program.name)
        self.program.normalize()
        self.table.refresh(self.program.points, keep, self.step if self.state in (RUNNING, PAUSED) else None)
        self.form.load(self.program.points, self.table.selected_rows())
        self._update_estimate()
        self.app.program_changed()

    def _selection_changed(self):
        self.form.load(self.program.points, self.table.selected_rows())
        self.app.program_changed()

    def _update_estimate(self):
        if self.program.points:
            seconds, length = self.program.estimate(self.app.printer.profile)
            self.estimate_var.set(t("run.estimate", time=format_duration(seconds), length=length))
        else:
            self.estimate_var.set("")

    # --- editing --------------------------------------------------------------------------------------------------------------
    def new_point(self, x=None, y=None, z=None):
        """A line with the default values of the settings (speed, frequency, duration, number)."""
        d = self.app.settings.program
        return Point(x, y, z, d.speed, d.pause, Measure(rate_hz=d.rate_hz, duration_s=d.duration_s, count=d.count))

    def add_point(self, x=None, y=None, z=None):
        """Add a line and tick it, ready to be edited in the form."""
        self.program.points.append(self.new_point(x, y, z))
        self.refresh(keep=[len(self.program.points) - 1])

    def _apply_form(self):
        rows = self.table.selected_rows()
        try:
            point_changes, measure_changes = self.form.changes(self.app.acquisition.max_rate or None)
        except ValueError as exc:
            messagebox.showwarning(t("program.title"), t("form.invalid", error=exc), parent=self.winfo_toplevel())
            return
        self._update_rows(rows, point_changes, measure_changes)

    def _update_rows(self, rows, point_changes, measure_changes):
        if not point_changes and not measure_changes:
            return
        for i in rows:
            point = self.program.points[i]
            measure = replace(point.measure, **measure_changes) if measure_changes else point.measure
            self.program.points[i] = replace(point, measure=measure, **point_changes)
        self.refresh(keep=rows)

    def _edit_cell(self, index, field, text):
        """Direct edit of one value: only this line changes, whatever is ticked."""
        try:
            text = text.strip().replace(",", ".")
            value = None if text == "" and field in ("x", "y", "z") else float(text)
            if (field == "speed" and (value is None or value <= 0)) or (field == "pause" and (value is None or value < 0)):
                raise ValueError(field)
        except ValueError:
            messagebox.showwarning(t("program.title"), t("form.invalid", error=field.upper()), parent=self.winfo_toplevel())
            return
        point = self.program.points[index]
        self.program.points[index] = replace(point, **{field: value})
        self.refresh()

    def _edit_measure(self, index):
        """Window to change the measurement of ONE line (double-click on the MEASURE column)."""
        point = self.program.points[index]
        win = tk.Toplevel(self.winfo_toplevel())
        win.title(t("form.measure_window", n=index + 1))
        win.configure(bg=C["surface"])
        win.transient(self.winfo_toplevel())
        win.resizable(False, False)
        pad = tk.Frame(win, bg=C["surface"])
        pad.pack(padx=18, pady=16)
        editor = MeasureEditor(pad, self.f, C["surface"])
        editor.pack(fill="x")
        m = point.measure
        editor.allow_path(index > 0)
        editor.set_values(m.kind, m.by, f"{m.count:g}", f"{m.duration_s:g}", f"{m.rate_hz:g}")
        loaded = editor.snapshot()

        def ok():
            try:
                changes = editor.changes(loaded, self.app.acquisition.max_rate or None)
            except ValueError as exc:
                messagebox.showwarning(t("program.title"), str(exc), parent=win)
                return
            win.destroy()
            self._update_rows([index], {}, changes)

        buttons = tk.Frame(pad, bg=C["surface"])
        buttons.pack(fill="x", pady=(14, 0))
        ttk.Button(buttons, text=t("form.ok"), style="Accent.TButton", width=8, command=ok).pack(side="right")
        ttk.Button(buttons, text=t("form.cancel"), style="Soft.TButton", command=win.destroy).pack(
            side="right", padx=(0, 8))
        win.bind("<Return>", lambda _e: ok())
        win.bind("<Escape>", lambda _e: win.destroy())
        win.grab_set()

    def _delete(self, index):
        keep = [r - 1 if r > index else r for r in self.table.selected_rows() if r != index]
        del self.program.points[index]
        self.refresh(keep=keep)

    def _move(self, direction):
        rows = self.table.selected_rows()
        points = self.program.points
        if not rows or (direction < 0 and rows[0] == 0) or (direction > 0 and rows[-1] == len(points) - 1):
            return
        for i in (rows if direction < 0 else reversed(rows)):
            points[i], points[i + direction] = points[i + direction], points[i]
        self.refresh(keep=[i + direction for i in rows])

    # --- files ------------------------------------------------------------------------------------------------------------------------
    def _folder(self):
        folder = self.app.settings.program.folder
        os.makedirs(folder, exist_ok=True)
        return folder

    def new(self):
        self.program = Program(t("program.untitled"))
        self.refresh(keep=[])

    def open(self):
        path = filedialog.askopenfilename(parent=self.winfo_toplevel(), initialdir=self._folder(),
                                          filetypes=[(t("program.filetype"), "*.json")])
        if not path:
            return
        try:
            self.program = program_io.load(path)
        except (OSError, program_io.ProgramFileError) as exc:
            messagebox.showerror(t("program.file_error"), str(exc), parent=self.winfo_toplevel())
            return
        if not self.program.name:
            self.program.name = os.path.splitext(os.path.basename(path))[0]
        self.refresh(keep=[])

    def save(self):
        name = (self.program.name or t("program.untitled")).strip()
        path = filedialog.asksaveasfilename(parent=self.winfo_toplevel(), initialdir=self._folder(),
                                            initialfile=f"{name}.json", defaultextension=".json",
                                            filetypes=[(t("program.filetype"), "*.json")])
        if path:
            try:
                program_io.save(self.program, path)
            except OSError as exc:
                messagebox.showerror(t("program.save_error"), str(exc), parent=self.winfo_toplevel())

    # --- running --------------------------------------------------------------------------------------------------------------------------
    def issues(self):
        """Problems of the program as translated text lines."""
        printer = self.app.printer
        found = self.program.validate(printer.profile.limits(), printer.effective_speed(1e9),
                                      self.app.acquisition.max_rate or None)
        return [t(f"issue.{code}", **details) for code, details in found]

    def _start(self):
        issues = self.issues()
        if issues:
            messagebox.showwarning(t("run.issues.title"), "\n".join(issues), parent=self.winfo_toplevel())
            return
        self.app.run_program(self.program)

    def _goto(self):
        self.app.go_to_start(self.program)

    def _pause_resume(self):
        (self.app.resume_program if self.state == PAUSED else self.app.pause_program)()

    def set_run_state(self, state, step, message=""):
        self.state, self.step = state, step
        total = len(self.program.points)
        running = state in (RUNNING, PAUSED)
        if running:
            text = t(f"run.state.{state}", n=(step or 0) + 1, total=total)
        elif state == "error":
            text = t("run.state.error", message=message)
        else:
            text = t(f"run.state.{state}")
        self.status_var.set(text)
        self.progress.set(100.0 if state == FINISHED else (100.0 * (step or 0) / total if running and total else 0.0))
        idle_ready = not running and self.app.printer.ready and not self.app.busy
        self.buttons["goto"].configure(state="normal" if idle_ready else "disabled")
        self.buttons["start"].configure(state="normal" if idle_ready else "disabled")
        self.buttons["pause"].configure(state="normal" if running else "disabled",
                                        text=t("run.resume") if state == PAUSED else t("run.pause"))
        self.buttons["stop"].configure(state="normal" if running else "disabled")
        self.table.locked = running
        self.form.set_locked(running)
        self.table.refresh(self.program.points, current=step if running else None)
