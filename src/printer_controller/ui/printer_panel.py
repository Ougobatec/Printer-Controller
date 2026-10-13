"""Left column: position, manual moves, move speed and the printer settings button."""

import tkinter as tk
from tkinter import ttk

from ..i18n import t
from .theme import C
from .widgets import Tooltip

AXES = "XYZ"
AXIS_COLORS = {"X": C["axis_x"], "Y": C["axis_y"], "Z": C["axis_z"]}


def section_title(parent, text, fonts, pady=(0, 6)):
    tk.Label(parent, text=text.upper(), bg=C["surface"], fg=C["muted"], font=fonts["section"],
             anchor="w").pack(fill="x", pady=pady)


class PrinterPanel(tk.Frame):
    """`on_jog_start(axis, direction)` / `on_jog_stop()` are called while a jog button is held."""

    def __init__(self, parent, fonts, settings, on_jog_start=None, on_jog_stop=None,
                 on_home_origin=None, on_open_settings=None):
        super().__init__(parent, bg=C["surface"])
        self._fonts = fonts
        self._buttons = []
        self.position_vars = {a: tk.StringVar(value="—") for a in AXES}
        self.kind_var = tk.StringVar(value="")
        self.speed_var = tk.StringVar(value="300")
        pad = tk.Frame(self, bg=C["surface"])
        pad.pack(fill="both", expand=True, padx=16, pady=16)

        # --- Position ---------------------------------------------------------
        section_title(pad, t("panel.position"), fonts)
        tiles = tk.Frame(pad, bg=C["surface"])
        tiles.pack(fill="x", pady=(0, 4))
        for i, axis in enumerate(AXES):
            tiles.grid_columnconfigure(i, weight=1, uniform="tile")
            tile = tk.Frame(tiles, bg=C["soft"])
            tile.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0))
            tk.Label(tile, text=axis, bg=C["soft"], fg=AXIS_COLORS[axis], font=fonts["section"]).pack(
                anchor="w", padx=10, pady=(8, 0))
            tk.Label(tile, textvariable=self.position_vars[axis], bg=C["soft"], fg=C["text"],
                     font=fonts["mono_big"], anchor="w").pack(fill="x", padx=10, pady=(0, 8))

        tk.Label(pad, textvariable=self.kind_var, bg=C["surface"], fg=C["faint"], font=fonts["small"],
                 anchor="e").pack(fill="x", pady=(0, 12))

        # --- Manual moves ------------------------------------------------------------
        section_title(pad, t("panel.jog"), fonts)
        grid = tk.Frame(pad, bg=C["surface"])
        grid.pack(fill="x")
        for col in (0, 1, 2, 4):
            grid.grid_columnconfigure(col, weight=1, uniform="jog")
        grid.grid_columnconfigure(3, minsize=16)

        def jog(text, axis, direction, row, col):
            button = ttk.Button(grid, text=text, style="Jog.TButton")
            button.grid(row=row, column=col, padx=3, pady=3, sticky="nsew")
            if on_jog_start:
                button.bind("<ButtonPress-1>", lambda _e: on_jog_start(axis, direction))
            if on_jog_stop:
                button.bind("<ButtonRelease-1>", lambda _e: on_jog_stop())
                button.bind("<Leave>", lambda _e: on_jog_stop())
            self._buttons.append(button)

        jog("Y +", "Y", 1, 0, 1)
        jog("X −", "X", -1, 1, 0)
        origin = ttk.Button(grid, text="0,0,0", style="Home.TButton", command=on_home_origin)
        origin.grid(row=1, column=1, padx=3, pady=3, sticky="nsew")
        Tooltip(origin, "X0 Y0 Z0")
        self._buttons.append(origin)
        jog("X +", "X", 1, 1, 2)
        jog("Y −", "Y", -1, 2, 1)
        jog("Z +", "Z", 1, 0, 4)
        tk.Label(grid, text="Z", bg=C["surface"], fg=C["faint"], font=fonts["section"]).grid(row=1, column=4)
        jog("Z −", "Z", -1, 2, 4)
        tk.Label(pad, text=t("panel.jog.hint"), bg=C["surface"], fg=C["muted"], font=fonts["small"],
                 anchor="w", justify="left", wraplength=290).pack(fill="x", pady=(6, 16))

        # --- Speed (mm/min everywhere) ----------------------------------------------------
        section_title(pad, t("panel.speed"), fonts)
        row = tk.Frame(pad, bg=C["surface"])
        row.pack(fill="x")
        self.spin = ttk.Spinbox(row, from_=1, to=60000, increment=50, textvariable=self.speed_var, width=7)
        self.spin.pack(side="left")
        tk.Label(row, text="mm/min", bg=C["surface"], fg=C["muted"], font=fonts["small"]).pack(
            side="left", padx=8)
        self.chips_frame = tk.Frame(pad, bg=C["surface"])
        self.chips_frame.pack(fill="x", pady=(8, 16))
        self._chips = {}
        self.speed_var.trace_add("write", lambda *_: self._sync_chips())
        self.set_quick_speeds(settings.printer.quick_speeds, speed_max=settings.printer.speed_cap)

        # --- Printer settings -------------------------------------------------------------------
        button = ttk.Button(pad, text=t("panel.printer_settings"), style="Soft.TButton",
                            command=on_open_settings)
        button.pack(fill="x")
        Tooltip(button, t("panel.printer_settings.tip"))
        self.set_enabled(False)

    # --- speed buttons: from the slowest to the fastest -------------------------------------
    def set_quick_speeds(self, speeds, speed_max=0.0):
        for child in self.chips_frame.winfo_children():
            child.destroy()
        self._chips = {}
        values = sorted({float(v) for v in speeds if v > 0 and (speed_max <= 0 or v <= speed_max)})
        for i, value in enumerate(values):
            self.chips_frame.grid_columnconfigure(i, weight=1, uniform="chip")
            chip = ttk.Button(self.chips_frame, text=f"{value:g}", style="Chip.TButton",
                              command=lambda v=value: self.speed_var.set(f"{v:g}"))
            chip.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 3, 0))
            self._chips[value] = chip
        self._sync_chips()

    def _sync_chips(self):
        try:
            current = float(self.speed_var.get().replace(",", "."))
        except ValueError:
            current = None
        for value, chip in self._chips.items():
            chip.configure(style="ChipOn.TButton" if current == value else "Chip.TButton")

    # --- state ------------------------------------------------------------------------------------
    def set_position(self, x, y, z, kind=""):
        for axis, value in zip(AXES, (x, y, z)):
            self.position_vars[axis].set("—" if value is None else f"{value:.2f}")
        self.kind_var.set(kind)

    def speed(self):
        """Speed typed or chosen (mm/min); 300 if the field is not a number."""
        try:
            return max(1.0, float(self.speed_var.get().replace(",", ".")))
        except ValueError:
            return 300.0

    def set_enabled(self, enabled):
        for button in self._buttons:
            button.configure(state="normal" if enabled else "disabled")
