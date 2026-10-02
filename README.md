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
