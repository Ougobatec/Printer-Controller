import argparse

import config
from gui import start_gui


def main():

    parser = argparse.ArgumentParser(
        description="Contrôle d'une imprimante Ender-3 par USB."
    )

    parser.add_argument(
        "--port",
        default=config.PORT,
        help=f"port série (défaut : {config.PORT})"
    )

    args = parser.parse_args()

    start_gui(port=args.port)


if __name__ == "__main__":
    main()
