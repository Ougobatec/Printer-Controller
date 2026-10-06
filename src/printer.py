"""Communication série avec l'imprimante (firmware Marlin)."""

import threading
import time

import config
import gcode
import motion


class PrinterError(Exception):
    """Erreur renvoyée par l'imprimante ou refus de sécurité."""


def list_ports():
    """Liste les ports série disponibles (ex. ["COM3", "COM11"])."""

    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []

    return sorted(p.device for p in lp.comports())


def open_serial(port, baudrate):
    """Ouvre un port série sans provoquer de reset de la carte."""

    import serial

    ser = serial.Serial(
        port=None,
        baudrate=baudrate,
        timeout=1,
        dsrdtr=False,
        rtscts=False
    )

    # Évite les changements automatiques des lignes
    # de contrôle pouvant provoquer un reset.
    ser.dtr = False
    ser.rts = False

    ser.port = port
    ser.open()

    return ser


class Printer:

    def __init__(
        self,
        port,
        baudrate=115200,
        serial_factory=None,
        startup_delay=None,
        limits=None
    ):

        self.port_name = port
        self.baudrate = baudrate
        self.serial = None

        # Fabrique du port série, conservée injectable pour l’architecture interne.
        self.serial_factory = serial_factory or open_serial

        self.startup_delay = (
            config.STARTUP_DELAY
            if startup_delay is None
            else startup_delay
        )

        self.limits = limits if limits is not None else config.AXIS_LIMITS

        # Dernière position connue {"X":.., "Y":.., "Z":..} ou None.
        self.last_position = None
        self.homed = False

        # Suivi de trajectoire (motion.MotionTracker) : alimenté à chaque
        # déplacement pour connaître la position de la tête PENDANT le
        # mouvement sans interroger le firmware.
        self.motion = None

        # Appelé avec ("TX" | "RX", texte) pour chaque ligne échangée.
        # Attention : appelé depuis le thread qui envoie la commande.
        self.on_traffic = None

        # Une seule commande bloquante à la fois.
        self.command_lock = threading.Lock()
        # Le jog est volontairement hors de command_lock : M410 doit pouvoir
        # interrompre immédiatement un mouvement en cours.
        self.jog_lock = threading.Lock()

        # Interrompt l'attente d'une réponse (arrêt, déconnexion).
        self._abort = threading.Event()

    # -------------------------------------------------
    # Connexion
    # -------------------------------------------------

    def connect(self):

        if self.is_connected():
            return

        self._abort.clear()

        self.serial = self.serial_factory(
            self.port_name,
            self.baudrate
        )

        self.last_position = None
        self.homed = False

        # Laisser Marlin démarrer
        time.sleep(self.startup_delay)

        # Vider les messages présents
        self.read_available()

    def disconnect(self):

        # Libère une éventuelle commande en attente de réponse.
        self._abort.set()

        with self.command_lock:

            if self.serial:

                try:
                    if self.serial.is_open:
                        self.serial.close()
                finally:
                    self.serial = None

        self.last_position = None
        self.homed = False
        if self.motion:
            self.motion.reset()

    def is_connected(self):

        return (
            self.serial is not None
            and self.serial.is_open
        )

    # -------------------------------------------------
    # Lecture / écriture bas niveau
    # -------------------------------------------------

    def _emit(self, direction, text):

        callback = self.on_traffic

        if callback:
            try:
                callback(direction, text)
            except Exception:
                pass

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
                self._emit("RX", line)

        return messages

    def _write_raw(self, command):
        """Écrit une commande sans attendre ni prendre le verrou."""

        if not self.is_connected():
            raise PrinterError("Imprimante non connectée.")

        self.serial.write((command + "\n").encode("ascii"))
        self.serial.flush()
        self._emit("TX", command)

    # -------------------------------------------------
    # Envoi d'une commande
    # -------------------------------------------------

    def send_command(self, command, timeout=None):
        """Envoie une commande et attend le `ok` de Marlin.

        Retourne la liste des lignes reçues (ok compris).
        """

        with self.command_lock:

            self._abort.clear()

            return self._send_command_locked(command, timeout)

    def _send_command_locked(self, command, timeout=None):

        if timeout is None:
            timeout = config.COMMAND_TIMEOUT

        if not self.is_connected():
            raise PrinterError("Imprimante non connectée.")

        command = command.strip()

        if not command:
            return []

        try:
            payload = (command + "\n").encode("ascii")
        except UnicodeEncodeError:
            raise PrinterError(
                "Le G-code ne doit contenir que des caractères ASCII."
            )

        # Élimine d'éventuelles réponses tardives d'une commande
        # précédente (timeout, arrêt) pour ne pas se désynchroniser.
        self.serial.reset_input_buffer()

        self.serial.write(payload)
        self.serial.flush()
        self._emit("TX", command)

        responses = []
        error = None

        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:

            if self._abort.is_set():
                raise PrinterError("Commande interrompue.")

            raw = self.serial.readline()

            if not raw:
                continue

            if self._abort.is_set():
                raise PrinterError("Commande interrompue.")

            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            responses.append(line)
            self._emit("RX", line)

            parsed = gcode.parse_position(line)
            if parsed:
                self.last_position = parsed

            lowered = line.lower()
            if lowered == "ok" or lowered.startswith("ok "):
                if error:
                    raise PrinterError(error)
                return responses

            if lowered.startswith("error:"):
                error = line
                if "halted" in lowered or "kill()" in lowered:
                    raise PrinterError(line)

            elif "busy" in lowered:
                deadline = time.monotonic() + timeout

        if error:
            raise PrinterError(error)

        raise TimeoutError(
            f"Aucun 'ok' reçu pour la commande : {command}"
        )

    # -------------------------------------------------
    # Jog continu
    # -------------------------------------------------

    def jog_start(self):
        with self.jog_lock:
            self._write_raw("G91")

    def jog_move(self, axis, distance, speed=None):
        """Lance un seul mouvement continu ; M410 l'interrompt au relâchement."""
        axis = axis.upper()
        if axis not in gcode.AXES:
            raise PrinterError(f"Axe invalide : {axis!r}")
        self._check_speed(speed)
        with self.jog_lock:
            p0 = dict(self.last_position) if self.last_position else None
            self._write_raw(f"G1 {axis}{distance:g} F{gcode._speed(speed):g}")
            if self.motion and p0:
                self.motion.start(p0, {axis: p0[axis] + distance}, gcode._speed(speed))

    def _send_raw_and_wait_ok(self, command, timeout=2.0):
        """Envoie une commande hors command_lock et attend son ``ok``."""
        if not self.is_connected():
            raise PrinterError("Imprimante non connectée.")
        self._write_raw(command)
        deadline = time.monotonic() + timeout
        position = None
        while time.monotonic() < deadline:
            raw = self.serial.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            self._emit("RX", line)
            parsed = gcode.parse_position(line)
            if parsed:
                position = parsed
                self.last_position = parsed
            lowered = line.lower()
            if lowered == "ok" or lowered.startswith("ok "):
                return position
            if lowered.startswith("error:"):
                raise PrinterError(line)
        raise TimeoutError(f"Aucun 'ok' reçu pour la commande : {command}")

    def _send_m114_and_wait(self, timeout=3.0):
        """Envoie un seul M114 et attend réellement la position et le ok."""
        if not self.is_connected():
            raise PrinterError("Imprimante non connectée.")
        self.serial.reset_input_buffer()
        self._write_raw(gcode.get_position())
        deadline = time.monotonic() + timeout
        position = None
        got_ok = False
        while time.monotonic() < deadline:
            raw = self.serial.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            self._emit("RX", line)
            parsed = gcode.parse_position(line)
            if parsed:
                position = parsed
                self.last_position = parsed
            lowered = line.lower()
            if lowered == "ok" or lowered.startswith("ok "):
                got_ok = True
                if position is not None:
                    return position
            elif lowered.startswith("error:"):
                raise PrinterError(line)
        if position is not None:
            return position
        if got_ok:
            raise PrinterError("M114 a répondu ok sans fournir la position.")
        raise TimeoutError("Aucune position reçue pour M114.")

    def jog_stop(self):
        """Arrête le jog, repasse en absolu, puis lit UNE fois la position."""
        with self.jog_lock:
            if not self.is_connected():
                return None
            self._abort.set()
            if self.motion:
                self.motion.abort()
            try:
                # M410 doit être confirmé avant d'interroger M114 : sinon le
                # firmware peut encore être occupé et ignorer/reporter plus
                # tard la réponse à M114.
                self._send_raw_and_wait_ok(gcode.quick_stop(), timeout=5.0)
                self._send_raw_and_wait_ok("G90", timeout=2.0)
                self._abort.clear()
                position = self._send_m114_and_wait(timeout=3.0)
                if self.motion:
                    self.motion.settle(position)
                return position
            except Exception:
                self._abort.clear()
                if self.motion:
                    self.motion.settle(self.last_position)
                return self.last_position

    # -------------------------------------------------
    # Arrêts
    # -------------------------------------------------

    def quick_stop(self):
        """Arrête immédiatement puis lit une seule fois la position réelle."""

        if not self.is_connected():
            return None

        # M410 doit partir immédiatement, même si une autre commande est
        # actuellement bloquée dans une lecture série.
        self._abort.set()
        self._write_raw(gcode.quick_stop())
        if self.motion:
            self.motion.abort()

        # L'ancienne commande doit avoir libéré le lecteur série avant que
        # cette méthode ne lise G90/M114.
        with self.command_lock:
            try:
                self._abort.clear()

                # La réponse éventuelle de M410 peut avoir été consommée par
                # la commande interrompue : on ne dépend donc pas de son ok.
                self._send_command_locked("G90", timeout=2.0)

                # Une seule lecture de position après l'arrêt effectif.
                position = self._send_m114_and_wait(timeout=3.0)
                if self.motion:
                    self.motion.settle(position)
                return position

            except Exception:
                self._abort.clear()
                if self.motion:
                    self.motion.settle(self.last_position)
                return self.last_position

    def emergency_stop(self):
        """Arrêt d'urgence (M112) : un redémarrage de la carte est nécessaire."""

        self._abort.set()
        self._write_raw(gcode.emergency_stop())
        self.last_position = None
        self.homed = False
        if self.motion:
            self.motion.reset()

    # -------------------------------------------------
    # Limites de sécurité
    # -------------------------------------------------

    def check_target(self, target):
        """Vérifie qu'une cible {"X":.., ...} est dans les limites."""

        if not config.ENFORCE_LIMITS:
            return

        for axis, value in target.items():

            if value is None or axis not in self.limits:
                continue

            low, high = self.limits[axis]

            if value < low - 1e-6 or value > high + 1e-6:
                raise PrinterError(
                    f"{axis} = {value:g} mm hors limites "
                    f"({low:g} à {high:g} mm)."
                )

    # -------------------------------------------------
    # Position
    # -------------------------------------------------

    def _refresh_position_locked(self, wait_for_moves=False):
        if wait_for_moves:
            self._send_command_locked(gcode.wait_for_moves(), timeout=config.MOVE_TIMEOUT)
        for line in self._send_command_locked(gcode.get_position()):
            position = gcode.parse_position(line)
            if position:
                self.last_position = position
                return position
        raise PrinterError("Réponse M114 illisible.")

    def get_position(self, wait_for_moves=False):
        """Retourne la position machine ; sans attente de mouvement par défaut."""
        with self.command_lock:
            self._abort.clear()
            return self._refresh_position_locked(wait_for_moves=wait_for_moves)

    def poll_position(self):
        """Interroge M114 seulement si le port n'est pas déjà utilisé."""
        if not self.is_connected():
            return None
        if not self.command_lock.acquire(blocking=False):
            return None
        try:
            self._abort.clear()
            return self._refresh_position_locked(wait_for_moves=False)
        finally:
            self.command_lock.release()

    # -------------------------------------------------
    # HOME
    # -------------------------------------------------

    def home(self, axes=None):

        with self.command_lock:

            self._abort.clear()

            if self.motion:
                self.motion.reset()

            responses = self._send_command_locked(
                gcode.home(axes),
                timeout=config.HOME_TIMEOUT
            )

            if not axes:
                self.homed = True

            position = self._refresh_position_locked(wait_for_moves=True)

            if self.motion:
                self.motion.reset(position)

            return responses

    def _wait_for_target_locked(self, target, timeout=None):
        """Attend la fin du mouvement puis lit UNE fois la position réelle."""
        if self._abort.is_set():
            raise PrinterError("Commande interrompue.")
        timeout = config.MOVE_TIMEOUT if timeout is None else timeout
        # Une seule synchronisation de mouvement : aucun M114 pendant le trajet.
        self._send_command_locked(gcode.wait_for_moves(), timeout=timeout)
        # M400 a répondu : le mouvement est terminé à CET instant.
        if self.motion:
            self.motion.finish()
        if self._abort.is_set():
            raise PrinterError("Commande interrompue.")
        position = self._refresh_position_locked(wait_for_moves=False)
        if self.motion:
            self.motion.settle(position)
        return position

    # -------------------------------------------------
    # Déplacement relatif
    # -------------------------------------------------

    def move_relative(self, axis, distance, speed=None, wait=True):

        axis = axis.upper()

        self._check_speed(speed)

        commands = gcode.relative_move(axis, distance, speed)

        with self.command_lock:

            self._abort.clear()

            target = None
            p0 = dict(self.last_position) if self.last_position else None
            if self.last_position is not None:
                target = self.last_position[axis] + distance
                self.check_target({axis: target})

            responses = []

            responses += self._send_command_locked(commands[0])

            try:
                responses += self._send_command_locked(commands[1])
                if self.motion and p0 and target is not None:
                    self.motion.start(p0, {axis: target}, speed or config.DEFAULT_SPEED)
            finally:
                # Ne jamais rester en mode relatif.
                try:
                    responses += self._send_command_locked(
                        commands[2],
                        timeout=5
                    )
                except Exception:
                    pass

            if wait:
                try:
                    if target is not None:
                        self._wait_for_target_locked({axis: target})
                    else:
                        self._refresh_position_locked(wait_for_moves=True)
                except Exception:
                    if self.motion:
                        self.motion.abort()
                    raise

            return responses

    # -------------------------------------------------
    # Déplacement absolu
    # -------------------------------------------------

    def move_absolute(self, x=None, y=None, z=None, speed=None, wait=True):

        self._check_speed(speed)

        commands = gcode.absolute_move(x, y, z, speed)

        self.check_target({"X": x, "Y": y, "Z": z})

        with self.command_lock:

            self._abort.clear()

            responses = []
            p0 = dict(self.last_position) if self.last_position else None

            for command in commands:
                responses += self._send_command_locked(command)

            # Le déplacement est maintenant dans la file du firmware.
            if self.motion and p0:
                self.motion.start(p0, {"X": x, "Y": y, "Z": z}, speed or config.DEFAULT_SPEED)

            if wait:
                try:
                    self._wait_for_target_locked({"X": x, "Y": y, "Z": z})
                except Exception:
                    if self.motion:
                        self.motion.abort()
                    raise

            return responses

    def _check_speed(self, speed):

        if speed is None:
            return

        if speed <= 0 or speed > config.MAX_SPEED:
            raise PrinterError(
                f"Vitesse {speed:g} mm/min invalide "
                f"(1 à {config.MAX_SPEED} mm/min)."
            )

    # -------------------------------------------------
    # Informations firmware
    # -------------------------------------------------

    def firmware_info(self):

        return self.send_command(gcode.firmware_info())

    def read_motion_settings(self):
        """Limites de vitesse / accélération du firmware (M503).

        Retourne {"max_speed": {...}, "max_accel": {...}, "print_accel": ...}
        ou {} si le firmware ne les fournit pas.
        """

        try:
            lines = self.send_command("M503", timeout=10)
        except (PrinterError, TimeoutError):
            return {}

        return motion.parse_m503(lines)
