"""Public-only endpoint migration; never changes gameplay or graphics settings."""
import json
import os
from pathlib import Path
import sys
import tempfile

HOST='riseofincarnates.artificerreviver.com'
PIN='fc47097a9b167799a4dde9c6610c6c3d84699df7c96442a6dfa9968abbc4a033'

def configure(root):
    root=Path(root)
    receipt=json.loads((root/'roi-update-base.json').read_bytes())
    if receipt.get('stream') not in ('stable/windows','stable/linux'):
        raise ValueError('Public endpoint requires the public update channel')
    config=root/'tools/config.json';public=root/'tools/public-lobby.json'
    if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (config,public,config.parent)):
        raise ValueError('Linked network settings refused')
    value=json.loads(config.read_text(encoding='utf-8-sig'))
    if value.get('release_mode')=='lan' or value.get('public_lobby_host') not in (None,HOST):
        raise ValueError('Unexpected public network target; settings unchanged')
    endpoint=dict(internet_host=HOST,lan_host=HOST,port=52715,certificate_sha256=PIN)
    if public.exists():
        previous=json.loads(public.read_bytes())
        if previous.get('internet_host')!=HOST or previous.get('certificate_sha256')!=PIN:
            raise ValueError('Unexpected public endpoint identity; settings unchanged')
    for path,data in ((public,endpoint),(config,dict(value,lobby_transport='tls-public',release_mode='internet',public_lobby_host=HOST))):
        if path.exists() and json.loads(path.read_text(encoding='utf-8-sig'))==data:continue
        with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as file:
            file.write(json.dumps(data,indent=2).encode())
            file.flush();os.fsync(file.fileno())
        try:os.replace(file.name,path)
        finally:Path(file.name).unlink(missing_ok=True)

if __name__=='__main__':configure(Path(__file__).resolve().parents[1])
