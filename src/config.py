# Configuration de l'imprimante

PORT = "COM11"
BAUDRATE = 115200

# Vitesse utilisée lorsqu'aucune vitesse personnalisée
# n'est indiquée.
DEFAULT_SPEED = 300  # mm/min

# Vitesses proposées dans l'interface
SPEED_SIZES = [
    100,
    300,
    500,
    1000,
    1500,
    3000
]

# Pas de déplacement manuel
STEP_SIZES = [
    0.1,
    1.0,
    10.0
]