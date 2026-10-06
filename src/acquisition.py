"""Moteur d'acquisition : flux direct, enregistrement, export.

Principe :

* un seul flux de lecture tourne dans un thread (« contrôle en direct ») ;
* tout ce qui est lu alimente un petit tampon `live` (pour vérifier que le
  capteur fonctionne) ;
* quand un SEGMENT est ouvert (`begin_segment`), les lectures sont en plus
  ENREGISTRÉES dans les résultats avec leur horodatage et leur position ;
* la position vient de `position_provider(t)` (le suivi de trajectoire de
  `motion.py`) : aucune interrogation de l'imprimante pendant la mesure.
  Quand un déplacement se termine, les positions estimées sont recalculées
  (`refine_positions`) avec sa durée réelle.

Valeur enregistrée = valeur brute de l'appareil × `multiplier`, exprimée
dans `unit`.
"""

import csv
import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import config
from devices import AcquisitionDevice, AcquisitionError, DEVICE_TYPES, device_class  # noqa: F401


@dataclass
class Measurement:
    timestamp: float
    value: float                       # valeur convertie (× multiplicateur)
    unit: str = "V"
    kind: str = "continu"              # "continu" (échantillon) | "point" (moyenne)
    mode: str = "libre"                # point | continu | trajet | parcours
    index: Optional[int] = None        # n° de position du programme (0 = première)
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    estimated: bool = False            # position calculée (tête en mouvement)
    n: int = 1
    std: Optional[float] = None
    vmin: Optional[float] = None
    vmax: Optional[float] = None
    raw: Optional[float] = None        # valeur brute de l'appareil


def describe_values(values):
    """Statistiques d'une liste de valeurs : dict(n, min, max, mean, std)."""
    n = len(values)
    if not n:
        return None
    mean = math.fsum(values) / n
    var = math.fsum((v - mean) ** 2 for v in values) / n
    return {"n": n, "min": min(values), "max": max(values), "mean": mean, "std": math.sqrt(var)}


class Acquisition:
    LIVE_SECONDS = 120

    def __init__(self, position_provider=None):
        self.device = None
        self.sample_rate = float(config.ACQ_SAMPLE_RATE)
        self.multiplier = float(config.MEASURE_MULTIPLIER)
        self.unit = str(config.MEASURE_UNIT)
        self.average_samples = int(config.ACQ_AVERAGE_SAMPLES)
        self.position_provider = position_provider
        self.on_error = None
        self.last_error = None

        self.latest = None             # (horodatage, valeur) de la dernière lecture
        self.revision = 0              # +1 à chaque changement des résultats
        self.trim_epoch = 0            # +1 quand des anciennes lignes sont supprimées

        self._records = []
        self._live = deque(maxlen=int(config.ACQ_MAX_RATE * self.LIVE_SECONDS))
        self._data_lock = threading.Lock()
        self._device_lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self._recording = False
        self._rate_override = None
        self._seg = None
        self.current_index = None
        self.current_mode = "libre"

    # ------------------------------------------------------------------
    # Connexion
    # ------------------------------------------------------------------
    @property
    def connected(self):
        return self.device is not None

    @property
    def description(self):
        return self.device.describe() if self.device else ""

    def connect(self, device):
        """Ouvre l'appareil (bloquant : à appeler depuis un thread)."""
        self.disconnect()
        with self._device_lock:
            device.open()
            self.device = device
        self.last_error = None

    def disconnect(self):
        self.end_segment()
        self.stop()
        with self._device_lock:
            device, self.device = self.device, None
        if device is not None:
            try:
                device.close()
            except Exception:
                pass

    def _read_raw(self):
        with self._device_lock:
            device = self.device
            if device is None:
                raise AcquisitionError("Aucun appareil de mesure connecté.")
            return float(device.read())

    def _position(self, timestamp):
        provider = self.position_provider
        pos = provider(timestamp) if provider else None
        if not pos:
            return None, None, None, False
        return pos.get("X"), pos.get("Y"), pos.get("Z"), bool(pos.get("estimated"))

    # ------------------------------------------------------------------
    # Flux de lecture
    # ------------------------------------------------------------------
    @property
    def is_running(self):
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self):
        if self.is_running:
            return
        if not self.connected:
            raise AcquisitionError("Aucun appareil de mesure connecté.")
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(3.0)

    def _loop(self):
        next_time = time.monotonic()
        while not self._stop.is_set():
            try:
                raw = self._read_raw()
            except Exception as exc:
                self._fail(exc)
                return
            ts = time.time()
            value = raw * self.multiplier
            with self._data_lock:
                self._live.append((ts, value))
                self.latest = (ts, value)
                if self._recording:
                    x, y, z, est = self._position(ts)
                    self._records.append(Measurement(
                        ts, value, self.unit, "continu", self.current_mode, self.current_index,
                        x, y, z, est, raw=raw))
                    self._trim_locked()
                    self.revision += 1
            rate = max(0.1, self._rate_override or self.sample_rate)
            next_time += 1.0 / rate
            delay = next_time - time.monotonic()
            if delay > 0:
                self._stop.wait(delay)
            else:
                next_time = time.monotonic()   # en retard : on ne rattrape pas

    def _fail(self, exc):
        message = str(exc) or exc.__class__.__name__
        self.last_error = message
        if self.on_error:
            try:
                self.on_error(message)
            except Exception:
                pass

    def _trim_locked(self):
        limit = int(config.ACQ_MAX_SAMPLES)
        if len(self._records) > limit:
            del self._records[: len(self._records) - int(limit * 0.9)]
            self.trim_epoch += 1

    # ------------------------------------------------------------------
    # Enregistrement lié au programme
    # ------------------------------------------------------------------
    @property
    def in_segment(self):
        return self._seg is not None

    def begin_segment(self, index=None, mode="continu", rate=None):
        """Commence à enregistrer. Le flux démarre seul s'il ne tourne pas."""
        if not self.connected:
            raise AcquisitionError("Aucun appareil de mesure connecté.")
        auto = not self.is_running
        with self._data_lock:
            self._seg = {"t0": time.time(), "auto": auto}
            self.current_index = index
            self.current_mode = mode
            self._rate_override = rate
            self._recording = True
        if auto:
            self.start()

    def set_index(self, index):
        self.current_index = index

    def set_rate(self, rate):
        """Change la fréquence de l'enregistrement en cours."""
        self._rate_override = rate

    def end_segment(self):
        """Arrête l'enregistrement ; recalcule les positions du segment."""
        seg = self._seg
        if seg is None:
            return
        with self._data_lock:
            self._recording = False
            self._rate_override = None
            self._seg = None
        if seg["auto"]:
            self.stop()
        self.current_index = None
        self.current_mode = "libre"
        self.refine_positions(seg["t0"])

    def refine_positions(self, since):
        """Recalcule les positions des échantillons depuis `since`.

        Appelé quand un déplacement est terminé : sa durée réelle est alors
        connue, ce qui corrige l'estimation faite en direct.
        """
        provider = self.position_provider
        if provider is None:
            return
        with self._data_lock:
            for m in reversed(self._records):
                if m.timestamp < since:
                    break
                if m.kind != "continu":
                    continue
                pos = provider(m.timestamp)
                if pos:
                    m.x, m.y, m.z = pos.get("X"), pos.get("Y"), pos.get("Z")
                    m.estimated = bool(pos.get("estimated"))
            self.revision += 1

    def measure(self, index=None, n=None, mode="point", cancel=None, rate=None):
        """Moyenne de `n` lectures, enregistrée comme UN résultat."""
        n = max(1, int(n or self.average_samples))
        raws = []
        for i in range(n):
            if cancel is not None and cancel.is_set():
                return None
            raws.append(self._read_raw())
            if i < n - 1:
                wait = 1.0 / max(0.1, rate or self.sample_rate)
                if cancel is not None:
                    if cancel.wait(wait):
                        return None
                else:
                    time.sleep(wait)
        values = [r * self.multiplier for r in raws]
        stats = describe_values(values)
        ts = time.time()
        x, y, z, est = self._position(ts)
        m = Measurement(ts, stats["mean"], self.unit, "point", mode, index, x, y, z, est,
                        n, stats["std"] if n > 1 else None, stats["min"], stats["max"],
                        raw=math.fsum(raws) / n)
        with self._data_lock:
            self._records.append(m)
            self._trim_locked()
            self.revision += 1
        return m

    # ------------------------------------------------------------------
    # Accès aux données
    # ------------------------------------------------------------------
    def live_since(self, t):
        out = []
        with self._data_lock:
            for item in reversed(self._live):
                if item[0] < t:
                    break
                out.append(item)
        out.reverse()
        return out

    def records_snapshot(self):
        with self._data_lock:
            return list(self._records)

    @property
    def record_count(self):
        return len(self._records)

    def clear(self):
        with self._data_lock:
            self._records.clear()
            self.trim_epoch += 1
            self.revision += 1

    def clear_live(self):
        with self._data_lock:
            self._live.clear()
            self.latest = None

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_csv(self, path):
        """Écrit toutes les mesures avec leurs coordonnées. Retourne le nombre de lignes."""
        rows = self.records_snapshot()
        if not rows:
            return 0

        def num(value, digits=9):
            if value is None:
                return ""
            text = f"{value:.{digits}g}"
            return text.replace(".", config.CSV_DECIMAL) if config.CSV_DECIMAL != "." else text

        t0 = rows[0].timestamp
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=config.CSV_DELIMITER)
            writer.writerow(["n", "temps_s", "horodatage", "type", "mode", "point",
                             "x_mm", "y_mm", "z_mm", "position_calculee",
                             "valeur", "unite", "brut", "n_moyenne", "ecart_type", "min", "max"])
            for i, m in enumerate(rows, start=1):
                writer.writerow([
                    i, num(m.timestamp - t0, 7),
                    datetime.fromtimestamp(m.timestamp).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                    "moyenne" if m.kind == "point" else "echantillon", m.mode,
                    "" if m.index is None else m.index + 1,
                    num(m.x, 7), num(m.y, 7), num(m.z, 7), "oui" if m.estimated else "non",
                    num(m.value), m.unit, num(m.raw), m.n, num(m.std), num(m.vmin), num(m.vmax)])
        return len(rows)
