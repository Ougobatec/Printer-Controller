"""Connection frame of the "Printer" header button: port, baud rate, connect."""

import tkinter as tk
from tkinter import ttk

from ..i18n import t
from .theme import C
from .widgets import Tooltip

BAUDRATES = ("9600", "19200", "38400", "57600", "115200", "230400", "250000", "460800", "500000", "921600")
SEPARATOR = " — "


class PrinterPopover:
    """`list_ports()` returns [(device, description)]; `on_connect(port, baud)`, `on_disconnect()`."""

    def __init__(self, fonts, settings, list_ports, on_connect, on_disconnect):
        self._fonts = fonts
        self._list_ports = list_ports
        self._on_connect, self._on_disconnect = on_connect, on_disconnect
        self._names = {}
        self.port_var = tk.StringVar(value=settings.printer.port)
        self.baud_var = tk.StringVar(value=str(settings.printer.baudrate))
        self._connected = False

    def build(self, frame):
        pad = tk.Frame(frame, bg=C["surface"])
        pad.pack(fill="x", padx=16, pady=14)
        f = self._fonts

        def caption(text, top=10):
            tk.Label(pad, text=text, bg=C["surface"], fg=C["muted"], font=f["small"]).pack(
                anchor="w", pady=(top, 2))

        tk.Label(pad, text=t("popover.printer.title"), bg=C["surface"], fg=C["muted"],
                 font=f["section"]).pack(anchor="w")
        caption(t("printer.port"))
        row = tk.Frame(pad, bg=C["surface"])
        row.pack(fill="x")
        self.port_combo = ttk.Combobox(row, textvariable=self.port_var, width=10, postcommand=self.refresh_ports)
        self.port_combo.pack(side="left", fill="x", expand=True)
        self.refresh_button = ttk.Button(row, text="↻", width=3, style="Soft.TButton", command=self.refresh_ports)
        self.refresh_button.pack(side="left", padx=(6, 0))
        Tooltip(self.refresh_button, t("printer.refresh.tip"))
        caption(t("printer.baud"))
        self.baud_combo = ttk.Combobox(pad, textvariable=self.baud_var, values=BAUDRATES, width=12)
        self.baud_combo.pack(anchor="w")
        self.connect_button = ttk.Button(pad, text=t("printer.connect"), style="Accent.TButton",
                                         command=self._toggle)
        self.connect_button.pack(fill="x", pady=(14, 0))
        tk.Label(pad, text=t("printer.connect.note"), bg=C["surface"], fg=C["faint"], font=f["small"],
                 justify="left", anchor="w", wraplength=340).pack(fill="x", pady=(8, 0))
        self.refresh_ports()

    # --- ports (shown with the name of the device) ---------------------------------------------
    def device(self):
        return self.port_var.get().split(SEPARATOR)[0].strip()

    def _label(self, device):
        name = self._names.get(device, "")
        return f"{device}{SEPARATOR}{name}" if name else device

    def refresh_ports(self):
        ports = self._list_ports()
        self._names = {p.device: p.description for p in ports}
        self.port_combo["values"] = [self._label(p.device) for p in ports]
        if self.device():
            self.port_var.set(self._label(self.device()))

    # --- actions ---------------------------------------------------------------------------------
    def _toggle(self):
        if self._connected:
            self._on_disconnect()
        else:
            self._on_connect(self.device(), self.baud_var.get().strip())

    def set_state(self, connected, busy):
        """Called by the app when the connection state changes."""
        self._connected = connected
        self.connect_button.configure(
            text=t("printer.disconnect") if connected else t("printer.connect"),
            style="Soft.TButton" if connected else "Accent.TButton",
            state="disabled" if busy else "normal")
        locked = "disabled" if connected or busy else "normal"
        self.port_combo.configure(state=locked)
        self.baud_combo.configure(state=locked)
        self.refresh_button.configure(state=locked)
