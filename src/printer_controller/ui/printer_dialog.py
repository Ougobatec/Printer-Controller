"""Window "Printer settings": identity, homing, values (with their source), safety and timeouts."""

import tkinter as tk
from tkinter import messagebox, ttk

from ..core.printer import profile as P
from ..i18n import t
from .theme import C
from .widgets import ScrollColumn

BADGE_COLORS = {P.DETECTED: C["ok"], P.MANUAL: C["warn"], P.DEFAULT: C["muted"]}


class PrinterDialog:
    def __init__(self, root, fonts, app):
        self.root, self.f, self.app = root, fonts, app
        self.printer, self.settings = app.printer, app.settings
        self.win = tk.Toplevel(root)
        self.win.title(t("dialog.printer.title"))
        self.win.configure(bg=C["surface"])
        self.win.transient(root)
        self.win.geometry("520x760")
        self.win.minsize(460, 400)
        column = ScrollColumn(self.win, bg=C["surface"])
        column.pack(fill="both", expand=True)
        self.body = tk.Frame(column.body, bg=C["surface"])
        self.body.pack(fill="both", expand=True, padx=20, pady=16)
        self._fields = {}                                # profile key -> (StringVar, badge label)
        self._build()
        self.refresh()

    # --- layout ---------------------------------------------------------------------------------
    def _title(self, text, top=18):
        tk.Label(self.body, text=text.upper(), bg=C["surface"], fg=C["muted"], font=self.f["section"],
                 anchor="w").pack(fill="x", pady=(top, 6))

    def _note(self, text):
        tk.Label(self.body, text=text, bg=C["surface"], fg=C["faint"], font=self.f["small"], anchor="w",
                 justify="left", wraplength=450).pack(fill="x", pady=(4, 0))

    def _label(self, parent, text, **grid):
        tk.Label(parent, text=text, bg=C["surface"], fg=C["muted"], font=self.f["small"]).grid(sticky="w", **grid)

    def _build(self):
        # Identity
        self._title(t("dialog.identity"), top=0)
        self.identity = tk.Label(self.body, bg=C["surface"], fg=C["text"], font=self.f["base"], anchor="w",
                                 justify="left")
        self.identity.pack(fill="x")

        # Homing
        self._title(t("dialog.homing"))
        self.homing_label = tk.Label(self.body, bg=C["surface"], fg=C["text"], font=self.f["small"], anchor="w")
        self.homing_label.pack(fill="x", pady=(0, 6))
        self.home_button = ttk.Button(self.body, text=t("dialog.home"), style="Soft.TButton",
                                      command=self.app.home_printer)
        self.home_button.pack(fill="x")
        self._note(t("dialog.home.note"))

        # Printer values
        self._title(t("dialog.values"))
        grid = tk.Frame(self.body, bg=C["surface"])
        grid.pack(fill="x")
        row = 0

        def section(text):
            nonlocal row
            tk.Label(grid, text=text, bg=C["surface"], fg=C["text"], font=self.f["bold"], anchor="w").grid(
                row=row, column=0, columnspan=4, sticky="w", pady=(8, 2))
            row += 1

        def field(key, label):
            nonlocal row
            self._label(grid, label, row=row, column=0)
            var = tk.StringVar()
            entry = ttk.Entry(grid, textvariable=var, width=9)
            entry.grid(row=row, column=1, padx=(8, 8), pady=1)
            badge = tk.Label(grid, bg=C["surface"], font=self.f["small"], anchor="w")
            badge.grid(row=row, column=2, sticky="w")
            for event in ("<Return>", "<FocusOut>"):
                entry.bind(event, lambda _e, k=key: self._commit(k))
            self._fields[key] = (var, badge)
            row += 1

        section(t("dialog.limits"))
        for axis in "XYZ":
            for end in ("min", "max"):
                field(f"limits.{axis}.{end}", f"{axis} {t('dialog.' + end)}")
        section(t("dialog.max_speed"))
        for axis in "XYZ":
            field(f"max_speed.{axis}", axis)
        section(t("dialog.max_accel"))
        for axis in "XYZ":
            field(f"max_accel.{axis}", axis)
        section(t("dialog.travel_accel"))
        field("travel_accel", "")
        self._note(t("dialog.values.note"))
        buttons = tk.Frame(self.body, bg=C["surface"])
        buttons.pack(fill="x", pady=(10, 0))
        self.reread_button = ttk.Button(buttons, text=t("dialog.reread"), style="Soft.TButton",
                                        command=self.app.reread_printer)
        self.reread_button.pack(side="left")
        ttk.Button(buttons, text=t("dialog.reset"), style="Ghost.TButton", command=self._reset).pack(
            side="left", padx=8)

        # Safety and app-side parameters
        s = self.settings.printer
        self._title(t("dialog.safety"))
        self.auto_home = tk.BooleanVar(value=s.auto_home)
        ttk.Checkbutton(self.body, text=t("dialog.auto_home"), variable=self.auto_home).pack(anchor="w")
        form = tk.Frame(self.body, bg=C["surface"])
        form.pack(fill="x", pady=(8, 0))
        self._vars = {}

        def setting(row_, label, key, value, width=10):
            self._label(form, label, row=row_, column=0, pady=2)
            var = tk.StringVar(value=value)
            ttk.Entry(form, textvariable=var, width=width).grid(row=row_, column=1, padx=8, pady=2, sticky="w")
            self._vars[key] = var

        setting(0, t("dialog.speed_cap"), "speed_cap", f"{s.speed_cap:g}")
        setting(1, t("dialog.quick_speeds"), "quick_speeds", ", ".join(f"{v:g}" for v in s.quick_speeds), 28)
        self._title(t("dialog.timeouts"))
        form = tk.Frame(self.body, bg=C["surface"])
        form.pack(fill="x")
        for i, key in enumerate(("startup_timeout", "command_timeout", "home_timeout", "move_margin")):
            self._label(form, t(f"dialog.{key}"), row=i, column=0, pady=2)
            var = tk.StringVar(value=f"{getattr(s, key):g}")
            ttk.Entry(form, textvariable=var, width=10).grid(row=i, column=1, padx=8, pady=2, sticky="w")
            self._vars[key] = var
        bar = tk.Frame(self.body, bg=C["surface"])
        bar.pack(fill="x", pady=(18, 0))
        ttk.Button(bar, text=t("dialog.apply"), style="Accent.TButton", command=self._apply).pack(side="right")
        ttk.Button(bar, text=t("dialog.close"), style="Soft.TButton", command=self.win.destroy).pack(
            side="right", padx=(0, 8))

    # --- showing the current state ----------------------------------------------------------------
    def refresh(self):
        if not self.win.winfo_exists():
            return
        p, connected = self.printer.profile, self.printer.connected
        unknown = t("dialog.unknown")
        self.identity.configure(text=f"{t('dialog.model')} : {p.model or unknown}\n"
                                     f"{t('dialog.firmware')} : {p.firmware or unknown}")
        self.homing_label.configure(text=t("dialog.homed") if self.printer.homed else
                                    t("dialog.not_homed") if connected else t("dialog.not_connected"))
        state = "normal" if connected and not self.app.busy else "disabled"
        self.home_button.configure(state=state)
        self.reread_button.configure(state=state)
        for key, (var, badge) in self._fields.items():
            var.set(f"{p.value(key):g}")
            source = p.source(key)
            badge.configure(text=t(f"dialog.source.{source}"), fg=BADGE_COLORS[source])

    def _commit(self, key):
        var, _ = self._fields[key]
        try:
            value = float(var.get().replace(",", "."))
            if value != self.printer.profile.value(key):
                self.printer.profile.set_manual(key, value)
                self.settings.printer.overrides = self.printer.profile.overrides()
                self.settings.save()
                self.app.profile_changed()
        except ValueError as exc:
            messagebox.showwarning(t("dialog.printer.title"), t("dialog.invalid", error=exc), parent=self.win)
        self.refresh()

    def _reset(self):
        if messagebox.askyesno(t("dialog.printer.title"), t("dialog.reset.confirm"), parent=self.win):
            self.printer.profile.reset_to_detected()
            self.settings.printer.overrides = {}
            self.settings.save()
            self.app.profile_changed()
            self.refresh()

    def _apply(self):
        s = self.settings.printer
        try:
            speeds = [float(v) for v in self._vars["quick_speeds"].get().replace(";", ",").split(",") if v.strip()]
            if not speeds or any(v <= 0 for v in speeds):
                raise ValueError(t("dialog.quick_speeds"))
            values = {key: float(self._vars[key].get().replace(",", ".")) for key in
                      ("speed_cap", "startup_timeout", "command_timeout", "home_timeout", "move_margin")}
            if values["speed_cap"] < 0 or any(values[k] <= 0 for k in values if k != "speed_cap"):
                raise ValueError("> 0")
        except ValueError as exc:
            messagebox.showwarning(t("dialog.printer.title"), t("dialog.invalid", error=exc), parent=self.win)
            return
        for key, value in values.items():
            setattr(s, key, value)
        s.quick_speeds = sorted(set(speeds))
        s.auto_home = bool(self.auto_home.get())
        self.settings.save()
        self.app.profile_changed()
        self.refresh()
