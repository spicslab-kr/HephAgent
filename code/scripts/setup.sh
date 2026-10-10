#!/usr/bin/env sh
# Create the local virtual environment and install the package in editable mode.
# Run from the repository root:  sh scripts/setup.sh
set -eu
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "python3 not found; install Python 3.11+"; exit 1; }
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
echo
echo "Setup complete. Activate with:  . .venv/bin/activate"
echo "Then try:                      sdlc --help   and   pytest"
