"""Interface translations. Code and JSON keys are English; the user picks fr or en."""

import json
from pathlib import Path

LANGUAGES = {"fr": "Français", "en": "English"}
_DIR = Path(__file__).parent / "locales"
_texts: dict = {}
_fallback: dict = {}


def _load(code):
    try:
        return json.loads((_DIR / f"{code}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def set_language(code: str):
    """Select the language ("fr" or "en"; anything else falls back to English)."""
    global _texts, _fallback
    _fallback = _load("en")
    _texts = _load(code) if code in LANGUAGES else _fallback


def t(key: str, **values) -> str:
    """Text of `key` in the current language ({name} placeholders filled from `values`)."""
    if not _fallback:
        set_language("en")
    text = _texts.get(key) or _fallback.get(key) or key
    try:
        return text.format(**values) if values else text
    except (KeyError, IndexError):
        return text
