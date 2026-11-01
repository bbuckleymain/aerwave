#!/usr/bin/env bash
#
# Launch this tree without installing anything. Uses .venv when ./install.sh
# has made one, and otherwise whatever python3 can already import.
#
#   ./run.sh                  open the window
#   ./run.sh /path/to/film.mkv   open the window on one file
#
set -euo pipefail
cd "$(dirname "$0")"

if [[ -x ".venv/bin/python" ]]; then
    exec .venv/bin/python -m aeris "$@"
fi
exec "${PYTHON:-python3}" -m aeris "$@"
