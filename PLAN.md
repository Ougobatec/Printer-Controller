# Plan de refonte — Printer Controller

Objectif : garder la même interface, mais avec un code et un repo beaucoup plus simples
(environ 4 000 lignes contre 7 800, aucun fichier de plus de 300 lignes).

## Décisions

| Sujet | Décision |
|---|---|
| Unité de vitesse | **mm/min partout** (les valeurs M203 en mm/s sont converties une seule fois, dans `core/printer/profile.py`). Accélérations en mm/s², positions en mm, durées en s, fréquences en Hz |
| Langue | Code, clés JSON, commentaires et logs en **anglais**. Interface en **français ou anglais** au choix (`locales/fr.json`, `locales/en.json`, appliqué au redémarrage) |
| Firmware | **Marlin uniquement** |
| Types de mesure | `none` (Aucune), `simple` (Simple), `average` (Moyenne), `trajet` → `path` (Trajet) |
| Trajet | Fréquence **ou** nombre de mesures ; un nombre N devient la fréquence N / durée estimée du mouvement. Interdit sur la première ligne |
| D1 Filtre du capteur | Appliqué à la mesure en direct uniquement ; les mesures du programme gardent la valeur brute (moyennes et écarts-types exacts) |
| D2 Paramètres modifiés de l'imprimante | Surcharges côté application seulement (jamais écrites dans l'imprimante : ni `M203` ni `M500`) |
| D3 Changement de langue | Appliqué au redémarrage |
| D4 Terminal pendant un programme | Seuls les arrêts sont autorisés |
| D5 Valeurs par défaut sans détection | Zone 200 × 200 × 200 mm, 6 000 mm/min en X et Y, 300 mm/min en Z, 500 mm/s², marquées « par défaut manuel » |

## Principes

- **Deux couches.** `core/` n'importe jamais Tkinter (testable avec une fausse imprimante et un faux capteur). `ui/` ne contient que de l'affichage.
- **Un seul endroit pour les paramètres : `settings.py`.** Les défauts sont les valeurs des dataclasses. `settings.json` ne stocke que les écarts (ignoré par git). « Remettre par défaut » = supprimer la clé.
- **Pas de classe géante.** Un panneau = une classe qui reçoit ses dépendances. Les panneaux reçoivent leurs dépendances et se parlent par des fonctions de rappel simples (le bus d'événements prévu au départ a été abandonné : inutile).
- **Un seul helper de thread** (`threads.py`) : tâche de fond puis retour dans l'interface.
- **Un point d'entrée** : `python -m printer_controller`.

## Arborescence

```
printer-controller/
├─ README.md · PLAN.md · pyproject.toml · .gitignore · start.bat · start.sh
├─ examples/                      programmes JSON d'exemple
├─ tests/                         pytest : core uniquement
└─ src/printer_controller/
   ├─ __main__.py                 seul point d'entrée
   ├─ app.py                      fenêtre principale : assemble les panneaux
   ├─ settings.py · i18n.py · threads.py
   ├─ locales/                    fr.json · en.json
   ├─ core/
   │  ├─ printer/   connection.py · gcode.py · profile.py · identify.py · motion.py · jog.py · homing.py
   │  ├─ sensor/    base.py · line_parser.py · serial_sensor.py · nidaq_sensor.py · filters.py · acquisition.py
   │  ├─ program/   model.py · io.py · runner.py · recorder.py
   │  └─ results/   store.py · export_xlsx.py · export_images.py
   └─ ui/
      ├─ theme.py · widgets.py · console.py · header.py · placeholder.py
      ├─ printer_panel.py · printer_popover.py · printer_dialog.py
      ├─ sensor_popover.py · sensor_live.py
      ├─ program_tab.py · measure_editor.py · results_tab.py
      ├─ view3d.py · point_panel.py · app_settings_dialog.py
```

## Modèle de données

**Settings** : sections `app`, `printer`, `sensor`, `program`, `results` (voir `settings.py`).

**PrinterProfile** : chaque champ est une valeur avec sa source (`detected`, `manual` ou `default`).

| Champ | Commande | Éditable |
|---|---|---|
| Modèle, firmware | `M115` | Non |
| Limites X/Y/Z | `M211` | Oui |
| Vitesse max par axe | `M503` (`M203`) | Oui |
| Accélération max, accélération de déplacement | `M503` (`M201`, `M204`) | Oui |
| Pas par mm (information) | `M503` (`M92`) | Non |
| Position, état de recalibrage | `M114`, `G28 O` | Non |

Un champ non fourni prend la valeur par défaut D5, marquée « paramètre par défaut manuel ». Un champ modifié passe à `manual`. « Remettre les valeurs détectées » supprime les surcharges.

**Programme** (JSON version 2) : `Point(x, y, z, speed, pause, measure)` avec `measure` = `none` | `simple` | `average` (par `duration` ou `count`, plus `rate_hz`) | `path` (par `rate_hz` ou `count`). Les anciens fichiers (parcours, point, continu) sont convertis à l'ouverture.

**Résultats** : `Measurement(n, timestamp, elapsed_s, x, y, z, position_kind[defined|computed], kind[simple|average|path], raw, value, unit, group_id)` et `Group(id, first_timestamp, duration_s, count, x, y, z, mean, std, min, max, unit)`.

## Phases

Chaque phase est livrée, testée avec les faux périphériques, puis validée sur le matériel avant la suivante.

- [x] **Phase 1 — Fondations.** Squelette du repo, `settings.py` (écarts seuls, migration de l'ancien `settings.json`), `i18n.py` fr/en, bus d'événements, threads, thème et widgets repris, fenêtre principale avec la mise en page actuelle (en-tête, colonne gauche, onglets, colonne droite, console masquable/redimensionnable/effaçable), tests.
- [x] **Phase 2 — Imprimante.**
  - 2.1 Connexion : ports COM avec leur nom, débit au choix ; un seul fil d'envoi attend chaque `ok` ; arrêts prioritaires (`M410`, `M112`).
  - 2.2 Identification (`M115`, `M503`, `M211`, `M114`) → `PrinterProfile`.
  - 2.3 Recalibrage : `G28 O` à chaque connexion (recalibre seulement si l'imprimante ne l'est pas depuis son démarrage, même avant la connexion à l'app).
  - 2.4 Fenêtre « Paramètres de l'imprimante » : identité, limites et vitesses avec badge de source, sécurité (recalibrer, recalibrage automatique, vitesse max bridée), délais de l'app, boutons de vitesse rapide. Limites toujours appliquées, modifiables à la main.
  - 2.5 Position en temps réel : profil trapézoïdal par axe, corrigé par `M114` quand le programme le permet, marquée « calculée » ou « vérifiée ».
  - 2.6 Déplacement manuel : maintenir pour avancer, relâcher pour arrêter ; segments courts, file bornée, une seule session, protection contre les appuis rapides.
  - 2.7 Terminal relié à l'imprimante (trafic en direct, saisie de G-code, historique).
  - 2.8 Cadre de connexion du bouton « Imprimante ».
  - Tests avec un `FakeMarlin`.
- [x] **Phase 3 — Appareil de mesure.** Interface `Sensor` ; instrument série (commande, fin de ligne, extraction par `line_parser.py` avec séparateurs, position, texte ou regex, exemple de réponse et valeur extraite, lecture d'une vraie ligne) ; NI-DAQ (détection des cartes, propriétés en lecture seule, voie / branchement / plage au choix) ; acquisition avec fréquence plafonnée, filtres, multiplicateur, unité ; graphe en direct sans enregistrement ; cadre de connexion du bouton « Carte ».
- [x] **Phase 4 — Programme.** Modèle et JSON v2, validation, nouveau / ouvrir / enregistrer ; exécution (aller au départ, lancer, pause/reprendre, arrêter) ; `recorder.py` (simple, moyenne, trajet avec coordonnées calculées) ; tableau à cases à cocher, cadre d'édition avec OK, popup de mesure ; valeurs par défaut des lignes, plafonnées par les maximums de l'imprimante et de la carte.
- [x] **Phase 5 — Résultats.** Tableau des mesures et tableau des groupes en temps réel ; export Excel (une feuille par tableau) ; graphes (valeur selon le temps, selon X/Y/Z, 3D) ; export PNG avec orientation 3D choisie ; dossier d'export par défaut.
- [x] **Phase 6 — Aide à la saisie.** Vue 3D de l'imprimante (axes, volume de travail, points et trajectoire du programme, position actuelle) ; panneau « Point » (position actuelle, aller au point).
- [x] **Phase 7 — Finition.** Fenêtre « Paramètres de l'application » par catégorie (programme, mesure, résultats, langue) ; README, scripts de lancement, tests complets.

## Risques

| Risque | Parade |
|---|---|
| Estimation de position imprécise (jonction entre mouvements) | Modèle conservateur, correction `M114`, écart mesuré et affiché, marqué « calculée » |
| `G28 O` absent d'un vieux Marlin | Repli sur un G28 complet après avertissement |
| La carte redémarre à la connexion (DTR) | Attente du message de démarrage ou d'une réponse à `M105` |
| Propriétés NI-DAQ variables selon les cartes | Champs manuels si la carte ne fournit pas la valeur |
| Plantage en cas d'appuis rapides sur le jog | Machine à états, file bornée, tests sous rafale |

## État d'avancement (toutes les phases sont réalisées)

| Phase | Réalisé |
|---|---|
| 1 | Squelette, `settings.py`, `i18n.py`, `threads.py`, thème, widgets, fenêtre, console |
| 2 | `core/printer/` : connexion série (un seul fil d'envoi, arrêts prioritaires), identification (`M115`, `M503`, `M211`, `M114`), profil avec sources détecté / manuel / par défaut, `G28 O` à la connexion, modèle de mouvement trapézoïdal, déplacement manuel sécurisé ; fenêtre « Paramètres de l'imprimante » ; cadre de connexion |
| 3 | `core/sensor/` : capteur série (commande, fin de ligne, extraction), NI-DAQ, filtres, acquisition ; cadre de connexion avec exemple de ligne et valeur extraite ; mesure en direct |
| 4 | `core/program/` : modèle (Aucune, Simple, Moyenne, Trajet), JSON v2 avec conversion des anciens fichiers, exécution (aller au départ, lancer, pause/reprendre, arrêter), enregistrement des mesures ; onglet Programme |
| 5 | `core/results/` : deux tableaux (mesures, groupes), export Excel (une feuille par tableau) et CSV, export PNG avec orientation 3D ; onglet Résultats |
| 6 | Vue 3D de l'imprimante (volume, points et trajectoire, position, point visé) et panneau « Point » |
| 7 | Fenêtre « Paramètres de l'application » par catégorie, README, scripts de lancement, tests |

### Écarts par rapport au plan

- **Taille du code** : environ 6 300 lignes pour le paquet (7 800 avant), dont environ 820 lignes reprises telles quelles (`theme.py`, `widgets.py`, `plots.py`). Quatre fichiers dépassent 300 lignes : `app.py`, `ui/sensor_popover.py`, `ui/widgets.py`, `ui/plots.py`.
- **Pause** : elle prend effet à la fin de l'étape en cours (un déplacement en cours n'est pas interrompu) ; « Arrêter » coupe tout de suite (`M410`).
- **NI-DAQ** : testé uniquement avec un faux module `nidaqmx` ; à vérifier sur votre carte (propriétés lues : résolution, fréquence max, plages, modes de branchement).
- **Imprimante** : testée avec une fausse Marlin (`tests/fake_marlin.py`) qui reproduit le comportement du firmware (`ok` dès qu'un mouvement est en file, `M114` donne la fin des mouvements en file, `G28 O` conditionnel, `M410`). À valider sur votre Ender-3.
