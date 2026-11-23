#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    printf '%s\n' 'Run setup_client.py first. This launcher must be used from Steam launch options.' >&2
    exit 1
fi
if [[ "${1-}" == '--doctor' ]]; then
    shift
    exec "$PYTHON" -I "$ROOT/tools/cachyos.py" launch --doctor -- "$@"
fi
"$PYTHON" -I "$ROOT/roi_update.py" --launch
exec "$PYTHON" -I "$ROOT/tools/cachyos.py" launch -- "$@"
