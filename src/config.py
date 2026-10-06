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
# Acquisition (mesure)
# -------------------------------------------------

# Système de mesure sélectionné au premier lancement ("nidaq" ou "serial").
# Ensuite, le dernier choix est mémorisé dans settings.json.
ACQ_BACKEND = "nidaq"

# Valeurs par défaut du pilote NI-DAQmx (modifiables dans l'interface)
DAQ_DEVICE = "Dev1"
DAQ_CHANNEL = "ai0"
DAQ_VOLTAGE_RANGE = (-10.0, 10.0)  # V

# Conversion affichée = valeur brute de l'appareil × multiplicateur
MEASURE_MULTIPLIER = 1000.0
MEASURE_UNIT = "V"

# Fréquence d'acquisition par défaut (Hz) et plafond accepté
ACQ_SAMPLE_RATE = 10
ACQ_MAX_RATE = 1000

# Nombre d'échantillons moyennés pour une mesure ponctuelle
ACQ_AVERAGE_SAMPLES = 5

# Nombre maximum de mesures conservées en mémoire
ACQ_MAX_SAMPLES = 200_000

# Export CSV (Excel en français : ";" et ",")
CSV_DELIMITER = ";"
CSV_DECIMAL = ","

# -------------------------------------------------
# Modèle de mouvement
# -------------------------------------------------
# Sert à estimer où se trouve la tête PENDANT un déplacement (affichage
# temps réel, coordonnées des mesures). Ces valeurs sont remplacées par
# celles du firmware (M503) à chaque connexion si l'imprimante les fournit.
# Défauts du Marlin d'origine de l'Ender-3 : l'axe Z est bridé (5 mm/s).
MAX_AXIS_SPEED = {"X": 500.0, "Y": 500.0, "Z": 5.0}     # mm/s   (M203)
MAX_AXIS_ACCEL = {"X": 500.0, "Y": 500.0, "Z": 100.0}   # mm/s²  (M201)
PRINT_ACCEL = 500.0                                      # mm/s²  (M204 P)

# Impose un recalibrage (G28) à chaque connexion à l'imprimante :
# aucun déplacement n'est possible tant qu'il n'a pas été fait.
FORCE_HOMING = True

# Réglages mémorisés entre deux lancements (colonnes, mesure, ...)
SETTINGS_FILE = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "settings.json")
)

# -------------------------------------------------
# Surcharge locale
# -------------------------------------------------

try:
    from config_local import *  # noqa: F401,F403
except ImportError:
    pass
