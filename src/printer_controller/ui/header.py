"""Header bar: title, printer / sensor connection buttons, settings, stop buttons."""

import tkinter as tk
from tkinter import ttk

from ..i18n import t
from .theme import C
from .widgets import HeaderButton, Tooltip


class Header(tk.Frame):
    def __init__(self, parent, fonts, on_printer, on_sensor, on_settings, on_stop, on_emergency):
        super().__init__(parent, bg=C["surface"], height=58)
        self.pack_propagate(False)

        # Stop buttons: always visible, far right
        self.emergency_button = ttk.Button(self, text=t("button.emergency"), style="Danger.TButton",
                                           command=on_emergency)
        self.emergency_button.pack(side="right", padx=(8, 20))
        Tooltip(self.emergency_button, t("button.emergency.tip"))
        self.stop_button = ttk.Button(self, text=t("button.stop"), style="DangerSoft.TButton",
                                      command=on_stop)
        self.stop_button.pack(side="right")
        Tooltip(self.stop_button, t("button.stop.tip"))

        tk.Label(self, text=t("app.title"), bg=C["surface"], fg=C["text"],
                 font=fonts["title"]).pack(side="left", padx=(20, 24))
        self.printer_button = HeaderButton(
            self, self._label("header.printer", "state.printer.disconnected"), fonts["base"], on_printer)
        self.printer_button.pack(side="left")
        Tooltip(self.printer_button, t("header.printer.tip"))
        self.sensor_button = HeaderButton(
            self, self._label("header.sensor", "state.sensor.disconnected"), fonts["base"], on_sensor)
        self.sensor_button.pack(side="left", padx=(10, 0))
        Tooltip(self.sensor_button, t("header.sensor.tip"))
        self.settings_button = HeaderButton(self, t("header.settings"), fonts["base"], on_settings)
        self.settings_button.dot.pack_forget()
        self.settings_button.label.pack_configure(padx=14)
        self.settings_button.pack(side="left", padx=(10, 0))
        Tooltip(self.settings_button, t("header.settings.tip"))
        self.set_stop_enabled(False)

    @staticmethod
    def _label(name_key, state_key):
        return f"{t(name_key)} · {t(state_key)}"

    def set_printer_state(self, color, text):
        self.printer_button.set(color, f"{t('header.printer')} · {text}")

    def set_sensor_state(self, color, text):
        self.sensor_button.set(color, f"{t('header.sensor')} · {text}")

    def set_stop_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        self.stop_button.configure(state=state)
        self.emergency_button.configure(state=state)
