#!/usr/bin/env bash
# Thin wrapper so the venv never has to be activated by hand.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/.venv/bin/python" "$HERE/run.py" "$@"
