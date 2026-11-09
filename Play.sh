#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    printf '%s\n' 'Run Setup-CachyOS.sh first. This launcher must be used from Steam launch options.' >&2
    exit 1
fi
if [[ "${1-}" == '--doctor' ]]; then
    shift
    exec "$PYTHON" -I "$ROOT/tools/cachyos.py" launch --doctor -- "$@"
fi
exec "$PYTHON" -I "$ROOT/tools/cachyos.py" launch -- "$@"
