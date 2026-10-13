"""Connection frame of the "Sensor" header button: serial instrument or NI-DAQ card."""

import tkinter as tk
from tkinter import ttk

from ..core.sensor import line_parser as lp
from ..core.sensor.nidaq_sensor import NidaqConfig, NidaqSensor
from ..core.sensor.serial_sensor import EOL, ENCODINGS, SerialConfig, SerialSensor
from ..i18n import t
from .theme import C

BAUDRATES = ("1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600")
SEPARATOR = " — "


def _labels(prefix, keys):
    return {key: t(f"{prefix}.{key}") for key in keys}


def _key(mapping, label, default):
    return next((k for k, v in mapping.items() if v == label), default)


class SensorPopover:
    """Callbacks: `on_connect(sensor)`, `on_disconnect()`, `run_bg(work, done)`,
    `read_line(config)` (blocking: one line from the device), `detect_cards()` (blocking)."""

    def __init__(self, fonts, settings, list_ports, on_connect, on_disconnect, run_bg, read_line, detect_cards):
        self.f, self.settings = fonts, settings
        self._list_ports, self._on_connect, self._on_disconnect = list_ports, on_connect, on_disconnect
        self._run_bg, self._read_line, self._detect_cards = run_bg, read_line, detect_cards
        self._connected = False
        self._inputs = []
        self._cards = {}                      # label -> NiDevice
        self._port_names = {}
        self.kinds = {"serial": t("sensor.type.serial"), "nidaq": t("sensor.type.nidaq")}
        self.modes = _labels("extract", lp.MODES)
        self.separators = _labels("sep", lp.SEPARATORS)
        self.eols = _labels("eol", EOL)

    # ------------------------------------------------------------------------------------------
    def build(self, frame):
        pad = tk.Frame(frame, bg=C["surface"])
        pad.pack(fill="x", padx=16, pady=14)
        self.pad = pad
        head = tk.Frame(pad, bg=C["surface"])
        head.pack(fill="x")
        tk.Label(head, text=t("popover.sensor.title"), bg=C["surface"], fg=C["muted"], font=self.f["section"]).pack(
            side="left")
        self.kind_var = tk.StringVar(value=self.kinds.get(self.settings.sensor.kind, self.kinds["serial"]))
        self.kind_combo = ttk.Combobox(pad, textvariable=self.kind_var, values=list(self.kinds.values()),
                                       state="readonly")
        self.kind_combo.pack(fill="x", pady=(8, 10))
        self.kind_combo.bind("<<ComboboxSelected>>", lambda _e: self._show_kind())
        self._register(self.kind_combo, "readonly")
        self.serial_frame = self._build_serial(pad)
        self.nidaq_frame = self._build_nidaq(pad)
        self.error_var = tk.StringVar()
        tk.Label(pad, textvariable=self.error_var, bg=C["surface"], fg=C["danger"], font=self.f["small"],
                 wraplength=410, justify="left", anchor="w").pack(fill="x", pady=(8, 0))
        buttons = tk.Frame(pad, bg=C["surface"])
        buttons.pack(fill="x", pady=(10, 0))
        self.apply_button = ttk.Button(buttons, text=t("sensor.apply"), style="Soft.TButton", command=self._apply)
        self.apply_button.pack(side="left")
        self._register(self.apply_button)
        self.connect_button = ttk.Button(buttons, text=t("sensor.connect"), style="Accent.TButton",
                                         command=self._toggle)
        self.connect_button.pack(side="right")
        self._show_kind()
        self.refresh_ports()
        self._update_extracted()

    def _row(self, parent, row, label):
        tk.Label(parent, text=label, bg=C["surface"], fg=C["muted"], font=self.f["small"]).grid(
            row=row, column=0, sticky="w", pady=3, padx=(0, 10))

    def _register(self, widget, active="normal"):
        """Inputs are locked while connected; `active` is the state they have otherwise."""
        self._inputs.append((widget, active))
        return widget

    def _entry(self, parent, row, var, label, width=14):
        self._row(parent, row, label)
        entry = ttk.Entry(parent, textvariable=var, width=width)
        entry.grid(row=row, column=1, sticky="w")
        return self._register(entry)

    def _combo(self, parent, row, var, values, label, width=26, state="readonly"):
        self._row(parent, row, label)
        combo = ttk.Combobox(parent, textvariable=var, values=values, state=state, width=width)
        combo.grid(row=row, column=1, sticky="w")
        return self._register(combo, state)

    @staticmethod
    def _show_rows(frame, rows):
        """Show or hide whole grid rows (label and input): {row: visible}."""
        for row, visible in rows.items():
            for widget in frame.grid_slaves(row=row):
                widget.grid() if visible else widget.grid_remove()

    # --- serial instrument ---------------------------------------------------------------------
    def _build_serial(self, parent):
        c = SerialConfig.from_dict(self.settings.sensor.serial)
        frame = tk.Frame(parent, bg=C["surface"])
        self.v = {}
        s = self.v
        s["port"], s["baud"] = tk.StringVar(value=c.port), tk.StringVar(value=str(c.baudrate))
        s["command"], s["eol"] = tk.StringVar(value=c.command), tk.StringVar(value=self.eols[c.eol if c.eol in EOL else "LF"])
        s["format"] = tk.StringVar(value=c.encoding)
        s["mode"] = tk.StringVar(value=self.modes.get(c.parser.mode, self.modes["number"]))
        s["sep"] = tk.StringVar(value=self.separators.get(c.parser.separator, self.separators["space"]))
        s["custom"], s["pos"] = tk.StringVar(value=c.parser.custom_separator), tk.StringVar(value=str(c.parser.position))
        s["text"], s["regex"] = tk.StringVar(value=c.parser.text), tk.StringVar(value=c.parser.regex)
        s["decimal"] = tk.StringVar(value=c.parser.decimal)
        s["timeout"], s["max_rate"] = tk.StringVar(value=f"{c.timeout_s:g}"), tk.StringVar(value=f"{c.max_rate_hz:g}")
        self.example = c.example
        r = 0
        self._row(frame, r, t("serial.port"))
        port_row = tk.Frame(frame, bg=C["surface"])
        port_row.grid(row=r, column=1, sticky="w")
        self.port_combo = ttk.Combobox(port_row, textvariable=s["port"], width=22, postcommand=self.refresh_ports)
        self.port_combo.pack(side="left")
        refresh = ttk.Button(port_row, text="↻", width=3, style="Soft.TButton", command=self.refresh_ports)
        refresh.pack(side="left", padx=(6, 0))
        self._register(self.port_combo)
        self._register(refresh)
        self._combo(frame, 1, s["baud"], BAUDRATES, t("serial.baud"), 14, "normal")
        self._entry(frame, 2, s["command"], t("serial.command"), 26)
        self._combo(frame, 3, s["eol"], list(self.eols.values()), t("serial.eol"), 14)
        self._combo(frame, 4, s["format"], ENCODINGS, t("serial.format"), 14)
        self._combo(frame, 5, s["mode"], list(self.modes.values()), t("serial.extraction"))
        self._combo(frame, 6, s["sep"], list(self.separators.values()), t("serial.separator"), 14)
        self._entry(frame, 7, s["custom"], t("serial.custom"), 6)
        self._entry(frame, 8, s["pos"], t("serial.position"), 6)
        self._entry(frame, 9, s["text"], t("serial.text"), 26)
        self._entry(frame, 10, s["regex"], t("serial.regex"), 26)
        self._combo(frame, 11, s["decimal"], (".", ","), t("serial.decimal"), 6)
        self._row(frame, 12, t("serial.example"))
        box = tk.Frame(frame, bg=C["surface"])
        box.grid(row=12, column=1, sticky="w")
        self.example_text = tk.Text(box, height=2, width=30, font=self.f["mono"], relief="solid", bd=1, wrap="word")
        self.example_text.insert("1.0", self.example)
        self.example_text.pack(side="left")
        self.read_button = ttk.Button(frame, text=t("serial.read_line"), style="Soft.TButton",
                                      command=self._read_example)
        self.read_button.grid(row=13, column=1, sticky="w", pady=(2, 2))
        self._register(self.example_text)
        self._register(self.read_button)
        self._row(frame, 14, t("serial.extracted"))
        self.extracted = tk.Label(frame, bg=C["surface"], font=self.f["bold"], anchor="w", justify="left", wraplength=250)
        self.extracted.grid(row=14, column=1, sticky="w")
        self._entry(frame, 15, s["timeout"], t("serial.timeout"), 8)
        self._entry(frame, 16, s["max_rate"], t("serial.max_rate"), 10)
        for name in ("mode", "sep", "custom", "pos", "text", "regex", "decimal"):
            s[name].trace_add("write", lambda *_: self._update_extracted())
        self.example_text.bind("<KeyRelease>", lambda _e: self._update_extracted())
        return frame

    def _parser_config(self):
        s = self.v
        try:
            position = int(s["pos"].get())
        except ValueError:
            position = 0
        return lp.ParserConfig(
            mode=_key(self.modes, s["mode"].get(), "number"), separator=_key(self.separators, s["sep"].get(), "space"),
            custom_separator=s["custom"].get(), position=position, text=s["text"].get(), regex=s["regex"].get(),
            decimal=s["decimal"].get() or ".")

    def _update_extracted(self, *_):
        config = self._parser_config()
        self._show_rows(self.serial_frame, {
            6: config.mode == "field", 7: config.mode == "field" and config.separator == "custom",
            8: config.mode in ("number", "field"), 9: config.mode == "after_text", 10: config.mode == "regex"})
        line = self.example_text.get("1.0", "end").strip()
        if not line:
            self.extracted.configure(text="—", fg=C["faint"])
            return
        try:
            self.extracted.configure(text=f"{lp.extract(line, config):g}", fg=C["ok"])
        except lp.ParseError as exc:
            self.extracted.configure(text=t(f"parse.{exc.code}", **exc.details), fg=C["danger"])

    def set_example(self, line):
        self.example_text.delete("1.0", "end")
        self.example_text.insert("1.0", line)
        self._update_extracted()

    def _read_example(self):
        self.error_var.set("")
        try:
            config = self.serial_config()
        except ValueError as exc:
            self.error_var.set(str(exc))
            return

        def failed(exc):
            self.error_var.set(t("serial.read_error", error=exc))

        self._run_bg(lambda: self._read_line(config), self.set_example, failed)

    def serial_config(self):
        s = self.v
        port = s["port"].get().split(SEPARATOR)[0].strip()
        if not port:
            raise ValueError(t("printer.no_port"))
        try:
            config = SerialConfig(
                port=port, baudrate=int(s["baud"].get()), command=s["command"].get(),
                eol=_key(self.eols, s["eol"].get(), "LF"), encoding=s["format"].get(), parser=self._parser_config(),
                example=self.example_text.get("1.0", "end").strip(),
                timeout_s=float(s["timeout"].get().replace(",", ".")),
                max_rate_hz=float(s["max_rate"].get().replace(",", ".")))
        except ValueError:
            raise ValueError(t("dialog.invalid", error=t("serial.baud"))) from None
        if config.baudrate <= 0 or config.timeout_s <= 0 or config.max_rate_hz <= 0 or config.parser.position < 1:
            raise ValueError(t("dialog.invalid", error="> 0"))
        return config

    def refresh_ports(self):
        ports = self._list_ports()
        self._port_names = {p.device: p.description for p in ports}
        self.port_combo["values"] = [f"{p.device}{SEPARATOR}{p.description}" if p.description else p.device
                                     for p in ports]
        device = self.v["port"].get().split(SEPARATOR)[0].strip()
        if device and self._port_names.get(device):
            self.v["port"].set(f"{device}{SEPARATOR}{self._port_names[device]}")

    # --- NI-DAQ card -------------------------------------------------------------------------------------
    def _build_nidaq(self, parent):
        c = NidaqConfig.from_dict(self.settings.sensor.nidaq)
        frame = tk.Frame(parent, bg=C["surface"])
        self.n = {k: tk.StringVar() for k in ("device", "channel", "mode", "range")}
        self.n["timeout"] = tk.StringVar(value=f"{c.timeout_s:g}")
        self._ni_saved = c
        self.ni_device_combo = self._combo(frame, 0, self.n["device"], [], t("nidaq.device"), 30)
        self.ni_device_combo.bind("<<ComboboxSelected>>", lambda _e: self._ni_device_changed())
        detect = ttk.Button(frame, text="↻", width=3, style="Soft.TButton", command=self.detect_cards)
        detect.grid(row=0, column=2, padx=(6, 0))
        self._register(detect)
        self.ni_type = tk.Label(frame, bg=C["surface"], fg=C["text"], font=self.f["small"], anchor="w")
        self._row(frame, 1, t("nidaq.type"))
        self.ni_type.grid(row=1, column=1, sticky="w")
        self.ni_channel_combo = self._combo(frame, 2, self.n["channel"], [], t("nidaq.channel"), 14)
        self.ni_mode_combo = self._combo(frame, 3, self.n["mode"], [], t("nidaq.mode"), 26)
        self.ni_range_combo = self._combo(frame, 4, self.n["range"], [], t("nidaq.range"), 14)
        self.ni_resolution = tk.Label(frame, bg=C["surface"], fg=C["text"], font=self.f["small"], anchor="w")
        self._row(frame, 5, t("nidaq.resolution"))
        self.ni_resolution.grid(row=5, column=1, sticky="w")
        self._entry(frame, 6, self.n["timeout"], t("nidaq.timeout"), 8)
        self.ni_rate = tk.Label(frame, bg=C["surface"], fg=C["text"], font=self.f["small"], anchor="w")
        self._row(frame, 7, t("nidaq.max_rate"))
        self.ni_rate.grid(row=7, column=1, sticky="w")
        self._ranges = {}
        return frame

    def detect_cards(self):
        """Look for NI cards (in the background) and fill the lists with what they accept."""
        self.error_var.set("")

        def done(devices):
            self._cards = {d.label: d for d in devices}
            self.ni_device_combo["values"] = list(self._cards)
            if not devices:
                self.error_var.set(t("nidaq.none"))
                return
            saved = next((d.label for d in devices if d.name == self._ni_saved.device), devices[0].label)
            self.n["device"].set(saved)
            self._ni_device_changed()

        def failed(exc):
            self.error_var.set(t("nidaq.missing") if "nidaqmx_missing" in str(exc) else str(exc))

        self._run_bg(self._detect_cards, done, failed)

    def _ni_device_changed(self):
        device = self._cards.get(self.n["device"].get())
        unknown = t("nidaq.unknown")
        if device is None:
            return
        self.ni_type.configure(text=device.product or unknown)
        self.ni_resolution.configure(text=t("nidaq.bits", bits=f"{device.resolution:g}") if device.resolution else unknown)
        self.ni_rate.configure(text=f"{device.max_rate_hz:,.0f} Hz".replace(",", " ") if device.max_rate_hz else unknown)
        self.ni_channel_combo["values"] = device.channels
        self.ni_mode_combo["values"] = [t(f"term.{n}") if f"term.{n}" in self._terms() else n for n in device.terminals]
        self._ranges = {(f"±{hi:g} V" if abs(lo + hi) < 1e-9 else f"{lo:g} … {hi:g} V"): (lo, hi) for lo, hi in device.ranges}
        self.ni_range_combo["values"] = list(self._ranges)
        saved = self._ni_saved
        if saved.device == device.name:
            self.n["channel"].set(saved.channel)
            self.n["mode"].set(t(f"term.{saved.terminal}"))
            wanted = next((k for k, v in self._ranges.items() if v == (saved.range_lo, saved.range_hi)), None)
        else:
            wanted = None
        self.n["channel"].set(self.n["channel"].get() if self.n["channel"].get() in device.channels else
                              (device.channels[0] if device.channels else ""))
        if not self.n["mode"].get() and device.terminals:
            self.n["mode"].set(self.ni_mode_combo["values"][0])
        self.n["range"].set(wanted or (list(self._ranges)[0] if self._ranges else ""))

    @staticmethod
    def _terms():
        return {f"term.{n}" for n in ("DEFAULT", "RSE", "NRSE", "DIFF", "PSEUDO_DIFF")}

    def nidaq_config(self):
        device = self._cards.get(self.n["device"].get())
        if device is None:
            raise ValueError(t("nidaq.none"))
        terminal = next((n for n in device.terminals if t(f"term.{n}") == self.n["mode"].get() or n == self.n["mode"].get()),
                        "DEFAULT")
        lo, hi = self._ranges.get(self.n["range"].get(), (-10.0, 10.0))
        try:
            timeout = float(self.n["timeout"].get().replace(",", "."))
        except ValueError:
            raise ValueError(t("dialog.invalid", error=t("nidaq.timeout"))) from None
        if timeout <= 0 or not self.n["channel"].get():
            raise ValueError(t("dialog.invalid", error="> 0"))
        return NidaqConfig(device.name, self.n["channel"].get(), terminal, lo, hi, timeout), device

    # --- buttons -----------------------------------------------------------------------------------------------
    def _kind(self):
        return _key(self.kinds, self.kind_var.get(), "serial")

    def _show_kind(self):
        serial = self._kind() == "serial"
        self.serial_frame.pack_forget()
        self.nidaq_frame.pack_forget()
        (self.serial_frame if serial else self.nidaq_frame).pack(fill="x", after=self.kind_combo)
        if not serial and not self._cards:
            self.detect_cards()

    def _apply(self):
        """Save the configuration without connecting."""
        self.error_var.set("")
        try:
            self._save()
        except ValueError as exc:
            self.error_var.set(str(exc))

    def _save(self):
        kind = self._kind()
        self.settings.sensor.kind = kind
        if kind == "serial":
            self.settings.sensor.serial = self.serial_config().to_dict()
        else:
            self.settings.sensor.nidaq = self.nidaq_config()[0].to_dict()
        self.settings.save()

    def _toggle(self):
        if self._connected:
            self._on_disconnect()
            return
        self.error_var.set("")
        try:
            self._save()
            if self._kind() == "serial":
                sensor = SerialSensor(self.serial_config())
            else:
                config, device = self.nidaq_config()
                sensor = NidaqSensor(config, device)
        except ValueError as exc:
            self.error_var.set(str(exc))
            return
        self._on_connect(sensor)

    def show_error(self, message):
        self.error_var.set(message)

    def set_state(self, connected, busy):
        self._connected = connected
        self.connect_button.configure(text=t("sensor.disconnect") if connected else t("sensor.connect"),
                                      style="Soft.TButton" if connected else "Accent.TButton",
                                      state="disabled" if busy else "normal")
        locked = connected or busy
        for widget, active in self._inputs:
            widget.configure(state="disabled" if locked else active)
