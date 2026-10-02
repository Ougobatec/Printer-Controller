"""Interface principale du contrôleur d'imprimante.

Une seule page de travail : connexion + télécommande à gauche, points et
programmes au centre, logs et terminal en bas. Les programmes et les points
sont éditables hors connexion. La partie acquisition reste masquée.
"""

import math
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import config
from printer import Printer, PrinterError, list_ports
from program import ERROR, FINISHED, RUNNING, STOPPED, Program, ProgramError, ProgramRunner, Waypoint


def parse_float(text, name="valeur"):
    text = str(text).strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"« {name} » doit être un nombre.") from None


def fmt(value):
    return "—" if value is None else f"{value:g}"


class EnderGUI:
    """Interface Tkinter du contrôleur."""

    def __init__(self, root, port=None):
        self.root = root
        self.root.title("Contrôle imprimante")
        self.root.geometry("1440x960")
        self.root.minsize(1120, 760)
        self.root.configure(bg="#e9eaec")

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
        self.selected_point = None
        self.step_text = ""

        # Compatibilité avec l'ancienne API / tests.
        self.step_var = tk.DoubleVar(value=1.0)
        self.rate_var = tk.StringVar(value="10")
        self.average_var = tk.StringVar(value="5")
        self.measure_var = tk.BooleanVar(value=True)
        self.value_var = tk.StringVar(value="--")
        self.count_var = tk.StringVar(value="0 mesure")

        self.port_var = tk.StringVar(value=port or config.PORT)
        self.speed_var = tk.IntVar(value=config.DEFAULT_SPEED)
        self.position_var = tk.StringVar(value="X : --     Y : --     Z : --")
        self.status_var = tk.StringVar(value="Déconnecté")
        self.program_name_var = tk.StringVar(value="Sans titre")
        self.progress_var = tk.StringVar(value="")
        self.logs_visible = tk.BooleanVar(value=True)
        self.log_height_var = tk.IntVar(value=7)

        self.point_vars = {a: tk.StringVar() for a in "XYZ"}
        self.point_step_var = tk.DoubleVar(value=1.0)
        self._program_edit_entry = None
        self.goto_vars = {a: tk.StringVar() for a in "XYZ"}
        self.edit_vars = {k: tk.StringVar() for k in ("x", "y", "z", "vitesse", "attente")}

        self.printer = self.create_printer(port or config.PORT)
        self.printer.on_traffic = self.on_traffic
        self.program = Program()
        self.runner = ProgramRunner(
            self.printer,
            on_step=lambda i, wp: self.post(self.step_started, i, wp),
            on_reached=lambda i, wp, pos: self.post(self.runner_reached, i, wp, pos),
            on_state=lambda s, m: self.post(self.run_state_changed, s, m),
        )

        # Caméras 3D : rotation + translation + zoom.
        # Vue initiale : X et Y sont volontairement échangés dans le schéma.
        # X+ suit donc la direction graphique qui représentait Y, et inversement.
        # C'est strictement graphique : les commandes G-code gardent leurs axes.
        self.view_yaw = 0.68 + math.pi
        self.view_pitch = 0.52
        self.view_zoom = 0.65
        # Vue initiale : origine des axes sur le côté gauche du volume.
        self.view_pan_x = -105.0
        self.view_pan_y = 35.0
        self.view_drag = None
        # Les deux vues 3D partagent exactement le même repère/caméra afin
        # que les axes et les gestes de rotation aient le même comportement.

        self.create_interface()
        self.refresh_program_tree()
        self.update_controls()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Escape>", lambda _e: self.quick_stop())
        self.poll_events()

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
        self.status_var.set(f"Erreur : {message}")
        if self.connected and not self.printer.is_connected():
            self.mark_disconnected()
        if on_error:
            on_error(message)

    # ------------------------------------------------------------------
    # Interface
    # ------------------------------------------------------------------
    def create_interface(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#e9eaec")
        style.configure("TLabel", background="#e9eaec", foreground="#202326")
        style.configure("TLabelframe", background="#e9eaec", bordercolor="#c7cbd0")
        style.configure("TLabelframe.Label", background="#e9eaec", foreground="#34383d", font=("TkDefaultFont", 10, "bold"))
        style.configure("TButton", padding=(10, 7), font=("TkDefaultFont", 9))
        style.configure("Primary.TButton", padding=(12, 9), font=("TkDefaultFont", 9, "bold"))
        style.configure("Treeview", rowheight=28, font=("TkDefaultFont", 9))
        style.configure("Treeview.Heading", font=("TkDefaultFont", 9, "bold"))
        style.configure("TEntry", padding=5)
        style.configure("TCombobox", padding=4)

        header = tk.Frame(self.root, bg="#24272b", height=58)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="CONTRÔLE IMPRIMANTE", bg="#24272b", fg="#f5f5f5",
                 font=("TkDefaultFont", 13, "bold")).pack(side="left", padx=18)
        tk.Label(header, textvariable=self.status_var, bg="#24272b", fg="#bfc4c9",
                 font=("TkDefaultFont", 9)).pack(side="left", padx=10)
        tk.Label(header, textvariable=self.position_var, bg="#24272b", fg="#f5f5f5",
                 font=("TkFixedFont", 11, "bold")).pack(side="right", padx=18)

        shell = ttk.Frame(self.root)
        shell.pack(fill="both", expand=True, padx=12, pady=12)

        sidebar_host = ttk.Frame(shell, width=310)
        sidebar_host.pack(side="left", fill="y", padx=(0, 10))
        sidebar_host.pack_propagate(False)

        # Les deux arrêts restent hors de la zone défilante : ils sont toujours visibles.
        safety_frame = ttk.LabelFrame(sidebar_host, text="Sécurité")
        safety_frame.pack(fill="x", side="top", pady=(0, 8))
        self.stop_button = ttk.Button(safety_frame, text="STOP", command=self.quick_stop)
        self.stop_button.pack(fill="x", padx=9, pady=(9, 4))
        self.emergency_button = tk.Button(
            safety_frame, text="ARRÊT D'URGENCE", command=self.emergency_stop,
            bg="#b3261e", fg="white", activebackground="#8d1712", activeforeground="white",
            relief="flat", bd=0, font=("TkDefaultFont", 9, "bold"), pady=9
        )
        self.emergency_button.pack(fill="x", padx=9, pady=(4, 9))

        scroll_host = ttk.Frame(sidebar_host)
        scroll_host.pack(fill="both", expand=True)
        self.sidebar_canvas = tk.Canvas(scroll_host, bg="#e9eaec", highlightthickness=0, width=300)
        self.sidebar_scroll = ttk.Scrollbar(scroll_host, orient="vertical", command=self.sidebar_canvas.yview)
        self.sidebar_canvas.configure(yscrollcommand=self.sidebar_scroll.set)
        self.sidebar_canvas.pack(side="left", fill="both", expand=True)
        self.sidebar = ttk.Frame(self.sidebar_canvas, width=290)
        self.sidebar_window = self.sidebar_canvas.create_window((0, 0), window=self.sidebar, anchor="nw")
        self.sidebar.bind("<Configure>", lambda _e: self._update_sidebar_scrollbar())
        self.sidebar_canvas.bind("<Configure>", lambda e: (
            self.sidebar_canvas.itemconfigure(self.sidebar_window, width=max(270, e.width)),
            self._update_sidebar_scrollbar()
        ))
        # La molette est capturée globalement uniquement lorsqu'elle se trouve
        # réellement dans la colonne gauche. Le scrollregion est recalculé
        # exactement sur le contenu afin d'éviter les marges artificielles.
        self.sidebar_canvas.bind_all("<MouseWheel>", self._sidebar_wheel, add="+")
        self.sidebar_canvas.bind_all("<Button-4>", self._sidebar_wheel, add="+")
        self.sidebar_canvas.bind_all("<Button-5>", self._sidebar_wheel, add="+")

        self.create_connection(self.sidebar)
        self.create_remote(self.sidebar)
        self._update_sidebar_scrollbar()

        self.center_host = ttk.Frame(shell)
        self.center_host.pack(side="left", fill="both", expand=True)
        self.create_scrollable_workspace(self.center_host)
        self.create_bottom_area()

    def _update_sidebar_scrollbar(self):
        if not hasattr(self, "sidebar_canvas") or not hasattr(self, "sidebar_scroll"):
            return
        self.root.update_idletasks()
        bbox = self.sidebar_canvas.bbox(self.sidebar_window)
        visible = max(1, self.sidebar_canvas.winfo_height())
        content = (bbox[3] - bbox[1]) if bbox else 0
        width = max(1, self.sidebar_canvas.winfo_width())
        # Le canvas doit avoir exactement la taille de son contenu : pas de
        # zone vide ajoutée en haut ou en bas par un scrollregion implicite.
        self.sidebar_canvas.configure(scrollregion=(0, 0, width, max(visible, content)))
        if content > visible + 1:
            self.sidebar_scroll.pack(side="right", fill="y")
        else:
            self.sidebar_scroll.pack_forget()
            self.sidebar_canvas.yview_moveto(0)

    def _sidebar_wheel(self, event):
        if not self.sidebar_scroll.winfo_ismapped():
            return
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if widget is None:
            return
        current = widget
        inside = False
        while current is not None:
            if current == self.sidebar_canvas:
                inside = True
                break
            try:
                current = current.master
            except AttributeError:
                break
        if not inside:
            return
        if getattr(event, "num", None) == 4:
            units = -3
        elif getattr(event, "num", None) == 5:
            units = 3
        else:
            delta = getattr(event, "delta", 0)
            units = -3 if delta > 0 else 3
        self.sidebar_canvas.yview_scroll(units, "units")

    def create_connection(self, parent):
        frame = ttk.LabelFrame(parent, text="Connexion")
        frame.pack(fill="x", pady=(0, 10))
        row = ttk.Frame(frame)
        row.pack(fill="x", padx=9, pady=9)
        ttk.Label(row, text="Port").pack(side="left")
        self.port_combo = ttk.Combobox(row, textvariable=self.port_var, values=list_ports(), width=12)
        self.port_combo.pack(side="left", padx=7, fill="x", expand=True)
        ttk.Button(row, text="Actualiser", width=10, command=self.refresh_ports).pack(side="right")
        self.connect_button = ttk.Button(frame, text="Connecter", style="Primary.TButton", command=self.toggle_connection)
        self.connect_button.pack(fill="x", padx=9, pady=(0, 9))
        self.w_connection = [self.port_combo, self.connect_button]

    def create_remote(self, parent):
        frame = ttk.LabelFrame(parent, text="Télécommande")
        frame.pack(fill="x", pady=(0, 10))
        grid = tk.Frame(frame, bg="#e9eaec")
        grid.pack(pady=9)
        self.remote_buttons = []
        self.w_motion = []

        def jog_button(text, axis, direction, row, col):
            b = ttk.Button(grid, text=text, width=8)
            b.grid(row=row, column=col, padx=4, pady=4, ipadx=2, ipady=3)
            b.bind("<ButtonPress-1>", lambda _e: self.start_jog(axis, direction))
            b.bind("<ButtonRelease-1>", lambda _e: self.stop_jog())
            b.bind("<Leave>", lambda _e: self.stop_jog())
            self.remote_buttons.append(b)
            self.w_motion.append(b)
            return b

        jog_button("Y +", "Y", 1, 0, 1)
        jog_button("X -", "X", -1, 1, 0)
        self.home_button = ttk.Button(grid, text="0,0,0", width=8, command=self.home)
        self.home_button.grid(row=1, column=1, padx=4, pady=4, ipadx=2, ipady=3)
        self.remote_buttons.append(self.home_button)
        self.w_motion.append(self.home_button)
        jog_button("X +", "X", 1, 1, 2)
        jog_button("Y -", "Y", -1, 2, 1)
        jog_button("Z +", "Z", 1, 3, 1)
        jog_button("Z -", "Z", -1, 4, 1)

        speed = ttk.Frame(frame)
        speed.pack(fill="x", padx=10, pady=(2, 8))
        ttk.Label(speed, text="Vitesse").pack(side="left")
        self.speed_spin = ttk.Spinbox(speed, from_=1, to=config.MAX_SPEED, increment=50, textvariable=self.speed_var, width=8)
        self.speed_spin.pack(side="right")
        ttk.Label(speed, text="mm/min").pack(side="right", padx=(0, 6))

        self.recalibrate_button = ttk.Button(frame, text="RECALIBRER", command=self.recalibrate)
        self.recalibrate_button.pack(fill="x", padx=10, pady=3)
        self.w_motion += [self.recalibrate_button]

    def create_side_status(self, parent):
        frame = ttk.LabelFrame(parent, text="Machine")
        frame.pack(fill="x")
        self.side_position = tk.Label(frame, textvariable=self.position_var, bg="#e9eaec", fg="#202326",
                                      font=("TkFixedFont", 11, "bold"), justify="left")
        self.side_position.pack(anchor="w", padx=10, pady=10)
        ttk.Label(frame, text="Maintenir un bouton pour déplacer. Relâcher pour arrêter.").pack(anchor="w", padx=10, pady=(0, 10))

    def create_scrollable_workspace(self, parent):
        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True)
        self.workspace_canvas = tk.Canvas(outer, bg="#e9eaec", highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=self.workspace_canvas.yview)
        self.workspace_canvas.configure(yscrollcommand=scrollbar.set)
        self.workspace_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.workspace = ttk.Frame(self.workspace_canvas)
        self.workspace_window = self.workspace_canvas.create_window((0, 0), window=self.workspace, anchor="nw")
        self.workspace.bind("<Configure>", lambda _e: self.workspace_canvas.configure(scrollregion=self.workspace_canvas.bbox("all")))
        self.workspace_canvas.bind("<Configure>", lambda e: self.workspace_canvas.itemconfigure(self.workspace_window, width=e.width))
        self.workspace_canvas.bind_all("<MouseWheel>", self._workspace_wheel, add="+")

        self.create_points_program_workspace(self.workspace)

    def _workspace_wheel(self, event):
        # Ne pas détourner la molette des vues 3D ni de la colonne gauche.
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if widget in (getattr(self, "canvas3d", None), getattr(self, "program_canvas", None)):
            return
        current = widget
        while current is not None:
            if current == getattr(self, "sidebar_canvas", None):
                return
            try:
                current = current.master
            except AttributeError:
                break
        self.workspace_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def create_points_program_workspace(self, parent):
        # Un seul point de travail : il est éditable en permanence et n'est pas
        # une collection de points indépendante du programme.
        points_frame = ttk.LabelFrame(parent, text="Point")
        points_frame.pack(fill="x", pady=(0, 10))

        point_body = ttk.Frame(points_frame)
        point_body.pack(fill="x", padx=10, pady=10)

        edit_frame = ttk.Frame(point_body)
        edit_frame.pack(side="left", fill="y", padx=(0, 12))
        ttk.Label(edit_frame, text="Coordonnées", font=("TkDefaultFont", 10, "bold")).pack(anchor="w", pady=(0, 8))
        for axis in "XYZ":
            row = ttk.Frame(edit_frame)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=axis, width=3).pack(side="left")
            entry = ttk.Entry(row, textvariable=self.point_vars[axis], width=11)
            entry.pack(side="left")
            entry.bind("<KeyRelease>", lambda _e: self.draw_3d())
            ttk.Button(row, text="−", width=3, command=lambda a=axis: self.change_point_axis(a, -1)).pack(side="left", padx=(4, 2))
            ttk.Button(row, text="+", width=3, command=lambda a=axis: self.change_point_axis(a, 1)).pack(side="left")

        step_row = ttk.Frame(edit_frame)
        step_row.pack(fill="x", pady=(4, 0))
        ttk.Label(step_row, text="Pas", width=3).pack(side="left")
        ttk.Entry(step_row, textvariable=self.point_step_var, width=11).pack(side="left")
        ttk.Label(step_row, text="mm", foreground="#5c6268").pack(side="left", padx=(5, 0))

        ttk.Button(edit_frame, text="APPLIQUER POSITION ACTUELLE", command=self.capture_point).pack(fill="x", pady=(12, 4))
        ttk.Button(edit_frame, text="DÉPLACER VERS LE POINT", command=self.goto_selected_point).pack(fill="x", pady=4)
        ttk.Button(edit_frame, text="AJOUTER AU PROGRAMME", command=self.add_selected_to_program).pack(fill="x", pady=4)
        ttk.Label(edit_frame, text="La position affichée est mise à jour\nen temps réel dans le schéma.", justify="left").pack(anchor="w", pady=(12, 0))

        view_frame = ttk.LabelFrame(point_body, text="Position XYZ")
        view_frame.pack(side="left", fill="both", expand=True)
        self.canvas3d = tk.Canvas(view_frame, bg="#f8f9fa", highlightthickness=1, highlightbackground="#d0d3d6", height=250, width=430)
        self.canvas3d.pack(fill="both", expand=True)
        self._bind_3d(self.canvas3d, "points")

        # Compatibilité interne / tests : le Treeview reste caché et peut être
        # alimenté par les acquisitions, mais il n'est jamais présenté à l'utilisateur.
        self.points_tree = ttk.Treeview(self.root, columns=("name", "x", "y", "z"), show="headings")
        self.points_tree.pack_forget()
        self.points_tree.bind("<<TreeviewSelect>>", self.point_selected)
        self.point_count_var = tk.StringVar(value="Point de travail")

        program_frame = ttk.LabelFrame(parent, text="Programme")
        program_frame.pack(fill="x", pady=(0, 10))

        header = ttk.Frame(program_frame)
        header.pack(fill="x", padx=10, pady=9)
        ttk.Label(header, text="Nom").pack(side="left")
        ttk.Entry(header, textvariable=self.program_name_var, width=28).pack(side="left", padx=7)
        ttk.Button(header, text="Nouveau", command=self.new_program).pack(side="left", padx=3)
        ttk.Button(header, text="Ouvrir", command=self.load_program).pack(side="left", padx=3)
        ttk.Label(header, textvariable=self.progress_var).pack(side="right")

        program_body = ttk.Frame(program_frame)
        program_body.pack(fill="x", padx=10, pady=(0, 10))
        table_frame = ttk.LabelFrame(program_body, text="Points du programme")
        table_frame.pack(side="left", fill="both", expand=True, padx=(0, 10))
        traj_frame = ttk.LabelFrame(program_body, text="Trajectoire 3D")
        traj_frame.pack(side="left", fill="both", expand=True)

        cols = ("n", "x", "y", "z", "speed", "wait")
        self.program_tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="browse", height=8)
        titles = {"n": "#", "x": "X", "y": "Y", "z": "Z", "speed": "Vitesse", "wait": "Attente"}
        for col in cols:
            self.program_tree.heading(col, text=titles[col])
            self.program_tree.column(col, width=65 if col == "n" else 75, anchor="center" if col == "n" else "e")
        self.program_tree.pack(fill="both", expand=True, padx=7, pady=7)
        self.program_tree.bind("<<TreeviewSelect>>", self.program_selected)
        self.program_tree.bind("<Double-1>", self.edit_program_cell)
        ttk.Label(table_frame, text="Double-cliquer sur X, Y, Z, vitesse ou attente pour modifier la valeur.", foreground="#5c6268").pack(anchor="w", padx=7, pady=(0, 5))

        toolbar = ttk.Frame(table_frame)
        toolbar.pack(fill="x", padx=7, pady=(0, 7))
        for text, command in (("Ajouter point vide", self.add_empty_program_point),
                              ("Remplir sélection", self.fill_selected_program_point),
                              ("Supprimer", self.delete_program_point),
                              ("Monter", lambda: self.move_program_point(-1)),
                              ("Descendre", lambda: self.move_program_point(1))):
            ttk.Button(toolbar, text=text, command=command).pack(side="left", padx=(0, 4))

        self.program_canvas = tk.Canvas(traj_frame, bg="#f8f9fa", highlightthickness=1, highlightbackground="#d0d3d6", height=250, width=430)
        self.program_canvas.pack(fill="both", expand=True)
        self._bind_3d(self.program_canvas, "program")

        actions = ttk.Frame(program_frame)
        actions.pack(fill="x", padx=10, pady=(0, 10))
        self.run_button = ttk.Button(actions, text="LANCER LE PROGRAMME", style="Primary.TButton", command=self.run_program)
        self.run_button.pack(side="left", padx=(0, 6))
        self.stop_program_button = ttk.Button(actions, text="STOPPER", command=self.stop_program)
        self.stop_program_button.pack(side="left", padx=4)
        self.return_button = ttk.Button(actions, text="REVENIR AU DÉPART", command=self.return_to_program_start)
        self.return_button.pack(side="left", padx=4)
        self.save_program_button = ttk.Button(actions, text="ENREGISTRER", command=self.save_program)
        self.save_program_button.pack(side="left", padx=4)
        self.w_program = [self.run_button, self.stop_program_button, self.return_button]

        parent.update_idletasks()
        self.draw_3d()
        self.draw_program_preview()

    def _bind_3d(self, canvas, kind):
        canvas.bind("<Configure>", lambda _e: (self.draw_3d() if kind == "points" else self.draw_program_preview()))
        canvas.bind("<ButtonPress-1>", lambda e: self._view_press(kind, e, "rotate"))
        canvas.bind("<B1-Motion>", lambda e: self._view_drag(kind, e, "rotate"))
        canvas.bind("<Shift-ButtonPress-1>", lambda e: self._view_press(kind, e, "pan"))
        canvas.bind("<Shift-B1-Motion>", lambda e: self._view_drag(kind, e, "pan"))
        canvas.bind("<ButtonPress-2>", lambda e: self._view_press(kind, e, "pan"))
        canvas.bind("<B2-Motion>", lambda e: self._view_drag(kind, e, "pan"))
        canvas.bind("<ButtonPress-3>", lambda e: self._view_press(kind, e, "pan"))
        canvas.bind("<B3-Motion>", lambda e: self._view_drag(kind, e, "pan"))
        canvas.bind("<MouseWheel>", lambda e: self._view_zoom_event(kind, e))
        canvas.bind("<Button-4>", lambda _e: self._zoom(kind, 1.1))
        canvas.bind("<Button-5>", lambda _e: self._zoom(kind, 0.9))

    def create_bottom_area(self):
        bottom = ttk.LabelFrame(self.root, text="Journal et terminal")
        bottom.pack(fill="x", padx=12, pady=(0, 12))
        toolbar = ttk.Frame(bottom)
        toolbar.pack(fill="x", padx=8, pady=6)
        ttk.Label(toolbar, text="Échanges avec l'imprimante").pack(side="left")
        ttk.Label(toolbar, text="Hauteur logs").pack(side="left", padx=(18, 5))
        self.log_height_spin = ttk.Spinbox(
            toolbar, from_=3, to=30, increment=1, width=4, textvariable=self.log_height_var,
            command=self.apply_log_height
        )
        self.log_height_spin.pack(side="left")
        self.log_height_spin.bind("<Return>", lambda _e: self.apply_log_height())
        self.log_height_spin.bind("<FocusOut>", lambda _e: self.apply_log_height())
        ttk.Checkbutton(toolbar, text="Afficher logs / terminal", variable=self.logs_visible, command=self.toggle_bottom_area).pack(side="right")

        self.bottom_body = ttk.Frame(bottom)
        self.bottom_body.pack(fill="x", padx=8, pady=(0, 8))

        logs = ttk.LabelFrame(self.bottom_body, text="Logs imprimante")
        logs.pack(fill="x", pady=(0, 6))
        self.log = tk.Text(logs, height=self.log_height_var.get(), state="disabled", bg="#151719", fg="#d9dde1", insertbackground="white",
                           relief="flat", font=("TkFixedFont", 9))
        scroll = ttk.Scrollbar(logs, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        # La molette dans les logs doit rester dans cette zone et ne jamais
        # remonter vers le workspace/les conteneurs parents.
        self.log.bind("<MouseWheel>", self._log_wheel)
        self.log.bind("<Button-4>", self._log_wheel)
        self.log.bind("<Button-5>", self._log_wheel)

        terminal = ttk.Frame(self.bottom_body)
        terminal.pack(fill="x")
        ttk.Label(terminal, text="Terminal").pack(side="left", padx=(0, 8))
        self.gcode_entry = ttk.Entry(terminal)
        self.gcode_entry.pack(side="left", fill="x", expand=True)
        self.gcode_entry.bind("<Return>", lambda _e: self.send_gcode())
        self.gcode_entry.bind("<Up>", lambda _e: self.history_move(-1))
        self.gcode_entry.bind("<Down>", lambda _e: self.history_move(1))
        ttk.Button(terminal, text="Envoyer", command=self.send_gcode).pack(side="left", padx=6)
        ttk.Button(terminal, text="Effacer", command=self.clear_log).pack(side="left")

    def toggle_bottom_area(self):
        if self.logs_visible.get():
            self.bottom_body.pack(fill="x", padx=8, pady=(0, 8))
        else:
            self.bottom_body.pack_forget()
        self.root.update_idletasks()

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

    def update_controls(self):
        idle = self.connected and not self.busy and not self.running
        self.set_enabled(self.w_motion, idle)
        self.set_enabled(self.w_connection, not self.busy and not self.running)
        self.set_enabled([self.run_button], self.connected and idle and len(self.program) > 0)
        self.set_enabled([self.stop_program_button], self.running)
        self.set_enabled([self.return_button], self.connected and not self.busy and not self.running and self.program_start_position is not None)
        # Les arrêts restent disponibles même pendant une commande, un jog ou un programme.
        self.set_enabled([self.stop_button, self.emergency_button], self.connected)
        self.connect_button.configure(text="Déconnecter" if self.connected else "Connecter")
        if self.running:
            state = "Programme en cours" + (f" — {self.step_text}" if self.step_text else "")
        elif self.busy:
            state = "Commande en cours..."
        elif self.connected:
            state = "Connecté"
        else:
            state = "Déconnecté"
        self.status_var.set(state)

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
        self.position_var.set("X : --     Y : --     Z : --")
        self.update_controls()
        self.draw_3d()
        self.draw_program_preview()

    # ------------------------------------------------------------------
    # Télécommande continue
    # ------------------------------------------------------------------
    def get_speed(self):
        try:
            speed = int(float(str(self.speed_var.get()).replace(",", ".")))
        except ValueError:
            raise ValueError("La vitesse doit être un nombre.")
        if speed < 1 or speed > config.MAX_SPEED:
            raise ValueError(f"La vitesse doit être comprise entre 1 et {config.MAX_SPEED} mm/min.")
        return speed

    # API de compatibilité : déplacement discret utilisé par les anciens
    # scripts/tests. L'interface actuelle privilégie le jog continu.
    def move(self, axis, direction):
        if not self.connected or self.busy or self.running or self.jogging:
            return
        try:
            distance = float(self.step_var.get()) * direction
            speed = self.get_speed()
            axis = axis.upper()
            if axis not in "XYZ":
                raise ValueError(f"Axe invalide : {axis}")
            if self.printer.last_position is not None:
                target = self.printer.last_position[axis] + distance
                low, high = config.AXIS_LIMITS[axis]
                if not low <= target <= high:
                    message = f"{axis} = {target:g} mm hors limites ({low:g} à {high:g} mm)."
                    self.write_log(f"ERREUR : {message}")
                    self.status_var.set(f"Erreur : {message}")
                    return
        except ValueError as exc:
            self.write_log(f"ERREUR : {exc}")
            self.status_var.set(f"Erreur : {exc}")
            return
        self.run_async(lambda: self.printer.move_relative(axis, distance, speed), lambda _: self.show_position(self.printer.last_position))

    def goto(self):
        if not self.connected or self.busy or self.running:
            return
        try:
            x = parse_float(self.goto_vars["X"].get(), "X")
            y = parse_float(self.goto_vars["Y"].get(), "Y")
            z = parse_float(self.goto_vars["Z"].get(), "Z")
            speed = self.get_speed()
            self.printer.check_target({"X": x, "Y": y, "Z": z})
        except (ValueError, PrinterError) as exc:
            self.write_log(f"ERREUR : {exc}")
            self.status_var.set(f"Erreur : {exc}")
            return
        self.run_async(lambda: self.printer.move_absolute(x, y, z, speed), lambda _: self.show_position(self.printer.last_position))

    def start_jog(self, axis, direction):
        if not self.connected or self.running or self.busy or self.jogging:
            return
        try:
            speed = self.get_speed()
        except ValueError as exc:
            self.write_log(f"ERREUR : {exc}")
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
        # jog_stop() a déjà récupéré M114 sans M400. Aucun second relevé
        # différé ne doit recréer un temps d'attente après le relâchement.
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
        """HOME utilisateur : déplacement absolu vers X0 Y0 Z0."""
        if not self.connected or self.busy or self.running:
            return
        try:
            speed = self.get_speed()
        except ValueError as exc:
            self.write_log(f"ERREUR : {exc}")
            return
        self.write_log("HOME : déplacement vers X0 Y0 Z0")
        self.run_async(lambda: self.printer.move_absolute(0, 0, 0, speed), lambda _: self.show_position(self.printer.last_position))

    def recalibrate(self):
        """Référencement mécanique de l'imprimante (G28)."""
        if not self.connected or self.busy or self.running:
            return
        self.write_log("RECALIBRER : recherche des bornes mécaniques (G28)")
        self.run_async(self.printer.home, lambda _: self.show_position(self.printer.last_position))

    def show_position(self, position):
        if not position:
            return
        self.position_var.set(f"X : {position['X']:g} mm     Y : {position['Y']:g} mm     Z : {position['Z']:g} mm")
        self.draw_3d()
        self.draw_program_preview()

    def get_position(self):
        if not self.connected:
            return
        self.run_async(self.printer.get_position, self.show_position, busy=False)

    def refresh_position_periodically(self):
        """Compatibilité : relevé manuel unique, sans boucle automatique."""
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
        try:
            self.printer.quick_stop()
            self.write_log("STOP : mouvements interrompus (M410).")
            self._refresh_position_after_stop()
        except Exception as exc:
            self.write_log(f"ERREUR STOP : {exc}")

    def _refresh_position_after_stop(self):
        """Attend uniquement que la commande interrompue libère le port, puis M114."""
        def worker():
            for _ in range(40):
                position = self.printer.poll_position()
                if position is not None:
                    self.post(self.show_position, position)
                    return
                time.sleep(0.05)
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
        self.write_log("ARRÊT D'URGENCE exécuté. Redémarrer l'imprimante avant reconnexion.")

    # ------------------------------------------------------------------
    # Points
    # ------------------------------------------------------------------
    def change_point_axis(self, axis, direction):
        """Incrémente une coordonnée du point de travail avec le pas choisi."""
        try:
            step = parse_float(self.point_step_var.get(), "pas")
            if step is None or step <= 0:
                raise ValueError("Le pas doit être strictement positif.")
            value = parse_float(self.point_vars[axis].get(), axis)
            if value is None:
                value = 0.0
            value += direction * step
            self.point_vars[axis].set(f"{value:g}")
            self.draw_3d()
        except ValueError as exc:
            messagebox.showwarning("Point", str(exc))

    def _point_values_from_tree(self, iid):
        values = self.points_tree.item(iid, "values")
        return tuple(parse_float(v, a) for a, v in zip("XYZ", values[1:4]))

    def _point_items(self):
        return self.points_tree.get_children()

    # Compatibilité interne pour les acquisitions : l'interface n'expose plus
    # une liste de points.
    def previous_point(self):
        return

    def next_point(self):
        return

    def new_point(self):
        return

    def capture_point(self):
        if not self.connected or not self.printer.last_position:
            messagebox.showinfo("Point", "Aucune position machine disponible.")
            return
        p = self.printer.last_position
        for axis in "XYZ":
            self.point_vars[axis].set(f"{p[axis]:g}")
        self.draw_3d()

    def add_point_values(self, x, y, z):
        # Conserve les points issus de l'acquisition pour compatibilité/export,
        # sans transformer l'éditeur de point en liste utilisateur.
        index = len(self._point_items()) + 1
        self.points_tree.insert("", "end", values=(f"P{index}", f"{x:g}", f"{y:g}", f"{z:g}"))

    def apply_point(self):
        try:
            values = [parse_float(self.point_vars[a].get(), a) for a in "XYZ"]
            if any(v is None for v in values):
                raise ValueError("Les trois coordonnées sont obligatoires.")
            self.draw_3d()
        except ValueError as exc:
            messagebox.showwarning("Point", str(exc))

    def point_selected(self, _event=None):
        self.draw_3d()

    def delete_selected_point(self):
        # Conservé pour compatibilité, volontairement inutilisé dans l'interface.
        return

    def goto_selected_point(self):
        if not self.connected or self.busy or self.running:
            return
        try:
            x, y, z = (parse_float(self.point_vars[a].get(), a) for a in "XYZ")
            if None in (x, y, z):
                raise ValueError("Les trois coordonnées sont obligatoires.")
            speed = self.get_speed()
            self.printer.check_target({"X": x, "Y": y, "Z": z})
        except (ValueError, PrinterError) as exc:
            messagebox.showwarning("Point", str(exc))
            return
        self.run_async(lambda: self.printer.move_absolute(x, y, z, speed), lambda _: self.show_position(self.printer.last_position))

    def add_selected_to_program(self):
        try:
            x, y, z = (parse_float(self.point_vars[a].get(), a) for a in "XYZ")
            if None in (x, y, z):
                raise ValueError("Les trois coordonnées sont obligatoires.")
            wp = Waypoint(x=x, y=y, z=z, vitesse=self.get_speed() if self.connected else config.DEFAULT_SPEED)
        except (ValueError, ProgramError) as exc:
            messagebox.showwarning("Programme", str(exc))
            return
        self.program.add(wp)
        self.refresh_program_tree()
        self.draw_program_preview()

    # ------------------------------------------------------------------
    # Caméras 3D
    # ------------------------------------------------------------------
    def _camera(self, kind=None):
        # Une seule caméra pour les deux vues : X, Y, Z et les gestes restent
        # strictement identiques entre le schéma des points et celui du programme.
        return (self.view_yaw, self.view_pitch, self.view_zoom, self.view_pan_x, self.view_pan_y)

    def _set_camera(self, kind, values):
        self.view_yaw, self.view_pitch, self.view_zoom, self.view_pan_x, self.view_pan_y = values
        # Les deux vues partagent la même caméra et doivent se rafraîchir
        # immédiatement, même lorsque l'utilisateur agit sur une seule vue.
        if hasattr(self, "canvas3d"):
            self.draw_3d()
        if hasattr(self, "program_canvas"):
            self.draw_program_preview()

    def _view_press(self, kind, event, mode):
        yaw, pitch, zoom, panx, pany = self._camera(kind)
        self.view_drag = (kind, event.x, event.y, yaw, pitch, panx, pany, mode)

    def _view_drag(self, kind, event, mode):
        if not self.view_drag or self.view_drag[0] != kind:
            return
        _, x0, y0, yaw, pitch, panx, pany, start_mode = self.view_drag
        mode = start_mode
        if mode == "rotate":
            # Rotation naturelle de la caméra : tirer la souris vers la droite
            # fait pivoter la vue vers la droite (sens inverse du déplacement
            # angulaire interne). Même convention sur les deux vues.
            yaw -= (event.x - x0) * 0.012
            pitch = max(-1.35, min(1.35, pitch + (event.y - y0) * 0.012))
        else:
            # Pan direct : la scène suit le déplacement de la souris.
            # Tirer vers la droite déplace donc le schéma vers la droite.
            panx += (event.x - x0) * 0.9
            pany += (event.y - y0) * 0.9
        self._set_camera(kind, (yaw, pitch, self._camera(kind)[2], panx, pany))

    def _view_zoom_event(self, kind, event):
        self._zoom(kind, 1.1 if event.delta > 0 else 0.9)

    def _zoom(self, kind, factor):
        cam = list(self._camera(kind))
        cam[2] = max(0.25, min(5.0, cam[2] * factor))
        self._set_camera(kind, cam)

    def _project(self, x, y, z, width, height, kind="points", bounds=(0, 220, 0, 220, 0, 250)):
        yaw, pitch, zoom, panx, pany = self._camera(kind)
        xmin, xmax, ymin, ymax, zmin, zmax = bounds
        cx, cy, cz = (xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2
        x -= cx
        y -= cy
        z -= cz
        # Représentation graphique : X et Y sont échangés volontairement.
        # Cela ne change jamais les coordonnées envoyées à l'imprimante.
        cyaw, syaw = math.cos(yaw), math.sin(yaw)
        cp, sp = math.cos(pitch), math.sin(pitch)

        # Rotation horizontale autour de Z, après échange graphique X <-> Y.
        graph_x, graph_y = y, x
        screen_x = graph_x * cyaw - graph_y * syaw
        depth = graph_x * syaw + graph_y * cyaw

        # Inclinaison de la caméra : Z reste positif vers le haut.
        screen_up = z * cp - depth * sp

        span = max(xmax - xmin, ymax - ymin, zmax - zmin)
        scale = min(width, height) / (span * 1.55) * zoom
        return width / 2 + screen_x * scale + panx, height / 2 - screen_up * scale + pany

    def _draw_cube(self, canvas, width, height, kind, bounds=(0, 220, 0, 220, 0, 250)):
        xmin, xmax, ymin, ymax, zmin, zmax = bounds
        corners = [(x, y, z) for z in (zmin, zmax) for y in (ymin, ymax) for x in (xmin, xmax)]
        # index = z*4 + y*2 + x
        edges = [(0,1),(0,2),(1,3),(2,3),(4,5),(4,6),(5,7),(6,7),(0,4),(1,5),(2,6),(3,7)]
        projected = [self._project(*p, width, height, kind, bounds) for p in corners]
        for a, b in edges:
            canvas.create_line(*projected[a], *projected[b], fill="#b9bdc2")
        # Origine du repère sur le coin bas/avant du volume. X et Y suivent
        # ici des directions graphiques échangées.
        origin = self._project(xmin, ymin, zmin, width, height, kind, bounds)
        axis_specs = [
            ((xmin + (xmax - xmin) * 0.25, ymin, zmin), "X"),
            ((xmin, ymin + (ymax - ymin) * 0.25, zmin), "Y"),
            ((xmin, ymin, zmin + (zmax - zmin) * 0.25), "Z"),
        ]
        for end, label in axis_specs:
            p = self._project(*end, width, height, kind, bounds)
            canvas.create_line(*origin, *p, fill="#6f7479", width=2, arrow=tk.LAST)
            canvas.create_text(p[0] + 8, p[1], text=label, fill="#44484d", anchor="w", font=("TkDefaultFont", 9, "bold"))

    def draw_3d(self):
        if not hasattr(self, "canvas3d"):
            return
        c = self.canvas3d
        c.delete("all")
        w, h = max(c.winfo_width(), 360), max(c.winfo_height(), 220)
        bounds = (0, 220, 0, 220, 0, 250)
        self._draw_cube(c, w, h, "points", bounds)
        try:
            x, y, z = (parse_float(self.point_vars[a].get(), a) for a in "XYZ")
            if None in (x, y, z):
                raise ValueError
            xy = self._project(x, y, z, w, h, "points", bounds)
            c.create_oval(xy[0]-4, xy[1]-4, xy[0]+4, xy[1]+4, fill="#202326", outline="")
            c.create_text(xy[0]+12, xy[1]-10, text=f"({x:g}, {y:g}, {z:g})", anchor="sw", fill="#202326", font=("TkDefaultFont", 9, "bold"))
        except ValueError:
            pass
        if self.printer.last_position:
            p = self.printer.last_position
            xy = self._project(p["X"], p["Y"], p["Z"], w, h, "points", bounds)
            c.create_oval(xy[0]-5, xy[1]-5, xy[0]+5, xy[1]+5, outline="#1f4f7a", width=2)
            c.create_text(xy[0]+9, xy[1]+9, text="Machine", anchor="nw", fill="#1f4f7a")
        c.create_text(12, 12, text="Glisser : rotation    Maj + glisser : déplacement    Molette : zoom", anchor="nw", fill="#5c6268")

    # ------------------------------------------------------------------
    # Programmes
    # ------------------------------------------------------------------
    def edit_program_cell(self, event):
        """Ouvre un petit champ d'édition directement dans une cellule."""
        if self.running:
            return
        region = self.program_tree.identify_region(event.x, event.y)
        column_id = self.program_tree.identify_column(event.x)
        item = self.program_tree.identify_row(event.y)
        if region != "cell" or not item or column_id == "#1":
            return

        column_index = int(column_id[1:]) - 1
        field = ("x", "y", "z", "vitesse", "attente")[column_index - 1]
        bbox = self.program_tree.bbox(item, column_id)
        if not bbox:
            return

        self.finish_program_cell_edit(save=True)
        wp = self.program[self.program_tree.index(item)]
        value = getattr(wp, field)
        variable = tk.StringVar(value="" if value is None else f"{value:g}")
        editor = ttk.Entry(self.program_tree, textvariable=variable)
        editor.place(x=bbox[0], y=bbox[1], width=bbox[2], height=bbox[3])
        self._program_edit_entry = editor
        editor.focus_set()
        editor.select_range(0, "end")
        editor.bind("<Return>", lambda _e: self.finish_program_cell_edit(True, item, field, variable))
        editor.bind("<Escape>", lambda _e: self.finish_program_cell_edit(False))
        editor.bind("<FocusOut>", lambda _e: self.finish_program_cell_edit(True, item, field, variable))

    def update_program_cell_value(self, index, field, text):
        """Modifie une cellule du programme et reconstruit le waypoint."""
        if field not in ("x", "y", "z", "vitesse", "attente"):
            raise ValueError(f"Colonne non modifiable : {field}")
        wp = self.program[index]
        values = {name: getattr(wp, name) for name in ("x", "y", "z", "vitesse", "attente")}
        parsed = parse_float(str(text).strip().replace("—", ""), field)
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
            items = self.program_tree.get_children()
            if items:
                self.program_tree.selection_set(items[index])
        except (ValueError, ProgramError, tk.TclError) as exc:
            messagebox.showwarning("Programme", str(exc))

    def refresh_program_tree(self):
        if not hasattr(self, "program_tree"):
            return
        self.program_tree.delete(*self.program_tree.get_children())
        self.program.name = self.program_name_var.get().strip() or "Sans titre"
        for i, wp in enumerate(self.program, start=1):
            self.program_tree.insert("", "end", values=(i, fmt(wp.x), fmt(wp.y), fmt(wp.z), fmt(wp.vitesse), fmt(wp.attente)))
        self.draw_program_preview()
        self.update_controls()

    def program_selected(self, _event=None):
        selection = self.program_tree.selection()
        if selection:
            index = self.program_tree.index(selection[0])
            wp = self.program[index]
            for axis, value in (("X", wp.x), ("Y", wp.y), ("Z", wp.z)):
                self.point_vars[axis].set("" if value is None else f"{value:g}")
        self.draw_3d()
        self.draw_program_preview()

    def goto_program_selected(self):
        selection = self.program_tree.selection()
        if not selection or not self.connected or self.busy or self.running:
            return
        index = self.program_tree.index(selection[0])
        wp = self.program[index]
        try:
            speed = self.get_speed()
            self.run_async(lambda: self.printer.move_absolute(wp.x, wp.y, wp.z, speed), lambda _: self.show_position(self.printer.last_position))
        except ValueError as exc:
            messagebox.showwarning("Programme", str(exc))

    def add_current_to_program(self):
        if not self.printer.last_position:
            messagebox.showinfo("Programme", "Aucune position machine connue. Cette action nécessite une connexion.")
            return
        p = self.printer.last_position
        self.program.add(Waypoint(x=p["X"], y=p["Y"], z=p["Z"], vitesse=self.get_speed()))
        self.refresh_program_tree()

    def add_empty_program_point(self):
        """Ajoute une ligne vide qui pourra être remplie ultérieurement."""
        try:
            self.program.add(Waypoint())
        except ProgramError as exc:
            messagebox.showwarning("Programme", str(exc))
            return
        self.refresh_program_tree()
        items = self.program_tree.get_children()
        if items:
            self.program_tree.selection_set(items[-1])
            self.program_tree.see(items[-1])
            self.program_selected()

    def fill_selected_program_point(self):
        selection = self.program_tree.selection()
        if not selection:
            messagebox.showinfo("Programme", "Sélectionnez d'abord un point à remplir.")
            return
        try:
            x, y, z = (parse_float(self.point_vars[a].get(), a) for a in "XYZ")
            if any(v is None for v in (x, y, z)):
                raise ValueError("Les trois coordonnées sont obligatoires pour remplir le point.")
            speed = self.get_speed() if self.connected else config.DEFAULT_SPEED
            wp = Waypoint(x=x, y=y, z=z, vitesse=speed)
        except (ValueError, ProgramError) as exc:
            messagebox.showwarning("Programme", str(exc))
            return
        self.program.update(self.program_tree.index(selection[0]), wp)
        self.refresh_program_tree()

    def delete_program_point(self):
        selection = self.program_tree.selection()
        if not selection:
            return
        self.program.remove(self.program_tree.index(selection[0]))
        self.refresh_program_tree()

    def move_program_point(self, offset):
        selection = self.program_tree.selection()
        if not selection:
            return
        index = self.program_tree.index(selection[0])
        new_index = self.program.move(index, offset)
        self.refresh_program_tree()
        items = self.program_tree.get_children()
        if items:
            self.program_tree.selection_set(items[new_index])
            self.program_tree.see(items[new_index])

    def new_program(self):
        if self.running:
            return
        self.program = Program()
        self.program_path = None
        self.program_name_var.set("Sans titre")
        self.progress_var.set("")
        self.refresh_program_tree()

    def load_program(self):
        path = filedialog.askopenfilename(initialdir=config.PROGRAMS_DIR, filetypes=[("Programme JSON", "*.json")])
        if not path:
            return
        try:
            self.program = Program.load(path)
        except ProgramError as exc:
            messagebox.showerror("Programme", str(exc))
            return
        self.program_path = path
        self.program_name_var.set(self.program.name)
        self.refresh_program_tree()

    def save_program(self):
        self.program.name = self.program_name_var.get().strip() or "Sans titre"
        path = self.program_path
        if not path:
            os.makedirs(config.PROGRAMS_DIR, exist_ok=True)
            path = filedialog.asksaveasfilename(initialdir=config.PROGRAMS_DIR, defaultextension=".json", filetypes=[("Programme JSON", "*.json")])
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
        self.program.name = self.program_name_var.get().strip() or "Sans titre"
        self.program_start_position = dict(self.printer.last_position) if self.printer.last_position else None
        self.running = True
        self.step_text = ""
        self.update_controls()
        self.write_log(f"Programme lancé : {self.program.name}")
        threading.Thread(target=self._run_program_worker, daemon=True).start()

    def _run_program_worker(self):
        self.runner.run(self.program)

    def step_started(self, index, waypoint):
        self.step_text = f"Point {index + 1}/{len(self.program)}"
        self.progress_var.set(self.step_text)
        self.draw_program_preview()
        self.update_controls()

    def runner_reached(self, index, waypoint, position):
        self.show_position(position)
        # L'acquisition reste masquée dans l'interface, mais le moteur de
        # mesure continue de pouvoir associer une mesure à chaque point.
        try:
            if self.measure_var.get():
                self.sync_acquisition_parameters()
                provider = self.acquisition.position_provider
                self.acquisition.position_provider = lambda p=dict(position): p
                try:
                    self.acquisition.measure(index=index)
                finally:
                    self.acquisition.position_provider = provider
                if not any(
                    abs(float(self.points_tree.item(i, "values")[1]) - position["X"]) < 1e-9 and
                    abs(float(self.points_tree.item(i, "values")[2]) - position["Y"]) < 1e-9 and
                    abs(float(self.points_tree.item(i, "values")[3]) - position["Z"]) < 1e-9
                    for i in self.points_tree.get_children()
                ):
                    self.add_point_values(position["X"], position["Y"], position["Z"])
        except Exception as exc:
            self.write_log(f"ERREUR ACQUISITION : {exc}")

    def run_state_changed(self, state, message=""):
        if state in (FINISHED, STOPPED, ERROR):
            self.running = False
            if state == FINISHED:
                self.progress_var.set("Programme terminé")
            elif state == STOPPED:
                self.progress_var.set("Programme stoppé")
            else:
                self.progress_var.set(f"Erreur : {message}")
            self.write_log(f"Programme : {state}" + (f" — {message}" if message else ""))
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
        self.run_async(lambda: self.printer.move_absolute(p["X"], p["Y"], p["Z"], self.get_speed()), lambda _: self.show_position(self.printer.last_position))

    def draw_program_preview(self):
        if not hasattr(self, "program_canvas"):
            return
        c = self.program_canvas
        c.delete("all")
        w, h = max(c.winfo_width(), 300), max(c.winfo_height(), 300)
        bounds = (0, 220, 0, 220, 0, 250)
        self._draw_cube(c, w, h, "program", bounds)
        points = []
        for i, wp in enumerate(self.program, start=1):
            if wp.x is None or wp.y is None or wp.z is None:
                continue
            xy = self._project(wp.x, wp.y, wp.z, w, h, "program", bounds)
            points.append((i, xy))
        for a, b in zip(points, points[1:]):
            c.create_line(*a[1], *b[1], fill="#40454a", width=3)
        selected = self.program_tree.selection() if hasattr(self, "program_tree") else ()
        selected_index = self.program_tree.index(selected[0]) + 1 if selected else None
        for i, xy in points:
            r = 7 if i == selected_index else 5
            c.create_oval(xy[0]-r, xy[1]-r, xy[0]+r, xy[1]+r, fill="#202326" if i == selected_index else "#666b70", outline="")
            c.create_text(xy[0]+10, xy[1]-9, text=f"P{i}", anchor="sw", fill="#34383d")
        if self.program_start_position:
            p = self.program_start_position
            xy = self._project(p["X"], p["Y"], p["Z"], w, h, "program", bounds)
            c.create_oval(xy[0]-5, xy[1]-5, xy[0]+5, xy[1]+5, outline="#1f4f7a", width=2)
            c.create_text(xy[0]+9, xy[1]+9, text="Départ", anchor="nw", fill="#1f4f7a")
        c.create_text(12, 12, text="Trajectoire XYZ — glisser : rotation    Maj + glisser : déplacement", anchor="nw", fill="#5c6268")

    # ------------------------------------------------------------------
    # Acquisition masquée / compatibilité
    # ------------------------------------------------------------------
    def sync_acquisition_parameters(self):
        try:
            self.acquisition.sample_rate = float(str(self.rate_var.get()).replace(",", "."))
            self.acquisition.average_samples = max(1, int(self.average_var.get()))
            return True
        except (ValueError, TypeError):
            return False

    def toggle_continuous(self):
        if self.acquisition.is_running:
            self.acquisition.stop()
        else:
            self.sync_acquisition_parameters()
            self.acquisition.start()

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------
    def _log_wheel(self, event):
        if getattr(event, "num", None) == 4:
            units = -3
        elif getattr(event, "num", None) == 5:
            units = 3
        else:
            delta = getattr(event, "delta", 0)
            units = -3 if delta > 0 else 3
        self.log.yview_scroll(units, "units")
        return "break"

    def apply_log_height(self):
        try:
            height = max(3, min(30, int(self.log_height_var.get())))
        except (TypeError, ValueError, tk.TclError):
            height = 7
        self.log_height_var.set(height)
        if hasattr(self, "log"):
            self.log.configure(height=height)
        self.root.update_idletasks()

    def write_log(self, text):
        if not hasattr(self, "log"):
            return
        stamp = time.strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"{stamp}  {text}\n")
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
