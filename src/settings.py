"""Réglages mémorisés entre deux lancements (fichier settings.json).

Contient la disposition de l'interface, le système de mesure choisi avec ses
réglages, le multiplicateur / l'unité, et le profil de mouvement.
"""

import json
import os
import threading

import config


class Settings:
    def __init__(self, path=None):
        self.path = path or config.SETTINGS_FILE
        self.data = {}
        self._lock = threading.Lock()
        self.load()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.data = {}

    def get(self, *keys, default=None):
        node = self.data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def set(self, *keys_and_value):
        *keys, value = keys_and_value
        with self._lock:
            node = self.data
            for key in keys[:-1]:
                node = node.setdefault(key, {})
                if not isinstance(node, dict):
                    return
            node[keys[-1]] = value

    def save(self):
        """Écriture atomique ; les erreurs disque ne doivent jamais gêner l'application."""
        tmp = self.path + ".tmp"
        try:
            with self._lock:
                text = json.dumps(self.data, indent=2, ensure_ascii=False)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, self.path)
        except OSError:
            pass
