"""Acquisition de mesures.

Ce module ne dépend PAS de l'imprimante : la position éventuelle est
fournie par une fonction `position_provider` qui retourne
{"X":.., "Y":.., "Z":..} ou None. Le système de mesure peut ainsi être
remplacé ou supprimé sans toucher au contrôle de l'Ender.
"""

import csv
import math
import random
import statistics
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import config


class AcquisitionError(Exception):
    """Erreur du système de mesure."""


# -------------------------------------------------
# Mesure
# -------------------------------------------------

@dataclass
class Measurement:
    timestamp: float                 # secondes (époque Unix)
    value: float
    unit: str = "V"
    kind: str = "point"              # "point" ou "continu"
    index: Optional[int] = None      # numéro de la position du programme
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    n: int = 1                       # nombre d'échantillons moyennés
    std: Optional[float] = None      # écart-type de ces échantillons


# -------------------------------------------------
# Périphériques
# -------------------------------------------------

class AcquisitionDevice(ABC):
    """Un instrument capable de renvoyer une valeur."""

    unit = "V"
    label = "Instrument"

    def open(self):
        pass

    def close(self):
        pass

    @abstractmethod
    def read(self):
        """Retourne une valeur (float)."""


class SimulatedDevice(AcquisitionDevice):
    """Signal factice : dépend de X si une position est connue."""

    label = "Simulation"

    def __init__(self, position_provider=None, noise=0.01):

        self.position_provider = position_provider
        self.noise = noise
        self._t0 = time.monotonic()

    def read(self):

        position = self.position_provider() if self.position_provider else None

        if position:
            phase = position["X"] / 20.0 + position["Y"] / 50.0
        else:
            phase = (time.monotonic() - self._t0) / 2.0

        return 1.0 + 0.5 * math.sin(phase) + random.gauss(0.0, self.noise)


class NIDAQDevice(AcquisitionDevice):
    """Entrée analogique d'une carte NI (ex. USB-6001) via nidaqmx."""

    label = "NI DAQ"

    def __init__(
        self,
        device=None,
        channel=None,
        voltage_range=None
    ):

        self.device = device or config.DAQ_DEVICE
        self.channel = channel or config.DAQ_CHANNEL
        self.voltage_range = voltage_range or config.DAQ_VOLTAGE_RANGE
        self._task = None

    def open(self):

        if self._task is not None:
            return

        try:
            import nidaqmx
        except ImportError:
            raise AcquisitionError(
                "Le module « nidaqmx » est absent "
                "(pip install nidaqmx) ou le pilote NI-DAQmx "
                "n'est pas installé."
            ) from None

        low, high = self.voltage_range

        task = nidaqmx.Task()

        try:
            task.ai_channels.add_ai_voltage_chan(
                f"{self.device}/{self.channel}",
                min_val=low,
                max_val=high
            )
        except Exception as e:
            task.close()
            raise AcquisitionError(
                f"Voie {self.device}/{self.channel} inaccessible : {e}"
            ) from None

        self._task = task

    def close(self):

        if self._task is not None:
            self._task.close()
            self._task = None

    def read(self):

        if self._task is None:
            self.open()

        return float(self._task.read())


def list_nidaq_devices():
    """Noms des cartes NI détectées (liste vide si nidaqmx absent)."""

    try:
        from nidaqmx.system import System
        return [d.name for d in System.local().devices]
    except Exception:
        return []


def create_device(backend=None, position_provider=None, **options):
    """Crée un périphérique : backend "simulation" ou "nidaq"."""

    backend = (backend or config.ACQ_BACKEND).lower()

    if backend == "simulation":
        return SimulatedDevice(position_provider)

    if backend == "nidaq":
        return NIDAQDevice(**options)

    raise AcquisitionError(f"Système de mesure inconnu : {backend!r}")


# -------------------------------------------------
# Acquisition
# -------------------------------------------------

class Acquisition:
    """Mesures ponctuelles et acquisition continue, dans un thread dédié."""

    def __init__(
        self,
        device,
        sample_rate=None,
        average_samples=None,
        position_provider=None,
        max_samples=None
    ):

        self.device = device
        self.sample_rate = sample_rate or config.ACQ_SAMPLE_RATE
        self.average_samples = average_samples or config.ACQ_AVERAGE_SAMPLES
        self.position_provider = position_provider

        self.latest = None
        self.last_error = None

        # Appelé depuis le thread d'acquisition en cas d'erreur.
        self.on_error = None

        self._samples = deque(maxlen=max_samples or config.ACQ_MAX_SAMPLES)
        self._points = []
        self._data_lock = threading.Lock()

        # Un seul accès à la fois à l'instrument.
        self._device_lock = threading.Lock()
        self._opened = False

        self._thread = None
        self._stop = threading.Event()

        self.t0 = time.time()

    # ---- lecture ----------------------------------------------------

    def _read_once(self):

        with self._device_lock:

            if not self._opened:
                self.device.open()
                self._opened = True

            return self.device.read()

    def _position(self):

        if not self.position_provider:
            return None, None, None

        position = self.position_provider()

        if not position:
            return None, None, None

        return position.get("X"), position.get("Y"), position.get("Z")

    def measure(self, index=None, n=None):
        """Mesure ponctuelle : moyenne de `n` échantillons.

        Bloquant. La position est lue à la fin de la mesure.
        """

        n = max(1, int(n or self.average_samples))
        interval = 1.0 / self.sample_rate

        values = []

        for i in range(n):

            values.append(self._read_once())

            if i < n - 1:
                time.sleep(interval)

        x, y, z = self._position()

        measurement = Measurement(
            timestamp=time.time(),
            value=statistics.fmean(values),
            unit=self.device.unit,
            kind="point",
            index=index,
            x=x, y=y, z=z,
            n=n,
            std=statistics.pstdev(values) if n > 1 else None
        )

        with self._data_lock:
            self._points.append(measurement)

        self.latest = measurement

        return measurement

    # ---- acquisition continue --------------------------------------

    @property
    def is_running(self):

        return self._thread is not None and self._thread.is_alive()

    def start(self):

        if self.is_running:
            return

        self.last_error = None
        self._stop.clear()

        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):

        self._stop.set()

        if self._thread is not None:
            self._thread.join(timeout=3)

        self._thread = None

    def _loop(self):

        period = 1.0 / self.sample_rate
        next_time = time.monotonic()

        while not self._stop.is_set():

            try:
                value = self._read_once()
            except Exception as e:

                self.last_error = str(e) or e.__class__.__name__

                if self.on_error:
                    self.on_error(self.last_error)

                return

            x, y, z = self._position()

            measurement = Measurement(
                timestamp=time.time(),
                value=value,
                unit=self.device.unit,
                kind="continu",
                x=x, y=y, z=z
            )

            with self._data_lock:
                self._samples.append(measurement)

            self.latest = measurement

            next_time += period
            delay = next_time - time.monotonic()

            if delay > 0:
                self._stop.wait(delay)
            else:
                # En retard : on repart de maintenant.
                next_time = time.monotonic()

    # ---- données ---------------------------------------------------

    def recent_values(self, count=200):
        """Les `count` dernières valeurs continues (pour un affichage)."""

        with self._data_lock:
            return [m.value for m in list(self._samples)[-count:]]

    @property
    def count(self):
        """Nombre total de mesures conservées."""

        with self._data_lock:
            return len(self._samples) + len(self._points)

    @property
    def points(self):

        with self._data_lock:
            return list(self._points)

    @property
    def records(self):
        """Toutes les mesures (ponctuelles et continues) dans l'ordre du temps."""

        with self._data_lock:
            data = list(self._points) + list(self._samples)

        return sorted(data, key=lambda m: m.timestamp)

    def clear(self):

        with self._data_lock:
            self._samples.clear()
            self._points.clear()

        self.latest = None
        self.t0 = time.time()

    def close(self):
        """Arrête l'acquisition et libère l'instrument."""

        self.stop()

        with self._device_lock:

            if self._opened:
                self.device.close()
                self._opened = False

    # ---- export ----------------------------------------------------

    def export_csv(self, path):
        """Écrit toutes les mesures dans un fichier CSV. Retourne leur nombre."""

        records = self.records

        def fmt(value, digits=6):

            if value is None:
                return ""

            text = f"{value:.{digits}g}"

            return text.replace(".", config.CSV_DECIMAL)

        with open(path, "w", newline="", encoding="utf-8-sig") as f:

            writer = csv.writer(f, delimiter=config.CSV_DELIMITER)

            writer.writerow([
                "temps_s", "horodatage", "type", "position",
                "x_mm", "y_mm", "z_mm",
                "valeur", "unite", "n", "ecart_type"
            ])

            for m in records:

                writer.writerow([
                    fmt(m.timestamp - self.t0, 9),
                    datetime.fromtimestamp(m.timestamp).isoformat(
                        sep=" ", timespec="milliseconds"
                    ),
                    m.kind,
                    "" if m.index is None else m.index + 1,
                    fmt(m.x, 7), fmt(m.y, 7), fmt(m.z, 7),
                    fmt(m.value, 9),
                    m.unit,
                    m.n,
                    fmt(m.std, 6)
                ])

        return len(records)
