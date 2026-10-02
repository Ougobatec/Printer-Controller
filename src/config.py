"""Configuration générale.

Pour une configuration propre à un poste, créer un fichier
`config_local.py` (ignoré par git) à côté de ce fichier : ses valeurs
écrasent celles ci-dessous.
"""

import os

# -------------------------------------------------
# Imprimante
# -------------------------------------------------

PORT = "COM11"
BAUDRATE = 115200

# Temps laissé à Marlin pour démarrer après l'ouverture du port (s)
STARTUP_DELAY = 3

# Vitesse utilisée lorsqu'aucune vitesse personnalisée
# n'est indiquée.
DEFAULT_SPEED = 300  # mm/min

# Vitesse maximale acceptée (sécurité)
MAX_SPEED = 6000  # mm/min

# Vitesses proposées dans l'interface
SPEED_SIZES = [
    100,
    300,
    500,
    1000,
    1500,
    3000
]


# Limites logicielles de la zone de travail (mm) : Ender-3 standard.
# Les déplacements XYZ de l'interface et des programmes qui sortent de
# ces limites sont refusés. (Le G-code saisi à la main n'est pas filtré.)
ENFORCE_LIMITS = True
AXIS_LIMITS = {
    "X": (0.0, 220.0),
    "Y": (0.0, 220.0),
    "Z": (0.0, 250.0),
}

# Délais d'attente maximum (s)
COMMAND_TIMEOUT = 30
HOME_TIMEOUT = 120
MOVE_TIMEOUT = 300

# -------------------------------------------------
# Programmes
# -------------------------------------------------

PROGRAMS_DIR = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "programs"
    )
)

# -------------------------------------------------
# Acquisition
# -------------------------------------------------

# "simulation" ou "nidaq"
ACQ_BACKEND = "simulation"

# NI DAQ 6001
DAQ_DEVICE = "Dev1"
DAQ_CHANNEL = "ai0"
DAQ_VOLTAGE_RANGE = (-10.0, 10.0)  # V

# Acquisition continue
ACQ_SAMPLE_RATE = 10  # Hz

# Nombre d'échantillons moyennés pour une mesure ponctuelle
ACQ_AVERAGE_SAMPLES = 5

# Nombre maximum d'échantillons continus conservés en mémoire
ACQ_MAX_SAMPLES = 100_000

# Export CSV (Excel en français : ";" et ",")
CSV_DELIMITER = ";"
CSV_DECIMAL = ","

# -------------------------------------------------
# Surcharge locale
# -------------------------------------------------

try:
    from config_local import *  # noqa: F401,F403
except ImportError:
    pass
