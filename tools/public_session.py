"""Local-only, last-launch diagnostic retention. This module has no network code."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import time
import uuid
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))

FILES = ('server.log', 'play.log', 'public-tunnel.err.log', 'proton-game.log',
         'proton-preflight.log', 'public-tunnel.log', 'server-process.log',
         'launcher.log', 'diagnostics-process.log')
TRUNCATED_ON_LAUNCH = set(FILES) - {'server.log'}
MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024


def read_json(path):
    if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
        raise ValueError('Linked report path refused')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    from battle_log_archive import private_directory, linked
    if linked(path) or linked(path.parent):
        raise ValueError('Linked report path refused')
    private_directory(path.parent)
    temp = path.with_suffix('.tmp')
    if linked(temp):
        raise ValueError('Linked temporary report refused')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def operating_system():
    result = dict(system=platform.system(), release=platform.release(),
                  architecture=platform.machine())
    if result['system'] == 'Linux':
        try:
            info = platform.freedesktop_os_release()
            result['distro'] = info.get('PRETTY_NAME') or info.get('NAME') or ''
        except (OSError, AttributeError):
            result['distro'] = ''
    return result


def stamp(path):
    from battle_log_archive import linked
    if linked(path) or not path.is_file():
        return None
    stat = path.stat()
    with path.open('rb') as source:
        prefix = source.read(128)
        source.seek(max(0, stat.st_size-128))
        tail = source.read(128)
    return dict(identity=[stat.st_dev, stat.st_ino], size=stat.st_size,
                prefix=hashlib.sha256(prefix).hexdigest(),
                tail=hashlib.sha256(tail).hexdigest())


def begin(root):
    root = Path(root)
    here = root/'tools'
    from battle_log_archive import private_directory
    folder = here/'bug-reports'
    private_directory(folder)
    current = folder/'last-launch.json'
    if current.exists():
        previous = read_json(current)
        if not previous.get('closed'):
            finish(root, previous['session'], recovered=True)
    started = time.time()
    item = dict(schema=1, session=uuid.uuid4().hex, started=started, closed=False,
                os=operating_system(), before={n:stamp(here/'logs'/n) for n in FILES})
    save(current, item)
    return item['session']


def capture(root, item):
    from battle_log_archive import linked
    from workbench_diagnostics import sanitize
    logs, warnings, total = {}, [], 0
    for name in FILES:
        path = Path(root)/'tools/logs'/name
        if linked(path) or not path.is_file():
            continue
        stat = path.stat()
        if stat.st_mtime < item['started']:
            continue
        offset = 0
        previous = item.get('before', {}).get(name)
        with path.open('rb') as source:
            if previous and name not in TRUNCATED_ON_LAUNCH:
                size = previous['size']
                if [stat.st_dev,stat.st_ino] == previous['identity'] and stat.st_size >= size:
                    source.seek(max(0,size-128))
                    if hashlib.sha256(source.read(min(size,128))).hexdigest() == previous['tail']:
                        offset = size
            source.seek(offset)
            limit = min(MAX_FILE, MAX_TOTAL-total)
            raw = source.read(limit+1)
        if len(raw)>limit:
            warnings.append(name+': capture limit exceeded; remaining log retained on this PC')
            raw=raw[:limit]
        total += len(raw)
        logs[name] = sanitize(raw.decode('utf-8','replace'), tail=False)
    return logs, warnings


def finish(root, session, recovered=False):
    if not isinstance(session,str) or not re.fullmatch('[0-9a-f]{32}',session):
        raise ValueError('Invalid launch identity')
    root=Path(root);folder=root/'tools/bug-reports';current=folder/'last-launch.json'
    item=read_json(current)
    if item['session']!=session:
        raise ValueError('Another launch owns the log capture')
    logs,warnings=capture(root,item)
    try:
        account=read_json(root/'tools/account-auth.json')
        uid=account.get('uid','')
        steam_id=uid if isinstance(uid,str) and uid.isdecimal() and len(uid)==17 else None
    except (OSError,ValueError):
        steam_id=None
    record=dict(schema=1,session=session,started=item['started'],ended=time.time(),
                steam_id=steam_id,os=item['os'],logs=logs,warnings=warnings,
                recovered_after_unclosed_launch=recovered)
    save(folder/(session+'.json'),record)
    item.update(closed=True,record=session+'.json')
    save(current,item)
    return record


def latest(root):
    folder=Path(root)/'tools/bug-reports';item=read_json(folder/'last-launch.json')
    if not item.get('closed'):
        raise ValueError('Launch not finalized; close the game before reporting')
    if not re.fullmatch('[0-9a-f]{32}\\.json',item.get('record','')):
        raise ValueError('Invalid retained launch path')
    return read_json(folder/item['record'])


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('begin','finish'))
    parser.add_argument('--session');args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    if args.action=='begin':print(begin(root))
    else:finish(root,args.session)


if __name__=='__main__':main()
