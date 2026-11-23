"""Migrate only Workbench network settings. Never import a save into the server."""
import json
from pathlib import Path

HOST = 'riseofincarnates.artificerreviver.com'
PIN = 'fc47097a9b167799a4dde9c6610c6c3d84699df7c96442a6dfa9968abbc4a033'


def configure(root):
    root = root.resolve()
    if json.loads((root/'roi-update-base.json').read_bytes())['stream'] != 'dev/workbench-windows':
        raise ValueError('Not a Workbench installation')
    path = root/'tools/config.json'
    cfg = json.loads(path.read_bytes())
    if (Path(cfg['game']).resolve() != root/'game' or cfg.get('lobby_mode') != 'server'
            or cfg.get('lobby_transport') not in ('experimental-local', 'tls-public')
            or cfg.get('lobby_server') != '127.0.0.1:52711' or cfg.get('lobby_listen') != '127.0.0.1'
            or cfg.get('public_lobby_host') not in (None, HOST) or cfg.get('lobby_token')):
        raise ValueError('Unexpected Workbench game or network configuration')
    def write(file, value):
        if file.exists() and json.loads(file.read_bytes()) == value:
            return
        import os, tempfile
        with tempfile.NamedTemporaryFile(dir=file.parent, delete=False) as tmp:
            tmp.write(json.dumps(value, indent=2).encode())
        try:
            os.replace(tmp.name, file)
        finally:
            Path(tmp.name).unlink(missing_ok=True)
    write(path.with_name('public-lobby.json'), dict(internet_host=HOST, lan_host=HOST,
          port=52714, certificate_sha256=PIN))
    write(path, dict(cfg, lobby_transport='tls-public', release_mode='internet', public_lobby_host=HOST))


if __name__ == '__main__':
    configure(Path(__file__).resolve().parents[1])
