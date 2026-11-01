#!/usr/bin/env bash
#
# The developer counterpart to dist/install-aeris.sh: this tree, installed
# into ./.venv in editable mode, so an edit takes effect the next time you run
# it with no reinstall. The shipping installer copies the source instead,
# which is what you want for a real install and not what you want here.
#
#   ./install.sh          create or update .venv and install in editable mode
#   ./install.sh --clean  throw .venv away first
#
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
VENV=".venv"

if [[ "${1:-}" == "--clean" ]]; then
    rm -rf "$VENV"
fi

if [[ ! -x "$VENV/bin/python" ]]; then
    # System site packages, because python-mpv needs the libmpv the distro
    # installed and PySide6 is a large download to repeat for no reason.
    "$PYTHON" -m venv --system-site-packages "$VENV"
fi

"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -e .
"$VENV/bin/python" -c 'import aeris; print("Aeris", aeris.__version__, "ready in .venv")'
echo "Run it with ./run.sh"
