#!/usr/bin/env sh
cd "$(dirname "$0")" || exit 1
[ -d .venv ] || python3 -m venv .venv || exit 1
.venv/bin/python -m pip install -q -r requirements.txt || exit 1
exec .venv/bin/python run.py "$@"
