import serial
import time
import threading

class Printer:

    def __init__(self, port, baudrate=115200):

        self.port_name = port
        self.baudrate = baudrate
        self.serial = None

        # Une seule commande à la fois.
        self.command_lock = threading.Lock()

    # -------------------------------------------------
    # Connexion
    # -------------------------------------------------

    def connect(self):

        if self.serial and self.serial.is_open:
            return

        self.serial = serial.Serial(
            port=None,
            baudrate=self.baudrate,
            timeout=1,
            dsrdtr=False,
            rtscts=False
        )

        # Évite les changements automatiques des lignes
        # de contrôle pouvant provoquer un reset.
        self.serial.dtr = False
        self.serial.rts = False

        self.serial.port = self.port_name
        self.serial.open()

        # Laisser Marlin démarrer
        time.sleep(3)

        # Vider les messages présents
        self.read_available()

    # -------------------------------------------------
    # Déconnexion
    # -------------------------------------------------

    def disconnect(self):

        with self.command_lock:

            if self.serial:

                if self.serial.is_open:
                    self.serial.close()

                self.serial = None

    # -------------------------------------------------
    # État
    # -------------------------------------------------

    def is_connected(self):

        return (
            self.serial is not None
            and self.serial.is_open
        )

    # -------------------------------------------------
    # Lecture des données disponibles
    # -------------------------------------------------

    def read_available(self):

        messages = []

        if not self.is_connected():
            return messages

        while self.serial.in_waiting:

            line = self.serial.readline().decode(
                "utf-8",
                errors="replace"
            ).strip()

            if line:
                messages.append(line)

        return messages

    # -------------------------------------------------
    # Envoi d'une commande
    # -------------------------------------------------

    def send_command(self, command, timeout=30):

        with self.command_lock:

            return self._send_command_locked(
                command,
                timeout
            )

    # -------------------------------------------------
    # Envoi interne
    # -------------------------------------------------

    def _send_command_locked(self, command, timeout=30):

        if not self.is_connected():
            raise RuntimeError(
                "Imprimante non connectée."
            )

        command = command.strip()

        if not command:
            return []

        self.serial.write(
            (command + "\n").encode("ascii")
        )

        self.serial.flush()

        responses = []

        start = time.time()

        while time.time() - start < timeout:

            if self.serial.in_waiting:

                line = self.serial.readline().decode(
                    "utf-8",
                    errors="replace"
                ).strip()

                if not line:
                    continue

                responses.append(line)

                # Marlin peut envoyer :
                #
                # busy: processing
                #
                # avant le OK.

                if line.lower() == "ok":
                    return responses

            else:

                time.sleep(0.01)

        raise TimeoutError(
            f"Aucun 'ok' reçu pour la commande : {command}"
        )

    # -------------------------------------------------
    # HOME
    # -------------------------------------------------

    def home(self):

        return self.send_command(
            "G28",
            timeout=60
        )

    # -------------------------------------------------
    # Position
    # -------------------------------------------------

    def get_position(self):

        return self.send_command(
            "M114"
        )

    # -------------------------------------------------
    # Déplacement relatif
    # -------------------------------------------------

    def move_relative(
        self,
        axis,
        distance,
        speed=None
    ):

        if speed is None:
            speed = 300

        with self.command_lock:

            responses = []

            responses += self._send_command_locked(
                "G91"
            )

            responses += self._send_command_locked(
                f"G1 {axis}{distance:g} F{speed}"
            )

            responses += self._send_command_locked(
                "G90"
            )

            return responses

    # -------------------------------------------------
    # Déplacement absolu
    # -------------------------------------------------

    def move_absolute(
        self,
        x=None,
        y=None,
        z=None,
        speed=None
    ):

        if speed is None:
            speed = 300

        command = "G90\nG1"

        if x is not None:
            command += f" X{x:g}"

        if y is not None:
            command += f" Y{y:g}"

        if z is not None:
            command += f" Z{z:g}"

        command += f" F{speed}"

        with self.command_lock:

            responses = []

            responses += self._send_command_locked(
                "G90"
            )

            responses += self._send_command_locked(
                command.split("\n")[1]
            )

            return responses

    # -------------------------------------------------
    # Informations firmware
    # -------------------------------------------------

    def firmware_info(self):

        return self.send_command(
            "M115"
        )