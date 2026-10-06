"""Modèle de mouvement de la tête (aucune dépendance graphique).

Marlin ne dit pas où est la tête pendant un déplacement, et on ne veut pas
interroger M114 en permanence. On la CALCULE donc :

* chaque déplacement suit un profil de vitesse trapézoïdal (accélération,
  vitesse de croisière, décélération) ;
* la vitesse le long de la trajectoire est plafonnée pour qu'AUCUN axe ne
  dépasse sa vitesse maximale : un mouvement qui comporte du Z est donc
  ralenti par le moteur de hauteur, plus lent que X et Y ;
* l'instant de départ et l'instant de fin (réponse à M400) sont connus
  avec précision : le profil est étiré pour coller à la durée RÉELLE du
  déplacement. Les coordonnées calculées restent ainsi justes même si le
  firmware est un peu plus lent ou plus rapide que le modèle.

`MotionTracker.position_at(t)` donne alors la position à n'importe quel
instant `t` (secondes epoch, comme `time.time()`).
"""

import bisect
import math
import re
import threading
import time

import config

AXES = ("X", "Y", "Z")


# ----------------------------------------------------------------------
# Profil de vitesse le long d'un segment droit
# ----------------------------------------------------------------------
class Profile:
    """Profil trapézoïdal (ou triangulaire si le segment est trop court)."""

    def __init__(self, length, speed, accel):
        self.length = max(0.0, float(length))
        self.speed = max(1e-6, float(speed))
        self.accel = max(1e-6, float(accel))
        self.peak = 0.0
        self.t_acc = 0.0
        self.t_cruise = 0.0
        self.duration = 0.0
        if self.length <= 1e-9:
            return
        d_acc = self.speed ** 2 / (2.0 * self.accel)
        if 2.0 * d_acc >= self.length:
            self.peak = math.sqrt(self.accel * self.length)
            self.t_acc = self.peak / self.accel
        else:
            self.peak = self.speed
            self.t_acc = self.speed / self.accel
            self.t_cruise = (self.length - 2.0 * d_acc) / self.speed
        self.duration = 2.0 * self.t_acc + self.t_cruise

    def distance(self, tau):
        """Distance parcourue (mm) `tau` secondes après le départ."""
        if self.length <= 1e-9:
            return 0.0
        tau = min(max(tau, 0.0), self.duration)
        if tau < self.t_acc:
            return 0.5 * self.accel * tau ** 2
        if tau < self.t_acc + self.t_cruise:
            return 0.5 * self.accel * self.t_acc ** 2 + self.peak * (tau - self.t_acc)
        remaining = self.duration - tau
        return self.length - 0.5 * self.accel * remaining ** 2


class MotionModel:
    """Limites de la machine : vitesses et accélérations par axe."""

    def __init__(self, max_speed=None, max_accel=None, print_accel=None):
        self.max_speed = dict(config.MAX_AXIS_SPEED)
        self.max_accel = dict(config.MAX_AXIS_ACCEL)
        self.print_accel = float(config.PRINT_ACCEL)
        self.update(max_speed, max_accel, print_accel)

    def update(self, max_speed=None, max_accel=None, print_accel=None):
        for axis, value in (max_speed or {}).items():
            if axis in AXES and value and value > 0:
                self.max_speed[axis] = float(value)
        for axis, value in (max_accel or {}).items():
            if axis in AXES and value and value > 0:
                self.max_accel[axis] = float(value)
        if print_accel and print_accel > 0:
            self.print_accel = float(print_accel)

    def to_dict(self):
        return {"max_speed": dict(self.max_speed), "max_accel": dict(self.max_accel),
                "print_accel": self.print_accel}

    def profile(self, p0, p1, feed):
        """Profil d'un déplacement p0 → p1 à la consigne `feed` (mm/min)."""
        delta = {a: float(p1[a]) - float(p0[a]) for a in AXES}
        length = math.sqrt(sum(d * d for d in delta.values()))
        speed = (feed or config.DEFAULT_SPEED) / 60.0
        accel = self.print_accel
        if length > 1e-9:
            for axis, d in delta.items():
                share = abs(d) / length
                if share <= 1e-9:
                    continue
                # Aucun axe ne doit dépasser sa vitesse / accélération max.
                speed = min(speed, self.max_speed.get(axis, math.inf) / share)
                accel = min(accel, self.max_accel.get(axis, math.inf) / share)
        return Profile(length, speed, accel)

    def limiting_axis(self, p0, p1, feed):
        """Axe qui bride la vitesse (ou None si c'est la consigne)."""
        length = math.dist([p0[a] for a in AXES], [p1[a] for a in AXES])
        if length <= 1e-9:
            return None
        speed = (feed or config.DEFAULT_SPEED) / 60.0
        limiting = None
        for axis in AXES:
            share = abs(p1[axis] - p0[axis]) / length
            if share > 1e-9 and self.max_speed.get(axis, math.inf) / share < speed:
                speed = self.max_speed[axis] / share
                limiting = axis
        return limiting


# ----------------------------------------------------------------------
# Suivi des déplacements
# ----------------------------------------------------------------------
class Segment:
    """Un déplacement en ligne droite p0 → p1."""

    __slots__ = ("t0", "t1", "p0", "p1", "feed", "profile", "end_pos", "aborted", "scale")

    def __init__(self, t0, p0, p1, feed, profile):
        self.t0 = t0
        self.t1 = None          # instant de fin réel (None tant que ça bouge)
        self.p0 = p0
        self.p1 = p1
        self.feed = feed
        self.profile = profile
        self.end_pos = None     # position réelle mesurée à l'arrêt
        self.aborted = False
        self.scale = 1.0

    def fraction(self, t):
        """Avancement 0..1 le long du segment à l'instant t."""
        length = self.profile.length
        if length <= 1e-9:
            return 1.0
        if self.t1 is None:
            return min(1.0, self.profile.distance(t - self.t0) / length)
        span = max(self.t1 - self.t0, 1e-3)
        if not self.aborted:
            # Profil étiré sur la durée réellement observée.
            tau = (t - self.t0) / span * self.profile.duration
            return min(1.0, self.profile.distance(tau) / length)
        tau = min(t - self.t0, span)
        return min(1.0, self.profile.distance(tau) * self.scale / length)

    def position(self, t):
        f = self.fraction(t)
        return {a: self.p0[a] + (self.p1[a] - self.p0[a]) * f for a in AXES}


class MotionTracker:
    """Journal des déplacements ; calcule la position à tout instant."""

    MAX_SEGMENTS = 8000

    def __init__(self, model=None):
        self.model = model or MotionModel()
        self._lock = threading.RLock()
        self._starts = []
        self._segments = []
        self._open = None
        self._rest = None

    # --- Alimentation (appelé par printer.py) -----------------------------
    def reset(self, pos=None):
        """Oublie l'historique (recalibrage, déconnexion...)."""
        with self._lock:
            self._starts.clear()
            self._segments.clear()
            self._open = None
            self._rest = dict(pos) if pos else None

    def set_rest(self, pos):
        with self._lock:
            if pos:
                self._rest = dict(pos)

    def start(self, p0, target, feed, t=None):
        """Début d'un déplacement vers `target` (None = axe inchangé)."""
        t = time.time() if t is None else t
        with self._lock:
            if self._open is not None:
                self._close(t, aborted=True)
            p0 = p0 or self._rest
            if not p0:
                return None
            p1 = {a: (target.get(a) if target.get(a) is not None else p0[a]) for a in AXES}
            seg = Segment(t, {a: float(p0[a]) for a in AXES}, {a: float(p1[a]) for a in AXES},
                          feed, self.model.profile(p0, p1, feed))
            self._segments.append(seg)
            self._starts.append(t)
            self._open = seg
            if len(self._segments) > self.MAX_SEGMENTS:
                del self._segments[:2000]
                del self._starts[:2000]
            return seg

    def finish(self, t=None):
        """Fin normale du déplacement (réponse à M400)."""
        with self._lock:
            if self._open is not None:
                self._close(time.time() if t is None else t, aborted=False)

    def abort(self, t=None):
        """Déplacement interrompu (M410, erreur...)."""
        with self._lock:
            if self._open is not None:
                self._close(time.time() if t is None else t, aborted=True)

    def settle(self, pos):
        """Position RÉELLE connue après l'arrêt (réponse à M114)."""
        if not pos:
            return
        with self._lock:
            self._rest = dict(pos)
            seg = self._segments[-1] if self._segments else None
            if seg is None or seg.t1 is None or seg.end_pos is not None:
                return
            seg.end_pos = {a: float(pos[a]) for a in AXES}
            if seg.aborted:
                modelled = seg.profile.distance(min(seg.t1 - seg.t0, seg.profile.duration))
                real = math.dist([seg.p0[a] for a in AXES], [pos[a] for a in AXES])
                seg.scale = (real / modelled) if modelled > 1e-9 else 0.0

    def _close(self, t, aborted):
        seg = self._open
        seg.t1 = max(t, seg.t0 + 1e-3)
        seg.aborted = aborted
        self._open = None

    # --- Interrogation ----------------------------------------------------
    @property
    def moving(self):
        return self._open is not None

    def position_at(self, t=None):
        """(position, en_mouvement) à l'instant t ; position None si inconnue."""
        t = time.time() if t is None else t
        with self._lock:
            if not self._segments:
                return (dict(self._rest) if self._rest else None), False
            i = bisect.bisect_right(self._starts, t) - 1
            if i < 0:
                return dict(self._segments[0].p0), False
            seg = self._segments[i]
            if seg.t1 is None or t <= seg.t1:
                return seg.position(t), True
            if seg.end_pos:
                return dict(seg.end_pos), False
            if seg.aborted:
                return seg.position(seg.t1), False
            return dict(seg.p1), False

    def sample_position(self, t):
        """Position pour une mesure horodatée `t` : dict XYZ + « estimated »."""
        pos, moving = self.position_at(t)
        if pos is None:
            return None
        pos["estimated"] = moving
        return pos


# ----------------------------------------------------------------------
# Firmware : limites de vitesse / accélération (M503)
# ----------------------------------------------------------------------
_PARAM = re.compile(r"([A-Za-z])\s*(-?\d+(?:\.\d+)?)")


def parse_m503(lines):
    """Extrait les limites M203 (vitesse), M201 (accélération) et M204 (P)."""
    out = {}
    for line in lines:
        match = re.search(r"\bM(203|201|204)\b(.*)", line)
        if not match:
            continue
        code, rest = match.groups()
        params = {k.upper(): float(v) for k, v in _PARAM.findall(rest)}
        if code == "203":
            out["max_speed"] = {a: params[a] for a in AXES if a in params}
        elif code == "201":
            out["max_accel"] = {a: params[a] for a in AXES if a in params}
        elif code == "204":
            value = params.get("P", params.get("S"))
            if value:
                out["print_accel"] = value
    return out


# ----------------------------------------------------------------------
# Estimation de la durée d'un programme
# ----------------------------------------------------------------------
def estimate_path(model, steps, start=None, overhead=0.15):
    """steps : liste de (x, y, z, vitesse_mm_min, attente_s).

    Retourne (durée_s, longueur_mm). `overhead` = délai de communication
    moyen par déplacement (M400 + M114).
    """
    pos = dict(start) if start else None
    total = 0.0
    length = 0.0
    for x, y, z, feed, wait in steps:
        target = {"X": x, "Y": y, "Z": z}
        if x is None and y is None and z is None:
            total += wait
            continue
        if pos is None:
            pos = {a: (target[a] if target[a] is not None else 0.0) for a in AXES}
        else:
            p1 = {a: (target[a] if target[a] is not None else pos[a]) for a in AXES}
            profile = model.profile(pos, p1, feed)
            total += profile.duration + overhead
            length += profile.length
            pos = p1
        total += wait
    return total, length
