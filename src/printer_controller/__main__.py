"""Single entry point: ``python -m printer_controller`` (or ``printer-controller``)."""

import argparse

from .app import App


def main():
    parser = argparse.ArgumentParser(description="Printer Controller")
    parser.add_argument("--settings", help="path of the settings file (default: settings.json "
                                           "at the root of the repository)")
    args = parser.parse_args()
    App(settings_path=args.settings).run()


if __name__ == "__main__":
    main()
