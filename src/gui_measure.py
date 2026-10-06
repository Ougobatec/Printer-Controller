"""Partie « mesure » de l'interface (mélangée à EnderGUI, voir gui.py).

* onglet Mesure     : choix du système de mesure et de ses réglages (le
                      formulaire se construit d'après les `Param` du pilote),
                      multiplicateur / unité, contrôle en direct du capteur ;
* onglet Résultats  : tableau de toutes les mesures avec leurs coordonnées,
                      courbes (temps ou position) et nuage 3D coloré ;
* pastille de la barre du haut : état de la mesure et valeur courante.
"""

import math
import os
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import config
from acquisition import Acquisition, describe_values
from devices import DEVICE_TYPES, device_class
from measure_run import ProgramMeasurer
from plots import Plot2D, Scatter3D
from theme import C
from widgets import ScrollColumn, Segmented

UNIT_PRESETS = ("V", "mV", "µV", "mm", "µm", "N", "°C", "bar", "%")
MULT_PRESETS = ("1", "10", "100", "1000", "0.001", "1e6")
CHART_VIEWS = [("time", "Temps"), ("X", "Position X"), ("Y", "Position Y"),
               ("Z", "Position Z"), ("3d", "Nuage 3D")]
MAX_TABLE_ROWS = 3000


def value_text(value):
    return "—" if value is None else f"{value:.6g}"


def coord_text(value):
    return "—" if value is None else f"{value:.2f}"


class MeasureMixin:
    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------
    def init_measure(self):
        self.acq = Acquisition(position_provider=self.tracker.sample_position)
        self.measurer = ProgramMeasurer(self.acq, self.tracker, self.printer)
        self.acq.on_error = lambda message: self.post(self.measure_error, message)

        saved = self.settings
        key = saved.get("measure", "type") or config.ACQ_BACKEND
        self.m_type_key = key if key in DEVICE_TYPES else next(iter(DEVICE_TYPES))
        self.m_type_var = tk.StringVar(value=device_class(self.m_type_key).title)
        self.m_param_vars = {}
        for type_key, cls in DEVICE_TYPES.items():
            stored = saved.get("measure", "devices", type_key, default={}) or {}
            self.m_param_vars[type_key] = {
                p.key: tk.StringVar(value=str(stored.get(p.key, p.default))) for p in cls.PARAMS}

        signal = saved.get("measure", "signal", default={}) or {}
        self.m_mult_var = tk.StringVar(value=f"{signal.get('multiplier', config.MEASURE_MULTIPLIER):g}")
        self.m_unit_var = tk.StringVar(value=str(signal.get("unit", config.MEASURE_UNIT)))
        self.m_note_var = tk.StringVar(value="")
        self.m_status_var = tk.StringVar(value="Non connecté")
        self.m_detect_var = tk.StringVar(value="")
        self.m_live_value_var = tk.StringVar(value="—")
        self.m_live_stats_var = tk.StringVar(value="")
        self.m_chart_var = tk.StringVar(value="time")
        self.m_summary_var = tk.StringVar(value="Aucune mesure")

        self.m_connecting = False
        self.m_state = "idle"
        self.m_choices = {}
        self.m_widgets = {}
        self.m_traces = []
        self.m_detect_job = None
        self.m_busy_stop = False

        self.measured_indices = set()
        self._res_rev = -1
        self._res_epoch = -1
        self._res_dirty = False      # tableau à reconstruire entièrement
        self._res_more = False       # il reste des lignes à ajouter
        self._idx_rev = -1
        self._res_rendered = 0
        self._res_t0 = 0.0
        self._res_stamp = 0.0
        self._res_selected = None
        self._was_in_segment = False
        self._overlay = (None, None, 0.0, [])
        self._apply_signal()
        for var in (self.m_mult_var, self.m_unit_var):
            var.trace_add("write", lambda *_: self._apply_signal())

    def start_measure_polling(self):
        self.root.after(200, self.detect_devices)
        self._poll_measure()

    # ------------------------------------------------------------------
    # Pastille de la barre du haut
    # ------------------------------------------------------------------
    def build_measure_chip(self, bar):
        chip = tk.Frame(bar, bg=C["surface"], cursor="hand2")
        chip.pack(side="left", padx=(24, 0))
        self.m_dot = tk.Canvas(chip, width=10, height=10, bg=C["surface"], highlightthickness=0)
        self.m_dot.pack(side="left", padx=(0, 8))
        self.m_dot_item = self.m_dot.create_oval(1, 1, 9, 9, fill=C["faint"], outline="")
        self.m_chip_label = tk.Label(chip, text="Mesure non connectée", bg=C["surface"],
                                     fg=C["muted"], font=self.f["base"], anchor="w")
        self.m_chip_label.pack(side="left")
        for widget in (chip, self.m_dot, self.m_chip_label):
            widget.bind("<Button-1>", lambda _e: self.tabs.select("mesure"))
        self.tip(chip, "Ouvrir l'onglet Mesure")

    def update_measure_chip(self):
        acq = self.acq
        if self.m_state == "error":
            color, text = C["danger"], "Mesure en erreur"
        elif self.m_connecting:
            color, text = C["warn"], "Mesure : connexion…"
        elif acq.connected:
            color = C["ok"]
            latest = acq.latest
            if acq.is_running and latest and time.time() - latest[0] < 3:
                text = f"Mesure · {value_text(latest[1])} {acq.unit}"
            else:
                text = "Mesure connectée"
        else:
            color, text = C["faint"], "Mesure non connectée"
        self.m_dot.itemconfigure(self.m_dot_item, fill=color)
        self.m_chip_label.configure(text=text)

    # ------------------------------------------------------------------
    # Onglet Mesure
    # ------------------------------------------------------------------
    def create_measure_tab(self, tab):
        col = ScrollColumn(tab)
        col.pack(fill="both", expand=True)
        body = col.body
        body.grid_columnconfigure(0, weight=1)

        # --- Appareil ---------------------------------------------------
        device_card = self.card(body)
        device_card.grid(row=0, column=0, sticky="ew")
        pad = tk.Frame(device_card, bg=C["surface"])
        pad.pack(fill="x", padx=16, pady=16)
        pad.grid_columnconfigure(0, weight=1)

        head = tk.Frame(pad, bg=C["surface"])
        head.grid(row=0, column=0, sticky="ew")
        tk.Label(head, text="APPAREIL DE MESURE", bg=C["surface"], fg=C["muted"],
                 font=self.f["section"]).pack(side="left")
        self.m_connect_btn = ttk.Button(head, text="Connecter", style="Accent.TButton",
                                        command=self.toggle_measure_connection)
        self.m_connect_btn.pack(side="right")
        status = tk.Frame(head, bg=C["surface"])
        status.pack(side="right", padx=14)
        self.m_status_dot = tk.Canvas(status, width=10, height=10, bg=C["surface"],
                                      highlightthickness=0)
        self.m_status_dot.pack(side="left", padx=(0, 6))
        self.m_status_item = self.m_status_dot.create_oval(1, 1, 9, 9, fill=C["faint"], outline="")
        tk.Label(status, textvariable=self.m_status_var, bg=C["surface"], fg=C["muted"],
                 font=self.f["small"]).pack(side="left")

        system = tk.Frame(pad, bg=C["surface"])
        system.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        system.grid_columnconfigure(0, weight=1)
        tk.Label(system, text="Système de mesure", bg=C["surface"], fg=C["muted"],
                 font=self.f["small"]).grid(row=0, column=0, sticky="w")
        self.m_type_combo = ttk.Combobox(
            system, textvariable=self.m_type_var, state="readonly",
            values=[cls.title for cls in DEVICE_TYPES.values()])
        self.m_type_combo.grid(row=1, column=0, sticky="ew")
        self.m_type_combo.bind("<<ComboboxSelected>>", lambda _e: self.change_device_type())
        self.m_detect_btn = ttk.Button(system, text="↻  Détecter", style="Soft.TButton",
                                       command=self.detect_devices)
        self.m_detect_btn.grid(row=1, column=1, padx=(8, 0))
        self.tip(self.m_detect_btn, "Recherche les cartes / ports disponibles et remplit les listes.")
        self.m_type_hint = tk.Label(pad, bg=C["surface"], fg=C["muted"], font=self.f["small"],
                                    justify="left", anchor="w")
        self.m_type_hint.grid(row=2, column=0, sticky="ew", pady=(6, 12))
        self.m_type_hint.bind(
            "<Configure>", lambda e: self.m_type_hint.configure(wraplength=max(200, e.width - 4)))

        self.m_form = tk.Frame(pad, bg=C["surface"])
        self.m_form.grid(row=3, column=0, sticky="ew")
        tk.Label(pad, textvariable=self.m_detect_var, bg=C["surface"], fg=C["muted"],
                 font=self.f["small"], anchor="w").grid(row=4, column=0, sticky="ew")

        tk.Frame(pad, bg=C["line"], height=1).grid(row=5, column=0, sticky="ew", pady=(14, 12))

        # --- Signal -------------------------------------------------------
        tk.Label(pad, text="SIGNAL", bg=C["surface"], fg=C["muted"], font=self.f["section"],
                 anchor="w").grid(row=6, column=0, sticky="ew", pady=(0, 6))
        signal = tk.Frame(pad, bg=C["surface"])
        signal.grid(row=7, column=0, sticky="ew")
        for i in range(2):
            signal.grid_columnconfigure(i, weight=1, uniform="sig")

        def cell(col, label, widget_factory, hint):
            frame = tk.Frame(signal, bg=C["surface"])
            frame.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 10, 0))
            tk.Label(frame, text=label, bg=C["surface"], fg=C["muted"],
                     font=self.f["small"]).pack(anchor="w")
            widget = widget_factory(frame)
            widget.pack(fill="x")
            self.tip(widget, hint)
            return widget

        self.m_mult_box = cell(0, "Multiplicateur ×", lambda f: ttk.Combobox(
            f, textvariable=self.m_mult_var, values=MULT_PRESETS, width=8),
            "La valeur affichée = valeur brute de l'appareil × ce nombre.")
        self.m_unit_box = cell(1, "Unité", lambda f: ttk.Combobox(
            f, textvariable=self.m_unit_var, values=UNIT_PRESETS, width=8),
            "Unité affichée à côté des mesures (saisie libre possible).")
        tk.Label(pad, textvariable=self.m_note_var, bg=C["surface"], fg=C["danger"],
                 font=self.f["small"], anchor="w").grid(row=8, column=0, sticky="ew", pady=(6, 0))
        tk.Label(pad, bg=C["surface"], fg=C["muted"], font=self.f["small"], anchor="w",
                 text="Valeur affichée = valeur brute × multiplicateur. Un changement ne "
                      "s'applique qu'aux nouvelles mesures. La fréquence et le nombre "
                      "d'échantillons se règlent dans le programme.").grid(row=9, column=0, sticky="ew")

        # --- Contrôle en direct ----------------------------------------------
        live_card = self.card(body)
        live_card.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        lpad = tk.Frame(live_card, bg=C["surface"])
        lpad.pack(fill="x", padx=16, pady=16)
        lhead = tk.Frame(lpad, bg=C["surface"])
        lhead.pack(fill="x")
        tk.Label(lhead, text="CONTRÔLE EN DIRECT", bg=C["surface"], fg=C["muted"],
                 font=self.f["section"]).pack(side="left")
        tk.Label(lhead, text="vérifier que le capteur répond · rien n'est enregistré",
                 bg=C["surface"], fg=C["faint"], font=self.f["small"]).pack(side="left", padx=10)
        self.live_button = ttk.Button(lhead, text="▶  Démarrer", style="Soft.TButton",
                                      command=self.toggle_live)
        self.live_button.pack(side="right")

        readout = tk.Frame(lpad, bg=C["surface"])
        readout.pack(fill="x", pady=(10, 4))
        tk.Label(readout, textvariable=self.m_live_value_var, bg=C["surface"], fg=C["text"],
                 font=self.f["mono_huge"]).pack(side="left")
        self.m_live_unit = tk.Label(readout, text="V", bg=C["surface"], fg=C["muted"],
                                    font=self.f["bold"])
        self.m_live_unit.pack(side="left", padx=(8, 0), pady=(8, 0))
        tk.Label(readout, textvariable=self.m_live_stats_var, bg=C["surface"], fg=C["muted"],
                 font=self.f["small"], justify="right").pack(side="right")
        self.live_plot = Plot2D(lpad, self.f, compact=True)
        self.live_plot.pack(fill="x")
        self.live_plot.show(empty="Contrôle arrêté")

        self.render_device_form()

    # --- Formulaire dynamique -------------------------------------------
    def current_device_class(self):
        return device_class(self.m_type_key)

    def change_device_type(self):
        title = self.m_type_var.get()
        for key, cls in DEVICE_TYPES.items():
            if cls.title == title:
                self.m_type_key = key
        self.m_detect_var.set("")
        self.render_device_form()
        self.detect_devices()
        self.save_measure_settings()

    def render_device_form(self):
        for var, trace_id in self.m_traces:
            try:
                var.trace_remove("write", trace_id)
            except Exception:
                pass
        self.m_traces = []
        for child in self.m_form.winfo_children():
            child.destroy()
        cls = self.current_device_class()
        self.m_type_hint.configure(text=cls.description)
        variables = self.m_param_vars[self.m_type_key]
        self.m_widgets = {}
        self.m_form.grid_columnconfigure(0, weight=1, uniform="mf")
        self.m_form.grid_columnconfigure(1, weight=1, uniform="mf")
        for i, p in enumerate(cls.PARAMS):
            cell = tk.Frame(self.m_form, bg=C["surface"])
            cell.grid(row=i // 2, column=i % 2, sticky="ew", padx=(0 if i % 2 == 0 else 12, 0),
                      pady=(0, 10))
            tk.Label(cell, text=p.label, bg=C["surface"], fg=C["muted"],
                     font=self.f["small"]).pack(anchor="w")
            if p.kind == "choice":
                values = p.choices if isinstance(p.choices, (list, tuple)) else \
                    self.m_choices.get((self.m_type_key, p.key), [])
                widget = ttk.Combobox(cell, textvariable=variables[p.key], values=list(values),
                                      state="normal" if p.editable else "readonly")
            else:
                widget = ttk.Entry(cell, textvariable=variables[p.key])
            widget.pack(fill="x")
            if p.hint:
                self.tip(widget, p.hint)
            self.m_widgets[p.key] = (widget, p)
            for dep in p.depends:
                if dep in variables:
                    trace_id = variables[dep].trace_add(
                        "write", lambda *_a, k=p.key: self._schedule_detect(k))
                    self.m_traces.append((variables[dep], trace_id))
        self.update_measure_controls()

    def _schedule_detect(self, only):
        if self.m_detect_job is not None:
            self.root.after_cancel(self.m_detect_job)
        self.m_detect_job = self.root.after(500, lambda: self.detect_devices(only))

    def detect_devices(self, only=None):
        self.m_detect_job = None
        if self.acq.connected or self.m_connecting:
            return
        key = self.m_type_key
        cls = self.current_device_class()
        values = {k: v.get() for k, v in self.m_param_vars[key].items()}

        def work():
            found = {}
            for p in cls.PARAMS:
                if callable(p.choices) and (only is None or p.key == only):
                    try:
                        found[p.key] = list(p.choices(values))
                    except Exception:
                        found[p.key] = []
            return key, found

        self.run_measure_async(work, self._detected)

    def _detected(self, result):
        key, found = result
        for param_key, values in found.items():
            self.m_choices[(key, param_key)] = values
            entry = self.m_widgets.get(param_key) if key == self.m_type_key else None
            if entry:
                widget, p = entry
                widget.configure(values=values)
                var = self.m_param_vars[key][param_key]
                if values and var.get() not in values:
                    var.set(values[0])
        if key != self.m_type_key:
            return
        cls = self.current_device_class()
        first = next((p for p in cls.PARAMS if callable(p.choices)), None)
        if first and first.key in found:
            values = found[first.key]
            if values:
                self.m_detect_var.set(f"Détecté — {first.label} : {', '.join(values[:8])}")
            else:
                self.m_detect_var.set(f"Rien de détecté pour « {first.label} » "
                                      "(pilote installé ? appareil branché ?). Saisie manuelle possible.")

    # --- Signal -----------------------------------------------------------
    def _apply_signal(self):
        acq = self.acq
        try:
            multiplier = float(self.m_mult_var.get().replace(",", "."))
            if multiplier == 0 or not math.isfinite(multiplier):
                raise ValueError
        except ValueError:
            self.m_note_var.set("Multiplicateur invalide (nombre différent de 0).")
            return
        self.m_note_var.set("")
        acq.multiplier = multiplier
        acq.unit = self.m_unit_var.get().strip() or "V"
        if hasattr(self, "m_live_unit"):
            self.m_live_unit.configure(text=acq.unit)

    def save_measure_settings(self):
        s = self.settings
        s.set("measure", "type", self.m_type_key)
        for key, variables in self.m_param_vars.items():
            s.set("measure", "devices", key, {k: v.get() for k, v in variables.items()})
        s.set("measure", "signal", {
            "multiplier": self.acq.multiplier, "unit": self.acq.unit})
        s.save()

    # --- Connexion ----------------------------------------------------------
    def run_measure_async(self, function, on_success=None, on_error=None):
        def worker():
            try:
                result = function()
            except Exception as exc:
                self.post(self._measure_async_failed, str(exc) or exc.__class__.__name__, on_error)
            else:
                if on_success:
                    self.post(on_success, result)

        threading.Thread(target=worker, daemon=True).start()

    def _measure_async_failed(self, message, on_error):
        self.write_log(f"ERREUR MESURE : {message}")
        if on_error:
            on_error(message)

    def toggle_measure_connection(self):
        if self.m_connecting or self.running:
            return
        if self.acq.connected:
            self.disconnect_measure()
        else:
            self.connect_measure()

    def connect_measure(self):
        cls = self.current_device_class()
        values = {k: v.get() for k, v in self.m_param_vars[self.m_type_key].items()}
        try:
            device = cls(**values)
        except Exception as exc:
            messagebox.showerror("Mesure", str(exc))
            return
        self._apply_signal()
        self.m_connecting = True
        self.m_state = "busy"
        self.set_measure_status("Connexion…", "busy")
        self.update_measure_controls()
        self.write_log(f"Mesure : connexion à {cls.title}…")

        def failed(message):
            self.m_connecting = False
            self.m_state = "error"
            self.set_measure_status("Échec de connexion", "error")
            self.update_measure_controls()
            messagebox.showerror("Connexion à l'appareil de mesure", message)

        self.run_measure_async(lambda: self.acq.connect(device), self.measure_connected, failed)

    def measure_connected(self, _=None):
        self.m_connecting = False
        self.m_state = "ok"
        self.acq.clear_live()
        self.set_measure_status(f"Connecté · {self.acq.description}", "ok")
        self.write_log(f"Mesure : connecté ({self.acq.description}).")
        self.save_measure_settings()
        self.update_measure_controls()

    def disconnect_measure(self):
        self.run_measure_async(self.acq.disconnect, self._measure_disconnected)

    def _measure_disconnected(self, _=None):
        self.m_state = "idle"
        self.set_measure_status("Non connecté", "idle")
        self.m_live_value_var.set("—")
        self.m_live_stats_var.set("")
        self.write_log("Mesure : déconnecté.")
        self.update_measure_controls()
        self.detect_devices()

    def measure_error(self, message):
        self.m_state = "error"
        self.set_measure_status("Erreur de lecture", "error")
        self.write_log(f"ERREUR MESURE : {message}")
        if self.running and self.acq.in_segment:
            self.write_log("Programme interrompu : la mesure ne répond plus.")
            self.stop_program()
        self.update_measure_controls()

    def set_measure_status(self, text, kind):
        colors = {"idle": C["faint"], "ok": C["ok"], "busy": C["warn"], "error": C["danger"]}
        self.m_status_var.set(text if len(text) <= 48 else text[:45] + "…")
        self.m_status_dot.itemconfigure(self.m_status_item, fill=colors.get(kind, C["faint"]))

    def toggle_live(self):
        acq = self.acq
        if not acq.connected or acq.in_segment:
            return
        if acq.is_running:
            self.live_button.configure(state="disabled")
            self.run_measure_async(acq.stop, lambda _: self.update_measure_controls())
        else:
            try:
                acq.start()
                if self.m_state == "error":
                    self.m_state = "ok"
                    self.set_measure_status(f"Connecté · {acq.description}", "ok")
            except Exception as exc:
                messagebox.showerror("Mesure", str(exc))
        self.update_measure_controls()

    def update_measure_controls(self):
        if not hasattr(self, "res_clear_btn"):
            return          # interface pas encore entièrement construite
        acq = self.acq
        connected = acq.connected
        locked = connected or self.m_connecting
        self.m_connect_btn.configure(
            text="Déconnecter" if connected else "Connecter",
            style="Soft.TButton" if connected else "Accent.TButton",
            state="disabled" if (self.m_connecting or self.running) else "normal")
        self.m_type_combo.configure(state="disabled" if locked else "readonly")
        self.m_detect_btn.configure(state="disabled" if locked else "normal")
        for widget, p in self.m_widgets.values():
            if locked:
                widget.configure(state="disabled")
            else:
                widget.configure(state="readonly" if (p.kind == "choice" and not p.editable)
                                 else "normal")
        for widget in (self.m_mult_box, self.m_unit_box):
            widget.configure(state="disabled" if self.running else "normal")
        running_live = acq.is_running and not acq.in_segment
        self.live_button.configure(
            text="■  Arrêter" if running_live else "▶  Démarrer",
            state="normal" if (connected and not acq.in_segment) else "disabled")
        if self.m_state == "ok" and not connected and not self.m_connecting:
            self.m_state = "idle"
            self.set_measure_status("Non connecté", "idle")
        has_data = acq.record_count > 0
        self.set_enabled([self.res_export_btn, self.res_clear_btn], has_data and not self.running)

    # --- Contrôle en direct -------------------------------------------------
    def refresh_live(self):
        acq = self.acq
        if not (acq.connected and acq.is_running):
            self.live_plot.show(empty="Contrôle arrêté" if acq.connected else "Appareil non connecté")
            return
        now = time.time()
        data = acq.live_since(now - 10)
        if not data:
            return
        xs = [t - now for t, _ in data]
        ys = [v for _, v in data]
        self.m_live_value_var.set(value_text(ys[-1]))
        stats = describe_values(ys)
        self.m_live_stats_var.set(
            f"min {value_text(stats['min'])}   max {value_text(stats['max'])}\n"
            f"moy {value_text(stats['mean'])}   σ {value_text(stats['std'])}   (10 s)")
        self.live_plot.show(lines=[(xs, ys, C["accent"])], unit=acq.unit, x_range=(-10, 0))

    # ------------------------------------------------------------------
    # Onglet Résultats
    # ------------------------------------------------------------------
    def create_results_tab(self, tab):
        col = ScrollColumn(tab)
        col.pack(fill="both", expand=True)
        card = self.card(col.body)
        card.pack(fill="both", expand=True)
        pad = tk.Frame(card, bg=C["surface"])
        pad.pack(fill="both", expand=True, padx=16, pady=16)
        pad.grid_columnconfigure(0, weight=1)
        pad.grid_rowconfigure(4, weight=1, minsize=150)

        head = tk.Frame(pad, bg=C["surface"])
        head.grid(row=0, column=0, sticky="ew")
        tk.Label(head, text="RÉSULTATS", bg=C["surface"], fg=C["muted"],
                 font=self.f["section"]).pack(side="left")
        self.res_clear_btn = ttk.Button(head, text="Effacer", style="Soft.TButton",
                                        command=self.clear_results)
        self.res_clear_btn.pack(side="right")
        self.res_export_btn = ttk.Button(head, text="Exporter CSV", style="Soft.TButton",
                                         command=self.export_results)
        self.res_export_btn.pack(side="right", padx=(0, 6))
        self.tip(self.res_export_btn, "Toutes les mesures avec leurs coordonnées (ouvrable dans Excel).")

        views = tk.Frame(pad, bg=C["surface"])
        views.grid(row=1, column=0, sticky="ew", pady=(10, 8))
        self.res_seg = Segmented(views, CHART_VIEWS, self.m_chart_var,
                                 command=lambda _v: self.res_view_changed())
        self.res_seg.pack(side="left")
        self.res_presets = tk.Frame(views, bg=C["surface"])
        for name, label in (("iso", "Iso"), ("dessus", "Dessus"), ("face", "Face")):
            ttk.Button(self.res_presets, text=label, style="Chip.TButton",
                       command=lambda n=name: self.scatter.set_view(n)).pack(side="left", padx=(4, 0))

        chart = tk.Frame(pad, bg=C["surface"], highlightthickness=1,
                         highlightbackground=C["line"], highlightcolor=C["line"])
        chart.grid(row=2, column=0, sticky="ew")
        chart.grid_columnconfigure(0, weight=1)
        self.res_plot = Plot2D(chart, self.f)
        self.res_plot.configure(height=240)
        self.res_plot.grid(row=0, column=0, sticky="nsew")
        self.scatter = Scatter3D(chart, self.f)
        self.scatter.configure(height=240)
        self.scatter.grid(row=0, column=0, sticky="nsew")
        tk.Misc.tkraise(self.res_plot)

        tk.Label(pad, textvariable=self.m_summary_var, bg=C["surface"], fg=C["muted"],
                 font=self.f["small"], anchor="w").grid(row=3, column=0, sticky="ew", pady=(8, 8))

        table = tk.Frame(pad, bg=C["surface"], highlightthickness=1,
                         highlightbackground=C["line"], highlightcolor=C["line"])
        table.grid(row=4, column=0, sticky="nsew")
        table.grid_columnconfigure(0, weight=1)
        table.grid_rowconfigure(0, weight=1)
        cols = ("n", "t", "x", "y", "z", "valeur", "type")
        self.results_tree = ttk.Treeview(table, columns=cols, show="headings",
                                         selectmode="browse", height=7)
        titles = {"n": "N°", "t": "TEMPS (s)", "x": "X", "y": "Y", "z": "Z",
                  "valeur": "VALEUR", "type": "MESURE"}
        widths = {"n": 46, "t": 92, "x": 64, "y": 64, "z": 64, "valeur": 110, "type": 100}
        for col_id in cols:
            anchor = "w" if col_id == "type" else "center"
            self.results_tree.heading(col_id, text=titles[col_id], anchor=anchor)
            self.results_tree.column(col_id, width=widths[col_id], minwidth=36, anchor=anchor,
                                     stretch=True)
        scroll = ttk.Scrollbar(table, orient="vertical", style="Thin.Vertical.TScrollbar",
                               command=self.results_tree.yview)

        def on_scroll(first, last):
            scroll.set(first, last)
            if float(first) <= 0.0 and float(last) >= 1.0:
                scroll.grid_remove()
            else:
                scroll.grid(row=0, column=1, sticky="ns")

        self.results_tree.configure(yscrollcommand=on_scroll)
        self.results_tree.grid(row=0, column=0, sticky="nsew")
        self.results_tree.tag_configure("est", foreground=C["muted"])
        self.results_tree.bind("<<TreeviewSelect>>", self.results_selected)
        tk.Label(pad, text="Les coordonnées en gris sont calculées (tête en mouvement) d'après "
                           "l'instant, la fréquence, la vitesse et les limites par axe.",
                 bg=C["surface"], fg=C["muted"], font=self.f["small"], anchor="w",
                 justify="left").grid(row=5, column=0, sticky="ew", pady=(6, 0))

    def res_view_changed(self):
        three_d = self.m_chart_var.get() == "3d"
        if three_d:
            tk.Misc.tkraise(self.scatter)
            self.res_presets.pack(side="left", padx=(12, 0))
        else:
            tk.Misc.tkraise(self.res_plot)
            self.res_presets.pack_forget()
        self._res_dirty = True

    def measure_tab_changed(self, key):
        if key == "resultats" and hasattr(self, "results_tree"):
            self._res_dirty = True

    def results_selected(self, _event=None):
        selection = self.results_tree.selection()
        records = self.acq.records_snapshot()
        self._res_selected = None
        if selection:
            number = int(self.results_tree.item(selection[0], "values")[0])
            if 1 <= number <= len(records):
                self._res_selected = records[number - 1]
                if self._res_selected.index is not None and not self.running:
                    self.select_program_row(self._res_selected.index)
        self.update_results_chart(records)

    def clear_results(self):
        if not self.acq.record_count or self.running:
            return
        if messagebox.askyesno("Effacer", f"Effacer les {self.acq.record_count} mesures enregistrées ?"):
            self.acq.clear()
            self._res_selected = None
            self._res_dirty = True

    def export_results(self):
        if not self.acq.record_count:
            return
        os.makedirs(config.PROGRAMS_DIR, exist_ok=True)
        path = filedialog.asksaveasfilename(
            initialdir=config.PROGRAMS_DIR, defaultextension=".csv",
            initialfile=f"mesures_{datetime.now():%Y%m%d_%H%M%S}.csv",
            filetypes=[("CSV (Excel)", "*.csv")])
        if not path:
            return
        try:
            count = self.acq.export_csv(path)
        except OSError as exc:
            messagebox.showerror("Export", str(exc))
            return
        self.write_log(f"{count} mesures exportées : {path}")

    # --- Rafraîchissement --------------------------------------------------
    def _poll_measure(self):
        try:
            self._measure_tick()
        except Exception as exc:
            self.write_log(f"ERREUR INTERNE : {exc!r}")
        self.root.after(120, self._poll_measure)

    def _measure_tick(self):
        acq = self.acq
        self.update_measure_chip()
        tab = self.tabs.current
        if tab == "mesure":
            self.refresh_live()
        elif acq.is_running and acq.latest:
            self.m_live_value_var.set(value_text(acq.latest[1]))

        if self._was_in_segment and not acq.in_segment:
            self._res_dirty = True          # positions recalculées : on refait le tableau
            self.update_measure_controls()
        self._was_in_segment = acq.in_segment

        now = time.time()
        if now - self._res_stamp < 0.4:
            return
        if tab == "resultats":
            changed = (acq.revision != self._res_rev or acq.trim_epoch != self._res_epoch
                       or self._res_dirty or self._res_more)
            if changed:
                self._res_stamp = now
                records = acq.records_snapshot()
                self.measured_indices = {m.index for m in records if m.index is not None}
                self.refresh_results(records)
                self.update_measure_controls()
        elif acq.revision != self._idx_rev:
            self._idx_rev = acq.revision
            self._res_stamp = now
            records = acq.records_snapshot()
            self.measured_indices = {m.index for m in records if m.index is not None}
            if self.show_measures_var.get():
                self.request_draw()
            self.update_measure_controls()

    def refresh_results(self, records=None, force=False):
        acq = self.acq
        if records is None:
            records = acq.records_snapshot()
        rebuild = self._res_dirty or acq.trim_epoch != self._res_epoch or force
        self._res_rev = acq.revision
        self._res_epoch = acq.trim_epoch
        self._res_dirty = False
        self._res_more = False
        self.update_results_table(records, rebuild)
        self.update_results_chart(records)
        if records:
            stats = describe_values([m.value for m in records])
            unit = records[-1].unit
            self.m_summary_var.set(
                f"{stats['n']} mesures   ·   min {value_text(stats['min'])}   max "
                f"{value_text(stats['max'])}   moyenne {value_text(stats['mean'])}   "
                f"σ {value_text(stats['std'])}   {unit}")
        else:
            self.m_summary_var.set("Aucune mesure — elles apparaîtront ici après un programme "
                                   "qui mesure.")

    def update_results_table(self, records, rebuild):
        tree = self.results_tree
        if rebuild:
            tree.delete(*tree.get_children())
            self._res_rendered = max(0, len(records) - MAX_TABLE_ROWS)
            self._res_t0 = records[0].timestamp if records else 0.0
        batch = records[self._res_rendered:self._res_rendered + 600]
        for offset, m in enumerate(batch):
            number = self._res_rendered + offset + 1
            kind = f"Moyenne ×{m.n}" if m.kind == "point" else m.mode.capitalize()
            tree.insert("", "end", values=(
                number, f"{m.timestamp - self._res_t0:.3f}",
                coord_text(m.x), coord_text(m.y), coord_text(m.z),
                f"{value_text(m.value)} {m.unit}", kind),
                tags=("est",) if m.estimated else ())
        self._res_rendered += len(batch)
        if self._res_rendered < len(records) and batch:
            self._res_more = True       # il reste des lignes : prochain passage
        children = tree.get_children()
        if len(children) > MAX_TABLE_ROWS:
            tree.delete(*children[:len(children) - MAX_TABLE_ROWS])
        if batch and not rebuild and tree.selection() == ():
            tree.see(tree.get_children()[-1])

    def update_results_chart(self, records):
        view = self.m_chart_var.get()
        unit = self.acq.unit
        if view == "3d":
            points = [(m.x, m.y, m.z, m.value) for m in records
                      if m.x is not None and m.y is not None and m.z is not None]
            self.scatter.show(points, unit, "Aucune mesure avec coordonnées.")
            return
        if not records:
            self.res_plot.show(empty="Aucune mesure")
            return
        t0 = records[0].timestamp

        def x_of(m):
            if view == "time":
                return m.timestamp - t0
            return getattr(m, view.lower())

        samples = [m for m in records if m.kind == "continu"]
        results = [m for m in records if m.kind == "point"]
        lines, dots = [], []
        if view == "time":
            if samples:
                lines.append(([x_of(m) for m in samples], [m.value for m in samples], C["accent"]))
            dots.append(([x_of(m) for m in results], [m.value for m in results], "#4a5160", 4))
            label = "Temps (s)"
        else:
            dots.append(([x_of(m) for m in samples], [m.value for m in samples], C["accent"], 2.5))
            dots.append(([x_of(m) for m in results], [m.value for m in results], "#4a5160", 4))
            label = f"Position {view} (mm)"
        highlight = None
        selected = self._res_selected
        if selected is not None:
            x = x_of(selected)
            if x is not None:
                highlight = (x, selected.value)
        self.res_plot.show(lines=lines, dots=dots, xlabel=label, unit=unit,
                           empty="Aucune mesure avec cette abscisse.", highlight=highlight)

    # --- Schéma 3D -----------------------------------------------------------
    def measure_overlay_points(self):
        """Mesures à tracer dans le schéma de la machine (≤ 1500, mises en cache)."""
        acq = self.acq
        rev, epoch, stamp, points = self._overlay
        if (rev, epoch) == (acq.revision, acq.trim_epoch) or time.time() - stamp < 0.4:
            return points
        records = [m for m in acq.records_snapshot()
                   if m.x is not None and m.y is not None and m.z is not None]
        stride = max(1, len(records) // 1500)
        points = [(m.x, m.y, m.z, m.value) for m in records[::stride]]
        self._overlay = (acq.revision, acq.trim_epoch, time.time(), points)
        return points

    def shutdown_measure(self):
        self.save_measure_settings()
        self.acq.disconnect()
