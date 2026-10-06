# Lancement rapide (environnement virtuel)

Windows : double-clic sur `start.bat`  (ou `start.bat --port COM3`).
Linux / macOS : `./start.sh`  (Tkinter requis : `sudo apt install python3-tk`).

Ces scripts créent `.venv`, installent `requirements.txt` puis lancent `run.py`.
À la main : `python -m venv .venv`, activer l'environnement, `pip install -r requirements.txt`,
`python run.py`.

# Nouveautés de l'interface

Cette version remplace l'ancien panneau de mesure par une interface en trois colonnes
(console redimensionnable) et trois onglets :
**Programme**, **Mesure**, **Résultats**.

- **Recalibrage forcé** à chaque connexion (`FORCE_HOMING` dans `config.py`) : aucun déplacement
  tant que G28 n'est pas fait. Le bouton « Aller au début » remplace l'ancien « Revenir au départ ».
- **Système de mesure interchangeable** (`devices.py`) : NI-DAQmx ou instrument série. Chaque
  pilote déclare ses réglages (`Param`) et l'interface génère le formulaire. Pour ajouter un
  système : une classe `AcquisitionDevice` + `@register`.
- **Multiplicateur et unité** (défaut ×1000, V) : valeur affichée = valeur brute × multiplicateur.
- **Deux modes de programme** : « Point par point » (vitesse et mesure propres à chaque position :
  aucune, point, continu, trajet) ou « Parcours » (une vitesse et une fréquence globales).
- **Coordonnées des mesures calculées** (`motion.py`) : instant, fréquence, vitesse, accélération
  et limites par axe (Z bridé). Elles sont lues sur le firmware (M503) au recalibrage et
  modifiables dans « Profil de mouvement… ». La tête bouge aussi en temps réel dans le schéma.
- **Résultats** : tableau complet avec coordonnées, courbes valeur/temps ou valeur/position,
  nuage 3D coloré, export CSV.
- Les réglages (colonnes, système de mesure, unité, profil de mouvement) sont mémorisés dans
  `settings.json`. Le module `matplotlib` n'est plus nécessaire.

# Ender Controller

Programme Python pour contrôler une imprimante **Creality Ender-3** par USB et, à terme, synchroniser son déplacement avec un système d'acquisition de mesures.

## Objectif

Le PC contrôle l'Ender directement par **USB / port série**.

Le logiciel doit permettre de :

- piloter les axes X, Y et Z ;
- effectuer le `HOME` ;
- lire la position de la tête ;
- envoyer du G-code ;
- choisir la vitesse de déplacement ;
- exécuter des programmes de positions ;
- sauvegarder et charger des programmes ;
- acquérir des mesures avec une carte **NI DAQ 6001** ;
- synchroniser les déplacements et les mesures.

L'acquisition de mesure est **indépendante du G-code** : elle peut fonctionner pendant les déplacements ou être déclenchée lorsqu'une position particulière est atteinte.

---

## Architecture

```
                         PC
                          │
              ┌───────────┴───────────┐
              │       Python          │
              │                       │
              │  Ender Controller     │
              │         │             │
              │    ┌────┴────┐        │
              │    │         │        │
              │ Ender      DAQ        │
              │ contrôle   acquisition│
              └────┬─────────┬───────┘
                   │         │
                 USB       USB
                   │         │
                   ▼         ▼
                Ender    NI DAQ 6001
                   │         │
                   │       mesure
                   │         │
                   ▼         ▼
                 Sonde    Voltmètre
                   │         │
                   └────┬────┘
                        │
                     signal
                   à mesurer
```

Les deux fonctions principales sont séparées :

```
Contrôle Ender                    Acquisition
     │                                │
     ▼                                ▼
G-code / mouvements              NI DAQ 6001
     │                                │
     ▼                                ▼
Position XYZ                      Mesure
```

Un autre capteur ou système de mesure pourra remplacer le voltmètre/DAQ sans modifier le cœur du contrôle de l'Ender.

---

## Contrôle de l'Ender

La communication avec l'imprimante utilise :

```
PC ─── USB ─── Ender
```

L'USB apparaît comme un port série sous Windows, par exemple :

```
COM11
```

Python communique avec le firmware Marlin au moyen de **PySerial**.

Les commandes principales sont notamment :

```
G28                  HOME
G90                  coordonnées absolues
G91                  coordonnées relatives
G1 X... Y... Z...    déplacement
M114                 position
M115                 informations firmware
```

Le programme attend les réponses du firmware, notamment `ok`, avant de considérer une commande comme terminée.

Cette synchronisation évite qu'une nouvelle commande de déplacement soit envoyée alors que la précédente est encore en cours.

### Vitesse de déplacement

La vitesse peut être :

- laissée à la **vitesse par défaut** définie dans `config.py` ;
- définie manuellement pour un déplacement particulier.

Exemple :

```
DEFAULT_SPEED = 300
```

La vitesse est exprimée en **mm/min** et correspond au paramètre `F` du G-code.

Par exemple :

```
G1 X50 Y30 F300
```

ou :

```
G1 X50 Y30 F1000
```

La vitesse peut donc être adaptée selon le type de déplacement.

---

## Imprimante utilisée

```
Machine : Ender-3
Firmware : Marlin
Protocol : 1.0
Extrudeur : 1
```

Informations retournées par `M115` :

```
FIRMWARE_NAME:Marlin
SOURCE_CODE_URL:https://github.com/MarlinFirmware/Marlin
PROTOCOL_VERSION:1.0
MACHINE_TYPE:Ender-3
EXTRUDER_COUNT:1
UUID:cede2a2f-41a2-4748-9b12-c55c62f367UUID
```

---

## Acquisition des mesures

L'acquisition est réalisée directement depuis Python avec une **NI DAQ 6001** connectée au PC.

```
PC
 │
 ├── USB → Ender
 │
 └── USB → NI DAQ 6001
              │
              ▼
          signal mesuré
```

Le système de mesure peut notamment comprendre :

```
Générateur
    │
    ▼
Pistes / dispositif sous test
    │
    ▼
Sonde
    │
    ▼
Voltmètre
    │
    ▼
NI DAQ 6001
    │
    ▼
Python
```

La partie **sonde + voltmètre + DAQ** est un module indépendant.

Elle pourra être :

- utilisée avec l'Ender ;
- désactivée pour utiliser uniquement l'imprimante ;
- remplacée par un autre capteur ;
- remplacée par un autre système d'acquisition.

Le contrôle de l'Ender ne doit donc pas dépendre de la présence d'un système de mesure.

---

## Acquisition parallèle aux déplacements

L'acquisition n'est **pas une commande G-code supplémentaire**.

Elle fonctionne en parallèle du contrôle de l'imprimante.

Par exemple :

```
Temps ─────────────────────────────────────────►

Ender :
       G1 X20 ───────────────► G1 X50 ─────────►

Mesure :
       ─────────────────────────────────────────
       acquisition continue

                  ▲       ▲       ▲
                  │       │       │
                mesure  mesure  mesure
```

Il doit être possible de :

- mesurer en continu pendant un déplacement ;
- lire une valeur à un instant donné ;
- déclencher une mesure lorsque l'Ender atteint une position ;
- associer une mesure à une position XYZ ;
- arrêter ou démarrer l'acquisition indépendamment des déplacements.

Les déplacements et l'acquisition doivent donc fonctionner dans des tâches indépendantes.

---

## Programmes de positions

Un programme contient une liste de positions :

```
Position 1
    ↓
Position 2
    ↓
Position 3
    ↓
...
```

Chaque position peut contenir :

```
X
Y
Z
Vitesse (optionnelle)
```

La vitesse peut être différente pour chaque position.

Si aucune vitesse n'est indiquée, `DEFAULT_SPEED` est utilisée.

Exemple :

```
{
    "nom": "Mesure",
    "positions": [
        {
            "x": 20.0,
            "y": 30.0,
            "z": 10.0
        },
        {
            "x": 50.0,
            "y": 30.0,
            "z": 10.0,
            "vitesse": 500
        },
        {
            "x": 80.0,
            "y": 30.0,
            "z": 10.0,
            "vitesse": 1000
        }
    ]
}
```

Dans cet exemple :

```
Position 1 → vitesse par défaut
Position 2 → 500 mm/min
Position 3 → 1000 mm/min
```

Les programmes sont sauvegardés au format JSON.

Le programme de positions contrôle uniquement le déplacement. Le système d'acquisition peut fonctionner indépendamment.

---

## Architecture logicielle

La structure prévue est :

```
ender_controller/
│
├── main.py
├── gui.py
├── printer.py
├── gcode.py
├── program.py
├── acquisition.py
├── config.py
├── requirements.txt
│
└── programs/
    ├── test.json
    └── mesures.json
```

### `main.py`

Point d'entrée de l'application.

### `gui.py`

Interface graphique :

- connexion ;
- contrôle XYZ ;
- HOME ;
- position ;
- choix de la vitesse ;
- G-code ;
- programmes ;
- acquisition.

### `printer.py`

Gestion de la communication avec l'Ender :

- connexion série ;
- déconnexion ;
- envoi G-code ;
- attente de `ok` ;
- HOME ;
- lecture de position.

### `gcode.py`

Génération des commandes G-code.

### `program.py`

Gestion des programmes de positions :

- ajout ;
- modification ;
- suppression ;
- vitesse par position ;
- sauvegarde ;
- chargement ;
- exécution.

### `acquisition.py`

Gestion du système de mesure.

Cette couche doit être indépendante de `printer.py` afin de pouvoir remplacer ou supprimer le système de mesure.

### `config.py`

Paramètres généraux :

```
PORT = "COM11"
BAUDRATE = 115200
DEFAULT_SPEED = 300
```

---

## Fonctionnement final

Le fonctionnement général sera :

```
                 Programme Python
                       │
          ┌────────────┴────────────┐
          │                         │
          ▼                         ▼
    Contrôle Ender             Acquisition
          │                         │
          ▼                         ▼
       G-code                  NI DAQ 6001
          │                         │
          ▼                         ▼
        Ender                    Mesure
          │                         │
          └────────────┬────────────┘
                       ▼
                 Données associées
                position / temps /
                     mesure
```

Les deux flux fonctionnent indépendamment mais peuvent être synchronisés par le logiciel.

Exemple :

```
Ender :       déplacement ───────────────────────►

Acquisition : ───────────────────────────────────►
                    │       │       │
                    ▼       ▼       ▼
                  mesure  mesure  mesure

Position :         X1      X2      X3
```

L'objectif final est donc d'obtenir un système de **positionnement XYZ piloté par Python**, auquel différents systèmes de mesure peuvent être connectés sans modifier le fonctionnement fondamental de l'imprimante.

## Mesure intégrée

L'application peut maintenant piloter un appareil de mesure réel NI-DAQ via `nidaqmx`.

### Connexion

Dans le panneau **Mesure**, sélectionner la carte NI et la voie analogique puis cliquer sur **Connecter**.
Le pilote NI-DAQmx et le module Python `nidaqmx` doivent être installés sur le PC.

### Acquisition temps réel

- fréquence configurable en Hz ;
- graphe temps réel ;
- valeur instantanée ;
- minimum, maximum, moyenne et écart-type ;
- effacement et export CSV ;
- axe horizontal au choix : temps, X, Y, Z ou point de programme.

### Mesure dans un programme

Chaque point peut être configuré avec :

- **aucune** : aucun relevé ;
- **point** : moyenne d'un nombre défini d'échantillons à l'arrivée du point ;
- **continu** : acquisition pendant une durée et à une fréquence définies après l'arrivée du point.

Ces paramètres sont sauvegardés dans le JSON du programme.

## Organisation de l'interface

L'interface est organisée en trois colonnes fixes sous la barre supérieure :

- gauche : contrôle direct de l'imprimante (position, jog, vitesse, recalibrage) ;
- centre : onglets **Programme** et **Mesure** ;
- droite : représentation 3D et édition du point sélectionné.

Les colonnes gauche/droite et l'onglet Mesure disposent de leur propre défilement vertical. Le tableau du programme possède également son propre défilement. La molette de la vue 3D reste réservée au zoom et la console conserve son défilement indépendant.
