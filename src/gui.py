"""Interface principale du contrôleur d'imprimante.

Une seule fenêtre, trois zones :

* à gauche  : la machine (position, télécommande, vitesse, arrêts) ;
* au centre : la vue 3D et l'éditeur de point ;
* à droite  : le programme (liste de positions, lancement) ;
* en bas    : console repliable (échanges série + terminal G-code).

La connexion se trouve dans la barre du haut. Les programmes et les points
restent éditables hors connexion.
"""

import math
import os
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

import config
from printer import Printer, PrinterError, list_ports
from program import ERROR, FINISHED, STOPPED, Program, ProgramError, ProgramRunner, Waypoint


# ----------------------------------------------------------------------
# Thème
# ----------------------------------------------------------------------

C = {
    "bg": "#f4f5f7",
    "surface": "#ffffff",
    "line": "#e4e6eb",
    "soft": "#f1f2f5",
    "soft_hover": "#e6e8ed",
    "soft_press": "#dcdfe5",
    "text": "#1b1d21",
    "muted": "#7a808b",
    "faint": "#b6bbc5",
    "accent": "#2563eb",
    "accent_hover": "#1d4fd0",
    "accent_press": "#1a43b3",
    "accent_soft": "#e6eeff",
    "danger": "#d92d20",
    "danger_hover": "#b42318",
    "danger_press": "#912018",
    "danger_soft": "#fdecea",
    "danger_soft_hover": "#fadad6",
    "ok": "#12a150",
    "ok_soft": "#e3f6ea",
    "warn": "#e8890c",
    "grid": "#eceef2",
    "edge": "#d3d7de",
    "path": "#9db4ee",
    "axis_x": "#e5484d",
    "axis_y": "#30a46c",
    "axis_z": "#3e63dd",
    "console": "#14161a",
    "console_text": "#d7dae0",
    "console_input": "#1d2027",
    "console_line": "#2a2e37",
}

STATUS_COLORS = {
    "idle": C["faint"],
    "ok": C["ok"],
    "busy": C["warn"],
    "run": C["accent"],
    "error": C["danger"],
}

PROGRAM_FIELDS = ("x", "y", "z", "vitesse", "attente")
NO_POSITION = "—"


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


class Tooltip:
    """Petite bulle d'aide affichée après un court survol."""

    def __init__(self, widget, text, delay=550):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._job = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._hide()
        self._job = self.widget.after(self.delay, self._show)

    def _show(self):
        self._job = None
        if self._tip is not None:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        tk.Label(tip, text=self.text, bg=C["text"], fg="white", padx=9, pady=5,
                 justify="left", wraplength=280).pack()
        self._tip = tip

    def _hide(self, _event=None):
        if self._job is not None:
            self.widget.after_cancel(self._job)
            self._job = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


class ThinProgress(tk.Canvas):
    """Barre de progression fine (4 px)."""

    def __init__(self, parent):
        super().__init__(parent, height=4, bg=C["soft"], highlightthickness=0)
        self._percent = 0.0
        self.bind("<Configure>", lambda _e: self._redraw())

    def set(self, percent):
        self._percent = max(0.0, min(100.0, float(percent)))
        self._redraw()

    def _redraw(self):
        self.delete("all")
        width = self.winfo_width()
        if self._percent > 0:
            self.create_rectangle(0, 0, width * self._percent / 100, 4,
                                  fill=C["accent"], outline="")


class EnderGUI:
    """Interface Tkinter du contrôleur."""

    def __init__(self, root, port=None):
        self.root = root
        self.root.title("Contrôle imprimante")
        self._place_window()
        self.root.configure(bg=C["bg"])

        # --- État -----------------------------------------------------
        self.connected = False
        self.busy = False
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
        self.program_start_position = None
        self.step_text = ""
        self.current_step = None
        self._last_selected = None
        self._program_edit_entry = None
        self._draw_job = None
        self._grip = None
        self._status_kind = "idle"

        # Appelé avec (index, waypoint, position) quand un point du programme
        # est atteint : point d'accroche pour brancher une acquisition.
        self.on_point_reached = None

        # --- Variables Tk ---------------------------------------------
        self.port_var = tk.StringVar(value=port or config.PORT)
        self.speed_var = tk.StringVar(value=str(config.DEFAULT_SPEED))
        self.status_var = tk.StringVar(value="Déconnecté")
        self.program_name_var = tk.StringVar(value="Sans titre")
        self.progress_var = tk.StringVar(value="")
        self.logs_visible = tk.BooleanVar(value=True)
        self.point_vars = {a: tk.StringVar() for a in "XYZ"}
        self.point_step_var = tk.StringVar(value="1")
        self.pos_vars = {a: tk.StringVar(value=NO_POSITION) for a in "XYZ"}

        # --- Imprimante / programme -----------------------------------
        self.printer = self.create_printer(port or config.PORT)
        self.printer.on_traffic = self.on_traffic
        self.program = Program()
        self.runner = ProgramRunner(
            self.printer,
            on_step=lambda i, wp: self.post(self.step_started, i, wp),
            on_reached=lambda i, wp, pos: self.post(self.runner_reached, i, wp, pos),
            on_state=lambda s, m: self.post(self.run_state_changed, s, m),
        )

        # --- Caméra 3D ------------------------------------------------
        # X et Y sont volontairement échangés dans le schéma (graphique
        # uniquement : les commandes G-code gardent leurs axes).
        self.reset_camera(redraw=False)
        self.view_drag = None

        self.create_interface()
        self.refresh_program_tree()
        self.update_controls()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Escape>", lambda _e: self.quick_stop())
        self.root.bind("<Control-s>", lambda _e: self.save_program())
        self.poll_events()

    # ------------------------------------------------------------------
    # Fenêtre
    # ------------------------------------------------------------------
    def _place_window(self):
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w, h = min(1360, sw - 40), min(880, sh - 90)
        self.root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
        self.root.minsize(min(1120, w), min(700, h))
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

    def run_async(self, function, on_success=None, busy=True, on_error=None):
        if busy:
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
            self.set_busy(False)
        if on_success:
            on_success(result)

    def async_failed(self, error, busy, on_error=None):
        if busy:
            self.set_busy(False)
        message = str(error) or error.__class__.__name__
        self.write_log(f"ERREUR : {message}")
        self.set_status(f"Erreur : {message}", "error")
        if self.connected and not self.printer.is_connected():
            self.mark_disconnected()
        if on_error:
            on_error(message)

    # ------------------------------------------------------------------
    # Styles
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
            "mono_big": tkfont.Font(family=mono, size=13, weight="bold"),
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

        s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        s.configure("Treeview", background=C["surface"], fieldbackground=C["surface"],
                    foreground=C["text"], rowheight=32, borderwidth=0, font=F["base"])
        s.map("Treeview", background=[("selected", C["accent_soft"])],
              foreground=[("selected", C["text"])])
        s.configure("Treeview.Heading", background=C["surface"], foreground=C["muted"],
                    font=F["section"], relief="flat", borderwidth=1, bordercolor=C["line"],
                    lightcolor=C["surface"], darkcolor=C["surface"], padding=(6, 8))
        s.map("Treeview.Heading", background=[("active", C["surface"])])

        for name, thumb, trough in (("Thin", C["faint"], C["surface"]),
                                    ("Dark", C["console_line"], C["console"])):
            style = f"{name}.Vertical.TScrollbar"
            s.layout(style, [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
            s.configure(style, background=thumb, troughcolor=trough, bordercolor=trough,
                        lightcolor=thumb, darkcolor=thumb, width=8, gripcount=0)
            s.map(style, background=[("active", C["muted"])])


    # ------------------------------------------------------------------
    # Construction de l'interface
    # ------------------------------------------------------------------
    def create_interface(self):
        self._setup_fonts()
        self._setup_styles()
        self.create_topbar()
        self.create_console()  # empaqueté avant le corps pour garder sa hauteur

        # Page défilante : si la fenêtre est trop basse, une barre apparaît.
        host = tk.Frame(self.root, bg=C["bg"])
        host.pack(fill="both", expand=True)
        self.page_canvas = tk.Canvas(host, bg=C["bg"], highlightthickness=0)
        self.page_scroll = ttk.Scrollbar(host, orient="vertical", style="Thin.Vertical.TScrollbar",
                                         command=self.page_canvas.yview)
        self.page_canvas.configure(yscrollcommand=self.page_scroll.set)
        self.page_canvas.pack(side="left", fill="both", expand=True)
        body = tk.Frame(self.page_canvas, bg=C["bg"])
        self.page_window = self.page_canvas.create_window((0, 0), window=body, anchor="nw")
        self.page_canvas.bind("<Configure>", self._page_resize)
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.root.bind_all(sequence, self._page_wheel, add="+")
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = self.card(body, width=300)
        left.grid(row=0, column=0, sticky="ns", padx=(12, 0), pady=12)
        left.pack_propagate(False)
        self.create_machine_panel(left)

        center = tk.Frame(body, bg=C["bg"])
        center.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        self.create_view_panel(center)
        self.create_point_panel(center)

        right = self.card(body, width=410)
        right.grid(row=0, column=2, sticky="ns", padx=(0, 12), pady=12)
        right.pack_propagate(False)
        self.create_program_panel(right)

        if self._small_screen:
            self.set_console_visible(False)

    MIN_PAGE_HEIGHT = 640

    def _page_resize(self, event):
        height = max(event.height, self.MIN_PAGE_HEIGHT)
        needs_scroll = height > event.height
        if needs_scroll and not self.page_scroll.winfo_ismapped():
            self.page_scroll.pack(side="right", fill="y")
        elif not needs_scroll and self.page_scroll.winfo_ismapped():
            self.page_scroll.pack_forget()
            self.page_canvas.yview_moveto(0)
        self.page_canvas.itemconfigure(self.page_window, width=event.width, height=height)
        self.page_canvas.configure(scrollregion=(0, 0, event.width, height))

    def _page_wheel(self, event):
        if not self.page_scroll.winfo_ismapped():
            return
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if widget is None or widget.winfo_toplevel() is not self.root:
            return
        # La vue 3D (zoom), le tableau et la console gardent leur molette.
        if widget in (getattr(self, "view", None), getattr(self, "log", None),
                      getattr(self, "program_tree", None)):
            return
        if getattr(event, "num", None) == 4:
            units = -3
        elif getattr(event, "num", None) == 5:
            units = 3
        else:
            units = -3 if getattr(event, "delta", 0) > 0 else 3
        self.page_canvas.yview_scroll(units, "units")

    # --- Helpers -------------------------------------------------------
    def card(self, parent, **kw):
        return tk.Frame(parent, bg=C["surface"], highlightthickness=1,
                        highlightbackground=C["line"], highlightcolor=C["line"], **kw)

    def section(self, parent, text, pady=(0, 6)):
        label = tk.Label(parent, text=text.upper(), bg=C["surface"], fg=C["muted"],
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
        self.port_combo = ttk.Combobox(bar, textvariable=self.port_var, values=list_ports(), width=10)
        self.port_combo.pack(side="right", padx=8)
        tk.Label(bar, text="Port", bg=C["surface"], fg=C["muted"],
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

    # --- Colonne machine -----------------------------------------------
    def create_machine_panel(self, parent):
        pad = tk.Frame(parent, bg=C["surface"])
        pad.pack(fill="both", expand=True, padx=16, pady=16)
        self.w_motion = []

        # Position
        self.section(pad, "Position (mm)")
        tiles = tk.Frame(pad, bg=C["surface"])
        tiles.pack(fill="x", pady=(0, 16))
        axis_colors = {"X": C["axis_x"], "Y": C["axis_y"], "Z": C["axis_z"]}
        for i, axis in enumerate("XYZ"):
            tiles.grid_columnconfigure(i, weight=1, uniform="tile", minsize=82)
            tile = tk.Frame(tiles, bg=C["soft"])
            tile.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0))
            tk.Label(tile, text=axis, bg=C["soft"], fg=axis_colors[axis],
                     font=self.f["section"]).pack(anchor="w", padx=8, pady=(7, 0))
            tk.Label(tile, textvariable=self.pos_vars[axis], bg=C["soft"], fg=C["text"],
                     font=self.f["mono_big"], anchor="w").pack(fill="x", padx=8, pady=(0, 7))

        # Télécommande
        self.section(pad, "Déplacement")
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
        self.home_button = ttk.Button(grid, text="0,0,0", style="Home.TButton", command=self.home)
        self.home_button.grid(row=1, column=1, padx=3, pady=3, sticky="nsew")
        self.tip(self.home_button, "Aller à X0 Y0 Z0")
        self.w_motion.append(self.home_button)
        jog_button("X +", "X", 1, 1, 2)
        jog_button("Y −", "Y", -1, 2, 1)
        jog_button("Z +", "Z", 1, 0, 4)
        tk.Label(grid, text="Z", bg=C["surface"], fg=C["faint"],
                 font=self.f["section"]).grid(row=1, column=4)
        jog_button("Z −", "Z", -1, 2, 4)
        tk.Label(pad, text="Maintenir un bouton pour déplacer, relâcher pour arrêter.",
                 bg=C["surface"], fg=C["muted"], font=self.f["small"], anchor="w",
                 justify="left", wraplength=260).pack(fill="x", pady=(6, 16))

        # Vitesse
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
        self.tip(self.recalibrate_button, "Recherche des butées mécaniques de l'imprimante (G28).")
        self.w_motion.append(self.recalibrate_button)

    def sync_speed_chips(self):
        try:
            current = float(str(self.speed_var.get()).replace(",", "."))
        except ValueError:
            current = None
        for value, chip in self.chips.items():
            chip.configure(style="ChipOn.TButton" if current == value else "Chip.TButton")

    # --- Vue 3D --------------------------------------------------------
    def create_view_panel(self, parent):
        card = self.card(parent)
        card.pack(fill="both", expand=True)
        self.view = tk.Canvas(card, bg=C["surface"], highlightthickness=0, height=300)
        self.view.pack(fill="both", expand=True, padx=1, pady=1)
        self._bind_view(self.view)
        reset = ttk.Button(self.view, text="Recentrer la vue", style="Ghost.TButton",
                           command=self.reset_camera)
        reset.place(relx=1.0, x=-10, y=10, anchor="ne")

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
    def create_point_panel(self, parent):
        card = self.card(parent)
        card.pack(fill="x", pady=(12, 0))
        pad = tk.Frame(card, bg=C["surface"])
        pad.pack(fill="x", padx=16, pady=14)

        top = tk.Frame(pad, bg=C["surface"])
        top.pack(fill="x", pady=(0, 8))
        tk.Label(top, text="POINT", bg=C["surface"], fg=C["muted"],
                 font=self.f["section"]).pack(side="left")
        tk.Label(top, text="Position visée, affichée en orange dans la vue 3D",
                 bg=C["surface"], fg=C["faint"], font=self.f["small"]).pack(side="left", padx=10)

        fields = tk.Frame(pad, bg=C["surface"])
        fields.pack(fill="x")
        for i in range(4):
            fields.grid_columnconfigure(i, weight=1, uniform="pt")
        for i, axis in enumerate("XYZ"):
            group = tk.Frame(fields, bg=C["surface"])
            group.grid(row=0, column=i, sticky="ew", padx=(0, 12))
            tk.Label(group, text=axis, bg=C["surface"], fg=C["muted"], font=self.f["small"],
                     anchor="w").pack(fill="x")
            line = tk.Frame(group, bg=C["surface"])
            line.pack(fill="x")
            minus = ttk.Button(line, text="−", width=2, style="Mini.TButton",
                               command=lambda a=axis: self.change_point_axis(a, -1))
            minus.pack(side="left")
            entry = ttk.Entry(line, textvariable=self.point_vars[axis], width=6)
            entry.pack(side="left", fill="x", expand=True, padx=3)
            entry.bind("<KeyRelease>", lambda _e: self.request_draw())
            plus = ttk.Button(line, text="+", width=2, style="Mini.TButton",
                              command=lambda a=axis: self.change_point_axis(a, 1))
            plus.pack(side="left")
        group = tk.Frame(fields, bg=C["surface"])
        group.grid(row=0, column=3, sticky="ew")
        tk.Label(group, text="Pas (mm)", bg=C["surface"], fg=C["muted"], font=self.f["small"],
                 anchor="w").pack(fill="x")
        ttk.Entry(group, textvariable=self.point_step_var, width=6).pack(fill="x")

        actions = tk.Frame(pad, bg=C["surface"])
        actions.pack(fill="x", pady=(12, 0))
        self.capture_button = ttk.Button(actions, text="Position actuelle", style="Soft.TButton",
                                         command=self.capture_point)
        self.capture_button.pack(side="left")
        self.tip(self.capture_button, "Copier la position de la machine dans ce point.")
        self.goto_button = ttk.Button(actions, text="Aller au point", style="Soft.TButton",
                                      command=self.goto_selected_point)
        self.goto_button.pack(side="left", padx=8)
        self.add_point_button = ttk.Button(actions, text="Ajouter au programme  →",
                                           style="Accent.TButton",
                                           command=self.add_selected_to_program)
        self.add_point_button.pack(side="right")
        self.w_goto = [self.goto_button]

    # --- Programme -----------------------------------------------------
    def create_program_panel(self, parent):
        pad = tk.Frame(parent, bg=C["surface"])
        pad.pack(fill="both", expand=True, padx=16, pady=16)
        self.w_edit = []

        self.section(pad, "Programme")
        name = ttk.Entry(pad, textvariable=self.program_name_var, font=self.f["name"])
        name.pack(fill="x")
        name.bind("<FocusOut>", lambda _e: self._sync_program_name())
        name.bind("<Return>", lambda _e: (self._sync_program_name(), self.root.focus_set()))
        self.w_edit.append(name)

        files = tk.Frame(pad, bg=C["surface"])
        files.pack(fill="x", pady=(8, 12))
        for i, (text, command) in enumerate((("Nouveau", self.new_program),
                                             ("Ouvrir", self.load_program),
                                             ("Enregistrer", self.save_program))):
            files.grid_columnconfigure(i, weight=1, uniform="file")
            b = ttk.Button(files, text=text, style="Soft.TButton", command=command)
            b.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0))
            if text != "Enregistrer":
                self.w_edit.append(b)
            else:
                self.save_program_button = b
                self.tip(b, "Enregistrer le programme en JSON (Ctrl+S).")

        # Run bar (en bas)
        run = tk.Frame(pad, bg=C["surface"])
        run.pack(side="bottom", fill="x", pady=(12, 0))
        self.run_button = ttk.Button(run, text="▶  Lancer le programme", style="Accent.TButton",
                                     command=self.run_program)
        self.run_button.pack(fill="x")
        second = tk.Frame(run, bg=C["surface"])
        second.pack(fill="x", pady=(6, 0))
        second.grid_columnconfigure(0, weight=1, uniform="run")
        second.grid_columnconfigure(1, weight=1, uniform="run")
        self.stop_program_button = ttk.Button(second, text="■  Stopper", style="Soft.TButton",
                                              command=self.stop_program)
        self.stop_program_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.return_button = ttk.Button(second, text="↩  Revenir au départ", style="Soft.TButton",
                                        command=self.return_to_program_start)
        self.return_button.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        self.tip(self.return_button, "Retourne à la position où se trouvait la machine avant le lancement.")

        progress = tk.Frame(pad, bg=C["surface"])
        progress.pack(side="bottom", fill="x", pady=(10, 0))
        tk.Label(progress, textvariable=self.progress_var, bg=C["surface"], fg=C["muted"],
                 font=self.f["small"], anchor="w").pack(fill="x", pady=(0, 4))
        self.progress_bar = ThinProgress(progress)
        self.progress_bar.pack(fill="x")

        # Outils de lignes
        tools = tk.Frame(pad, bg=C["surface"])
        tools.pack(side="bottom", fill="x", pady=(8, 0))
        specs = (("＋ Ligne", self.add_empty_program_point, "Ajouter une ligne vide à remplir ensuite.", 0),
                 ("Remplir", self.fill_selected_program_point,
                  "Remplir la ligne sélectionnée avec le point et la vitesse courants.", 0),
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

        tk.Label(pad, text="Double-clic pour modifier · vitesse en mm/min · attente en s",
                 bg=C["surface"], fg=C["muted"], font=self.f["small"],
                 anchor="w").pack(side="bottom", fill="x", pady=(8, 0))

        # Tableau
        table = tk.Frame(pad, bg=C["surface"], highlightthickness=1,
                         highlightbackground=C["line"], highlightcolor=C["line"])
        table.pack(fill="both", expand=True)
        cols = ("n", "x", "y", "z", "speed", "wait")
        self.program_tree = ttk.Treeview(table, columns=cols, show="headings",
                                         selectmode="browse", height=8)
        titles = {"n": "#", "x": "X", "y": "Y", "z": "Z", "speed": "VITESSE", "wait": "ATTENTE"}
        widths = {"n": 30, "x": 54, "y": 54, "z": 54, "speed": 72, "wait": 72}
        for col in cols:
            anchor = "center" if col == "n" else "e"
            self.program_tree.heading(col, text=titles[col], anchor=anchor)
            self.program_tree.column(col, width=widths[col], minwidth=30, anchor=anchor)
        table.grid_columnconfigure(0, weight=1)
        table.grid_rowconfigure(0, weight=1)
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

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Aller à ce point", command=self.goto_program_selected)
        self.menu.add_separator()
        self.menu.add_command(label="Monter", command=lambda: self.move_program_point(-1))
        self.menu.add_command(label="Descendre", command=lambda: self.move_program_point(1))
        self.menu.add_command(label="Supprimer", command=self.delete_program_point)

    # --- Console -------------------------------------------------------
    def create_console(self):
        self.console = tk.Frame(self.root, bg=C["console"])
        self.console.pack(side="bottom", fill="x")

        grip = tk.Frame(self.console, bg=C["console_line"], height=4, cursor="sb_v_double_arrow")
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
        self.log = tk.Text(logs, height=5, state="disabled", bg=C["console"], fg=C["console_text"],
                           insertbackground="white", relief="flat", highlightthickness=0,
                           font=self.f["mono"], wrap="word", padx=0, pady=2)
        scroll = ttk.Scrollbar(logs, orient="vertical", style="Dark.Vertical.TScrollbar",
                               command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        for tag, color in (("stamp", "#5b616e"), ("tx", "#7aa2ff"), ("rx", "#9aa1ad"),
                           ("err", "#ff7a70"), ("warn", "#f5b45a"), ("info", C["console_text"])):
            self.log.tag_configure(tag, foreground=color)
        # La molette reste dans la console.
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

    def toggle_bottom_area(self):
        self.set_console_visible(self.logs_visible.get())

    def _grip_press(self, event):
        self._grip = (event.y_root, int(self.log.cget("height")))

    def _grip_drag(self, event):
        if not self._grip or not self.logs_visible.get():
            return
        y0, h0 = self._grip
        line = max(1, self.f["mono"].metrics("linespace"))
        height = max(3, min(30, h0 + round((y0 - event.y_root) / line)))
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
        shown = text if len(text) <= 50 else text[:47] + "…"
        self.status_var.set(shown)
        self.status_dot.itemconfigure(self._dot, fill=STATUS_COLORS.get(kind, C["faint"]))
        self.status_label.configure(fg=C["danger"] if kind == "error" else C["muted"])

    def update_controls(self):
        idle = self.connected and not self.busy and not self.running
        self.set_enabled(self.w_motion, idle)
        self.set_enabled(self.w_goto, idle)
        self.set_enabled([self.capture_button], self.connected and not self.running)
        self.set_enabled(self.w_connection, not self.busy and not self.running)
        self.set_enabled(self.w_edit, not self.running)
        self.set_enabled([self.add_point_button], not self.running)
        self.set_enabled([self.run_button], self.connected and idle and len(self.program) > 0)
        self.set_enabled([self.stop_program_button], self.running)
        self.set_enabled([self.return_button], self.connected and not self.busy
                         and not self.running and self.program_start_position is not None)
        # Les arrêts restent disponibles même pendant une commande, un jog ou un programme.
        self.set_enabled([self.stop_button, self.emergency_button], self.connected)
        self.set_enabled([self.gcode_entry, self.send_button], self.connected)
        self.connect_button.configure(text="Déconnecter" if self.connected else "Connecter",
                                      style="Soft.TButton" if self.connected else "Accent.TButton")
        if self.running:
            self.set_status("Programme en cours" + (f" — {self.step_text}" if self.step_text else ""), "run")
        elif self.busy:
            self.set_status("Commande en cours…", "busy")
        elif self.connected:
            self.set_status("Connecté", "ok")
        else:
            self.set_status("Déconnecté", "idle")

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
            messagebox.showwarning("Connexion", "Indiquez un port série.")
            return
        self.write_log(f"Connexion à {port}...")
        self.printer.port_name = port
        self.run_async(self.printer.connect, self.connection_success,
                       on_error=lambda msg: messagebox.showerror("Erreur de connexion", msg))

    def connection_success(self, _=None):
        self.connected = True
        self.write_log("Connexion réussie.")
        self.update_controls()
        self.get_position()

    def disconnect(self):
        self.printer.disconnect()
        self.mark_disconnected()
        self.write_log("Connexion fermée.")

    def mark_disconnected(self):
        self.connected = False
        self.printer.last_position = None
        for var in self.pos_vars.values():
            var.set(NO_POSITION)
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
        if not self.connected or self.running or self.busy or self.jogging:
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
            # Le mouvement reste pris en charge par le firmware jusqu'au
            # relâchement ; on n'envoie aucun segment supplémentaire.
            self._jog_stop.wait()
        except Exception as exc:
            if not self._jog_stop.is_set():
                self.post(self.write_log, f"ERREUR JOG : {exc}")
        finally:
            # Si la limite est atteinte sans relâchement, arrêter également
            # le jog ici. Sinon stop_jog() aura déjà envoyé M410.
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
        # jog_stop() a déjà récupéré M114 sans M400 : pas de second relevé.
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

    def home(self):
        """Déplacement absolu vers X0 Y0 Z0."""
        if not self.connected or self.busy or self.running:
            return
        try:
            speed = self.get_speed()
        except ValueError as exc:
            self.write_log(f"ERREUR : {exc}")
            self.set_status(f"Erreur : {exc}", "error")
            return
        self.write_log("HOME : déplacement vers X0 Y0 Z0")
        self.run_async(lambda: self.printer.move_absolute(0, 0, 0, speed),
                       lambda _: self.show_position(self.printer.last_position))

    def recalibrate(self):
        """Référencement mécanique de l'imprimante (G28)."""
        if not self.connected or self.busy or self.running:
            return
        self.write_log("RECALIBRER : recherche des bornes mécaniques (G28)")
        self.run_async(self.printer.home, lambda _: self.show_position(self.printer.last_position))

    def show_position(self, position):
        if not position:
            return
        for axis in "XYZ":
            self.pos_vars[axis].set(f"{position[axis]:g}")
        self.request_draw()

    def get_position(self):
        if not self.connected:
            return
        self.run_async(self.printer.get_position, self.show_position, busy=False)

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
        self.run_async(lambda: self.printer.send_command(command), lambda _: self.get_position())

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
        if not self.connected or self.busy or self.running:
            return
        try:
            x, y, z = self._read_point()
            speed = self.get_speed()
            self.printer.check_target({"X": x, "Y": y, "Z": z})
        except (ValueError, PrinterError) as exc:
            messagebox.showwarning("Point", str(exc))
            return
        self.run_async(lambda: self.printer.move_absolute(x, y, z, speed),
                       lambda _: self.show_position(self.printer.last_position))

    def add_selected_to_program(self):
        if self.running:
            return
        try:
            x, y, z = self._read_point()
            wp = Waypoint(x=x, y=y, z=z, vitesse=self.get_speed())
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
        elif kind == "diamond":
            c.create_polygon(top[0], top[1] - 7, top[0] + 7, top[1], top[0], top[1] + 7,
                             top[0] - 7, top[1], fill=color, outline="white", width=1)
        else:  # square
            c.create_rectangle(top[0] - 5, top[1] - 5, top[0] + 5, top[1] + 5,
                               outline=color, width=2)
        if label:
            if below:
                self._label(c, top[0] + 12, top[1] + 8, label, "nw", label_color or color,
                            self.f["small"])
            else:
                self._label(c, top[0] + 12, top[1] - 8, label, "sw", label_color or color,
                            self.f["small"])

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
            c.create_line(*a[1], *b2[1], fill=C["path"], width=2, capstyle="round")
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
                              fill="#4a5160", outline="white")
                color = C["muted"]
            self._label(c, xy[0] - 8, xy[1] - 7, str(i + 1), "se", color,
                        self.f["bold"] if i == selected else self.f["small"])

        if self.program_start_position:
            p = self.program_start_position
            self._marker(c, P, (p["X"], p["Y"], p["Z"]), zmin, C["ok"], "square", "Départ",
                         below=True)

        try:
            x, y, z = self._read_point()
            self._marker(c, P, (x, y, z), zmin, C["warn"], "diamond",
                         f"({x:g}, {y:g}, {z:g})", C["text"])
        except ValueError:
            pass

        if self.printer.last_position:
            p = self.printer.last_position
            self._marker(c, P, (p["X"], p["Y"], p["Z"]), zmin, C["accent"], "ring", "Machine",
                         below=True)

        # Légende + aide
        lx, ly = 16, 18
        for text, color in (("Machine", C["accent"]), ("Point", C["warn"]),
                            ("Programme", "#4a5160")):
            c.create_oval(lx, ly - 4, lx + 8, ly + 4, fill=color, outline="")
            item = c.create_text(lx + 14, ly, text=text, anchor="w", fill=C["muted"],
                                 font=self.f["small"])
            lx = c.bbox(item)[2] + 16
        c.create_text(16, h - 14, anchor="w", fill=C["faint"], font=self.f["small"],
                      text="Glisser : pivoter    Maj + glisser : déplacer    Molette : zoom")

    # ------------------------------------------------------------------
    # Programme
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
                values=(i, fmt(wp.x), fmt(wp.y), fmt(wp.z), fmt(wp.vitesse), fmt(wp.attente)))
        if len(self.program):
            self.empty_hint.place_forget()
            if previous is not None:
                self.select_program_row(previous)
        else:
            self._last_selected = None
            self.empty_hint.place(relx=0.5, rely=0.42, anchor="center")
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
        self.request_draw()

    def program_menu(self, event):
        item = self.program_tree.identify_row(event.y)
        if not item:
            return
        self.program_tree.selection_set(item)
        idle = self.connected and not self.busy and not self.running
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
        field = PROGRAM_FIELDS[int(column_id[1:]) - 2]
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
        wp = self.program[index]
        values = {name: getattr(wp, name) for name in PROGRAM_FIELDS}
        parsed = parse_float(str(text).strip().replace(NO_POSITION, ""), field)
        if field == "attente" and parsed is None:
            parsed = 0.0
        values[field] = parsed
        self.program.update(index, Waypoint(**values))

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

    def goto_program_selected(self):
        selection = self.program_tree.selection()
        if not selection or not self.connected or self.busy or self.running:
            return
        wp = self.program[self.program_tree.index(selection[0])]
        try:
            if wp.x is None and wp.y is None and wp.z is None:
                raise ValueError("Cette ligne est vide.")
            speed = self.get_speed()
            self.printer.check_target({"X": wp.x, "Y": wp.y, "Z": wp.z})
        except (ValueError, PrinterError) as exc:
            messagebox.showwarning("Programme", str(exc))
            return
        self.run_async(lambda: self.printer.move_absolute(wp.x, wp.y, wp.z, speed),
                       lambda _: self.show_position(self.printer.last_position))

    def add_empty_program_point(self):
        """Ajoute une ligne vide qui pourra être remplie ultérieurement."""
        if self.running:
            return
        self.program.add(Waypoint())
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
            wp = Waypoint(x=x, y=y, z=z, vitesse=self.get_speed(),
                          attente=self.program[index].attente)
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

    def run_program(self):
        if not self.connected or self.busy or self.running or not len(self.program):
            return
        problems = self.program.validate()
        if problems:
            messagebox.showwarning("Programme", "\n".join(problems))
            return
        self._sync_program_name()
        self.program_start_position = dict(self.printer.last_position) if self.printer.last_position else None
        self.running = True
        self.step_text = ""
        self.current_step = None
        self.progress_bar.set(0)
        self.update_controls()
        self.write_log(f"Programme lancé : {self.program.name}")
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
        if self.on_point_reached:
            try:
                self.on_point_reached(index, waypoint, position)
            except Exception as exc:
                self.write_log(f"ERREUR : {exc}")

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
                if self.printer.last_position:
                    self.show_position(self.printer.last_position)
            else:
                self.progress_var.set(f"Erreur : {message}")
            self.write_log(f"Programme : {state}" + (f" — {message}" if message else ""))
            self.request_draw()
        self.update_controls()

    def stop_program(self):
        if not self.running:
            return
        self.write_log("Arrêt du programme demandé.")
        self.runner.stop()

    def return_to_program_start(self):
        if not self.connected or self.busy or self.running or not self.program_start_position:
            return
        p = self.program_start_position
        try:
            speed = self.get_speed()
        except ValueError as exc:
            messagebox.showwarning("Programme", str(exc))
            return
        self.run_async(lambda: self.printer.move_absolute(p["X"], p["Y"], p["Z"], speed),
                       lambda _: self.show_position(self.printer.last_position))

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
        for action in (self.runner.stop, self.printer.disconnect):
            try:
                action()
            except Exception:
                pass
        self.root.destroy()


def start_gui(port=None):
    root = tk.Tk()
    EnderGUI(root, port=port)
    root.mainloop()
