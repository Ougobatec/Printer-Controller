"""Point entry: type coordinates, see them in the 3D view, copy the printer position, go there, add."""

import tkinter as tk
from tkinter import ttk

from ..i18n import t
from .theme import C
from .widgets import Tooltip


class PointPanel(tk.Frame):
    """Callbacks: `on_change()`, `on_current()`, `on_goto()`, `on_add()`."""

    def __init__(self, parent, fonts, on_change, on_current, on_goto, on_add):
        super().__init__(parent, bg=C["surface"], highlightthickness=1, highlightbackground=C["line"])
        self.vars = {a: tk.StringVar() for a in "XYZ"}
        self.step_var = tk.StringVar(value="1")
        self._on_change = on_change
        pad = tk.Frame(self, bg=C["surface"])
        pad.pack(fill="x", padx=16, pady=14)
        pad.grid_columnconfigure(0, weight=1)
        top = tk.Frame(pad, bg=C["surface"])
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        tk.Label(top, text=t("point.title").upper(), bg=C["surface"], fg=C["muted"], font=fonts["section"]).pack(
            side="left")
        tk.Label(top, text=t("point.subtitle"), bg=C["surface"], fg=C["faint"], font=fonts["small"]).pack(
            side="left", padx=10)
        fields = tk.Frame(pad, bg=C["surface"])
        fields.grid(row=1, column=0, sticky="ew")
        for i, axis in enumerate("XYZ"):
            fields.grid_columnconfigure(i, weight=1, uniform="pt")
            group = tk.Frame(fields, bg=C["surface"])
            group.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 10, 0))
            tk.Label(group, text=axis, bg=C["surface"], fg=C["muted"], font=fonts["small"], anchor="w").pack(fill="x")
            line = tk.Frame(group, bg=C["surface"])
            line.pack(fill="x")
            ttk.Button(line, text="−", width=2, style="Mini.TButton", command=lambda a=axis: self._nudge(a, -1)).pack(
                side="left")
            entry = ttk.Entry(line, textvariable=self.vars[axis], width=5)
            entry.pack(side="left", fill="x", expand=True, padx=3)
            entry.bind("<KeyRelease>", lambda _e: on_change())
            ttk.Button(line, text="+", width=2, style="Mini.TButton", command=lambda a=axis: self._nudge(a, 1)).pack(
                side="left")
        step = tk.Frame(pad, bg=C["surface"])
        step.grid(row=2, column=0, sticky="w", pady=(10, 0))
        tk.Label(step, text=t("point.step"), bg=C["surface"], fg=C["muted"], font=fonts["small"]).pack(side="left")
        ttk.Entry(step, textvariable=self.step_var, width=4).pack(side="left", padx=(6, 2))
        tk.Label(step, text="mm", bg=C["surface"], fg=C["muted"], font=fonts["small"]).pack(side="left")
        actions = tk.Frame(pad, bg=C["surface"])
        actions.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        for column in (0, 1):
            actions.grid_columnconfigure(column, weight=1, uniform="act")
        self.current_button = ttk.Button(actions, text=t("point.current"), style="Soft.TButton", command=on_current)
        self.current_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        Tooltip(self.current_button, t("point.current.tip"))
        self.goto_button = ttk.Button(actions, text=t("point.goto"), style="Soft.TButton", command=on_goto)
        self.goto_button.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        ttk.Button(pad, text=t("point.add"), style="Accent.TButton", command=on_add).grid(
            row=4, column=0, sticky="ew", pady=(10, 0))

    def _nudge(self, axis, direction):
        try:
            step = float(self.step_var.get().replace(",", "."))
            value = float(self.vars[axis].get().replace(",", ".") or 0)
        except ValueError:
            return
        self.vars[axis].set(f"{round(value + direction * step, 4):g}")
        self._on_change()

    def values(self):
        """(x, y, z) or None when a field is not a number."""
        try:
            return tuple(float(self.vars[a].get().replace(",", ".")) for a in "XYZ")
        except ValueError:
            return None

    def set_values(self, x, y, z):
        for axis, value in zip("XYZ", (x, y, z)):
            self.vars[axis].set("" if value is None else f"{value:g}")
        self._on_change()

    def set_printer_ready(self, ready):
        self.goto_button.configure(state="normal" if ready else "disabled")
        self.current_button.configure(state="normal" if ready else "disabled")
