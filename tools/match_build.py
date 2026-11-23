"""Identify the installed signed release to the dedicated matchmaker."""
import base64
import hashlib
import json
from pathlib import Path
import sys


def fingerprint(root=None):
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    try:
        from roi_update import verify, validate
        base = json.loads((root/'roi-update-base.json').read_bytes())
        envelope = base['release']
        installed = root/'.roi-update'/'installed.json'
        if installed.exists():
            envelope = json.loads(installed.read_bytes())
        manifest = verify(json.dumps(envelope).encode())
        validate(manifest, base['stream'])
        return hashlib.sha256(base64.b64decode(envelope['payload'], validate=True)).digest()
    finally:
        sys.path.remove(str(root))
