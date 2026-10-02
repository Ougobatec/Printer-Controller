"""Génération et analyse des commandes G-code (Marlin)."""

import re

import config

_NUMBER = r"(-?\d+(?:\.\d+)?)"

_POSITION_RE = re.compile(
    rf"X:\s*{_NUMBER}\s+Y:\s*{_NUMBER}\s+Z:\s*{_NUMBER}"
)

AXES = ("X", "Y", "Z")


def _speed(speed):

    return config.DEFAULT_SPEED if speed is None else speed


def relative_move(axis, distance, speed=None):

    axis = axis.upper()

    if axis not in AXES:
        raise ValueError(f"Axe invalide : {axis!r}")

    return [
        "G91",
        f"G1 {axis}{distance:g} F{_speed(speed):g}",
        "G90"
    ]


def absolute_move(x=None, y=None, z=None, speed=None):

    if x is None and y is None and z is None:
        raise ValueError("Au moins un axe doit être indiqué.")

    command = "G1"

    if x is not None:
        command += f" X{x:g}"

    if y is not None:
        command += f" Y{y:g}"

    if z is not None:
        command += f" Z{z:g}"

    command += f" F{_speed(speed):g}"

    return [
        "G90",
        command
    ]


def home(axes=None):
    """HOME de tous les axes, ou seulement de ceux indiqués ("XY")."""

    if not axes:
        return "G28"

    return "G28 " + " ".join(axes.upper())


def get_position():

    return "M114"


def firmware_info():

    return "M115"


def wait_for_moves():
    """Attend la fin de tous les mouvements en file d'attente."""

    return "M400"


def quick_stop():
    """Arrête les mouvements en cours (l'imprimante reste utilisable)."""

    return "M410"


def emergency_stop():
    """Arrêt d'urgence : Marlin se bloque jusqu'à un redémarrage."""

    return "M112"


def parse_position(line):
    """Extrait {"X":..., "Y":..., "Z":...} d'une réponse M114.

    Retourne None si la ligne n'est pas une réponse de position.
    """

    match = _POSITION_RE.search(line)

    if not match:
        return None

    x, y, z = (float(v) for v in match.groups())

    return {"X": x, "Y": y, "Z": z}
