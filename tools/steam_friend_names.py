"""Private, account-bound display-name snapshot from Proton to its Linux service."""
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
import unicodedata

HERE = Path(__file__).resolve().parent
FILE = HERE / 'steam-friend-names.json'
MAX_BYTES = 256 * 1024
MAX_AGE = 90
_cached = None
_lock = threading.Lock()


def clean(value):
    if not isinstance(value, str):
        return ''
    value = ''.join(c for c in value if not unicodedata.category(c).startswith('C')).strip()
    if value == '[unknown]':
        return ''
    return value.encode('utf-8')[:255].decode('utf-8', 'ignore')


def valid_uid(value):
    return (isinstance(value, str) and value.isascii() and value.isdigit()
            and str(int(value)) == value and 0 < int(value) < 2**64)


def validate(value, owner, now):
    if (not isinstance(value, dict) or set(value) != {'schema', 'owner', 'updated', 'names'}
            or value['schema'] != 1 or not valid_uid(owner) or value['owner'] != owner
            or type(value['updated']) not in (int, float)
            or not 0 <= now - value['updated'] <= MAX_AGE
            or not isinstance(value['names'], dict) or len(value['names']) > 512):
        raise ValueError('Invalid or stale Steam name snapshot')
    names = {}
    for uid, name in value['names'].items():
        if not valid_uid(uid) or not isinstance(name, str) or len(name.encode('utf-8')) > 255:
            raise ValueError('Invalid Steam display-name entry')
        name = clean(name)
        if name:
            names[uid] = name
    return names


def get(uid, owner=None, path=None, now=None):
    """Read display text only. Never use this snapshot to authorize an identity."""
    global _cached
    path = Path(path) if path is not None else FILE
    now = time.time() if now is None else now
    try:
        if owner is None:
            from roi_account_client import read_credential
            credential = read_credential(HERE / 'account-auth.json')
            owner = credential['uid'] if credential else None
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES
                or (os.name == 'posix' and info.st_uid != os.getuid())):
            return ''
        key = (str(path), info.st_mtime_ns, info.st_size, owner)
        with _lock:
            if _cached is not None and _cached[0] == key:
                value = _cached[1]
            else:
                fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                with os.fdopen(fd, 'rb') as stream:
                    opened = os.fstat(stream.fileno())
                    if (opened.st_ino, opened.st_dev) != (info.st_ino, info.st_dev):
                        return ''
                    raw = stream.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    return ''
                value = json.loads(raw)
                _cached = key, value
            return validate(value, owner, now).get(str(uid), '')
    except (OSError, ValueError, TypeError, KeyError, ImportError):
        return ''


def collect(directory):
    """Run read-only Steam interface calls on the existing directory worker."""
    def job():
        import ctypes
        import steam_dir as sdk
        directory.api.SteamUser.restype = sdk.c_vp
        directory.api.SteamFriends.restype = sdk.c_vp
        user, friends = directory.api.SteamUser(), directory.api.SteamFriends()
        if not user or not friends:
            return None
        sid = sdk.c_u64()
        sdk._vcall(user, 2, sdk.c_vp, (sdk.c_vp, ctypes.cast(ctypes.byref(sid), sdk.c_vp)))
        owner = str(sid.value)
        if not valid_uid(owner):
            return None
        count = sdk._vcall(friends, 3, sdk.c_int, (sdk.c_int, 4))
        names = {}
        for index in range(min(512, max(0, count))):
            friend = sdk.c_u64()
            sdk._vcall(friends, 4, sdk.c_vp,
                       (sdk.c_vp, ctypes.cast(ctypes.byref(friend), sdk.c_vp)),
                       (sdk.c_int, index), (sdk.c_int, 4))
            raw = sdk._vcall(friends, 7, sdk.c_char_p, (sdk.c_u64, friend.value))
            name = clean((raw or b'').decode('utf-8', 'replace'))
            if valid_uid(str(friend.value)) and name:
                names[str(friend.value)] = name
        return dict(schema=1, owner=owner, updated=time.time(), names=names)
    return directory._do(job, timeout=3)


def publish(value, path=None):
    path = Path(path) if path is not None else FILE
    validate(value, value['owner'], time.time())
    fd, temp = tempfile.mkstemp(prefix='.steam-names-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def start():
    """Optional daemon: no hooks, callbacks, invitations, tickets or network writes."""
    stop = threading.Event()

    def loop():
        try:
            import steam_dir
            directory = steam_dir.get(log=lambda message: None)
            while not stop.is_set():
                value = collect(directory)
                if value is not None:
                    publish(value)
                stop.wait(30)
        except (OSError, ValueError, TypeError, AttributeError):
            pass  # Names remain optional; never stop the game for display data.

    threading.Thread(target=loop, name='Steam display names', daemon=True).start()
    return stop
