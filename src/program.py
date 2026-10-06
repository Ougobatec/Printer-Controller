"""Programmes de positions : édition, sauvegarde JSON et exécution."""

import json
import threading
from dataclasses import dataclass
from typing import Optional

import config


class ProgramError(Exception):
    """Programme invalide ou fichier illisible."""


# Façons d'exécuter un programme :
#  POINTS   : chaque position a sa vitesse et sa mesure (aucune, point, continu, trajet)
#  PARCOURS : une vitesse et une fréquence de mesure globales pour tout le programme
POINTS = "points"
PARCOURS = "parcours"
MEASURE_MODES = ("aucune", "point", "continu", "trajet")


# -------------------------------------------------
# Position
# -------------------------------------------------

def _number(value, field, allow_none=True):

    if value is None:

        if allow_none:
            return None

        raise ProgramError(f"« {field} » est obligatoire.")

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProgramError(f"« {field} » doit être un nombre : {value!r}")

    return float(value)


@dataclass
class Waypoint:
    """Une position du programme.

    Un axe à None n'est pas déplacé. `vitesse` à None utilise la
    vitesse par défaut. `attente` est un temps de stabilisation (s)
    après l'arrivée, avant la mesure éventuelle.
    """

    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    vitesse: Optional[float] = None
    attente: float = 0.0
    mesure: str = "aucune"
    mesure_n: int = 5
    mesure_duree: float = 2.0
    mesure_frequence: float = 10.0

    def __post_init__(self):

        self.x = _number(self.x, "x")
        self.y = _number(self.y, "y")
        self.z = _number(self.z, "z")
        self.vitesse = _number(self.vitesse, "vitesse")
        self.attente = _number(self.attente or 0.0, "attente", False)
        self.mesure = str(self.mesure or "aucune").lower()
        if self.mesure not in MEASURE_MODES:
            raise ProgramError("Le mode de mesure doit être « aucune », « point », "
                               "« continu » ou « trajet ».")
        try:
            self.mesure_n = max(1, int(self.mesure_n))
        except (TypeError, ValueError):
            raise ProgramError("Le nombre d'échantillons de mesure doit être un entier positif.") from None
        self.mesure_duree = _number(self.mesure_duree or 0.0, "durée de mesure", False)
        self.mesure_frequence = _number(self.mesure_frequence or 0.0, "fréquence de mesure", False)
        if self.mesure_duree < 0:
            raise ProgramError("La durée de mesure ne peut pas être négative.")
        if self.mesure_frequence <= 0:
            raise ProgramError("La fréquence de mesure doit être strictement positive.")

        if self.vitesse is not None and self.vitesse <= 0:
            raise ProgramError("La vitesse doit être strictement positive.")

        if self.attente < 0:
            raise ProgramError("L'attente ne peut pas être négative.")

    def to_dict(self):

        data = {}

        for key in ("x", "y", "z", "vitesse"):

            value = getattr(self, key)

            if value is not None:
                data[key] = value

        if self.attente:
            data["attente"] = self.attente
        if self.mesure != "aucune":
            data["mesure"] = self.mesure
            if self.mesure == "point":
                data["mesure_n"] = self.mesure_n
                data["mesure_frequence"] = self.mesure_frequence
            else:
                if self.mesure == "continu":
                    data["mesure_duree"] = self.mesure_duree
                data["mesure_frequence"] = self.mesure_frequence

        return data

    @classmethod
    def from_dict(cls, data):

        if not isinstance(data, dict):
            raise ProgramError(f"Position invalide : {data!r}")

        return cls(
            x=data.get("x"),
            y=data.get("y"),
            z=data.get("z"),
            vitesse=data.get("vitesse"),
            attente=data.get("attente", 0.0),
            mesure=data.get("mesure", "aucune"),
            mesure_n=data.get("mesure_n", 5),
            mesure_duree=data.get("mesure_duree", 2.0),
            mesure_frequence=data.get("mesure_frequence", config.ACQ_SAMPLE_RATE)
        )


# -------------------------------------------------
# Programme
# -------------------------------------------------

class Program:

    def __init__(self, name="Sans titre", positions=None):

        self.name = name
        self.positions = list(positions or [])

        # Mode « parcours » : réglages globaux
        self.mode = PARCOURS
        self.vitesse = float(config.DEFAULT_SPEED)       # mm/min
        self.frequence = float(config.ACQ_SAMPLE_RATE)   # Hz
        self.parcours_mesure = True

    def __len__(self):
        return len(self.positions)

    def __iter__(self):
        return iter(self.positions)

    def __getitem__(self, index):
        return self.positions[index]

    # ---- édition ----------------------------------------------------

    def add(self, waypoint):
        self.positions.append(waypoint)

    def insert(self, index, waypoint):
        self.positions.insert(index, waypoint)

    def update(self, index, waypoint):
        self.positions[index] = waypoint

    def remove(self, index):
        del self.positions[index]

    def clear(self):
        self.positions.clear()

    def move(self, index, offset):
        """Décale une position de `offset` rangs. Retourne le nouvel index."""

        new_index = index + offset

        if not 0 <= new_index < len(self.positions):
            return index

        item = self.positions.pop(index)
        self.positions.insert(new_index, item)

        return new_index

    # ---- vérification ----------------------------------------------

    def validate(self, limits=None, max_speed=None):
        """Retourne la liste des problèmes (vide si le programme est valide)."""

        limits = config.AXIS_LIMITS if limits is None else limits
        max_speed = config.MAX_SPEED if max_speed is None else max_speed

        problems = []

        if self.mode == PARCOURS:
            if not 0 < self.vitesse <= max_speed:
                problems.append(f"Vitesse du parcours invalide (1 à {max_speed:g} mm/min).")
            if self.parcours_mesure and not 0 < self.frequence <= config.ACQ_MAX_RATE:
                problems.append(f"Fréquence de mesure invalide (0 à {config.ACQ_MAX_RATE:g} Hz).")

        if not self.positions:
            problems.append("Le programme ne contient aucune position.")
        elif not any(wp.x is not None or wp.y is not None or wp.z is not None for wp in self.positions):
            problems.append("Le programme ne contient aucune position remplie.")

        for i, wp in enumerate(self.positions, start=1):

            for axis in ("X", "Y", "Z"):

                value = getattr(wp, axis.lower())

                if value is None or axis not in limits:
                    continue

                low, high = limits[axis]

                if not low <= value <= high:
                    problems.append(
                        f"Position {i} : {axis} = {value:g} hors limites "
                        f"({low:g} à {high:g} mm)."
                    )

            if wp.vitesse is not None and wp.vitesse > max_speed:
                problems.append(
                    f"Position {i} : vitesse {wp.vitesse:g} mm/min "
                    f"supérieure au maximum ({max_speed:g})."
                )

        return problems

    # ---- estimation ---------------------------------------------------

    def estimate(self, model, start=None):
        """(durée en s, longueur en mm) estimées avec le modèle de mouvement."""

        from motion import estimate_path

        steps = []
        for wp in self.positions:
            feed = (wp.vitesse or self.vitesse) if self.mode == PARCOURS else wp.vitesse
            steps.append((wp.x, wp.y, wp.z, feed, wp.attente))

        return estimate_path(model, steps, start)

    # ---- JSON -----------------------------------------------------------

    def to_dict(self):

        data = {"nom": self.name, "mode": self.mode}

        if self.mode == PARCOURS:
            data["parcours"] = {
                "vitesse": self.vitesse,
                "mesure": self.parcours_mesure,
                "frequence": self.frequence,
            }

        positions = []

        for wp in self.positions:
            item = wp.to_dict()
            if self.mode == PARCOURS:
                item["mesure_frequence"] = wp.mesure_frequence
            positions.append(item)

        data["positions"] = positions

        return data

    @classmethod
    def from_dict(cls, data):

        if not isinstance(data, dict) or "positions" not in data:
            raise ProgramError("Fichier de programme invalide.")

        if not isinstance(data["positions"], list):
            raise ProgramError("« positions » doit être une liste.")

        positions = []

        for i, item in enumerate(data["positions"], start=1):

            try:
                positions.append(Waypoint.from_dict(item))
            except ProgramError as e:
                raise ProgramError(f"Position {i} : {e}") from None

        program = cls(str(data.get("nom", "Sans titre")), positions)

        program.mode = POINTS

        if data.get("mode") == PARCOURS:
            program.mode = PARCOURS
            settings = data.get("parcours") or {}
            try:
                program.vitesse = float(settings.get("vitesse", program.vitesse))
                program.frequence = float(settings.get("frequence", program.frequence))
            except (TypeError, ValueError):
                raise ProgramError("Réglages du parcours invalides.") from None
            program.parcours_mesure = bool(settings.get("mesure", True))

        return program

    def save(self, path):

        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=4, ensure_ascii=False)
            f.write("\n")

    @classmethod
    def load(cls, path):

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise ProgramError(f"JSON invalide : {e}") from None

        return cls.from_dict(data)


# -------------------------------------------------
# Exécution
# -------------------------------------------------

IDLE = "idle"
RUNNING = "running"
PAUSED = "paused"
FINISHED = "finished"
STOPPED = "stopped"
ERROR = "error"


class ProgramRunner:
    """Exécute un programme sur une imprimante.

    `run()` est bloquant : l'appeler depuis un thread. Les callbacks
    sont appelés depuis ce thread (ils peuvent donc bloquer).

    on_begin(program)                   avant la première position
    on_step(index, waypoint)            avant le déplacement
    before_move(index, waypoint, speed) juste avant l'envoi du déplacement
    after_move(index, waypoint)         déplacement terminé (avant l'attente)
    on_measure(index, waypoint, pos)    arrivée et attente terminées, mode
                                        « point par point » : mesure à
                                        l'arrivée ; le programme attend.
    on_reached(index, waypoint, pos)    arrivée (et mesure) terminées
    on_end(program, state)              fin, quelle que soit l'issue
    on_state(state, message)            changement d'état
    """

    def __init__(
        self,
        printer,
        on_step=None,
        on_reached=None,
        on_measure=None,
        on_state=None,
        on_begin=None,
        on_end=None,
        before_move=None,
        after_move=None
    ):

        self.printer = printer
        self.on_step = on_step
        self.on_reached = on_reached
        self.on_measure = on_measure
        self.on_state = on_state
        self.on_begin = on_begin
        self.on_end = on_end
        self.before_move = before_move
        self.after_move = after_move

        self.state = IDLE
        self.error = None

        self._stop = threading.Event()
        self._resume = threading.Event()
        self._resume.set()

    @property
    def active(self):
        return self.state in (RUNNING, PAUSED)

    def _set_state(self, state, message=""):

        self.state = state

        if self.on_state:
            self.on_state(state, message)

    def run(self, program, start_index=0):
        """Exécute le programme. Retourne l'état final."""

        problems = program.validate()

        if problems:
            raise ProgramError("\n".join(problems))

        self.error = None
        self._stop.clear()
        self._resume.set()

        self._set_state(RUNNING)

        final = STOPPED
        message = ""

        try:

            if self.on_begin:
                self.on_begin(program)

            for index in range(start_index, len(program)):

                self._resume.wait()

                if self._stop.is_set():
                    break

                waypoint = program[index]

                if self.on_step:
                    self.on_step(index, waypoint)

                if waypoint.x is None and waypoint.y is None and waypoint.z is None:
                    continue

                if program.mode == PARCOURS:
                    speed = waypoint.vitesse or program.vitesse
                else:
                    speed = waypoint.vitesse

                if self.before_move:
                    self.before_move(index, waypoint, speed)

                self.printer.move_absolute(
                    waypoint.x,
                    waypoint.y,
                    waypoint.z,
                    speed
                )

                if self.after_move:
                    self.after_move(index, waypoint)

                if waypoint.attente > 0:
                    if self._stop.wait(waypoint.attente):
                        break

                if self._stop.is_set():
                    break

                position = self.printer.get_position()

                if (
                    self.on_measure
                    and program.mode == POINTS
                    and waypoint.mesure in ("point", "continu")
                ):
                    self.on_measure(index, waypoint, position)

                if self.on_reached:
                    self.on_reached(index, waypoint, position)

            else:
                final = FINISHED

        except Exception as e:

            if self._stop.is_set():
                # Arrêt demandé : l'erreur vient de l'interruption.
                final = STOPPED
            else:
                final = ERROR
                message = str(e) or e.__class__.__name__
                self.error = message

        try:
            if self.on_end:
                self.on_end(program, final)
        except Exception as e:
            if final != ERROR:
                final = ERROR
                message = str(e) or e.__class__.__name__
                self.error = message

        self._set_state(final, message)
        return final

    def pause(self):
        """Met en pause après le déplacement en cours."""

        if self.state == RUNNING:
            self._resume.clear()
            self._set_state(PAUSED)

    def resume(self):

        if self.state == PAUSED:
            self._resume.set()
            self._set_state(RUNNING)

    def stop(self):
        """Arrête immédiatement (le mouvement en cours est interrompu)."""

        if not self.active:
            return

        self.cancel()

        try:
            self.printer.quick_stop()
        except Exception:
            pass

    def cancel(self):
        """Annule l'exécution sans envoyer de commande de mouvement."""

        self._stop.set()
        self._resume.set()
