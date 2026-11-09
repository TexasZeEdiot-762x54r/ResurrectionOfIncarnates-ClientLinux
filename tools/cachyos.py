"""Experimental native-Linux setup and Steam/Proton launcher for the revival client."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from cachyos_paths import native_path

SOURCE_SHA = '34a46fbc9e2ea6c6c9eebfddd0d6e4bef388a3eea5088838dd134588225cedbb'
PORTS = {52710, 52711, 52713}
MIN_DEPOT_BYTES = 500 * 1024 * 1024


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('Duplicate JSON key: ' + key)
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs)


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix=path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2, ensure_ascii=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def supported_platform():
    if platform.system() != 'Linux' or platform.machine().lower() not in ('x86_64', 'amd64'):
        raise RuntimeError('This package targets x86-64 Linux with native Steam and Proton')
    if os.geteuid() == 0:
        raise RuntimeError('Do not run setup or the launcher as root/sudo')
    if os.environ.get('FLATPAK_ID'):
        raise RuntimeError('Flatpak Steam is not supported by this experimental wrapper')


def checked_path(value):
    value = str(value)
    if any(c in value for c in '\0\r\n\\<>:"|?*'):
        raise ValueError('Use a path without Windows-incompatible characters')
    return Path(value).expanduser().resolve()


def validate_sources(root, steam, depot):
    root, steam, depot = map(checked_path, (root, steam, depot))
    if steam.name.lower() == 'roi.exe':
        steam = steam.parent.parent
    elif steam.name.lower() == 'exe':
        steam = steam.parent
    for source in (steam, depot):
        if root == source or root.is_relative_to(source) or source.is_relative_to(root):
            raise ValueError('The new revival folder must be separate from both source installations')
    original = steam / 'exe' / 'roi.exe'
    if not original.is_file() or checksum(original) != SOURCE_SHA:
        raise ValueError('The supported clean Steam exe is missing. The full asset depot alone is not sufficient. '
                         'Required roi.exe SHA256: ' + SOURCE_SHA)
    large = depot / 'resource' / 'masterWindows' / 'GxArchivedFile001.dat'
    if not large.is_file() or large.stat().st_size < MIN_DEPOT_BYTES:
        raise ValueError('Select the completed full depot containing resource/masterWindows/GxArchivedFile001.dat')
    if not (steam / 'resource' / 'masterWindows').is_dir():
        raise ValueError('The clean Steam resource tables are required as well as roi.exe')
    return steam, depot


def checked_destination(root, target):
    root, target = Path(root), Path(target)
    current = target
    while True:
        if current.is_symlink():
            raise ValueError('Refusing a symlinked destination: ' + str(current))
        if current == root:
            break
        if current == current.parent:
            raise ValueError('Destination is not inside its declared root')
        current = current.parent
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Destination escapes its declared root')
    return target


def checked_extract(archive, destination, expected):
    archive, destination = Path(archive), Path(destination)
    checked_destination(destination, destination)
    if checksum(archive) != expected:
        raise ValueError('Vendored runtime digest mismatch: ' + archive.name)
    with zipfile.ZipFile(archive) as source:
        names = source.namelist()
        if len(names) > 10000 or len({n.casefold() for n in names}) != len(names):
            raise ValueError('Duplicate/oversized runtime inventory')
        if sum(item.file_size for item in source.infolist()) > 200 * 1024 * 1024:
            raise ValueError('Runtime extraction exceeds bound')
        for item in source.infolist():
            rel = PurePosixPath(item.filename)
            if (rel.is_absolute() or '\\' in item.filename or ':' in item.filename
                    or any(p in ('', '.', '..') for p in item.filename.rstrip('/').split('/'))
                    or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError('Unsafe runtime member')
            target = destination.joinpath(*rel.parts)
            checked_destination(destination, target)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            raw = source.read(item)
            if target.exists():
                if target.is_symlink() or target.read_bytes() != raw:
                    raise ValueError('Existing runtime file differs; preserve it: ' + str(target))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(raw)


def copy_tree(source, target, skip=()):
    source, target = Path(source), Path(target)
    checked_destination(target, target)
    for item in source.rglob('*'):
        if item.is_symlink():
            raise ValueError('Do not copy symlinked game files: ' + str(item))
        relative = item.relative_to(source)
        if relative.as_posix() in skip:
            continue
        out = target / relative
        checked_destination(target, out)
        if item.is_dir():
            out.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            out.parent.mkdir(parents=True, exist_ok=True)
            # Replace this copy's directory entry, never truncate an existing
            # hardlink that might still refer to the clean source installation.
            fd, temporary = tempfile.mkstemp(prefix='roi-copy-', dir=out.parent)
            os.close(fd)
            try:
                shutil.copy2(item, temporary)
                os.chmod(temporary, stat.S_IMODE(os.stat(temporary).st_mode) | stat.S_IWUSR)
                os.replace(temporary, out)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)


def call_tool(python, *args):
    subprocess.run([str(python), '-I', *map(str, args)], cwd=HERE, check=True)


def verify_package():
    from verify_release import verify
    verify(ROOT)


def setup(args):
    raise ValueError('Use setup_client.py to install the experimental asset delta')


def steam_command(command, cfg, environ):
    command = list(command)
    if command and command[0] == '--':
        command.pop(0)
    if environ.get('SteamAppId') != '258160' or not environ.get('STEAM_COMPAT_DATA_PATH'):
        raise ValueError('Launch through the actual game in Steam, with Proton selected')
    matches = [i for i, arg in enumerate(command) if arg.replace('\\', '/').rsplit('/', 1)[-1].lower() == 'roi.exe']
    if len(matches) != 1:
        raise ValueError('Expected exactly one original roi.exe in Steam %command%')
    at = matches[0]
    prefix = command[:at]
    if (not any(arg.replace('\\', '/').rsplit('/', 1)[-1] == 'proton' for arg in prefix)
            or not any(arg in ('run', 'waitforexitandrun') for arg in prefix)):
        raise ValueError('An unrecognized launch command was supplied; select a Steam Proton compatibility tool')
    if Path(command[at]).resolve() != (Path(cfg['steam']) / 'exe' / 'roi.exe').resolve():
        raise ValueError('Steam is launching a different original installation than the one used for setup')
    return prefix, command[at + 1:]


def owned_listeners(pid, proc_root=Path('/proc')):
    base = Path(proc_root)
    inodes = set()
    for descriptor in (base / str(pid) / 'fd').iterdir():
        try:
            link = os.readlink(descriptor)
        except OSError:
            continue
        if link.startswith('socket:[') and link.endswith(']'):
            inodes.add(link[8:-1])
    result = set()
    for line in (base / 'net' / 'tcp').read_text().splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 10 and fields[3] == '0A' and fields[9] in inodes:
            address, port = fields[1].split(':')
            if address != '0100007F':
                raise RuntimeError('A child service unexpectedly bound outside IPv4 loopback')
            result.add(int(port, 16))
    return result


def signal_owned(child, sig):
    if os.name == 'posix' and os.getpgid(child.pid) == child.pid:
        os.killpg(child.pid, sig)
    elif sig == signal.SIGTERM:
        child.terminate()
    else:
        child.kill()


def stop_owned(children):
    for child in reversed(children):
        if child.poll() is None:
            try:
                signal_owned(child, signal.SIGTERM)
            except ProcessLookupError:
                pass
    for child in reversed(children):
        try:
            child.wait(timeout=6)
        except subprocess.TimeoutExpired:
            try:
                signal_owned(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=3)


def launch(args):
    supported_platform()
    os.environ['ROI_DEPLOYMENT_FILE'] = str(ROOT / 'deployment.local.json')
    verify_package()
    cfg = strict_json((HERE / 'config.json').read_text())
    if cfg.get('release_mode') == 'lan':
        raise ValueError('Steam-free LAN battle adapter has not passed review; no online fallback')
    ready = strict_json((ROOT / '.cachyos-ready.json').read_text())
    if checksum(Path(cfg['game']) / 'exe' / 'roi.exe') != ready['patched_sha256']:
        raise RuntimeError('Patched executable changed since setup; preserve it for review')
    prefix, game_args = steam_command(args.steam_command, cfg, os.environ)
    windows_python = str(HERE / 'python-win' / 'python.exe')
    entry = native_path(str(HERE / 'proton_entry.py'), windows=True)
    command = prefix + [windows_python, '-I', entry]
    logs = HERE / 'logs'
    logs.mkdir(exist_ok=True)
    os.umask(0o077)
    if HERE.is_symlink() or HERE.stat().st_uid != os.getuid():
        raise ValueError('Use a private user-owned client folder')
    HERE.chmod(0o700)
    env = os.environ.copy()
    env['ROI_PROTON_PRIVATE'] = '1'
    env['ROI_DEPLOYMENT_FILE'] = str(ROOT / 'deployment.local.json')
    env['ROI_GAME_ARGUMENTS_JSON'] = json.dumps(game_args)
    # Only host Python services use this POSIX deployment override. The debugger
    # does not read deployment settings; it uses generated config.json via Z:.
    import fcntl
    with (ROOT / '.cachyos-launch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('This revival client is already starting or running')
        call_tool(sys.executable, HERE / 'public_tunnel.py', '--check')
        with (logs / 'proton-preflight.log').open('w') as output:
            subprocess.run(command + ['--preflight'], env=env, cwd=Path(cfg['game']) / 'exe',
                           stdout=output, stderr=subprocess.STDOUT, timeout=120, check=True)
        if args.doctor:
            print('Pinned gateway and Proton API/path preflight passed. No game was launched. '
                  'Debugger hooks and Steam battles remain unverified.')
            return
        with (logs/'account-enroll.log').open('w') as output:
            subprocess.run(command+['--enroll'],env=env,cwd=Path(cfg['game'])/'exe',stdout=output,
                           stderr=subprocess.STDOUT,timeout=120,check=True)
        from network_defaults import LOOPBACK
        for port in PORTS:
            with socket.socket() as probe:
                probe.settimeout(0.25)
                if probe.connect_ex((LOOPBACK, port)) == 0:
                    raise RuntimeError('A required local port is occupied: ' + str(port) + '. No existing service was stopped.')
        children, streams = [], []
        old_handlers = {}
        def interrupted(signum, frame):
            raise KeyboardInterrupt('Launcher interrupted')
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                old_handlers[sig] = signal.signal(sig, interrupted)
            for script, logname in (('server.py', 'server-process.log'), ('public_tunnel.py', 'public-tunnel.log')):
                stream = (logs / logname).open('w')
                streams.append(stream)
                children.append(subprocess.Popen([sys.executable, '-I', str(HERE / script)], env=env,
                                cwd=HERE, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True))
            deadline = time.monotonic() + 10
            while True:
                if any(child.poll() is not None for child in children):
                    raise RuntimeError('A local child service failed. Check tools/logs; no unrelated process was stopped.')
                listening = set().union(*(owned_listeners(child.pid) for child in children))
                if PORTS <= listening:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('Timed out waiting for owned loopback services')
                time.sleep(0.1)
            print('Public internet services ready. Starting the instrumented game inside Steam Proton.', flush=True)
            stream = (logs / 'proton-game.log').open('w')
            streams.append(stream)
            game = subprocess.Popen(command + ['--launch'], env=env, cwd=Path(cfg['game']) / 'exe',
                                    stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            children.append(game)
            while game.poll() is None:
                if any(child.poll() is not None for child in children[:-1]):
                    raise RuntimeError('A local service exited during the game. Check tools/logs.')
                time.sleep(0.3)
            if game.returncode:
                raise RuntimeError('Proton/game exited unsuccessfully. Check tools/logs/proton-game.log and play.log.')
        finally:
            stop_owned(children)
            for stream in streams:
                stream.close()
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    install = sub.add_parser('setup')
    install.add_argument('--steam', required=True)
    install.add_argument('--depot', required=True)
    install.add_argument('--check-sources', action='store_true')
    play = sub.add_parser('launch')
    play.add_argument('--doctor', action='store_true')
    play.add_argument('steam_command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == 'setup':
        setup(args)
    else:
        launch(args)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print(type(error).__name__ + ': ' + str(error), file=sys.stderr)
        raise SystemExit(1)
