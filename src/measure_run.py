"""Lien entre l'exécution d'un programme et l'acquisition.

Le `ProgramRunner` appelle ces méthodes dans SON thread : elles peuvent donc
bloquer (mesure ponctuelle, attente d'une durée) sans geler l'interface.

Deux façons de mesurer, choisies dans le programme :

* « point par point » : chaque position a sa mesure (aucune, point, continu
  à l'arrivée, ou trajet pendant le déplacement) ;
* « parcours » : une vitesse et une fréquence globales ; l'acquisition tourne
  pendant TOUT le programme et chaque mesure reçoit ses coordonnées calculées
  à partir de l'instant, de la vitesse et du profil de mouvement.
"""

import threading
import time

import config
from program import PARCOURS, POINTS


class ProgramMeasurer:
    def __init__(self, acquisition, tracker, printer):
        self.acq = acquisition
        self.tracker = tracker
        self.printer = printer
        self.cancel = threading.Event()
        self._program = None
        self._leg_start = 0.0

    # --- Information ---------------------------------------------------
    @staticmethod
    def program_needs_measure(program):
        if program.mode == PARCOURS:
            return bool(program.parcours_mesure)
        return any(wp.mesure != "aucune" for wp in program)

    def _recording_parcours(self):
        p = self._program
        return bool(p and p.mode == PARCOURS and p.parcours_mesure and self.acq.connected)

    # --- Hooks du ProgramRunner (thread du programme) ------------------
    def abort(self):
        self.cancel.set()

    def begin(self, program):
        self.cancel.clear()
        self._program = program
        self.tracker.set_rest(self.printer.last_position)
        if self._recording_parcours():
            self.acq.begin_segment(None, "parcours", program.frequence)

    def before_move(self, index, wp, speed):
        self._leg_start = time.time()
        if self._recording_parcours():
            self.acq.set_index(index)
            self.acq.set_rate(wp.mesure_frequence)
        elif (self._program.mode == POINTS and wp.mesure == "trajet" and self.acq.connected):
            self.acq.begin_segment(index, "trajet", wp.mesure_frequence)

    def after_move(self, index, wp):
        # Le déplacement est terminé : sa durée réelle est connue, on
        # recalcule les coordonnées des mesures prises pendant le trajet.
        if self._recording_parcours():
            self.acq.refine_positions(self._leg_start)
        elif self.acq.in_segment and wp.mesure == "trajet":
            self.acq.end_segment()

    def measure(self, index, wp, position):
        """Mesure à l'arrivée (modes « point » et « continu »)."""
        if not self.acq.connected:
            return
        if wp.mesure == "point":
            self.acq.measure(index, wp.mesure_n, "point", self.cancel, wp.mesure_frequence)
        elif wp.mesure == "continu" and wp.mesure_duree > 0:
            self.acq.begin_segment(index, "continu", wp.mesure_frequence)
            try:
                self.cancel.wait(wp.mesure_duree)
            finally:
                self.acq.end_segment()

    def end(self, program, state):
        """Fin du programme (terminé, stoppé ou en erreur)."""
        if self.acq.in_segment:
            self.acq.end_segment()
        self._program = None
