#!/bin/sh
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
python -c "import printer_controller" 2>/dev/null || pip install -e .
exec python -m printer_controller "$@"
