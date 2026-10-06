"""Interface principale du contrôleur d'imprimante.

Disposition :

* barre du haut : état de l'imprimante et de la mesure, connexion, STOP et
  ARRÊT D'URGENCE (toujours visibles) ;
* à gauche : la machine (position, télécommande, vitesse, recalibrage) ;
* au centre, trois onglets : Programme · Mesure · Résultats ;
* à droite : vue 3D (la tête s'y déplace en temps réel) et éditeur de point ;
* en bas : console repliable (échanges série + terminal G-code).

Seule la console se redimensionne (barre au-dessus d'elle). Chaque colonne défile si la fenêtre est trop petite.
La partie « mesure » vit dans gui_measure.py.
"""

import math
import os
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import replace
from tkinter import filedialog, messagebox, ttk

import config
from gui_measure import MeasureMixin
from motion import MotionModel, MotionTracker
from printer import Printer, PrinterError, list_ports
from program import (ERROR, FINISHED, PARCOURS, POINTS, STOPPED, Program, ProgramError,
                     ProgramRunner, Waypoint)
from settings import Settings
from theme import C, STATUS_COLORS, colormap
from widgets import (ScrollColumn, Segmented, TabBar, ThinProgress, Tooltip,
                     install_wheel_router)

PROGRAM_FIELDS = ("x", "y", "z", "vitesse", "attente", "mesure_frequence")
NO_POSITION = "—"

MODE_HINTS = {
    POINTS: "Chaque position a sa propre vitesse et sa propre mesure.",
    PARCOURS: "Vitesse et fréquence de mesure globales, modifiables point par point. "
              "Les mesures ne tombent pas forcément sur les points : leurs coordonnées sont "
              "calculées d'après le temps, la fréquence et la vitesse de la machine.",
}
STRIP_HINTS = {
    "aucune": "Le point est atteint sans mesure.",
    "point": "Moyenne de N lectures à la fréquence choisie, une fois le point atteint et "
             "la machine stabilisée (colonne « Attente »).",
    "continu": "Enregistre en continu pendant la durée choisie, une fois arrivé au point.",
    "trajet": "Enregistre en continu pendant le déplacement vers ce point.",
}


def parse_float(text, name="valeur"):
    text = str(text).strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"« {name} » doit être un nombre.") from None


def fmt(value):
    return NO_POSITION if value is None else f"{value:g}"


def format_duration(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {sec:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


def measure_label(wp):
    if wp.mesure == "point":
        return f"Point ×{wp.mesure_n}"
    if wp.mesure == "continu":
        return f"Continu {wp.mesure_duree:g} s · {wp.mesure_frequence:g} Hz"
    if wp.mesure == "trajet":
        return f"Trajet · {wp.mesure_frequence:g} Hz"
    return "Aucune"


def speed_label(wp):
    if wp.vitesse is None:
        return f"{config.DEFAULT_SPEED:g} (défaut)"
    return f"{wp.vitesse:g}"


class EnderGUI(MeasureMixin):
    """Interface Tkinter du contrôleur."""

    LEFT_WIDTH = 350
    RIGHT_WIDTH = 470

    def __init__(self, root, port=None):
        self.root = root
        self.settings = Settings()
        self.root.title("Contrôle imprimante")
        self._place_window()
        self.root.configure(bg=C["bg"])

        # --- État -----------------------------------------------------
        self.connected = False
        self.busy = False
        self.busy_text = ""
        self.running = False
        self.jogging = False
        self.jog_axis = None
        self.jog_direction = 0
        self._jog_stop = threading.Event()
        self._jog_stop_command_sent = False
        self._jog_started = False
        self.events = queue.Queue()
        self.command_history = []
        self.history_index = 0
        self.program_path = None
        self.step_text = ""
        self.current_step = None
        self._last_selected = None
        self._program_edit_entry = None
        self._draw_job = None
        self._grip = None
        self._status_kind = "idle"
        self._live_pos = None
        self._live_end = None
        self._tile_stamp = 0.0
        self._loading_strip = False

        # --- Variables Tk ---------------------------------------------
        self.port_var = tk.StringVar(value=port or self.settings.get("printer", "port") or config.PORT)
        self.speed_var = tk.StringVar(value=str(config.DEFAULT_SPEED))
        self.status_var = tk.StringVar(value="Déconnecté")
        self.program_name_var = tk.StringVar(value="Sans titre")
        self.progress_var = tk.StringVar(value="")
        self.estimate_var = tk.StringVar(value="")
        self.logs_visible = tk.BooleanVar(value=True)
        self.point_vars = {a: tk.StringVar() for a in "XYZ"}
        self.point_step_var = tk.StringVar(value="1")
        self.pos_vars = {a: tk.StringVar(value=NO_POSITION) for a in "XYZ"}
        self.mode_var = tk.StringVar(value=PARCOURS)
        self.par_speed_var = tk.StringVar()
        self.par_freq_var = tk.StringVar()
        self.par_measure_var = tk.BooleanVar(value=True)
        self.strip_mode_var = tk.StringVar(value="aucune")
        self.strip_n_var = tk.StringVar()
        self.strip_dur_var = tk.StringVar()
        self.strip_freq_var = tk.StringVar()
        self.strip_title_var = tk.StringVar(value="MESURE DU POINT")
        self.show_measures_var = tk.BooleanVar(value=False)

        # --- Imprimante / mouvement / programme -----------------------
        self.printer = self.create_printer(self.port_var.get())
        self.printer.on_traffic = self.on_traffic
        self.motion_model = MotionModel()
        saved = self.settings.get("motion")
        if isinstance(saved, dict):
            self.motion_model.update(saved.get("max_speed"), saved.get("max_accel"),
                                     saved.get("print_accel"))
        self.tracker = MotionTracker(self.motion_model)
        self.printer.motion = self.tracker
        self.program = Program()

        self.init_measure()
        self.runner = ProgramRunner(
            self.printer,
            on_step=lambda i, wp: self.post(self.step_started, i, wp),
            on_reached=lambda i, wp, pos: self.post(self.runner_reached, i, wp, pos),
            on_measure=self.measurer.measure,
            on_state=lambda s, m: self.post(self.run_state_changed, s, m),
            on_begin=self.measurer.begin,
            on_end=self.measurer.end,
            before_move=self.measurer.before_move,
            after_move=self.measurer.after_move,
        )

        # --- Caméra 3D ------------------------------------------------
        # X et Y sont volontairement échangés dans le schéma (graphique
        # uniquement : les commandes G-code gardent leurs axes).
        self.reset_camera(redraw=False)
        self.view_drag = None

        self.create_interface()
        self.apply_mode_layout()
        self.refresh_program_tree()
        self.update_controls()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Escape>", lambda _e: self.quick_stop())
        self.root.bind("<Control-s>", lambda _e: self.save_program())
        install_wheel_router(self.root)
        self.poll_events()
        self.animate()
        self.start_measure_polling()

    # ------------------------------------------------------------------
    # Fenêtre
    # ------------------------------------------------------------------
    def _place_window(self):
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w, h = min(1600, sw - 40), min(940, sh - 90)
        self.root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
        self.root.minsize(min(1300, w), min(640, h))
        self._small_screen = h < 800

    # ------------------------------------------------------------------
    # Threads / modèle
    # ------------------------------------------------------------------
    def create_printer(self, port):
        # Connexion réelle uniquement : aucun simulateur de firmware.
        return Printer(port, config.BAUDRATE)

    def post(self, function, *args):
        self.events.put(lambda: function(*args))

    def poll_events(self):
        try:
            while True:
                action = self.events.get_nowait()
                try:
                    action()
                except Exception as exc:
                    self.write_log(f"ERREUR INTERNE : {exc!r}")
        except queue.Empty:
            pass
        self.root.after(40, self.poll_events)

    def run_async(self, function, on_success=None, busy=True, on_error=None, text=""):
        if busy:
            self.busy_text = text
            self.set_busy(True)

        def worker():
            try:
                result = function()
            except Exception as exc:
                self.post(self.async_failed, exc, busy, on_error)
            else:
                self.post(self.async_done, result, on_success, busy)

        threading.Thread(target=worker, daemon=True).start()

    def async_done(self, result, on_success, busy):
        if busy:
            self.busy_text = ""
            self.set_busy(False)
        if on_success:
            on_success(result)

    def async_failed(self, error, busy, on_error=None):
        if busy:
            self.busy_text = ""
            self.set_busy(False)
        message = str(error) or error.__class__.__name__
        self.write_log(f"ERREUR : {message}")
        self.set_status(f"Erreur : {message}", "error")
        if self.connected and not self.printer.is_connected():
            self.mark_disconnected()
        if on_error:
            on_error(message)

    # ------------------------------------------------------------------
    # Polices et styles
    # ------------------------------------------------------------------
    def _setup_fonts(self):
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            tkfont.nametofont(name).configure(size=10)
        family = tkfont.nametofont("TkDefaultFont").actual("family")
        available = set(tkfont.families(self.root))
        mono = next((f for f in ("Cascadia Mono", "Consolas", "SF Mono", "Menlo",
                                 "DejaVu Sans Mono", "Liberation Mono") if f in available),
                    tkfont.nametofont("TkFixedFont").actual("family"))
        self.f = {
            "base": tkfont.Font(family=family, size=10),
            "small": tkfont.Font(family=family, size=9),
            "section": tkfont.Font(family=family, size=8, weight="bold"),
            "bold": tkfont.Font(family=family, size=10, weight="bold"),
            "title": tkfont.Font(family=family, size=12, weight="bold"),
            "name": tkfont.Font(family=family, size=13, weight="bold"),
            "jog": tkfont.Font(family=family, size=11, weight="bold"),
            "mono": tkfont.Font(family=mono, size=9),
            "mono_big": tkfont.Font(family=mono, size=15, weight="bold"),
            "mono_huge": tkfont.Font(family=mono, size=22, weight="bold"),
        }

    def _setup_styles(self):
        s = ttk.Style(self.root)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        F = self.f
        s.configure(".", background=C["surface"], foreground=C["text"], font=F["base"],
                    borderwidth=0, focuscolor=C["surface"], focusthickness=0)

        def button(name, bg, fg, hover, press, dis_bg, dis_fg, pad=(14, 8), font=None):
            s.configure(name, background=bg, foreground=fg, bordercolor=bg, lightcolor=bg,
                        darkcolor=bg, focuscolor=bg, focusthickness=0, relief="flat",
                        borderwidth=0, padding=pad, font=font or F["base"], anchor="center")
            for key in ("background", "bordercolor", "lightcolor", "darkcolor"):
                s.map(name, **{key: [("disabled", dis_bg), ("pressed", press), ("active", hover)]})
            s.map(name, foreground=[("disabled", dis_fg)])

        soft = (C["soft"], C["text"], C["soft_hover"], C["soft_press"], C["soft"], C["faint"])
        button("TButton", *soft)
        button("Soft.TButton", *soft)
        button("Accent.TButton", C["accent"], "white", C["accent_hover"], C["accent_press"],
               "#b9cbf7", "white", pad=(14, 10), font=F["bold"])
        button("Danger.TButton", C["danger"], "white", C["danger_hover"], C["danger_press"],
               "#f1b9b4", "white", pad=(14, 10), font=F["bold"])
        button("DangerSoft.TButton", C["danger_soft"], C["danger"], C["danger_soft_hover"],
               "#f6c4be", C["soft"], C["faint"], pad=(14, 10), font=F["bold"])
        button("Ghost.TButton", C["surface"], C["muted"], C["soft"], C["soft_hover"],
               C["surface"], C["faint"], pad=(10, 6), font=F["small"])
        button("Jog.TButton", C["soft"], C["text"], C["soft_hover"], C["accent_soft"],
               C["soft"], C["faint"], pad=(0, 10), font=F["jog"])
        button("Home.TButton", C["accent_soft"], C["accent"], "#d7e4ff", "#c8daff",
               C["soft"], C["faint"], pad=(0, 10), font=F["bold"])
        button("Tool.TButton", *soft, pad=(8, 7))
        button("Chip.TButton", C["soft"], C["muted"], C["soft_hover"], C["soft_press"],
               C["soft"], C["faint"], pad=(5, 4), font=F["small"])
        button("ChipOn.TButton", C["accent_soft"], C["accent"], C["accent_soft"], C["accent_soft"],
               C["accent_soft"], C["faint"], pad=(5, 4), font=F["bold"])
        button("Mini.TButton", C["soft"], C["text"], C["soft_hover"], C["soft_press"],
               C["soft"], C["faint"], pad=(0, 6), font=F["bold"])
        button("Seg.TButton", C["soft"], C["muted"], C["soft_hover"], C["soft_press"],
               C["soft"], C["faint"], pad=(12, 6), font=F["base"])
        button("SegOn.TButton", C["accent"], "white", C["accent_hover"], C["accent_press"],
               "#b9cbf7", "white", pad=(12, 6), font=F["bold"])
        button("Console.TButton", C["console"], C["console_text"], C["console_line"],
               C["console_line"], C["console"], "#5b616e", pad=(10, 4), font=F["small"])
        button("Send.TButton", C["accent"], "white", C["accent_hover"], C["accent_press"],
               "#2b3a66", "#7f8bb0", pad=(14, 5), font=F["bold"])

        field = dict(fieldbackground=C["surface"], foreground=C["text"], bordercolor=C["line"],
                     lightcolor=C["line"], darkcolor=C["line"], insertcolor=C["text"],
                     padding=(8, 6))
        focus = dict(bordercolor=[("focus", C["accent"])], lightcolor=[("focus", C["accent"])],
                     darkcolor=[("focus", C["accent"])],
                     fieldbackground=[("disabled", C["soft"])], foreground=[("disabled", C["faint"])])
        for name in ("TEntry", "TSpinbox"):
            s.configure(name, **field)
            s.map(name, **focus)
        s.configure("TCombobox", **field, background=C["soft"], arrowcolor=C["muted"],
                    selectbackground=C["surface"], selectforeground=C["text"])
        s.map("TCombobox", **focus, background=[("active", C["soft_hover"])],
              arrowcolor=[("disabled", C["faint"])])
        self.root.option_add("*TCombobox*Listbox.font", F["base"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", C["accent_soft"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", C["text"])

        s.configure("TCheckbutton", background=C["surface"], foreground=C["text"],
                    font=F["base"], focuscolor=C["surface"])
        s.map("TCheckbutton", background=[("active", C["surface"])],
              foreground=[("disabled", C["faint"])])

        s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        s.configure("Treeview", background=C["surface"], fieldbackground=C["surface"],
                    foreground=C["text"], rowheight=30, borderwidth=0, font=F["base"])
        s.map("Treeview", background=[("selected", C["accent_soft"])],
              foreground=[("selected", C["text"])])
        s.configure("Treeview.Heading", background=C["surface"], foreground=C["muted"],
                    font=F["section"], relief="flat", borderwidth=1, bordercolor=C["line"],
                    lightcolor=C["surface"], darkcolor=C["surface"], padding=(6, 8))
        s.map("Treeview.Heading", background=[("active", C["surface"])])

        for name, thumb, trough in (("Thin", C["faint"], C["bg"]),
                                    ("Dark", C["console_line"], C["console"])):
            style = f"{name}.Vertical.TScrollbar"
            s.layout(style, [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
            s.configure(style, background=thumb, troughcolor=trough, bordercolor=trough,
                        lightcolor=thumb, darkcolor=thumb, width=5, gripcount=0)
            s.map(style, background=[("active", C["muted"])])

    # ------------------------------------------------------------------
    # Construction de l'interface
    # ------------------------------------------------------------------
    def create_interface(self):
        self._setup_fonts()
        self._setup_styles()
        self.create_topbar()
        self.create_console()  # empaqueté avant le corps pour garder sa hauteur

        outer = tk.Frame(self.root, bg=C["bg"])
        outer.pack(fill="both", expand=True, padx=12, pady=12)
        outer.grid_rowconfigure(0, weight=1)
        outer.grid_columnconfigure(0, minsize=self.LEFT_WIDTH)
        outer.grid_columnconfigure(1, weight=1)
        outer.grid_columnconfigure(2, minsize=self.RIGHT_WIDTH)
        left = ScrollColumn(outer)
        left.grid(row=0, column=0, sticky="nsew")
        center = tk.Frame(outer, bg=C["bg"])
        center.grid(row=0, column=1, sticky="nsew", padx=12)
        right = ScrollColumn(outer)
        right.grid(row=0, column=2, sticky="nsew")

        # Gauche : machine
        card = self.card(left.body)
        card.pack(fill="x", anchor="n")
        self.create_machine_panel(card)

        # Centre : onglets
        self.tabs = TabBar(center, self.f["bold"], on_change=self.tab_changed)
        self.tabs.pack(fill="both", expand=True)
        self.create_program_tab(self.tabs.add("programme", "Programme"))
        self.create_measure_tab(self.tabs.add("mesure", "Mesure"))
        self.create_results_tab(self.tabs.add("resultats", "Résultats"))

        # Droite : vue 3D + point
        body = right.body
        body.grid_columnconfigure(0, weight=1)
        view_card = self.card(body)
        view_card.grid(row=0, column=0, sticky="ew")
        self.create_view_panel(view_card)
        point_card = self.card(body)
        point_card.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.create_point_panel(point_card)

        if self._small_screen:
            self.set_console_visible(False)

    def tab_changed(self, key):
        self.measure_tab_changed(key)

    # --- Helpers -------------------------------------------------------
    def card(self, parent, **kw):
        return tk.Frame(parent, bg=C["surface"], highlightthickness=1,
                        highlightbackground=C["line"], highlightcolor=C["line"], **kw)

    def section(self, parent, text, pady=(0, 6)):
        label = tk.Label(parent, text=text.upper(), bg=parent.cget("bg"), fg=C["muted"],
                         font=self.f["section"], anchor="w")
        label.pack(fill="x", pady=pady)
        return label

    def tip(self, widget, text):
        Tooltip(widget, text)
        return widget

    # --- Barre du haut -------------------------------------------------
    def create_topbar(self):
        bar = tk.Frame(self.root, bg=C["surface"], height=58)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Frame(self.root, bg=C["line"], height=1).pack(fill="x")

        # Côté droit empaqueté en premier : les arrêts restent toujours visibles.
        self.emergency_button = ttk.Button(bar, text="ARRÊT D'URGENCE", style="Danger.TButton",
                                           command=self.emergency_stop)
        self.emergency_button.pack(side="right", padx=(8, 20))
        self.tip(self.emergency_button,
                 "Envoie M112 et coupe la connexion. Redémarrer l'imprimante avant de se reconnecter.")
        self.stop_button = ttk.Button(bar, text="STOP", style="DangerSoft.TButton",
                                      command=self.quick_stop)
        self.stop_button.pack(side="right")
        self.tip(self.stop_button, "Interrompt le mouvement en cours (M410). Raccourci : Échap.")
        tk.Frame(bar, bg=C["line"], width=1).pack(side="right", fill="y", pady=14, padx=16)

        self.connect_button = ttk.Button(bar, text="Connecter", style="Accent.TButton",
                                         command=self.toggle_connection)
        self.connect_button.pack(side="right")
        self.refresh_button = ttk.Button(bar, text="↻", width=3, style="Soft.TButton",
                                         command=self.refresh_ports)
        self.refresh_button.pack(side="right", padx=(0, 8))
        self.tip(self.refresh_button, "Actualiser la liste des ports série")
        self.port_combo = ttk.Combobox(bar, textvariable=self.port_var, values=list_ports(), width=9)
        self.port_combo.pack(side="right", padx=8)
        tk.Label(bar, text="Imprimante", bg=C["surface"], fg=C["muted"],
                 font=self.f["small"]).pack(side="right")
        self.w_connection = [self.port_combo, self.connect_button, self.refresh_button]

        tk.Label(bar, text="Contrôle imprimante", bg=C["surface"], fg=C["text"],
                 font=self.f["title"]).pack(side="left", padx=(20, 18))
        status = tk.Frame(bar, bg=C["surface"])
        status.pack(side="left")
        self.status_dot = tk.Canvas(status, width=10, height=10, bg=C["surface"],
                                    highlightthickness=0)
        self.status_dot.pack(side="left", padx=(0, 8))
        self._dot = self.status_dot.create_oval(1, 1, 9, 9, fill=C["faint"], outline="")
        self.status_label = tk.Label(status, textvariable=self.status_var, bg=C["surface"],
                                     fg=C["muted"], font=self.f["base"], anchor="w")
        self.status_label.pack(side="left")
        self.build_measure_chip(bar)

    # --- Colonne machine -----------------------------------------------
    def create_machine_panel(self, card):
        pad = tk.Frame(card, bg=C["surface"])
        pad.pack(fill="both", expand=True, padx=16, pady=16)
        self.w_motion = []

        self.section(pad, "Position (mm)")
        tiles = tk.Frame(pad, bg=C["surface"])
        tiles.pack(fill="x", pady=(0, 12))
        axis_colors = {"X": C["axis_x"], "Y": C["axis_y"], "Z": C["axis_z"]}
        self.pos_labels = {}
        for i, axis in enumerate("XYZ"):
            tiles.grid_columnconfigure(i, weight=1, uniform="tile")
            tile = tk.Frame(tiles, bg=C["soft"])
            tile.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0))
            tk.Label(tile, text=axis, bg=C["soft"], fg=axis_colors[axis],
                     font=self.f["section"]).pack(anchor="w", padx=10, pady=(8, 0))
            label = tk.Label(tile, textvariable=self.pos_vars[axis], bg=C["soft"], fg=C["text"],
                             font=self.f["mono_big"], anchor="w")
            label.pack(fill="x", padx=10, pady=(0, 8))
            self.pos_labels[axis] = label

        # Recalibrage obligatoire à la connexion : message tant qu'il n'est pas fait.
        self.homing_slot = tk.Frame(pad, bg=C["surface"])
        self.homing_slot.pack(fill="x")
        self.homing_note = tk.Label(
            self.homing_slot, bg=C["warn_soft"], fg=C["warn"], font=self.f["bold"],
            justify="left", anchor="w", padx=10, pady=8,
            text="Recalibrage requis avant tout déplacement.\nUtilisez « Recalibrer (G28) ».")
        self.homing_slot.bind(
            "<Configure>", lambda e: self.homing_note.configure(wraplength=max(120, e.width - 24)))

        self.section(pad, "Déplacement", pady=(8, 6))
        grid = tk.Frame(pad, bg=C["surface"])
        grid.pack(fill="x")
        for col in (0, 1, 2, 4):
            grid.grid_columnconfigure(col, weight=1, uniform="jog")
        grid.grid_columnconfigure(3, minsize=16)

        def jog_button(text, axis, direction, row, col):
            b = ttk.Button(grid, text=text, style="Jog.TButton")
            b.grid(row=row, column=col, padx=3, pady=3, sticky="nsew")
            b.bind("<ButtonPress-1>", lambda _e: self.start_jog(axis, direction))
            b.bind("<ButtonRelease-1>", lambda _e: self.stop_jog())
            b.bind("<Leave>", lambda _e: self.stop_jog())
            self.w_motion.append(b)
            return b

        jog_button("Y +", "Y", 1, 0, 1)
        jog_button("X −", "X", -1, 1, 0)
        self.origin_button = ttk.Button(grid, text="0,0,0", style="Home.TButton",
                                        command=self.go_origin)
        self.origin_button.grid(row=1, column=1, padx=3, pady=3, sticky="nsew")
        self.tip(self.origin_button, "Aller à X0 Y0 Z0")
        self.w_motion.append(self.origin_button)
        jog_button("X +", "X", 1, 1, 2)
        jog_button("Y −", "Y", -1, 2, 1)
        jog_button("Z +", "Z", 1, 0, 4)
        tk.Label(grid, text="Z", bg=C["surface"], fg=C["faint"],
                 font=self.f["section"]).grid(row=1, column=4)
        jog_button("Z −", "Z", -1, 2, 4)
        tk.Label(pad, text="Maintenir un bouton pour déplacer, relâcher pour arrêter.",
                 bg=C["surface"], fg=C["muted"], font=self.f["small"], anchor="w",
                 justify="left", wraplength=300).pack(fill="x", pady=(6, 14))

        self.section(pad, "Vitesse")
        row = tk.Frame(pad, bg=C["surface"])
        row.pack(fill="x")
        self.speed_spin = ttk.Spinbox(row, from_=1, to=config.MAX_SPEED, increment=50,
                                      textvariable=self.speed_var, width=7)
        self.speed_spin.pack(side="left")
        tk.Label(row, text="mm/min", bg=C["surface"], fg=C["muted"],
                 font=self.f["small"]).pack(side="left", padx=8)
        chips = tk.Frame(pad, bg=C["surface"])
        chips.pack(fill="x", pady=(8, 16))
        self.chips = {}
        for i, value in enumerate(config.SPEED_SIZES):
            chips.grid_columnconfigure(i, weight=1, uniform="chip")
            chip = ttk.Button(chips, text=str(value), style="Chip.TButton",
                              command=lambda v=value: self.speed_var.set(str(v)))
            chip.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 3, 0))
            self.chips[value] = chip
        self.speed_var.trace_add("write", lambda *_: self.sync_speed_chips())
        self.sync_speed_chips()

        self.recalibrate_button = ttk.Button(pad, text="Recalibrer (G28)", style="Soft.TButton",
                                             command=self.recalibrate)
        self.recalibrate_button.pack(fill="x")
        self.tip(self.recalibrate_button,
                 "Recherche des butées mécaniques (G28). Obligatoire après chaque connexion.")
        self.profile_button = ttk.Button(pad, text="Profil de mouvement…", style="Ghost.TButton",
                                         command=self.open_motion_profile)
        self.profile_button.pack(fill="x", pady=(8, 0))
        self.tip(self.profile_button,
                 "Vitesses et accélérations maximales par axe (Z est bridé). Elles servent à "
                 "calculer la position pendant un déplacement.")

    def sync_speed_chips(self):
        try:
            current = float(str(self.speed_var.get()).replace(",", "."))
        except ValueError:
            current = None
        for value, chip in self.chips.items():
            chip.configure(style="ChipOn.TButton" if current == value else "Chip.TButton")

    # --- Vue 3D --------------------------------------------------------
    def create_view_panel(self, card):
        self.view = tk.Canvas(card, bg=C["surface"], highlightthickness=0, height=380, width=300)
        self.view.captures_wheel = True
        self.view.pack(fill="x", padx=1, pady=1)
        self._bind_view(self.view)
        reset = ttk.Button(self.view, text="⟲", width=3, style="Ghost.TButton",
                           command=self.reset_camera)
        reset.place(relx=1.0, x=-8, y=8, anchor="ne")
        self.tip(reset, "Recentrer la vue")
        self.measures_chip = ttk.Button(self.view, text="Mesures", style="Chip.TButton",
                                        command=self.toggle_show_measures)
        self.measures_chip.place(relx=1.0, x=-54, y=10, anchor="ne")
        self.tip(self.measures_chip, "Affiche les mesures dans le schéma, colorées selon leur valeur.")

    def toggle_show_measures(self):
        self.show_measures_var.set(not self.show_measures_var.get())
        self.measures_chip.configure(
            style="ChipOn.TButton" if self.show_measures_var.get() else "Chip.TButton")
        self.request_draw()

    def _bind_view(self, canvas):
        canvas.bind("<Configure>", lambda _e: self.request_draw())
        canvas.bind("<ButtonPress-1>", lambda e: self._view_press(e, "rotate"))
        canvas.bind("<B1-Motion>", self._view_drag)
        canvas.bind("<Shift-ButtonPress-1>", lambda e: self._view_press(e, "pan"))
        canvas.bind("<Shift-B1-Motion>", self._view_drag)
        for button in (2, 3):
            canvas.bind(f"<ButtonPress-{button}>", lambda e: self._view_press(e, "pan"))
            canvas.bind(f"<B{button}-Motion>", self._view_drag)
        canvas.bind("<MouseWheel>", lambda e: self._zoom(1.1 if e.delta > 0 else 0.9))
        canvas.bind("<Button-4>", lambda _e: self._zoom(1.1))
        canvas.bind("<Button-5>", lambda _e: self._zoom(0.9))

    # --- Éditeur de point ----------------------------------------------
    def create_point_panel(self, card):
        pad = tk.Frame(card, bg=C["surface"])
        pad.pack(fill="x", padx=16, pady=14)
        pad.grid_columnconfigure(0, weight=1)

        top = tk.Frame(pad, bg=C["surface"])
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        tk.Label(top, text="POINT", bg=C["surface"], fg=C["muted"],
                 font=self.f["section"]).pack(side="left")
        tk.Label(top, text="position visée (losange orange)", bg=C["surface"], fg=C["faint"],
                 font=self.f["small"]).pack(side="left", padx=10)

        fields = tk.Frame(pad, bg=C["surface"])
        fields.grid(row=1, column=0, sticky="ew")
        for i in range(3):
            fields.grid_columnconfigure(i, weight=1, uniform="pt")
        for i, axis in enumerate("XYZ"):
            group = tk.Frame(fields, bg=C["surface"])
            group.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 10, 0))
            tk.Label(group, text=axis, bg=C["surface"], fg=C["muted"], font=self.f["small"],
                     anchor="w").pack(fill="x")
            line = tk.Frame(group, bg=C["surface"])
            line.pack(fill="x")
            minus = ttk.Button(line, text="−", width=2, style="Mini.TButton",
                               command=lambda a=axis: self.change_point_axis(a, -1))
            minus.pack(side="left")
            entry = ttk.Entry(line, textvariable=self.point_vars[axis], width=5)
            entry.pack(side="left", fill="x", expand=True, padx=3)
            entry.bind("<KeyRelease>", lambda _e: self.request_draw())
            plus = ttk.Button(line, text="+", width=2, style="Mini.TButton",
                              command=lambda a=axis: self.change_point_axis(a, 1))
            plus.pack(side="left")

        actions = tk.Frame(pad, bg=C["surface"])
        actions.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        tk.Label(actions, text="Pas", bg=C["surface"], fg=C["muted"],
                 font=self.f["small"]).pack(side="left")
        ttk.Entry(actions, textvariable=self.point_step_var, width=4).pack(side="left", padx=(6, 2))
        tk.Label(actions, text="mm", bg=C["surface"], fg=C["muted"],
                 font=self.f["small"]).pack(side="left")
        self.goto_button = ttk.Button(actions, text="Aller au point", style="Soft.TButton",
                                      command=self.goto_selected_point)
        self.goto_button.pack(side="right")
        self.capture_button = ttk.Button(actions, text="Position actuelle", style="Soft.TButton",
                                         command=self.capture_point)
        self.capture_button.pack(side="right", padx=(0, 6))
        self.tip(self.capture_button, "Copier la position de la machine dans ce point.")
        self.w_goto = [self.goto_button]

        self.add_point_button = ttk.Button(pad, text="←  Ajouter au programme",
                                           style="Accent.TButton",
                                           command=self.add_selected_to_program)
        self.add_point_button.grid(row=3, column=0, sticky="ew", pady=(10, 0))

    # --- Onglet Programme ----------------------------------------------
    def create_program_tab(self, tab):
        col = ScrollColumn(tab)
        col.pack(fill="both", expand=True)
        card = self.card(col.body)
        card.pack(fill="both", expand=True)
        pad = tk.Frame(card, bg=C["surface"])
        pad.pack(fill="both", expand=True, padx=16, pady=16)
        pad.grid_columnconfigure(0, weight=1)
        pad.grid_rowconfigure(5, weight=1, minsize=170)
        self.w_edit = []
        self._syncing_par = False

        tk.Label(pad, text="PROGRAMME", bg=C["surface"], fg=C["muted"], font=self.f["section"],
                 anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 6))
        name = ttk.Entry(pad, textvariable=self.program_name_var, font=self.f["name"])
        name.grid(row=1, column=0, sticky="ew")
        name.bind("<FocusOut>", lambda _e: self._sync_program_name())
        name.bind("<Return>", lambda _e: (self._sync_program_name(), self.root.focus_set()))
        self.w_edit.append(name)

        files = tk.Frame(pad, bg=C["surface"])
        files.grid(row=2, column=0, sticky="ew", pady=(8, 12))
        for i, (text, command) in enumerate((("Nouveau", self.new_program),
                                             ("Ouvrir", self.load_program),
                                             ("Enregistrer", self.save_program))):
            files.grid_columnconfigure(i, weight=1, uniform="file")
            b = ttk.Button(files, text=text, style="Soft.TButton", command=command)
            b.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0))
            if text == "Enregistrer":
                self.tip(b, "Enregistrer le programme en JSON (Ctrl+S).")
            else:
                self.w_edit.append(b)

        # Façon d'exécuter le programme
        mode_box = tk.Frame(pad, bg=C["surface"])
        mode_box.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self.mode_seg = Segmented(mode_box, [(PARCOURS, "Parcours"), (POINTS, "Point par point")],
                                  self.mode_var, command=self.set_program_mode)
        self.mode_seg.pack(anchor="w")
        self.mode_hint = tk.Label(mode_box, bg=C["surface"], fg=C["muted"], font=self.f["small"],
                                  justify="left", anchor="w", text=MODE_HINTS[POINTS])
        self.mode_hint.pack(fill="x", pady=(6, 0))
        mode_box.bind("<Configure>", lambda e: self.mode_hint.configure(wraplength=max(200, e.width - 4)))

        # Réglages globaux (mode parcours)
        self.parcours_box = tk.Frame(pad, bg=C["surface"])
        self.parcours_box.grid(row=4, column=0, sticky="ew", pady=(0, 10))
        tk.Label(self.parcours_box, text="Vitesse", bg=C["surface"], fg=C["muted"],
                 font=self.f["small"]).grid(row=0, column=0, sticky="w")
        speed_entry = ttk.Entry(self.parcours_box, textvariable=self.par_speed_var, width=7)
        speed_entry.grid(row=0, column=1, padx=(6, 4))
        tk.Label(self.parcours_box, text="mm/min", bg=C["surface"], fg=C["muted"],
                 font=self.f["small"]).grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(self.parcours_box, text="Mesurer", variable=self.par_measure_var,
                        command=self._commit_parcours).grid(row=0, column=3, padx=(18, 8))
        tk.Label(self.parcours_box, text="Fréquence", bg=C["surface"], fg=C["muted"],
                 font=self.f["small"]).grid(row=0, column=4, sticky="w")
        self.par_freq_entry = ttk.Entry(self.parcours_box, textvariable=self.par_freq_var, width=6)
        self.par_freq_entry.grid(row=0, column=5, padx=(6, 4))
        tk.Label(self.parcours_box, text="Hz", bg=C["surface"], fg=C["muted"],
                 font=self.f["small"]).grid(row=0, column=6, sticky="w")
        self.w_edit += [speed_entry, self.par_freq_entry]
        for entry in (speed_entry, self.par_freq_entry):
            entry.bind("<Return>", lambda _e: self._commit_parcours())
            entry.bind("<FocusOut>", lambda _e: self._commit_parcours())
        self.tip(speed_entry, "Vitesse par défaut du programme : valider avec Entrée l'applique à tous "
                              "les points (chaque point peut ensuite être modifié).")
        self.tip(self.par_freq_entry, "Fréquence de mesure par défaut : valider avec Entrée l'applique "
                                      "à tous les points.")

        # Tableau
        table = tk.Frame(pad, bg=C["surface"], highlightthickness=1,
                         highlightbackground=C["line"], highlightcolor=C["line"])
        table.grid(row=5, column=0, sticky="nsew")
        table.grid_columnconfigure(0, weight=1)
        table.grid_rowconfigure(0, weight=1)
        cols = ("n", "x", "y", "z", "speed", "freq", "wait", "mesure")
        self.program_tree = ttk.Treeview(table, columns=cols, show="headings",
                                         selectmode="browse", height=5)
        titles = {"n": "#", "x": "X", "y": "Y", "z": "Z", "speed": "VITESSE",
                  "freq": "FRÉQ. (Hz)", "wait": "ATTENTE", "mesure": "MESURE"}
        widths = {"n": 44, "x": 72, "y": 72, "z": 72, "speed": 112, "freq": 84, "wait": 84, "mesure": 150}
        for col_id in cols:
            anchor = "w" if col_id == "mesure" else "center"
            self.program_tree.heading(col_id, text=titles[col_id], anchor=anchor)
            self.program_tree.column(col_id, width=widths[col_id], minwidth=40, anchor=anchor,
                                     stretch=True)
        scroll = ttk.Scrollbar(table, orient="vertical", style="Thin.Vertical.TScrollbar",
                               command=self.program_tree.yview)

        def on_scroll(first, last):
            scroll.set(first, last)
            if float(first) <= 0.0 and float(last) >= 1.0:
                scroll.grid_remove()
            else:
                scroll.grid(row=0, column=1, sticky="ns")

        self.program_tree.configure(yscrollcommand=on_scroll)
        self.program_tree.grid(row=0, column=0, sticky="nsew")
        self.program_tree.tag_configure("incomplete", foreground=C["faint"])
        self.program_tree.tag_configure("active", background=C["ok_soft"])
        self.program_tree.bind("<<TreeviewSelect>>", self.program_selected)
        self.program_tree.bind("<Double-1>", self.edit_program_cell)
        self.program_tree.bind("<Button-3>", self.program_menu)
        self.program_tree.bind("<Button-2>", self.program_menu)
        self.empty_hint = tk.Label(
            self.program_tree, bg=C["surface"], fg=C["muted"], font=self.f["small"],
            text="Aucun point.\nAjoutez-en depuis le panneau « Point ».", justify="center")

        # Outils de lignes
        tools = tk.Frame(pad, bg=C["surface"])
        tools.grid(row=6, column=0, sticky="ew", pady=(8, 0))
        specs = (("＋ Ligne", self.add_empty_program_point, "Ajouter une ligne vide à remplir ensuite.", 0),
                 ("Remplir", self.fill_selected_program_point,
                  "Remplir la ligne sélectionnée avec le point courant.", 0),
                 ("▲", lambda: self.move_program_point(-1), "Monter la ligne.", 1),
                 ("▼", lambda: self.move_program_point(1), "Descendre la ligne.", 1),
                 ("Supprimer", self.delete_program_point, "Supprimer la ligne sélectionnée.", 0))
        for i, (text, command, hint, small) in enumerate(specs):
            tools.grid_columnconfigure(i, weight=0 if small else 1, uniform=None if small else "tool")
            b = ttk.Button(tools, text=text, style="Tool.TButton", command=command,
                           width=3 if small else None)
            b.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 4, 0))
            self.tip(b, hint)
            self.w_edit.append(b)
        tk.Label(pad, text="Double-clic sur une valeur pour la modifier · vitesse en mm/min · attente en s",
                 bg=C["surface"], fg=C["muted"], font=self.f["small"], anchor="w").grid(
            row=7, column=0, sticky="ew", pady=(6, 0))

        # Mesure du point sélectionné (mode point par point)
        self.strip = tk.Frame(pad, bg=C["soft"])
        self.strip.grid(row=8, column=0, sticky="ew", pady=(12, 0))
        self._build_strip(self.strip)
        self.parcours_info = tk.Label(
            pad, bg=C["soft"], fg=C["muted"], font=self.f["small"], justify="left", anchor="w",
            padx=12, pady=10,
            text="Parcours : l'acquisition tourne pendant tout le programme. Chaque mesure reçoit "
                 "ses coordonnées calculées d'après son instant, la vitesse et les limites par axe "
                 "(Z est plus lent).")
        self.parcours_info.grid(row=8, column=0, sticky="ew", pady=(12, 0))
        self.parcours_info.bind(
            "<Configure>", lambda e: self.parcours_info.configure(wraplength=max(200, e.width - 28)))

        tk.Label(pad, textvariable=self.estimate_var, bg=C["surface"], fg=C["muted"],
                 font=self.f["small"], anchor="w").grid(row=9, column=0, sticky="ew", pady=(10, 0))
        progress = tk.Frame(pad, bg=C["surface"])
        progress.grid(row=10, column=0, sticky="ew", pady=(6, 0))
        tk.Label(progress, textvariable=self.progress_var, bg=C["surface"], fg=C["muted"],
                 font=self.f["small"], anchor="w").pack(fill="x", pady=(0, 4))
        self.progress_bar = ThinProgress(progress)
        self.progress_bar.pack(fill="x")

        run = tk.Frame(pad, bg=C["surface"])
        run.grid(row=11, column=0, sticky="ew", pady=(12, 0))
        self.run_button = ttk.Button(run, text="▶  Lancer le programme", style="Accent.TButton",
                                     command=self.run_program)
        self.run_button.pack(fill="x")
        second = tk.Frame(run, bg=C["surface"])
        second.pack(fill="x", pady=(6, 0))
        second.grid_columnconfigure(0, weight=1, uniform="run")
        second.grid_columnconfigure(1, weight=1, uniform="run")
        self.start_button = ttk.Button(second, text="⇤  Aller au début", style="Soft.TButton",
                                       command=self.go_to_program_start)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.tip(self.start_button, "Déplace la machine à la première position du programme. "
                                    "Ensuite, lancez le programme.")
        self.stop_program_button = ttk.Button(second, text="■  Stopper", style="Soft.TButton",
                                              command=self.stop_program)
        self.stop_program_button.grid(row=0, column=1, sticky="ew", padx=(3, 0))

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Aller à ce point", command=self.goto_program_selected)
        self.menu.add_separator()
        self.menu.add_command(label="Monter", command=lambda: self.move_program_point(-1))
        self.menu.add_command(label="Descendre", command=lambda: self.move_program_point(1))
        self.menu.add_command(label="Supprimer", command=self.delete_program_point)

    # --- Mesure du point (bandeau) --------------------------------------
    def _build_strip(self, strip):
        inner = tk.Frame(strip, bg=C["soft"])
        inner.pack(fill="x", padx=12, pady=10)
        inner.grid_columnconfigure(0, weight=1)
        head = tk.Frame(inner, bg=C["soft"])
        head.grid(row=0, column=0, sticky="ew")
        tk.Label(head, textvariable=self.strip_title_var, bg=C["soft"], fg=C["muted"],
                 font=self.f["section"]).pack(side="left")
        self.strip_all_button = ttk.Button(head, text="Appliquer à tous les points",
                                           style="Ghost.TButton", command=self.strip_apply_all)
        self.strip_all_button.pack(side="right")
        self.strip_seg = Segmented(
            inner, [("aucune", "Aucune"), ("point", "Point"), ("continu", "Continu"),
                    ("trajet", "Trajet")], self.strip_mode_var, command=self.strip_changed,
            bg=C["soft"])
        self.strip_seg.grid(row=1, column=0, sticky="w", pady=(8, 0))

        fields = tk.Frame(inner, bg=C["soft"])
        fields.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.strip_fields = {}
        self.strip_entries = []

        def entry(parent, var, width=6):
            e = ttk.Entry(parent, textvariable=var, width=width)
            e.bind("<Return>", lambda _e: self.strip_apply())
            e.bind("<FocusOut>", lambda _e: self.strip_apply())
            self.strip_entries.append(e)
            return e

        def label(parent, text, col):
            tk.Label(parent, text=text, bg=C["soft"], fg=C["muted"], font=self.f["small"]).grid(
                row=0, column=col, sticky="w", padx=(0 if col == 0 else 14, 6))

        f_point = tk.Frame(fields, bg=C["soft"])
        label(f_point, "Échantillons moyennés", 0)
        entry(f_point, self.strip_n_var).grid(row=0, column=1)
        label(f_point, "Fréquence (Hz)", 2)
        entry(f_point, self.strip_freq_var).grid(row=0, column=3)
        f_continu = tk.Frame(fields, bg=C["soft"])
        label(f_continu, "Durée (s)", 0)
        entry(f_continu, self.strip_dur_var).grid(row=0, column=1)
        label(f_continu, "Fréquence (Hz)", 2)
        entry(f_continu, self.strip_freq_var).grid(row=0, column=3)
        f_trajet = tk.Frame(fields, bg=C["soft"])
        label(f_trajet, "Fréquence (Hz)", 0)
        entry(f_trajet, self.strip_freq_var).grid(row=0, column=1)
        f_none = tk.Frame(fields, bg=C["soft"], height=1)
        for name, frame in (("point", f_point), ("continu", f_continu),
                            ("trajet", f_trajet), ("aucune", f_none)):
            self.strip_fields[name] = frame
        self.strip_hint = tk.Label(inner, bg=C["soft"], fg=C["muted"], font=self.f["small"],
                                   justify="left", anchor="w")
        self.strip_hint.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        inner.bind("<Configure>", lambda e: self.strip_hint.configure(wraplength=max(200, e.width - 4)))

    def _show_strip_fields(self, mode):
        for name, frame in self.strip_fields.items():
            if name == mode:
                frame.grid(row=0, column=0, sticky="w")
            else:
                frame.grid_remove()
        self.strip_hint.configure(text=STRIP_HINTS.get(mode, ""))

    def load_strip(self):
        """Recopie la mesure du point sélectionné dans le bandeau."""
        selection = self.program_tree.selection()
        self._loading_strip = True
        try:
            enabled = bool(selection) and not self.running
            self.strip_seg.set_enabled(enabled)
            self.set_enabled(self.strip_entries + [self.strip_all_button], enabled)
            if not selection:
                self.strip_title_var.set("MESURE DU POINT — sélectionnez un point")
                self.strip_mode_var.set("aucune")
                self.strip_seg.set("aucune")
                self._show_strip_fields("aucune")
                return
            index = self.program_tree.index(selection[0])
            if index >= len(self.program):
                return
            wp = self.program[index]
            self.strip_title_var.set(f"MESURE DU POINT {index + 1}")
            self.strip_seg.set(wp.mesure)
            self.strip_n_var.set(str(wp.mesure_n))
            self.strip_dur_var.set(f"{wp.mesure_duree:g}")
            self.strip_freq_var.set(f"{wp.mesure_frequence:g}")
            self._show_strip_fields(wp.mesure)
        finally:
            self._loading_strip = False

    def _strip_waypoint(self, wp):
        """Waypoint `wp` mis à jour avec les valeurs du bandeau (ValueError si invalide)."""
        mode = self.strip_mode_var.get()
        n = parse_float(self.strip_n_var.get(), "échantillons")
        dur = parse_float(self.strip_dur_var.get(), "durée")
        freq = parse_float(self.strip_freq_var.get(), "fréquence")
        if n is None or n < 1 or n != int(n):
            raise ValueError("Le nombre d'échantillons doit être un entier supérieur ou égal à 1.")
        if dur is None or dur <= 0:
            raise ValueError("La durée doit être strictement positive.")
        if freq is None or not 0 < freq <= config.ACQ_MAX_RATE:
            raise ValueError(f"La fréquence doit être comprise entre 0 et {config.ACQ_MAX_RATE:g} Hz.")
        return replace(wp, mesure=mode, mesure_n=int(n), mesure_duree=dur, mesure_frequence=freq)

    def strip_changed(self, mode):
        self._show_strip_fields(mode)
        self.strip_apply()

    def strip_apply(self):
        if self._loading_strip or self.running:
            return
        selection = self.program_tree.selection()
        if not selection:
            return
        index = self.program_tree.index(selection[0])
        try:
            self.program.update(index, self._strip_waypoint(self.program[index]))
        except (ValueError, ProgramError) as exc:
            messagebox.showwarning("Mesure", str(exc))
            self.load_strip()
            return
        self.refresh_program_tree()

    def strip_apply_all(self):
        if self.running or not len(self.program):
            return
        try:
            template = self._strip_waypoint(Waypoint())
        except (ValueError, ProgramError) as exc:
            messagebox.showwarning("Mesure", str(exc))
            return
        for i, wp in enumerate(self.program):
            self.program.update(i, replace(
                wp, mesure=template.mesure, mesure_n=template.mesure_n,
                mesure_duree=template.mesure_duree, mesure_frequence=template.mesure_frequence))
        self.refresh_program_tree()
        self.write_log(f"Mesure « {template.mesure} » appliquée aux {len(self.program)} points.")

    # --- Mode du programme ---------------------------------------------
    def set_program_mode(self, mode):
        if self.running:
            self.mode_seg.set(self.program.mode)
            return
        self.program.mode = mode
        self.apply_mode_layout()
        self.refresh_program_tree()

    def apply_mode_layout(self):
        parcours = self.program.mode == PARCOURS
        self.mode_seg.set(self.program.mode)
        self.mode_hint.configure(text=MODE_HINTS[self.program.mode])
        self._syncing_par = True
        self.par_speed_var.set(f"{self.program.vitesse:g}")
        self.par_freq_var.set(f"{self.program.frequence:g}")
        self.par_measure_var.set(self.program.parcours_mesure)
        self._syncing_par = False
        if parcours:
            self.parcours_box.grid()
            self.strip.grid_remove()
            self.parcours_info.grid()
            self.program_tree.configure(displaycolumns=("n", "x", "y", "z", "speed", "freq", "wait"))
            self.program_tree.heading("wait", text="PAUSE (s)")
        else:
            self.parcours_box.grid_remove()
            self.parcours_info.grid_remove()
            self.strip.grid()
            self.program_tree.configure(
                displaycolumns=("n", "x", "y", "z", "speed", "wait", "mesure"))
            self.program_tree.heading("wait", text="STABILISATION (s)")
        self.par_freq_entry.configure(
            state="normal" if self.par_measure_var.get() and not self.running else "disabled")
        self.update_estimate()

    def _commit_parcours(self):
        """Valide les réglages globaux : un changement s'applique à tous les points."""
        if self._syncing_par or self.running:
            return
        try:
            speed = parse_float(self.par_speed_var.get(), "vitesse")
            freq = parse_float(self.par_freq_var.get(), "fréquence")
            if speed is None or not 0 < speed <= config.MAX_SPEED:
                raise ValueError(f"La vitesse doit être comprise entre 1 et {config.MAX_SPEED} mm/min.")
            if freq is None or not 0 < freq <= config.ACQ_MAX_RATE:
                raise ValueError(f"La fréquence doit être comprise entre 0 et {config.ACQ_MAX_RATE:g} Hz.")
        except ValueError as exc:
            messagebox.showwarning("Parcours", str(exc))
            self.apply_mode_layout()
            return
        program = self.program
        speed_changed = speed != program.vitesse
        freq_changed = freq != program.frequence
        program.vitesse, program.frequence = speed, freq
        program.parcours_mesure = bool(self.par_measure_var.get())
        if speed_changed or freq_changed:
            for i, wp in enumerate(program):
                changes = {}
                if speed_changed:
                    changes["vitesse"] = speed
                if freq_changed:
                    changes["mesure_frequence"] = freq
                program.update(i, replace(wp, **changes))
        self.par_freq_entry.configure(state="normal" if program.parcours_mesure else "disabled")
        self.refresh_program_tree()

    def speed_text(self, wp):
        if wp.vitesse is not None:
            return f"{wp.vitesse:g}"
        default = self.program.vitesse if self.program.mode == PARCOURS else config.DEFAULT_SPEED
        return f"{default:g} (défaut)"

    def update_estimate(self):
        if not len(self.program):
            self.estimate_var.set("")
            return
        try:
            duration, length = self.program.estimate(self.motion_model, self.printer.last_position)
        except Exception:
            self.estimate_var.set("")
            return
        text = f"Durée estimée ≈ {format_duration(duration)} · {length:.0f} mm de trajet"
        slow = self._slowest_axis_note()
        self.estimate_var.set(text + (f" · {slow}" if slow else ""))

    def _slowest_axis_note(self):
        """Signale les déplacements dont la vitesse est bridée par un axe."""
        limited = set()
        pos = None
        for wp in self.program:
            if wp.x is None and wp.y is None and wp.z is None:
                continue
            target = {"X": wp.x, "Y": wp.y, "Z": wp.z}
            if pos is None:
                pos = {a: (target[a] if target[a] is not None else 0.0) for a in "XYZ"}
                continue
            nxt = {a: (target[a] if target[a] is not None else pos[a]) for a in "XYZ"}
            feed = (wp.vitesse or self.program.vitesse) if self.program.mode == PARCOURS else wp.vitesse
            axis = self.motion_model.limiting_axis(pos, nxt, feed)
            if axis:
                limited.add(axis)
            pos = nxt
        if not limited:
            return ""
        return "vitesse bridée par " + ", ".join(sorted(limited))

    # --- Console -------------------------------------------------------
    def create_console(self):
        self.console = tk.Frame(self.root, bg=C["console"])
        self.console.pack(side="bottom", fill="x")

        grip = tk.Frame(self.console, bg=C["console_line"], height=5, cursor="sb_v_double_arrow")
        grip.pack(fill="x")
        grip.bind("<ButtonPress-1>", self._grip_press)
        grip.bind("<B1-Motion>", self._grip_drag)

        header = tk.Frame(self.console, bg=C["console"])
        header.pack(fill="x", padx=14, pady=(4, 2))
        tk.Label(header, text="Console", bg=C["console"], fg=C["console_text"],
                 font=self.f["bold"]).pack(side="left")
        tk.Label(header, text="échanges avec l'imprimante · glisser la barre du haut pour redimensionner",
                 bg=C["console"], fg="#5b616e", font=self.f["small"]).pack(side="left", padx=12)
        self.console_toggle = ttk.Button(header, text="Masquer", style="Console.TButton",
                                         command=self.toggle_console)
        self.console_toggle.pack(side="right")
        ttk.Button(header, text="Effacer", style="Console.TButton",
                   command=self.clear_log).pack(side="right", padx=4)

        self.console_body = tk.Frame(self.console, bg=C["console"])
        self.console_body.pack(fill="x", padx=14, pady=(2, 12))

        logs = tk.Frame(self.console_body, bg=C["console"])
        logs.pack(fill="x")
        lines = int(self.settings.get("layout", "console_lines", default=4) or 4)
        self.log = tk.Text(logs, height=max(2, min(30, lines)), state="disabled", bg=C["console"],
                           fg=C["console_text"], insertbackground="white", relief="flat",
                           highlightthickness=0, font=self.f["mono"], wrap="word", padx=0, pady=2)
        scroll = ttk.Scrollbar(logs, orient="vertical", style="Dark.Vertical.TScrollbar",
                               command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        for tag, color in (("stamp", "#5b616e"), ("tx", "#7aa2ff"), ("rx", "#9aa1ad"),
                           ("err", "#ff7a70"), ("warn", "#f5b45a"), ("info", C["console_text"])):
            self.log.tag_configure(tag, foreground=color)
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.log.bind(sequence, self._log_wheel)

        terminal = tk.Frame(self.console_body, bg=C["console"])
        terminal.pack(fill="x", pady=(8, 0))
        tk.Label(terminal, text="›", bg=C["console"], fg="#7aa2ff",
                 font=self.f["title"]).pack(side="left", padx=(0, 8))
        self.gcode_entry = tk.Entry(
            terminal, bg=C["console_input"], fg="#f1f3f6", insertbackground="white", relief="flat",
            highlightthickness=1, highlightbackground=C["console_line"],
            highlightcolor=C["accent"], font=self.f["mono"], disabledbackground=C["console"],
            disabledforeground="#5b616e")
        self.gcode_entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.gcode_entry.bind("<Return>", lambda _e: self.send_gcode())
        self.gcode_entry.bind("<Up>", lambda _e: self.history_move(-1))
        self.gcode_entry.bind("<Down>", lambda _e: self.history_move(1))
        self.send_button = ttk.Button(terminal, text="Envoyer", style="Send.TButton",
                                      command=self.send_gcode)
        self.send_button.pack(side="left", padx=(8, 0))

    def set_console_visible(self, visible):
        self.logs_visible.set(visible)
        if visible:
            self.console_body.pack(fill="x", padx=14, pady=(2, 12))
        else:
            self.console_body.pack_forget()
        self.console_toggle.configure(text="Masquer" if visible else "Afficher")

    def toggle_console(self):
        self.set_console_visible(not self.logs_visible.get())

    def _grip_press(self, event):
        self._grip = (event.y_root, int(self.log.cget("height")))

    def _grip_drag(self, event):
        if not self._grip or not self.logs_visible.get():
            return
        y0, h0 = self._grip
        line = max(1, self.f["mono"].metrics("linespace"))
        height = max(2, min(30, h0 + round((y0 - event.y_root) / line)))
        if height != int(self.log.cget("height")):
            self.log.configure(height=height)

    # ------------------------------------------------------------------
    # État / connexion
    # ------------------------------------------------------------------
    def set_enabled(self, widgets, enabled):
        state = "normal" if enabled else "disabled"
        for widget in widgets:
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass

    def set_status(self, text, kind="idle"):
        self._status_kind = kind
        shown = text if len(text) <= 46 else text[:43] + "…"
        self.status_var.set(shown)
        self.status_dot.itemconfigure(self._dot, fill=STATUS_COLORS.get(kind, C["faint"]))
        self.status_label.configure(fg=C["danger"] if kind == "error" else C["muted"])

    def update_controls(self):
        homed = self.connected and self.printer.homed
        idle = self.connected and not self.busy and not self.running
        ready = idle and homed
        has_start = self._first_waypoint() is not None
        self.set_enabled(self.w_motion, ready)
        self.set_enabled(self.w_goto, ready)
        self.set_enabled([self.capture_button], self.connected and not self.running)
        self.set_enabled([self.recalibrate_button], idle)
        self.set_enabled(self.w_connection, not self.busy and not self.running)
        self.set_enabled(self.w_edit, not self.running)
        self.mode_seg.set_enabled(not self.running)
        self.set_enabled([self.add_point_button], not self.running)
        self.set_enabled([self.run_button], ready and len(self.program) > 0)
        self.set_enabled([self.start_button], ready and has_start)
        self.set_enabled([self.stop_program_button], self.running)
        # Les arrêts restent disponibles même pendant une commande, un jog ou un programme.
        self.set_enabled([self.stop_button, self.emergency_button], self.connected)
        self.set_enabled([self.gcode_entry, self.send_button], self.connected)
        self.connect_button.configure(text="Déconnecter" if self.connected else "Connecter",
                                      style="Soft.TButton" if self.connected else "Accent.TButton")
        if self.connected and not homed:
            self.homing_note.pack(fill="x", pady=(0, 10))
        else:
            self.homing_note.pack_forget()
        if self.running:
            self.set_status("Programme en cours" + (f" — {self.step_text}" if self.step_text else ""), "run")
        elif self.busy:
            self.set_status(self.busy_text or "Commande en cours…", "busy")
        elif self.connected and not homed:
            self.set_status("Connecté · recalibrage requis", "busy")
        elif self.connected:
            self.set_status("Connecté", "ok")
        else:
            self.set_status("Déconnecté", "idle")
        if hasattr(self, "strip_seg"):
            self.load_strip()
        self.update_measure_controls()

    def set_busy(self, busy):
        self.busy = busy
        self.update_controls()

    def refresh_ports(self):
        self.port_combo["values"] = list_ports()

    def toggle_connection(self):
        if self.busy or self.running:
            return
        if self.connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        port = self.port_var.get().strip()
        if not port:
            messagebox.showwarning("Connexion", "Indiquez le port série de l'imprimante.")
            return
        self.write_log(f"Connexion à {port}...")
        self.printer.port_name = port
        self.run_async(self.printer.connect, self.connection_success, text="Connexion…",
                       on_error=lambda msg: messagebox.showerror("Erreur de connexion", msg))

    def connection_success(self, _=None):
        self.connected = True
        self.settings.set("printer", "port", self.port_var.get().strip())
        self.write_log("Connexion réussie.")
        self.update_controls()
        self.root.after(150, self.require_homing)

    def require_homing(self):
        """Le recalibrage est imposé à chaque connexion : sans lui, la position
        du firmware est inconnue et les valeurs saisies peuvent dépasser la zone."""
        if not self.connected or self.printer.homed:
            return
        if config.FORCE_HOMING:
            messagebox.showinfo(
                "Recalibrage nécessaire",
                "L'imprimante doit être recalibrée après chaque connexion.\n\n"
                "Dégagez la zone de travail : la tête va chercher les butées (G28).")
            if self.connected and not self.printer.homed:
                self.recalibrate()
        else:
            self.get_position()

    def disconnect(self):
        self.printer.disconnect()
        self.mark_disconnected()
        self.write_log("Connexion fermée.")

    def mark_disconnected(self):
        self.connected = False
        self.printer.last_position = None
        self._live_pos = None
        for var in self.pos_vars.values():
            var.set(NO_POSITION)
        self._tiles_estimated(False)
        self.update_controls()
        self.request_draw()

    # ------------------------------------------------------------------
    # Télécommande continue
    # ------------------------------------------------------------------
    def get_speed(self):
        try:
            speed = int(float(str(self.speed_var.get()).replace(",", ".")))
        except ValueError:
            raise ValueError("La vitesse doit être un nombre.") from None
        if speed < 1 or speed > config.MAX_SPEED:
            raise ValueError(f"La vitesse doit être comprise entre 1 et {config.MAX_SPEED} mm/min.")
        return speed

    def start_jog(self, axis, direction):
        if (not self.connected or self.running or self.busy or self.jogging
                or not self.printer.homed):
            return
        try:
            speed = self.get_speed()
        except ValueError as exc:
            self.write_log(f"ERREUR : {exc}")
            self.set_status(f"Erreur : {exc}", "error")
            return
        self.jogging = True
        self.jog_axis = axis
        self.jog_direction = direction
        self._jog_stop.clear()
        self._jog_stop_command_sent = False
        self._jog_started = False
        self.update_controls()
        threading.Thread(target=self._jog_worker, args=(axis, direction, speed), daemon=True).start()

    def _jog_worker(self, axis, direction, speed):
        # Un seul G1 long est beaucoup plus fluide que l'envoi de petits
        # segments successifs : Marlin peut conserver son accélération et sa
        # vitesse constante. Le relâchement envoie M410 pour couper ce G1.
        try:
            self.printer.jog_start()
            self._jog_started = True
            position = dict(self.printer.last_position) if self.printer.last_position else None
            low, high = config.AXIS_LIMITS.get(axis, (-math.inf, math.inf))
            if position is not None:
                current = position[axis]
                distance = (high - current) if direction > 0 else (low - current)
            else:
                distance = (high - low) * direction
            if direction > 0:
                distance = max(0.0, distance)
            else:
                distance = min(0.0, distance)
            if abs(distance) < 1e-6:
                return
            self.printer.jog_move(axis, distance, speed)
            self._jog_stop.wait()
        except Exception as exc:
            if not self._jog_stop.is_set():
                self.post(self.write_log, f"ERREUR JOG : {exc}")
        finally:
            if self._jog_started and not self._jog_stop_command_sent:
                self._jog_stop.set()
                self._jog_stop_command_sent = True
                try:
                    if self.connected:
                        self.printer.jog_stop()
                except Exception as exc:
                    self.post(self.write_log, f"ERREUR STOP JOG : {exc}")
            self.post(self._jog_finished)

    def _jog_finished(self):
        self.jogging = False
        self.jog_axis = None
        self.jog_direction = 0
        self._jog_started = False
        self.update_controls()
        if self.connected and self.printer.last_position:
            self.show_position(self.printer.last_position)

    def stop_jog(self):
        if not self.jogging or self._jog_stop_command_sent:
            return
        self._jog_stop.set()
        self._jog_stop_command_sent = True
        try:
            if self.connected and self._jog_started:
                self.printer.jog_stop()
        except Exception as exc:
            self.write_log(f"ERREUR STOP JOG : {exc}")

    def go_origin(self):
        """Déplacement absolu vers X0 Y0 Z0."""
        if not self.connected or self.busy or self.running or not self.printer.homed:
            return
        try:
            speed = self.get_speed()
        except ValueError as exc:
            self.write_log(f"ERREUR : {exc}")
            self.set_status(f"Erreur : {exc}", "error")
            return
        self.write_log("Déplacement vers X0 Y0 Z0")
        self.run_async(lambda: self.printer.move_absolute(0, 0, 0, speed),
                       lambda _: self.show_position(self.printer.last_position),
                       text="Déplacement…")

    def recalibrate(self):
        """Référencement mécanique de l'imprimante (G28) puis lecture des limites du firmware."""
        if not self.connected or self.busy or self.running:
            return
        self.write_log("RECALIBRAGE : recherche des butées mécaniques (G28)")

        def work():
            self.printer.home()
            return self.printer.read_motion_settings()

        self.run_async(work, self.homing_done, text="Recalibrage en cours…")

    def homing_done(self, limits):
        self.show_position(self.printer.last_position)
        self.apply_firmware_limits(limits)
        self.write_log("Recalibrage terminé.")

    def apply_firmware_limits(self, limits):
        if limits:
            self.motion_model.update(limits.get("max_speed"), limits.get("max_accel"),
                                     limits.get("print_accel"))
            self.settings.set("motion", self.motion_model.to_dict())
            speeds = self.motion_model.max_speed
            self.write_log("Limites du firmware lues : vitesse max "
                           + ", ".join(f"{a} {speeds[a]:g}" for a in "XYZ") + " mm/s")
        else:
            self.write_log("Limites du firmware indisponibles (M503) : profil de mouvement conservé.")
        self.update_estimate()

    def _tiles_estimated(self, estimated):
        color = C["muted"] if estimated else C["text"]
        for label in self.pos_labels.values():
            label.configure(fg=color)

    def show_position(self, position):
        if not position:
            return
        self._live_pos = None
        self._live_end = None
        self._tiles_estimated(False)
        for axis in "XYZ":
            self.pos_vars[axis].set(f"{position[axis]:g}")
        self.request_draw()

    def get_position(self):
        if not self.connected:
            return
        self.run_async(self.printer.get_position, self.show_position, busy=False)

    def animate(self):
        """Fait « bouger » la tête dans le schéma pendant un déplacement.

        La position affichée est CALCULÉE (modèle de mouvement). Quand le
        mouvement s'arrête (fin normale ou STOP), on garde la dernière
        position calculée jusqu'à l'arrivée de la vraie (M114) : aucun retour
        en arrière dans le schéma.
        """
        delay = 120
        try:
            now = time.time()
            if self.tracker.moving:
                pos, _moving = self.tracker.position_at(now)
                if pos:
                    self._live_pos = pos
                    self._live_end = None
                    if now - self._tile_stamp > 0.08:
                        self._tile_stamp = now
                        for axis in "XYZ":
                            self.pos_vars[axis].set(f"{pos[axis]:.1f}")
                        self._tiles_estimated(True)
                    self.request_draw()
                delay = 33
            elif self._live_pos is not None:
                if self._live_end is None:
                    self._live_end = now
                pos, _moving = self.tracker.position_at(now)
                if pos:
                    self._live_pos = pos
                if now - self._live_end > 4.0:
                    # La vraie position n'est pas arrivée : on se rabat dessus.
                    self._live_end = None
                    self._live_pos = None
                    self.show_position(self.printer.last_position)
                self.request_draw()
                delay = 60
        except Exception as exc:
            self.write_log(f"ERREUR INTERNE : {exc!r}")
        self.root.after(delay, self.animate)

    # ------------------------------------------------------------------
    # Profil de mouvement
    # ------------------------------------------------------------------
    def open_motion_profile(self):
        win = tk.Toplevel(self.root)
        win.title("Profil de mouvement")
        win.configure(bg=C["surface"])
        win.transient(self.root)
        win.resizable(False, False)
        pad = tk.Frame(win, bg=C["surface"])
        pad.pack(padx=20, pady=18)
        tk.Label(pad, text="Vitesses et accélérations maximales de la machine", bg=C["surface"],
                 fg=C["text"], font=self.f["bold"]).grid(row=0, column=0, columnspan=4, sticky="w")
        tk.Label(pad, bg=C["surface"], fg=C["muted"], font=self.f["small"], justify="left",
                 wraplength=420,
                 text="Elles servent à calculer où se trouve la tête pendant un déplacement (schéma "
                      "en temps réel, coordonnées des mesures). Un mouvement qui comporte du Z est "
                      "ralenti par l'axe le plus lent. Elles sont lues sur l'imprimante (M503) à "
                      "chaque recalibrage.").grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 12))
        model = self.motion_model
        speed_vars = {a: tk.StringVar(value=f"{model.max_speed[a]:g}") for a in "XYZ"}
        accel_vars = {a: tk.StringVar(value=f"{model.max_accel[a]:g}") for a in "XYZ"}
        print_var = tk.StringVar(value=f"{model.print_accel:g}")
        for i, axis in enumerate("XYZ"):
            tk.Label(pad, text=axis, bg=C["surface"], fg=C["muted"],
                     font=self.f["section"]).grid(row=2, column=i + 1)
        tk.Label(pad, text="Vitesse max (mm/s)", bg=C["surface"], fg=C["text"]).grid(
            row=3, column=0, sticky="w", pady=4)
        tk.Label(pad, text="Accélération max (mm/s²)", bg=C["surface"], fg=C["text"]).grid(
            row=4, column=0, sticky="w", pady=4)
        for i, axis in enumerate("XYZ"):
            ttk.Entry(pad, textvariable=speed_vars[axis], width=8).grid(row=3, column=i + 1, padx=4)
            ttk.Entry(pad, textvariable=accel_vars[axis], width=8).grid(row=4, column=i + 1, padx=4)
        tk.Label(pad, text="Accélération de déplacement (mm/s²)", bg=C["surface"],
                 fg=C["text"]).grid(row=5, column=0, sticky="w", pady=4)
        ttk.Entry(pad, textvariable=print_var, width=8).grid(row=5, column=1, padx=4)

        def fill(limits):
            for a in "XYZ":
                if a in limits.get("max_speed", {}):
                    speed_vars[a].set(f"{limits['max_speed'][a]:g}")
                if a in limits.get("max_accel", {}):
                    accel_vars[a].set(f"{limits['max_accel'][a]:g}")
            if limits.get("print_accel"):
                print_var.set(f"{limits['print_accel']:g}")

        def read_firmware():
            if not self.connected or self.busy or self.running:
                messagebox.showinfo("Profil de mouvement",
                                    "Connectez l'imprimante (et attendez la fin des commandes).",
                                    parent=win)
                return

            def done(limits):
                if limits:
                    fill(limits)
                else:
                    messagebox.showinfo("Profil de mouvement",
                                        "Le firmware ne fournit pas ses limites (M503).", parent=win)

            self.run_async(self.printer.read_motion_settings, done, text="Lecture du firmware…")

        def defaults():
            fill({"max_speed": config.MAX_AXIS_SPEED, "max_accel": config.MAX_AXIS_ACCEL,
                  "print_accel": config.PRINT_ACCEL})

        def apply():
            try:
                speed = {a: float(speed_vars[a].get().replace(",", ".")) for a in "XYZ"}
                accel = {a: float(accel_vars[a].get().replace(",", ".")) for a in "XYZ"}
                p = float(print_var.get().replace(",", "."))
                if min(list(speed.values()) + list(accel.values()) + [p]) <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning("Profil de mouvement",
                                       "Toutes les valeurs doivent être des nombres positifs.",
                                       parent=win)
                return
            self.motion_model.update(speed, accel, p)
            self.settings.set("motion", self.motion_model.to_dict())
            self.update_estimate()
            win.destroy()

        buttons = tk.Frame(pad, bg=C["surface"])
        buttons.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(16, 0))
        ttk.Button(buttons, text="Lire le firmware", style="Soft.TButton",
                   command=read_firmware).pack(side="left")
        ttk.Button(buttons, text="Valeurs Ender-3", style="Soft.TButton",
                   command=defaults).pack(side="left", padx=6)
        ttk.Button(buttons, text="Appliquer", style="Accent.TButton",
                   command=apply).pack(side="right")

    # ------------------------------------------------------------------
    # Terminal / arrêts
    # ------------------------------------------------------------------
    def send_gcode(self):
        if not self.connected or self.busy or self.running:
            return
        command = self.gcode_entry.get().strip()
        if not command:
            return
        self.command_history.append(command)
        self.history_index = len(self.command_history)
        self.gcode_entry.delete(0, "end")
        self.printer.last_position = None
        words = command.upper().split()
        is_home = bool(words) and words[0] == "G28" and len(words) == 1

        def done(_):
            if is_home:
                self.printer.homed = True
            self.get_position()
            self.update_controls()

        self.run_async(lambda: self.printer.send_command(command), done)

    def history_move(self, step):
        if not self.command_history:
            return
        self.history_index = max(0, min(len(self.command_history), self.history_index + step))
        self.gcode_entry.delete(0, "end")
        if self.history_index < len(self.command_history):
            self.gcode_entry.insert(0, self.command_history[self.history_index])

    def quick_stop(self):
        if not self.connected:
            return
        if self.jogging:
            self.stop_jog()
            self.write_log("STOP : jog interrompu (M410).")
            return
        if self.running:
            self.stop_program()
            self.write_log("STOP : programme interrompu (M410).")
            return

        def worker():
            try:
                position = self.printer.quick_stop()
                self.post(self.write_log, "STOP : mouvements interrompus (M410).")
                if position:
                    self.post(self.show_position, position)
            except Exception as exc:
                self.post(self.write_log, f"ERREUR STOP : {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def emergency_stop(self):
        # M112 doit partir immédiatement, sans boîte de dialogue ni attente.
        if not self.connected:
            return
        self._jog_stop.set()
        self.measurer.abort()
        self.runner.cancel()
        try:
            self.printer.emergency_stop()
        except Exception as exc:
            self.write_log(f"ERREUR ARRÊT D'URGENCE : {exc}")
        try:
            self.printer.disconnect()
        finally:
            self.mark_disconnected()
            self.running = False
            self.update_controls()
        self.write_log("ARRÊT D'URGENCE exécuté. Redémarrer l'imprimante avant reconnexion.")

    # ------------------------------------------------------------------
    # Point
    # ------------------------------------------------------------------
    def _read_point(self):
        """Retourne (x, y, z) du point édité, ou lève ValueError."""
        values = [parse_float(self.point_vars[a].get(), a) for a in "XYZ"]
        if any(v is None for v in values):
            raise ValueError("Les trois coordonnées sont obligatoires.")
        return tuple(values)

    def change_point_axis(self, axis, direction):
        """Incrémente une coordonnée du point avec le pas choisi."""
        try:
            step = parse_float(self.point_step_var.get(), "pas")
            if step is None or step <= 0:
                raise ValueError("Le pas doit être strictement positif.")
            value = parse_float(self.point_vars[axis].get(), axis)
            if value is None:
                value = 0.0
            self.point_vars[axis].set(f"{value + direction * step:g}")
            self.request_draw()
        except ValueError as exc:
            messagebox.showwarning("Point", str(exc))

    def capture_point(self):
        if not self.connected or not self.printer.last_position:
            messagebox.showinfo("Point", "Aucune position machine disponible.")
            return
        p = self.printer.last_position
        for axis in "XYZ":
            self.point_vars[axis].set(f"{p[axis]:g}")
        self.request_draw()

    def goto_selected_point(self):
        if not self.connected or self.busy or self.running or not self.printer.homed:
            return
        try:
            x, y, z = self._read_point()
            speed = self.get_speed()
            self.printer.check_target({"X": x, "Y": y, "Z": z})
        except (ValueError, PrinterError) as exc:
            messagebox.showwarning("Point", str(exc))
            return
        self.run_async(lambda: self.printer.move_absolute(x, y, z, speed),
                       lambda _: self.show_position(self.printer.last_position),
                       text="Déplacement…")

    def add_selected_to_program(self):
        if self.running:
            return
        try:
            x, y, z = self._read_point()
            if self.program.mode == PARCOURS:
                wp = Waypoint(x=x, y=y, z=z, vitesse=self.program.vitesse,
                              mesure_frequence=self.program.frequence)
            else:
                wp = Waypoint(x=x, y=y, z=z, vitesse=self.get_speed())
            if self.program.mode == POINTS and len(self.program):
                # Une série de points partage presque toujours la même mesure.
                last = self.program[len(self.program) - 1]
                wp = replace(wp, mesure=last.mesure, mesure_n=last.mesure_n,
                             mesure_duree=last.mesure_duree,
                             mesure_frequence=last.mesure_frequence)
        except (ValueError, ProgramError) as exc:
            messagebox.showwarning("Programme", str(exc))
            return
        self.program.add(wp)
        self.refresh_program_tree()
        self.select_program_row(len(self.program) - 1)

    # ------------------------------------------------------------------
    # Vue 3D
    # ------------------------------------------------------------------
    def reset_camera(self, redraw=True):
        self.view_yaw = 0.68 + math.pi
        self.view_pitch = 0.52
        self.view_zoom = 1.0
        self.view_pan_x = 0.0
        self.view_pan_y = 0.0
        if redraw:
            self.request_draw()

    def _view_press(self, event, mode):
        self.view_drag = (event.x, event.y, self.view_yaw, self.view_pitch,
                          self.view_pan_x, self.view_pan_y, mode)

    def _view_drag(self, event):
        if not self.view_drag:
            return
        x0, y0, yaw, pitch, panx, pany, mode = self.view_drag
        if mode == "rotate":
            self.view_yaw = yaw - (event.x - x0) * 0.012
            self.view_pitch = max(-1.35, min(1.35, pitch + (event.y - y0) * 0.012))
        else:
            self.view_pan_x = panx + (event.x - x0)
            self.view_pan_y = pany + (event.y - y0)
        self.request_draw()

    def _zoom(self, factor):
        self.view_zoom = max(0.25, min(5.0, self.view_zoom * factor))
        self.request_draw()

    @staticmethod
    def _bounds():
        lim = config.AXIS_LIMITS
        return (*lim["X"], *lim["Y"], *lim["Z"])

    def _project(self, x, y, z, width, height, bounds):
        xmin, xmax, ymin, ymax, zmin, zmax = bounds
        x -= (xmin + xmax) / 2
        y -= (ymin + ymax) / 2
        z -= (zmin + zmax) / 2
        cyaw, syaw = math.cos(self.view_yaw), math.sin(self.view_yaw)
        cp, sp = math.cos(self.view_pitch), math.sin(self.view_pitch)
        # Représentation graphique : X et Y sont échangés volontairement.
        graph_x, graph_y = y, x
        screen_x = graph_x * cyaw - graph_y * syaw
        depth = graph_x * syaw + graph_y * cyaw
        screen_up = z * cp - depth * sp
        radius = 0.5 * math.sqrt((xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2)
        scale = (min(width, height) / 2 - 30) / radius * 1.12 * self.view_zoom
        return (width / 2 + screen_x * scale + self.view_pan_x,
                height / 2 - screen_up * scale + self.view_pan_y)

    def request_draw(self):
        if self._draw_job is None:
            self._draw_job = self.root.after(15, self.draw_scene)

    @staticmethod
    def _label(c, x, y, text, anchor, fill, font):
        """Texte avec halo blanc pour rester lisible par-dessus le schéma."""
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)):
            c.create_text(x + dx, y + dy, text=text, anchor=anchor, fill=C["surface"], font=font)
        c.create_text(x, y, text=text, anchor=anchor, fill=fill, font=font)

    def _marker(self, c, P, p, floor_z, color, kind="ring", label=None, label_color=None,
                below=False):
        x, y, z = p
        top = P(x, y, z)
        c.create_line(*top, *P(x, y, floor_z), fill=C["faint"], dash=(2, 3))
        if kind == "ring":
            c.create_oval(top[0] - 8, top[1] - 8, top[0] + 8, top[1] + 8, outline=color, width=2)
            c.create_oval(top[0] - 3, top[1] - 3, top[0] + 3, top[1] + 3, fill=color, outline="")
        else:  # diamond
            c.create_polygon(top[0], top[1] - 7, top[0] + 7, top[1], top[0], top[1] + 7,
                             top[0] - 7, top[1], fill=color, outline="white", width=1)
        if label:
            if below:
                self._label(c, top[0] + 12, top[1] + 8, label, "nw", label_color or color,
                            self.f["small"])
            else:
                self._label(c, top[0] + 12, top[1] - 8, label, "sw", label_color or color,
                            self.f["small"])

    def machine_position(self):
        """Position affichée : calculée pendant un mouvement, lue sinon."""
        return self._live_pos or self.printer.last_position

    def draw_scene(self):
        self._draw_job = None
        c = self.view
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 80 or h < 80:
            return
        b = self._bounds()
        xmin, xmax, ymin, ymax, zmin, zmax = b

        def P(x, y, z):
            return self._project(x, y, z, w, h, b)

        # Sol (grille)
        n = 4
        for i in range(n + 1):
            t = i / n
            y = ymin + (ymax - ymin) * t
            x = xmin + (xmax - xmin) * t
            c.create_line(*P(xmin, y, zmin), *P(xmax, y, zmin), fill=C["grid"])
            c.create_line(*P(x, ymin, zmin), *P(x, ymax, zmin), fill=C["grid"])

        # Volume de travail
        corners = {(i, j, k): P(xmax if i else xmin, ymax if j else ymin, zmax if k else zmin)
                   for i in (0, 1) for j in (0, 1) for k in (0, 1)}
        for (i, j, k), p in corners.items():
            for di, dj, dk in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
                q = (i + di, j + dj, k + dk)
                if q in corners:
                    color = C["edge"] if k == 0 and dk == 0 else C["grid"]
                    c.create_line(*p, *corners[q], fill=color, width=1)

        # Axes à l'origine
        origin = P(xmin, ymin, zmin)
        for end, label, color in (((xmin + (xmax - xmin) * 0.25, ymin, zmin), "X", C["axis_x"]),
                                  ((xmin, ymin + (ymax - ymin) * 0.25, zmin), "Y", C["axis_y"]),
                                  ((xmin, ymin, zmin + (zmax - zmin) * 0.25), "Z", C["axis_z"])):
            p = P(*end)
            c.create_line(*origin, *p, fill=color, width=2, arrow=tk.LAST)
            self._label(c, p[0] + 8, p[1], label, "w", color, self.f["bold"])

        # Trajectoire du programme
        selection = self.program_tree.selection()
        selected = self.program_tree.index(selection[0]) if selection else None
        pts = []
        for i, wp in enumerate(self.program):
            if wp.x is None or wp.y is None or wp.z is None:
                continue
            pts.append((i, P(wp.x, wp.y, wp.z)))
        for a, b2 in zip(pts, pts[1:]):
            active = self.running and self.current_step is not None and b2[0] == self.current_step
            c.create_line(*a[1], *b2[1], fill=C["ok"] if active else C["path"],
                          width=3 if active else 2, capstyle="round")
        done = getattr(self, "measured_indices", set())
        for i, xy in pts:
            if self.running and i == self.current_step:
                c.create_oval(xy[0] - 10, xy[1] - 10, xy[0] + 10, xy[1] + 10,
                              outline=C["ok"], width=2)
            if i == selected:
                c.create_oval(xy[0] - 7, xy[1] - 7, xy[0] + 7, xy[1] + 7,
                              fill=C["accent"], outline="white", width=2)
                color = C["accent"]
            else:
                c.create_oval(xy[0] - 4, xy[1] - 4, xy[0] + 4, xy[1] + 4,
                              fill=C["ok"] if i in done else "#4a5160", outline="white")
                color = C["muted"]
            self._label(c, xy[0] - 8, xy[1] - 7, str(i + 1), "se", color,
                        self.f["bold"] if i == selected else self.f["small"])

        # Mesures colorées selon leur valeur
        if self.show_measures_var.get():
            overlay = self.measure_overlay_points()
            if overlay:
                values = [p[3] for p in overlay]
                vmin, vmax = min(values), max(values)
                span = vmax - vmin if vmax - vmin > 1e-12 else 1.0
                for x, y, z, v in overlay:
                    sx, sy = P(x, y, z)
                    c.create_oval(sx - 3, sy - 3, sx + 3, sy + 3,
                                  fill=colormap((v - vmin) / span), outline="")

        try:
            x, y, z = self._read_point()
            self._marker(c, P, (x, y, z), zmin, C["warn"], "diamond",
                         f"({x:g}, {y:g}, {z:g})", C["text"])
        except ValueError:
            pass

        position = self.machine_position()
        if position:
            self._marker(c, P, (position["X"], position["Y"], position["Z"]), zmin, C["accent"],
                         "ring", "Machine", below=True)

        # Légende + aide
        lx, ly = 16, 18
        for text, color in (("Machine", C["accent"]), ("Point", C["warn"]),
                            ("Programme", "#4a5160")):
            c.create_oval(lx, ly - 4, lx + 8, ly + 4, fill=color, outline="")
            item = c.create_text(lx + 14, ly, text=text, anchor="w", fill=C["muted"],
                                 font=self.f["small"])
            lx = c.bbox(item)[2] + 14
        help_text = ("Glisser : pivoter    Maj + glisser : déplacer    Molette : zoom"
                     if w >= 440 else "Glisser · Maj+glisser · Molette")
        c.create_text(16, h - 14, anchor="w", fill=C["faint"], font=self.f["small"], text=help_text)

    # ------------------------------------------------------------------
    # Programme : tableau
    # ------------------------------------------------------------------
    def _sync_program_name(self):
        self.program.name = self.program_name_var.get().strip() or "Sans titre"

    def _row_tags(self, wp, active=False):
        tags = []
        if wp.x is None or wp.y is None or wp.z is None:
            tags.append("incomplete")
        if active:
            tags.append("active")
        return tuple(tags)

    def select_program_row(self, index, copy=False):
        """Sélectionne une ligne. `copy` recopie ses valeurs dans l'éditeur de point."""
        items = self.program_tree.get_children()
        if not items:
            return
        index = max(0, min(index, len(items) - 1))
        self._last_selected = None if copy else index
        self.program_tree.selection_set(items[index])
        self.program_tree.see(items[index])

    def refresh_program_tree(self):
        if not hasattr(self, "program_tree"):
            return
        selection = self.program_tree.selection()
        previous = self.program_tree.index(selection[0]) if selection else None
        self._sync_program_name()
        self.program_tree.delete(*self.program_tree.get_children())
        for i, wp in enumerate(self.program, start=1):
            self.program_tree.insert(
                "", "end", tags=self._row_tags(wp, self.running and i - 1 == self.current_step),
                values=(i, fmt(wp.x), fmt(wp.y), fmt(wp.z), self.speed_text(wp), f"{wp.mesure_frequence:g}", fmt(wp.attente),
                        measure_label(wp)))
        if len(self.program):
            self.empty_hint.place_forget()
            if previous is not None:
                self.select_program_row(previous)
        else:
            self._last_selected = None
            self.empty_hint.place(relx=0.5, rely=0.42, anchor="center")
        self.update_estimate()
        self.request_draw()
        self.update_controls()

    def program_selected(self, _event=None):
        selection = self.program_tree.selection()
        if selection:
            index = self.program_tree.index(selection[0])
            if index != self._last_selected and index < len(self.program):
                self._last_selected = index
                wp = self.program[index]
                for axis, value in (("X", wp.x), ("Y", wp.y), ("Z", wp.z)):
                    self.point_vars[axis].set("" if value is None else f"{value:g}")
        else:
            self._last_selected = None
        self.load_strip()
        self.request_draw()

    def program_menu(self, event):
        item = self.program_tree.identify_row(event.y)
        if not item:
            return
        self.program_tree.selection_set(item)
        idle = self.connected and not self.busy and not self.running and self.printer.homed
        self.menu.entryconfigure(0, state="normal" if idle else "disabled")
        for index in (2, 3, 4):
            self.menu.entryconfigure(index, state="disabled" if self.running else "normal")
        self.menu.tk_popup(event.x_root, event.y_root)

    def edit_program_cell(self, event):
        """Ouvre un petit champ d'édition directement dans une cellule."""
        if self.running:
            return
        region = self.program_tree.identify_region(event.x, event.y)
        column_id = self.program_tree.identify_column(event.x)
        item = self.program_tree.identify_row(event.y)
        if region != "cell" or not item or column_id == "#1":
            return
        display = self.program_tree.cget("displaycolumns")
        if isinstance(display, str):
            display = (display,)
        if tuple(display) == ("#all",):
            display = self.program_tree.cget("columns")
        try:
            col_name = display[int(column_id[1:]) - 1]
        except (IndexError, ValueError):
            return
        if isinstance(col_name, int):
            col_name = self.program_tree.cget("columns")[col_name]
        field = {"x": "x", "y": "y", "z": "z", "speed": "vitesse", "freq": "mesure_frequence",
                 "wait": "attente"}.get(str(col_name))
        if field is None:
            return          # colonne « Mesure » : se règle dans le bandeau sous le tableau
        bbox = self.program_tree.bbox(item, column_id)
        if not bbox:
            return

        self.finish_program_cell_edit(save=True)
        wp = self.program[self.program_tree.index(item)]
        value = getattr(wp, field)
        variable = tk.StringVar(value="" if value is None else f"{value:g}")
        editor = ttk.Entry(self.program_tree, textvariable=variable, justify="right")
        editor.place(x=bbox[0], y=bbox[1], width=bbox[2], height=bbox[3])
        self._program_edit_entry = editor
        editor.focus_set()
        editor.select_range(0, "end")
        editor.bind("<Return>", lambda _e: self.finish_program_cell_edit(True, item, field, variable))
        editor.bind("<Escape>", lambda _e: self.finish_program_cell_edit(False))
        editor.bind("<FocusOut>", lambda _e: self.finish_program_cell_edit(True, item, field, variable))

    def update_program_cell_value(self, index, field, text):
        """Modifie une cellule du programme et reconstruit le waypoint."""
        if field not in PROGRAM_FIELDS:
            raise ValueError(f"Colonne non modifiable : {field}")
        parsed = parse_float(str(text).strip().replace(NO_POSITION, ""), field)
        if field == "attente" and parsed is None:
            parsed = 0.0
        if field == "mesure_frequence" and (parsed is None or not 0 < parsed <= config.ACQ_MAX_RATE):
            raise ValueError(f"La fréquence doit être comprise entre 0 et {config.ACQ_MAX_RATE:g} Hz.")
        self.program.update(index, replace(self.program[index], **{field: parsed}))

    def finish_program_cell_edit(self, save=True, item=None, field=None, variable=None):
        editor = self._program_edit_entry
        if editor is None:
            return
        self._program_edit_entry = None
        try:
            editor.destroy()
        except tk.TclError:
            pass
        if not save or not item or not field or variable is None:
            return
        try:
            index = self.program_tree.index(item)
            self.update_program_cell_value(index, field, variable.get())
            self.refresh_program_tree()
            self.select_program_row(index, copy=True)
        except (ValueError, ProgramError, tk.TclError) as exc:
            messagebox.showwarning("Programme", str(exc))

    def _first_waypoint(self):
        """(index, waypoint) de la première position qui comporte une coordonnée."""
        for i, wp in enumerate(self.program):
            if wp.x is not None or wp.y is not None or wp.z is not None:
                return i, wp
        return None

    def _move_to_waypoint(self, wp, label):
        try:
            if self.program.mode == PARCOURS:
                speed = wp.vitesse or self.program.vitesse
            else:
                speed = wp.vitesse or self.get_speed()
            self.printer.check_target({"X": wp.x, "Y": wp.y, "Z": wp.z})
        except (ValueError, PrinterError) as exc:
            messagebox.showwarning("Programme", str(exc))
            return
        self.write_log(label)
        self.run_async(lambda: self.printer.move_absolute(wp.x, wp.y, wp.z, speed),
                       lambda _: self.show_position(self.printer.last_position),
                       text="Déplacement…")

    def goto_program_selected(self):
        selection = self.program_tree.selection()
        if (not selection or not self.connected or self.busy or self.running
                or not self.printer.homed):
            return
        index = self.program_tree.index(selection[0])
        wp = self.program[index]
        if wp.x is None and wp.y is None and wp.z is None:
            messagebox.showwarning("Programme", "Cette ligne est vide.")
            return
        self._move_to_waypoint(wp, f"Déplacement à la position {index + 1}")

    def go_to_program_start(self):
        """Va à la première position du programme (sans le lancer)."""
        if (not self.connected or self.busy or self.running or not self.printer.homed):
            return
        first = self._first_waypoint()
        if first is None:
            messagebox.showwarning("Programme", "Le programme ne contient aucune position.")
            return
        index, wp = first
        self._move_to_waypoint(wp, f"Déplacement au début du programme (position {index + 1})")

    def add_empty_program_point(self):
        """Ajoute une ligne vide qui pourra être remplie ultérieurement."""
        if self.running:
            return
        wp = Waypoint()
        if self.program.mode == PARCOURS:
            wp = Waypoint(vitesse=self.program.vitesse, mesure_frequence=self.program.frequence)
        elif len(self.program):
            last = self.program[len(self.program) - 1]
            wp = replace(wp, vitesse=last.vitesse, mesure=last.mesure, mesure_n=last.mesure_n,
                         mesure_duree=last.mesure_duree, mesure_frequence=last.mesure_frequence)
        self.program.add(wp)
        self.refresh_program_tree()
        self.select_program_row(len(self.program) - 1, copy=True)

    def fill_selected_program_point(self):
        selection = self.program_tree.selection()
        if not selection:
            messagebox.showinfo("Programme", "Sélectionnez d'abord une ligne à remplir.")
            return
        index = self.program_tree.index(selection[0])
        try:
            x, y, z = self._read_point()
            wp = replace(self.program[index], x=x, y=y, z=z)
            if self.program.mode == POINTS:
                wp = replace(wp, vitesse=self.get_speed())
        except (ValueError, ProgramError) as exc:
            messagebox.showwarning("Programme", str(exc))
            return
        self.program.update(index, wp)
        self.refresh_program_tree()

    def delete_program_point(self):
        selection = self.program_tree.selection()
        if not selection or self.running:
            return
        index = self.program_tree.index(selection[0])
        self.program.remove(index)
        self.refresh_program_tree()
        self._last_selected = None
        self.select_program_row(index)

    def move_program_point(self, offset):
        selection = self.program_tree.selection()
        if not selection or self.running:
            return
        index = self.program_tree.index(selection[0])
        new_index = self.program.move(index, offset)
        self.refresh_program_tree()
        self.select_program_row(new_index)

    def new_program(self):
        if self.running:
            return
        self.program = Program()
        self.program_path = None
        self.program_name_var.set("Sans titre")
        self.progress_var.set("")
        self.progress_bar.set(0)
        self.apply_mode_layout()
        self.refresh_program_tree()

    def load_program(self):
        if self.running:
            return
        path = filedialog.askopenfilename(initialdir=config.PROGRAMS_DIR,
                                          filetypes=[("Programme JSON", "*.json")])
        if not path:
            return
        try:
            self.program = Program.load(path)
        except ProgramError as exc:
            messagebox.showerror("Programme", str(exc))
            return
        self.program_path = path
        self.program_name_var.set(self.program.name)
        self.progress_var.set("")
        self.progress_bar.set(0)
        self._last_selected = None
        self.apply_mode_layout()
        self.refresh_program_tree()

    def save_program(self):
        self._sync_program_name()
        path = self.program_path
        if not path:
            os.makedirs(config.PROGRAMS_DIR, exist_ok=True)
            path = filedialog.asksaveasfilename(initialdir=config.PROGRAMS_DIR,
                                                defaultextension=".json",
                                                filetypes=[("Programme JSON", "*.json")])
        if not path:
            return
        try:
            self.program.save(path)
        except OSError as exc:
            messagebox.showerror("Programme", str(exc))
            return
        self.program_path = path
        self.write_log(f"Programme enregistré : {path}")

    # ------------------------------------------------------------------
    # Programme : exécution
    # ------------------------------------------------------------------
    def run_program(self):
        if (not self.connected or self.busy or self.running or not len(self.program)
                or not self.printer.homed):
            return
        problems = self.program.validate()
        if problems:
            messagebox.showwarning("Programme", "\n".join(problems))
            return
        self._sync_program_name()

        if self.measurer.program_needs_measure(self.program):
            if not self.acq.connected:
                if not messagebox.askyesno(
                        "Mesure",
                        "Le programme prévoit des mesures, mais l'appareil de mesure n'est pas "
                        "connecté (onglet « Mesure »).\n\nLancer quand même, sans mesurer ?"):
                    return
            elif self.acq.record_count:
                choice = messagebox.askyesnocancel(
                    "Mesures existantes",
                    "Des mesures sont déjà enregistrées.\n\nOui : les effacer avant de lancer\n"
                    "Non : les conserver et ajouter les nouvelles\nAnnuler : ne pas lancer")
                if choice is None:
                    return
                if choice:
                    self.acq.clear()

        self.running = True
        self.step_text = ""
        self.current_step = None
        self._records_at_start = self.acq.record_count
        self.progress_bar.set(0)
        self.update_controls()
        self.write_log(f"Programme lancé : {self.program.name} ({self.program.mode})")
        threading.Thread(target=self._run_program_worker, daemon=True).start()

    def _run_program_worker(self):
        try:
            self.runner.run(self.program)
        except Exception as exc:
            self.post(self.run_state_changed, ERROR, str(exc) or exc.__class__.__name__)

    def _mark_active_row(self, index):
        for i, item in enumerate(self.program_tree.get_children()):
            if i < len(self.program):
                self.program_tree.item(item, tags=self._row_tags(self.program[i], i == index))

    def step_started(self, index, waypoint):
        self.current_step = index
        total = max(1, len(self.program))
        self.step_text = f"Point {index + 1}/{len(self.program)}"
        self.progress_var.set(self.step_text)
        self.progress_bar.set(index / total * 100)
        self._mark_active_row(index)
        self.program_tree.see(self.program_tree.get_children()[index])
        self.request_draw()
        self.update_controls()

    def runner_reached(self, index, waypoint, position):
        self.show_position(position)
        self.progress_bar.set((index + 1) / max(1, len(self.program)) * 100)

    def run_state_changed(self, state, message=""):
        if state in (FINISHED, STOPPED, ERROR):
            self.running = False
            self.current_step = None
            self._mark_active_row(None)
            if state == FINISHED:
                self.progress_var.set("Programme terminé")
                self.progress_bar.set(100)
            elif state == STOPPED:
                self.progress_var.set("Programme stoppé")
            else:
                self.progress_var.set(f"Erreur : {message}")
            self.write_log(f"Programme : {state}" + (f" — {message}" if message else ""))
            if self.printer.last_position:
                self.show_position(self.printer.last_position)
            if self.acq.record_count > getattr(self, "_records_at_start", 0):
                self.tabs.select("resultats")
            self.request_draw()
        self.update_controls()

    def stop_program(self):
        if not self.running:
            return
        self.write_log("Arrêt du programme demandé.")
        self.measurer.abort()
        self.runner.stop()

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------
    def _log_wheel(self, event):
        if getattr(event, "num", None) == 4:
            units = -3
        elif getattr(event, "num", None) == 5:
            units = 3
        else:
            units = -3 if getattr(event, "delta", 0) > 0 else 3
        self.log.yview_scroll(units, "units")
        return "break"

    def write_log(self, text):
        if not hasattr(self, "log"):
            return
        upper = text.upper()
        if text.startswith(">"):
            tag = "tx"
        elif text.startswith("<"):
            tag = "rx"
        elif upper.startswith("ERREUR") or "ERREUR" in upper[:24]:
            tag = "err"
        elif upper.startswith(("STOP", "ARRÊT")):
            tag = "warn"
        else:
            tag = "info"
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S") + "  ", "stamp")
        self.log.insert("end", text + "\n", tag)
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 2500:
            self.log.delete("1.0", f"{lines - 2500 + 1}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def on_traffic(self, direction, text):
        arrow = ">" if direction == "TX" else "<"
        self.post(self.write_log, f"{arrow} {text}")

    def close(self):
        self._jog_stop.set()
        self.measurer.abort()
        try:
            self.settings.set("layout", "console_lines", int(self.log.cget("height")))
        except Exception:
            pass
        for action in (self.runner.stop, self.shutdown_measure, self.printer.disconnect):
            try:
                action()
            except Exception:
                pass
        self.settings.save()
        self.root.destroy()


def start_gui(port=None):
    root = tk.Tk()
    EnderGUI(root, port=port)
    # Certains gestionnaires de fenêtres peuvent laisser la fenêtre principale
    # non présentée après une création depuis PowerShell ou un raccourci.
    root.state("normal")
    root.deiconify()
    root.lift()
    root.after_idle(lambda: root.focus_force())
    root.mainloop()
