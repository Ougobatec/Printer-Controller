import json

from printer_controller.settings import Settings, migrate_legacy


def test_defaults_when_file_missing(tmp_path):
    s = Settings.load(tmp_path / "settings.json")
    assert s.printer.baudrate == 115200
    assert s.printer.quick_speeds == [100.0, 300.0, 500.0, 1000.0, 1500.0, 3000.0]
    assert s.app.language == "fr"


def test_only_differences_are_saved(tmp_path):
    path = tmp_path / "settings.json"
    s = Settings.load(path)
    s.printer.port = "COM3"
    s.printer.command_timeout = 12.5
    s.save()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"version": 2, "printer": {"port": "COM3", "command_timeout": 12.5}}
    again = Settings.load(path)
    assert again.printer.port == "COM3" and again.printer.command_timeout == 12.5
    assert again.printer.baudrate == 115200


def test_reset_value_and_section(tmp_path):
    s = Settings.load(tmp_path / "s.json")
    s.set("printer.baudrate", 250000)
    assert s.get("printer.baudrate") == 250000
    s.reset("printer.baudrate")
    assert s.printer.baudrate == 115200
    s.printer.port = "COM9"
    s.reset("printer")
    assert s.printer.port == ""


def test_invalid_values_are_ignored(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"version": 2, "printer": {"baudrate": -5, "port": 12,
                                "command_timeout": "x", "home_timeout": 99}}), encoding="utf-8")
    s = Settings.load(path)
    assert s.printer.baudrate == 115200           # not > 0
    assert s.printer.port == ""                   # wrong type
    assert s.printer.command_timeout == 30.0      # wrong type
    assert s.printer.home_timeout == 99.0         # valid


def test_set_validates(tmp_path):
    s = Settings.load(tmp_path / "s.json")
    for path, value in (("printer.baudrate", 0), ("printer.port", 3), ("program.count", -1)):
        try:
            s.set(path, value)
        except ValueError:
            continue
        raise AssertionError(f"{path}={value!r} should have been refused")


def test_corrupt_file_gives_defaults(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json", encoding="utf-8")
    assert Settings.load(path).printer.baudrate == 115200


def test_legacy_migration(tmp_path):
    legacy = {"printer": {"port": "COM5", "baudrate": 250000},
              "defaults": {"DEFAULT_SPEED": 450, "SPEED_SIZES": [200, 800], "HOME_TIMEOUT": 90,
                           "CSV_DELIMITER": ","},
              "measure": {"signal": {"multiplier": 1000.0, "unit": "mV"}}}
    path = tmp_path / "s.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    s = Settings.load(path)
    assert (s.printer.port, s.printer.baudrate) == ("COM5", 250000)
    assert s.program.speed == 450 and s.printer.quick_speeds == [200.0, 800.0]
    assert s.printer.home_timeout == 90 and s.results.csv_delimiter == ","
    assert (s.sensor.multiplier, s.sensor.unit) == (1000.0, "mV")
    assert migrate_legacy({})["version"] == 2
