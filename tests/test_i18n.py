import json
from pathlib import Path

from printer_controller import i18n

LOCALES = Path(i18n.__file__).parent / "locales"


def test_both_languages_have_the_same_keys():
    en = json.loads((LOCALES / "en.json").read_text(encoding="utf-8"))
    fr = json.loads((LOCALES / "fr.json").read_text(encoding="utf-8"))
    assert set(en) == set(fr)
    assert all(en.values()) and all(fr.values())


def test_translation_and_fallback():
    i18n.set_language("fr")
    assert i18n.t("button.emergency") == "ARRÊT D'URGENCE"
    i18n.set_language("en")
    assert i18n.t("button.emergency") == "EMERGENCY STOP"
    assert i18n.t("does.not.exist") == "does.not.exist"       # unknown key: the key itself
    i18n.set_language("xx")                                   # unknown language: English
    assert i18n.t("tabs.program") == "Program"


def test_placeholders():
    i18n.set_language("en")
    assert i18n.t("run.state.running", n=2, total=5) == "Running — line 2/5"
