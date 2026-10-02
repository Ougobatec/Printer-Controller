import tkinter as tk
from tkinter import ttk, messagebox
import threading

import config as config
from printer import Printer

class EnderGUI:

    def __init__(self, root):

        self.root = root

        self.root.title("Contrôle Ender")
        self.root.geometry("700x800")

        self.printer = Printer(
            config.PORT,
            config.BAUDRATE
        )

        self.connected = False
        self.busy = False

        # -------------------------------------------------
        # Variables
        # -------------------------------------------------

        self.port_var = tk.StringVar(
            value=config.PORT
        )

        self.step_var = tk.DoubleVar(
            value=1.0
        )

        self.speed_mode_var = tk.StringVar(
            value="Défaut"
        )

        self.speed_var = tk.IntVar(
            value=config.DEFAULT_SPEED
        )

        self.position_var = tk.StringVar(
            value="X : --     Y : --     Z : --"
        )

        self.status_var = tk.StringVar(
            value="Déconnecté"
        )

        # -------------------------------------------------
        # Interface
        # -------------------------------------------------

        self.create_interface()

        self.root.protocol(
            "WM_DELETE_WINDOW",
            self.close
        )

    # -------------------------------------------------
    # Interface
    # -------------------------------------------------

    def create_interface(self):

        # ===============================
        # CONNEXION
        # ===============================

        connection_frame = ttk.LabelFrame(
            self.root,
            text="Connexion"
        )

        connection_frame.pack(
            fill="x",
            padx=10,
            pady=10
        )

        ttk.Label(
            connection_frame,
            text="Port :"
        ).grid(
            row=0,
            column=0,
            padx=5,
            pady=5
        )

        ttk.Entry(
            connection_frame,
            textvariable=self.port_var,
            width=12
        ).grid(
            row=0,
            column=1,
            padx=5
        )

        self.connect_button = ttk.Button(
            connection_frame,
            text="Connecter",
            command=self.toggle_connection
        )

        self.connect_button.grid(
            row=0,
            column=2,
            padx=5
        )

        ttk.Label(
            connection_frame,
            text="État :"
        ).grid(
            row=1,
            column=0,
            padx=5
        )

        ttk.Label(
            connection_frame,
            textvariable=self.status_var
        ).grid(
            row=1,
            column=1,
            columnspan=2,
            sticky="w"
        )

        # ===============================
        # CONTRÔLE XYZ
        # ===============================

        control_frame = ttk.LabelFrame(
            self.root,
            text="Contrôle XYZ"
        )

        control_frame.pack(
            padx=10,
            pady=10
        )

        ttk.Button(
            control_frame,
            text="X +",
            width=10,
            command=lambda: self.move("X", 1)
        ).grid(
            row=0,
            column=1,
            padx=5,
            pady=5
        )

        ttk.Button(
            control_frame,
            text="Y -",
            width=10,
            command=lambda: self.move("Y", -1)
        ).grid(
            row=1,
            column=0,
            padx=5,
            pady=5
        )

        self.home_button = ttk.Button(
            control_frame,
            text="HOME",
            width=10,
            command=self.home
        )

        self.home_button.grid(
            row=1,
            column=1,
            padx=5,
            pady=5
        )

        ttk.Button(
            control_frame,
            text="Y +",
            width=10,
            command=lambda: self.move("Y", 1)
        ).grid(
            row=1,
            column=2,
            padx=5,
            pady=5
        )

        ttk.Button(
            control_frame,
            text="X -",
            width=10,
            command=lambda: self.move("X", -1)
        ).grid(
            row=2,
            column=1,
            padx=5,
            pady=5
        )

        ttk.Button(
            control_frame,
            text="Z +",
            width=10,
            command=lambda: self.move("Z", 1)
        ).grid(
            row=3,
            column=0,
            padx=5,
            pady=5
        )

        ttk.Button(
            control_frame,
            text="Z -",
            width=10,
            command=lambda: self.move("Z", -1)
        ).grid(
            row=3,
            column=2,
            padx=5,
            pady=5
        )

        # ===============================
        # PAS
        # ===============================

        step_frame = ttk.LabelFrame(
            self.root,
            text="Pas de déplacement"
        )

        step_frame.pack(
            fill="x",
            padx=10,
            pady=10
        )

        self.step_combo = ttk.Combobox(
            step_frame,
            textvariable=self.step_var,
            values=config.STEP_SIZES,
            state="readonly",
            width=10
        )

        self.step_combo.pack(
            padx=10,
            pady=10
        )

        # ===============================
        # VITESSE
        # ===============================

        speed_frame = ttk.LabelFrame(
            self.root,
            text="Vitesse"
        )

        speed_frame.pack(
            fill="x",
            padx=10,
            pady=10
        )

        ttk.Label(
            speed_frame,
            text="Mode :"
        ).grid(
            row=0,
            column=0,
            padx=5,
            pady=5
        )

        self.speed_mode_combo = ttk.Combobox(
            speed_frame,
            textvariable=self.speed_mode_var,
            values=["Défaut", "Personnalisée"],
            state="readonly",
            width=15
        )

        self.speed_mode_combo.grid(
            row=0,
            column=1,
            padx=5
        )

        self.speed_mode_combo.bind(
            "<<ComboboxSelected>>",
            self.speed_mode_changed
        )

        ttk.Label(
            speed_frame,
            text="Vitesse :"
        ).grid(
            row=0,
            column=2,
            padx=5
        )

        self.speed_combo = ttk.Combobox(
            speed_frame,
            textvariable=self.speed_var,
            values=config.SPEED_SIZES,
            state="readonly",
            width=10
        )

        self.speed_combo.grid(
            row=0,
            column=3,
            padx=5
        )

        self.speed_label = ttk.Label(
            speed_frame,
            text=f"(défaut : {config.DEFAULT_SPEED} mm/min)"
        )

        self.speed_label.grid(
            row=0,
            column=4,
            padx=5
        )

        self.update_speed_widgets()

        # ===============================
        # POSITION
        # ===============================

        position_frame = ttk.LabelFrame(
            self.root,
            text="Position"
        )

        position_frame.pack(
            fill="x",
            padx=10,
            pady=10
        )

        ttk.Label(
            position_frame,
            textvariable=self.position_var,
            font=("Arial", 14)
        ).pack(
            padx=10,
            pady=15
        )

        ttk.Button(
            position_frame,
            text="Lire position (M114)",
            command=self.get_position
        ).pack(
            pady=(0, 10)
        )

        # ===============================
        # G-CODE
        # ===============================

        gcode_frame = ttk.LabelFrame(
            self.root,
            text="Commande G-code"
        )

        gcode_frame.pack(
            fill="x",
            padx=10,
            pady=10
        )

        self.gcode_entry = ttk.Entry(
            gcode_frame
        )

        self.gcode_entry.pack(
            side="left",
            fill="x",
            expand=True,
            padx=5,
            pady=5
        )

        self.gcode_button = ttk.Button(
            gcode_frame,
            text="Envoyer",
            command=self.send_gcode
        )

        self.gcode_button.pack(
            side="right",
            padx=5
        )

        # ===============================
        # JOURNAL
        # ===============================

        log_frame = ttk.LabelFrame(
            self.root,
            text="Journal"
        )

        log_frame.pack(
            fill="both",
            expand=True,
            padx=10,
            pady=10
        )

        self.log = tk.Text(
            log_frame,
            height=12,
            state="disabled"
        )

        self.log.pack(
            fill="both",
            expand=True
        )

    # -------------------------------------------------
    # Vitesse
    # -------------------------------------------------

    def speed_mode_changed(self, event=None):

        self.update_speed_widgets()

    def update_speed_widgets(self):

        if self.speed_mode_var.get() == "Défaut":

            self.speed_combo.configure(
                state="disabled"
            )

        else:

            self.speed_combo.configure(
                state="readonly"
            )

    def get_speed(self):

        if self.speed_mode_var.get() == "Défaut":

            return config.DEFAULT_SPEED

        return self.speed_var.get()

    # -------------------------------------------------
    # Journal
    # -------------------------------------------------

    def write_log(self, text):

        self.log.configure(
            state="normal"
        )

        self.log.insert(
            "end",
            text + "\n"
        )

        self.log.see("end")

        self.log.configure(
            state="disabled"
        )

    # -------------------------------------------------
    # État occupation
    # -------------------------------------------------

    def set_busy(self, busy):

        self.busy = busy

        if busy:

            self.status_var.set(
                "Commande en cours..."
            )

        else:

            if self.connected:
                self.status_var.set("Connecté")

    # -------------------------------------------------
    # Connexion
    # -------------------------------------------------

    def toggle_connection(self):

        if self.busy:
            return

        if self.connected:

            self.disconnect()

        else:

            self.connect()

    def connect(self):

        port = self.port_var.get().strip()

        self.write_log(
            f"Connexion à {port}..."
        )

        self.set_busy(True)

        def worker():

            try:

                self.printer.port_name = port
                self.printer.connect()

                self.root.after(
                    0,
                    self.connection_success
                )

            except Exception as e:

                self.root.after(
                    0,
                    lambda e=e:
                    self.connection_error(str(e))
                )

        threading.Thread(
            target=worker,
            daemon=True
        ).start()

    def connection_success(self):

        self.connected = True

        self.status_var.set(
            "Connecté"
        )

        self.connect_button.configure(
            text="Déconnecter"
        )

        self.set_busy(False)

        self.write_log(
            "Connexion réussie."
        )

        self.get_position()

    def connection_error(self, error):

        self.connected = False

        self.set_busy(False)

        self.write_log(
            f"ERREUR : {error}"
        )

        messagebox.showerror(
            "Erreur de connexion",
            error
        )

    def disconnect(self):

        self.printer.disconnect()

        self.connected = False

        self.status_var.set(
            "Déconnecté"
        )

        self.connect_button.configure(
            text="Connecter"
        )

        self.write_log(
            "Connexion fermée."
        )

    # -------------------------------------------------
    # HOME
    # -------------------------------------------------

    def home(self):

        if not self.connected or self.busy:
            return

        self.set_busy(True)

        self.write_log(
            "HOME en cours..."
        )

        def worker():

            try:

                responses = self.printer.home()

                for response in responses:

                    self.root.after(
                        0,
                        lambda r=response:
                        self.write_log("> " + r)
                    )

                self.root.after(
                    0,
                    self.home_finished
                )

            except Exception as e:

                self.root.after(
                    0,
                    lambda e=e:
                    self.command_error(str(e))
                )

        threading.Thread(
            target=worker,
            daemon=True
        ).start()

    def home_finished(self):

        self.write_log(
            "HOME terminé."
        )

        self.set_busy(False)

        self.get_position()

    # -------------------------------------------------
    # Déplacement
    # -------------------------------------------------

    def move(self, axis, direction):

        if not self.connected or self.busy:
            return

        step = self.step_var.get()

        distance = step * direction

        speed = self.get_speed()

        self.set_busy(True)

        self.write_log(
            f"Déplacement {axis} {distance:g} mm "
            f"à {speed} mm/min"
        )

        def worker():

            try:

                responses = self.printer.move_relative(
                    axis,
                    distance,
                    speed
                )

                for response in responses:

                    self.root.after(
                        0,
                        lambda r=response:
                        self.write_log("> " + r)
                    )

                self.root.after(
                    0,
                    self.move_finished
                )

            except Exception as e:

                self.root.after(
                    0,
                    lambda e=e:
                    self.command_error(str(e))
                )

        threading.Thread(
            target=worker,
            daemon=True
        ).start()

    def move_finished(self):

        self.set_busy(False)

        self.get_position()

    # -------------------------------------------------
    # Position
    # -------------------------------------------------

    def get_position(self):

        if not self.connected or self.busy:
            return

        self.set_busy(True)

        def worker():

            try:

                responses = self.printer.get_position()

                for response in responses:

                    self.root.after(
                        0,
                        lambda r=response:
                        self.write_log("> " + r)
                    )

                    if response.startswith("X:"):

                        self.root.after(
                            0,
                            lambda r=response:
                            self.update_position(r)
                        )

                self.root.after(
                    0,
                    lambda: self.set_busy(False)
                )

            except Exception as e:

                self.root.after(
                    0,
                    lambda e=e:
                    self.command_error(str(e))
                )

        threading.Thread(
            target=worker,
            daemon=True
        ).start()

    def update_position(self, response):

        try:

            parts = response.split()

            x = parts[0].split(":")[1]
            y = parts[1].split(":")[1]
            z = parts[2].split(":")[1]

            self.position_var.set(
                f"X : {x} mm     "
                f"Y : {y} mm     "
                f"Z : {z} mm"
            )

        except Exception:
            pass

    # -------------------------------------------------
    # G-code
    # -------------------------------------------------

    def send_gcode(self):

        if not self.connected or self.busy:
            return

        command = self.gcode_entry.get().strip()

        if not command:
            return

        self.set_busy(True)

        self.write_log(
            f"> {command}"
        )

        def worker():

            try:

                responses = self.printer.send_command(
                    command
                )

                for response in responses:

                    self.root.after(
                        0,
                        lambda r=response:
                        self.write_log("> " + r)
                    )

                self.root.after(
                    0,
                    lambda: self.set_busy(False)
                )

            except Exception as e:

                self.root.after(
                    0,
                    lambda e=e:
                    self.command_error(str(e))
                )

        threading.Thread(
            target=worker,
            daemon=True
        ).start()

    # -------------------------------------------------
    # Erreur commande
    # -------------------------------------------------

    def command_error(self, error):

        self.write_log(
            f"ERREUR : {error}"
        )

        self.set_busy(False)

    # -------------------------------------------------
    # Fermeture
    # -------------------------------------------------

    def close(self):

        try:

            self.printer.disconnect()

        except Exception:
            pass

        self.root.destroy()

def start_gui():

    root = tk.Tk()

    app = EnderGUI(root)

    root.mainloop()