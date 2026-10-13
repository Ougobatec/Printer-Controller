# Printer Controller

Pilote une imprimante **Marlin** (G-code) en USB, exécute des programmes de déplacement avec mesures
(carte NI-DAQ ou instrument série quelconque) et affiche les résultats : tableaux, graphes, export
Excel et images PNG. L'interface est en français ou en anglais.

## Lancer

- Windows : double-clic sur `start.bat`
- Linux / macOS : `./start.sh`

Les scripts créent un environnement `.venv`, installent le projet puis lancent
`python -m printer_controller`. Les cartes NI-DAQ demandent en plus le pilote NI-DAQmx et
`pip install .[nidaq]`. Option : `--settings CHEMIN` pour utiliser un autre fichier de paramètres.

## Utilisation

1. **Imprimante** (bouton en haut) : choisir le port (avec le nom de l'appareil) et le débit, puis
   Connecter. L'app lit le modèle, le firmware, les limites, les vitesses et accélérations, et
   recalibre l'imprimante si elle ne l'est pas déjà depuis son démarrage (`G28 O`).
   « Paramètres de l'imprimante » (colonne gauche) affiche chaque valeur avec sa source
   (détecté, manuel, par défaut) et permet de les modifier ou de revenir aux valeurs détectées.
   Les déplacements hors limites sont toujours refusés.
2. **Carte** (bouton en haut) : instrument série (commande, fin de ligne, extraction de la valeur
   avec exemple de ligne et valeur extraite) ou carte NI-DAQ détectée. L'onglet **Mesure** montre la
   valeur en direct (multiplicateur, unité, filtre, fréquence) sans rien enregistrer.
3. **Programme** : créer des lignes depuis le panneau « Point » (« Ajouter au programme »), puis les
   cocher et les régler dans le formulaire (OK pour valider). Chaque ligne a une vitesse (mm/min),
   une pause et une mesure : *Aucune*, *Simple* (une lecture), *Moyenne* (durée ou nombre de
   mesures + fréquence) ou *Trajet* (mesures pendant le déplacement, positions calculées ; impossible
   sur la première ligne). Enregistrer / ouvrir en JSON ; lancer, pause/reprendre, arrêter.
4. **Résultats** : tableau des mesures et tableau des groupes (moyennes) en temps réel, graphes
   (temps, X, Y, Z, 3D), export Excel (une feuille par tableau) et export des graphes en PNG avec
   l'orientation de la vue 3D de votre choix.

Le terminal (en bas) montre les échanges avec l'imprimante et envoie du G-code ; il se masque, se
redimensionne et s'efface. Pendant un programme, seul STOP est autorisé.

## Paramètres

Tout est défini dans `src/printer_controller/settings.py` : les valeurs par défaut sont celles des
dataclasses. `settings.json` (à la racine, ignoré par git) ne contient que ce que vous avez changé.
Supprimer une clé remet sa valeur par défaut. Les paramètres de l'imprimante sont dans la fenêtre
« Paramètres de l'imprimante », ceux de l'application (programme, mesure en direct, résultats,
langue) dans le bouton « Paramètres » de l'en-tête. Vitesses en mm/min partout.

## Organisation

```
src/printer_controller/
  app.py          fenêtre principale
  settings.py     tous les paramètres
  i18n.py         traductions (locales/fr.json, locales/en.json)
  threads.py      tâches de fond
  core/           logique sans interface (imprimante, capteur, programme, résultats)
  ui/             interface Tkinter
examples/         programmes d'exemple (JSON)
tests/            tests (fausse imprimante Marlin, faux capteurs)
```

## Tests

```
pip install -e .[dev]
pytest                      # logique : imprimante, capteur, programme, résultats
xvfb-run pytest             # Linux sans écran : ajoute les tests de la fenêtre
```

Voir [PLAN.md](PLAN.md) pour les décisions de conception et l'état d'avancement.
