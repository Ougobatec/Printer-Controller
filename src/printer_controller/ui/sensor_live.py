"""Live monitoring of the sensor (Measure tab): value, multiplier, unit, filter, frequency, graph.

Nothing is recorded here: it only shows that the sensor works.
"""

import tkinter as tk
from tkinter import ttk

from ..core.sensor.filters import KINDS
from ..i18n import t
from .plots import Plot2D
from .theme import C

REFRESH_WINDOW_S = 60.0


class SensorLive(tk.Frame):
    def __init__(self, parent, fonts, settings, acquisition):
        super().__init__(parent, bg=C["surface"], highlightthickness=1, highlightbackground=C["line"])
        self.f, self.settings, self.acq = fonts, settings, acquisition
        s = settings.sensor
        pad = tk.Frame(self, bg=C["surface"])
        pad.pack(fill="both", expand=True, padx=16, pady=14)

        head = tk.Frame(pad, bg=C["surface"])
        head.pack(fill="x")
        tk.Label(head, text=t("live.title"), bg=C["surface"], fg=C["muted"], font=fonts["section"]).pack(side="left")
        tk.Label(head, text=t("live.subtitle"), bg=C["surface"], fg=C["faint"], font=fonts["small"]).pack(
            side="left", padx=10)
        self.toggle_button = ttk.Button(head, text=t("live.start"), style="Accent.TButton", command=self._toggle)
        self.toggle_button.pack(side="right")

        self.value_var = tk.StringVar(value="—")
        value_row = tk.Frame(pad, bg=C["surface"])
        value_row.pack(fill="x", pady=(8, 4))
        tk.Label(value_row, textvariable=self.value_var, bg=C["surface"], fg=C["text"],
                 font=fonts["mono_big"]).pack(side="left")
        self.unit_label = tk.Label(value_row, text=s.unit, bg=C["surface"], fg=C["muted"], font=fonts["bold"])
        self.unit_label.pack(side="left", padx=(8, 0), pady=(6, 0))

        self.vars = {
            "multiplier": tk.StringVar(value=f"{s.multiplier:g}"), "unit": tk.StringVar(value=s.unit),
            "filter": tk.StringVar(value=t(f"filter.{s.filter}") if s.filter in KINDS else t("filter.none")),
            "filter_size": tk.StringVar(value=str(s.filter_size)),
            "live_rate_hz": tk.StringVar(value=f"{s.live_rate_hz:g}"),
            "live_history": tk.StringVar(value=str(s.live_history))}
        form = tk.Frame(pad, bg=C["surface"])
        form.pack(fill="x", pady=(6, 0))
        self._controls = []
        col = 0

        def field(label, key, width=8, combo=None):
            nonlocal col
            tk.Label(form, text=label, bg=C["surface"], fg=C["muted"], font=fonts["small"]).grid(
                row=0, column=col, sticky="w", padx=(0 if col == 0 else 14, 6))
            if combo:
                widget = ttk.Combobox(form, textvariable=self.vars[key], values=combo, state="readonly", width=width)
                widget.bind("<<ComboboxSelected>>", lambda _e: self._commit())
            else:
                widget = ttk.Entry(form, textvariable=self.vars[key], width=width)
                widget.bind("<Return>", lambda _e: self._commit())
                widget.bind("<FocusOut>", lambda _e: self._commit())
            widget.grid(row=0, column=col + 1)
            self._controls.append(widget)
            col += 2

        field(t("live.multiplier"), "multiplier")
        field(t("live.unit"), "unit", 6)
        field(t("live.rate"), "live_rate_hz", 7)
        form2 = tk.Frame(pad, bg=C["surface"])
        form2.pack(fill="x", pady=(8, 0))
        form, col = form2, 0
        field(t("live.filter"), "filter", 16, [t(f"filter.{k}") for k in KINDS])
        field(t("live.filter_size"), "filter_size", 5)
        field(t("live.history"), "live_history", 7)

        self.note_var = tk.StringVar()
        tk.Label(pad, textvariable=self.note_var, bg=C["surface"], fg=C["danger"], font=fonts["small"],
                 anchor="w", wraplength=600, justify="left").pack(fill="x", pady=(6, 0))
        self.plot = Plot2D(pad, fonts)
        self.plot.configure(height=260)
        self.plot.pack(fill="both", expand=True, pady=(4, 0))
        self.refresh()

    # --- settings ------------------------------------------------------------------------------
    def _commit(self):
        s, v = self.settings.sensor, self.vars

        def number(key, label, kind=float, minimum=0.0):
            try:
                value = kind(v[key].get().replace(",", "."))
            except ValueError:
                raise ValueError(label) from None
            if value < minimum or (kind is float and value == 0 and key != "multiplier"):
                raise ValueError(label)
            return value

        try:
            multiplier = float(v["multiplier"].get().replace(",", "."))
            if multiplier == 0:
                raise ValueError
        except ValueError:
            self.note_var.set(t("live.invalid", field=t("live.multiplier")))
            return
        try:
            rate = number("live_rate_hz", t("live.rate"))
            size = number("filter_size", t("live.filter_size"), int, 1)
            history = number("live_history", t("live.history"), int, 10)
        except ValueError as exc:
            self.note_var.set(t("live.invalid", field=str(exc)))
            return
        self.note_var.set("")
        if self.acq.max_rate and rate > self.acq.max_rate:
            rate = self.acq.max_rate
            v["live_rate_hz"].set(f"{rate:g}")
            self.note_var.set(t("live.rate.limited", rate=f"{rate:g}"))
        s.multiplier, s.unit, s.live_rate_hz = multiplier, v["unit"].get().strip(), rate
        s.filter_size, s.live_history = size, history
        s.filter = next((k for k in KINDS if t(f"filter.{k}") == v["filter"].get()), "none")
        self.acq.apply_settings()
        self.settings.save()
        self.unit_label.configure(text=s.unit)

    # --- running --------------------------------------------------------------------------------------
    def _toggle(self):
        if self.acq.live_running:
            self.acq.stop_live()
        else:
            self._commit()
            self.acq.start_live()
        self.refresh()

    def refresh(self):
        """Called a few times per second."""
        connected, running = self.acq.connected, self.acq.live_running
        self.toggle_button.configure(text=t("live.stop") if running else t("live.start"),
                                     style="Soft.TButton" if running else "Accent.TButton",
                                     state="normal" if connected else "disabled")
        if self.acq.error and not running:
            self.note_var.set(t("live.error", error=self.acq.error))
        values = self.acq.live_values()
        if values:
            self.value_var.set(f"{values[-1][1]:.6g}")
            last = values[-1][0]
            xs = [x - last for x, _ in values if last - x <= REFRESH_WINDOW_S]
            ys = [y for x, y in values if last - x <= REFRESH_WINDOW_S]
            self.plot.show(lines=[(xs, ys, C["accent"])], xlabel=t("live.time"), unit=self.settings.sensor.unit)
        else:
            self.value_var.set("—")
            self.plot.show(empty=t("live.empty") if connected else t("live.not_connected"))
