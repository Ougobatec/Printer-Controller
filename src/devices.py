"""Pilotes des appareils de mesure.

Chaque pilote décrit ses réglages dans `PARAMS` ; l'interface construit
elle-même le formulaire de connexion à partir de cette description. Pour
ajouter un système de mesure :

    @register
    class MonAppareil(AcquisitionDevice):
        key = "mon_appareil"
        title = "Mon appareil"
        PARAMS = (Param("port", "Port", "choice", "COM3", choices=...),)

        def open(self): ...
        def read(self): ...      # retourne UNE valeur (float), unité native
        def close(self): ...
"""

import re
from dataclasses import dataclass

import config


class AcquisitionError(Exception):
    """Erreur de l'appareil de mesure ou de l'acquisition."""


@dataclass
class Param:
    """Un réglage de connexion affiché dans l'interface.

    kind     : "text" | "number" | "choice"
    choices  : liste de valeurs, ou fonction(valeurs_actuelles) -> liste
               (appelée dans un thread : elle peut interroger le matériel)
    editable : pour "choice", autorise la saisie libre
    depends  : clés dont dépend la liste de choix (rechargée si elles changent)
    """

    key: str
    label: str
    kind: str = "text"
    default: str = ""
    choices: object = None
    editable: bool = True
    hint: str = ""
    depends: tuple = ()


class AcquisitionDevice:
    """Interface commune des appareils de mesure."""

    key = "base"
    title = "Instrument"
    description = ""
    native_unit = "V"
    PARAMS = ()

    def __init__(self, **values):
        self.values = {p.key: str(values.get(p.key, p.default)).strip() for p in self.PARAMS}

    @classmethod
    def defaults(cls):
        return {p.key: str(p.default) for p in cls.PARAMS}

    def number(self, key, label, minimum=None, integer=False):
        text = self.values.get(key, "").replace(",", ".")
        try:
            value = int(float(text)) if integer else float(text)
        except ValueError:
            raise AcquisitionError(f"« {label} » doit être un nombre.") from None
        if minimum is not None and value < minimum:
            raise AcquisitionError(f"« {label} » doit être supérieur ou égal à {minimum:g}.")
        return value

    def describe(self):
        """Résumé affiché dans l'état de connexion."""
        return self.title

    def open(self):
        pass

    def close(self):
        pass

    def read(self):
        raise NotImplementedError


DEVICE_TYPES = {}


def register(cls):
    DEVICE_TYPES[cls.key] = cls
    return cls


def device_class(key):
    return DEVICE_TYPES.get(key) or next(iter(DEVICE_TYPES.values()))


# ----------------------------------------------------------------------
# NI-DAQmx
# ----------------------------------------------------------------------
def list_nidaq_devices():
    """Noms des cartes NI détectées ([] si pilote absent)."""
    try:
        import nidaqmx.system
        return [d.name for d in nidaqmx.system.System.local().devices]
    except Exception:
        return []


def list_nidaq_channels(device):
    """Voies d'entrée analogiques d'une carte (ex. ["ai0", "ai1", ...])."""
    if not device:
        return []
    try:
        import nidaqmx.system
        dev = nidaqmx.system.Device(device)
        return [c.name.split("/", 1)[-1] for c in dev.ai_physical_chans]
    except Exception:
        return []


# libellé affiché -> noms possibles de la constante TerminalConfiguration
TERMINALS = {
    "Par défaut": ("DEFAULT",),
    "Référencée (RSE)": ("RSE",),
    "Non référencée (NRSE)": ("NRSE",),
    "Différentielle": ("DIFF", "DIFFERENTIAL"),
    "Pseudo-différentielle": ("PSEUDO_DIFF", "PSEUDODIFFERENTIAL"),
}


def _nidaq_channels_for(values):
    channels = list_nidaq_channels(values.get("device", ""))
    return channels or [f"ai{i}" for i in range(8)]


@register
class NIDAQDevice(AcquisitionDevice):
    key = "nidaq"
    title = "NI-DAQmx (carte National Instruments)"
    description = "Entrée analogique en tension d'une carte NI (USB-6001, ...)."
    native_unit = "V"
    PARAMS = (
        Param("device", "Carte", "choice", config.DAQ_DEVICE,
              choices=lambda values: list_nidaq_devices(),
              hint="Nom de la carte dans NI MAX (ex. Dev1)."),
        Param("channel", "Voie", "choice", config.DAQ_CHANNEL,
              choices=_nidaq_channels_for, depends=("device",),
              hint="Entrée analogique (ex. ai0)."),
        Param("terminal", "Branchement", "choice", "Par défaut",
              choices=list(TERMINALS), editable=False,
              hint="Mode de mesure de l'entrée : référencée, différentielle..."),
        Param("range", "Plage de tension", "choice", f"±{config.DAQ_VOLTAGE_RANGE[1]:g} V",
              choices=["±10 V", "±5 V", "±1 V", "±0,2 V"],
              hint="Plage la plus étroite qui contient le signal = meilleure résolution."),
        Param("timeout", "Délai max de lecture (s)", "number", "2",
              hint="Temps maximum attendu pour UNE lecture avant de signaler une erreur "
                   "(carte débranchée...). Sans effet sur la vitesse de mesure."),
    )

    def __init__(self, **values):
        super().__init__(**values)
        self._task = None

    def describe(self):
        return f"{self.values['device']}/{self.values['channel']} · {self.values['range']}"

    def _range(self):
        text = self.values["range"].replace("±", "").replace("V", "").replace(",", ".").strip()
        try:
            value = abs(float(text))
        except ValueError:
            raise AcquisitionError("Plage de tension invalide (ex. ±10 V).") from None
        if value <= 0:
            raise AcquisitionError("La plage de tension doit être positive.")
        return value

    def open(self):
        try:
            import nidaqmx
            from nidaqmx.constants import TerminalConfiguration
        except ImportError:
            raise AcquisitionError(
                "Le module « nidaqmx » est introuvable (pip install nidaqmx) "
                "ou le pilote NI-DAQmx n'est pas installé."
            ) from None

        device, channel = self.values["device"], self.values["channel"]
        if not device or not channel:
            raise AcquisitionError("Indiquez la carte et la voie.")
        limit = self._range()
        self.timeout = self.number("timeout", "Délai max de lecture", minimum=0.1)

        terminal = TerminalConfiguration.DEFAULT
        for name in TERMINALS.get(self.values["terminal"], ("DEFAULT",)):
            if hasattr(TerminalConfiguration, name):
                terminal = getattr(TerminalConfiguration, name)
                break

        task = nidaqmx.Task()
        try:
            task.ai_channels.add_ai_voltage_chan(
                f"{device}/{channel}", terminal_config=terminal,
                min_val=-limit, max_val=limit)
        except Exception as exc:
            task.close()
            raise AcquisitionError(f"Impossible d'ouvrir {device}/{channel} : {exc}") from None
        self._task = task

    def close(self):
        if self._task is not None:
            try:
                self._task.close()
            finally:
                self._task = None

    def read(self):
        if self._task is None:
            raise AcquisitionError("Carte non ouverte.")
        return float(self._task.read(timeout=self.timeout))


# ----------------------------------------------------------------------
# Instrument série (texte)
# ----------------------------------------------------------------------
EOL = {"LF  (\\n)": "\n", "CR+LF  (\\r\\n)": "\r\n", "CR  (\\r)": "\r"}
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _serial_ports(values):
    from printer import list_ports
    return list_ports()


@register
class SerialDevice(AcquisitionDevice):
    key = "serial"
    title = "Instrument série (texte)"
    description = ("Multimètre, capteur ou carte qui répond par du texte sur un port série/USB : "
                   "on lit un nombre dans chaque ligne reçue.")
    native_unit = "V"
    PARAMS = (
        Param("port", "Port", "choice", "", choices=_serial_ports,
              hint="Port série de l'instrument (différent de celui de l'imprimante)."),
        Param("baudrate", "Débit (bauds)", "choice", "115200",
              choices=["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"]),
        Param("command", "Commande de lecture", "text", "",
              hint="Envoyée avant chaque lecture (ex. READ?). Vide = l'appareil envoie ses valeurs seul."),
        Param("eol", "Fin de ligne", "choice", "LF  (\\n)", choices=list(EOL), editable=False),
        Param("field", "Valeur n°", "number", "1",
              hint="Quel nombre lire dans la réponse (1 = le premier). Séparateur décimal : le point."),
        Param("timeout", "Délai max de réponse (s)", "number", "1",
              hint="Temps maximum attendu pour une réponse avant de signaler une erreur."),
    )

    def __init__(self, **values):
        super().__init__(**values)
        self._serial = None

    def describe(self):
        return f"{self.values['port']} · {self.values['baudrate']} bauds"

    def open(self):
        try:
            import serial
        except ImportError:
            raise AcquisitionError("Le module « pyserial » est introuvable.") from None
        port = self.values["port"]
        if not port:
            raise AcquisitionError("Indiquez le port de l'instrument.")
        baud = self.number("baudrate", "Débit", minimum=50, integer=True)
        self._timeout = self.number("timeout", "Délai de réponse", minimum=0.05)
        self._field = self.number("field", "Valeur n°", minimum=1, integer=True)
        self._eol = EOL.get(self.values["eol"], "\n")
        try:
            self._serial = serial.Serial(port, baud, timeout=self._timeout)
        except Exception as exc:
            raise AcquisitionError(f"Impossible d'ouvrir {port} : {exc}") from None

    def close(self):
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None

    def read(self):
        ser = self._serial
        if ser is None:
            raise AcquisitionError("Port non ouvert.")
        command = self.values["command"]
        try:
            if command:
                ser.reset_input_buffer()
                ser.write((command + self._eol).encode("ascii", errors="replace"))
                ser.flush()
                line = ser.readline()
            else:
                # Appareil qui émet seul : on garde la ligne la plus récente.
                line = ser.readline()
                while ser.in_waiting:
                    newer = ser.readline()
                    if newer:
                        line = newer
        except Exception as exc:
            raise AcquisitionError(f"Erreur de lecture série : {exc}") from None
        if not line:
            raise AcquisitionError("Aucune réponse de l'instrument (délai dépassé).")
        numbers = _NUMBER.findall(line.decode("ascii", errors="replace"))
        if len(numbers) < self._field:
            raise AcquisitionError(
                f"Réponse inattendue : {line.decode('ascii', errors='replace').strip()!r}")
        return float(numbers[self._field - 1])
