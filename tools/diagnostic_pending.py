"""One private, filtered retry report per confirmed Linux account; no credentials."""
import json
import os
from pathlib import Path
import re
import tempfile
import time

MAX_BYTES = 16384
MAX_AGE = 7 * 86400


class Pending:
    def __init__(self, here, uid, allowed):
        if not isinstance(uid, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', uid) or int(uid) >= 2**64:
            raise ValueError('Invalid confirmed identity')
        self.here = Path(here)
        self.uid = uid
        self.allowed = set(allowed)

    def path(self):
        if os.name != 'posix':
            return None
        root = self.here / 'logs'
        if root.is_symlink() or not root.is_dir() or root.stat().st_uid != os.getuid():
            return None
        directory = root / 'diagnostics-pending'
        if directory.is_symlink():
            return None
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.stat().st_uid != os.getuid():
            return None
        directory.chmod(0o700)
        path = directory / (self.uid + '.json')
        return None if path.is_symlink() else path

    def valid(self, body):
        return (isinstance(body, dict) and set(body) == {'session', 'version', 'build', 'logs'}
                and isinstance(body['session'], str) and re.fullmatch('[0-9a-f]{32}', body['session'])
                and isinstance(body['version'], str) and re.fullmatch(r'2\.06 - R[1-9][0-9]{0,8}', body['version'])
                and isinstance(body['build'], str) and re.fullmatch('[0-9a-f]{64}', body['build'])
                and isinstance(body['logs'], dict) and 1 <= len(body['logs']) <= len(self.allowed)
                and all(k in self.allowed and isinstance(v, str) and len(v) <= 2600
                        for k, v in body['logs'].items()))

    def read(self):
        path = self.path()
        if path is None or not path.exists():
            return None
        if time.time() - path.stat().st_mtime > MAX_AGE:
            path.unlink()
            return None
        with path.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('Invalid pending report')
        body = json.loads(raw)
        if not self.valid(body):
            raise ValueError('Invalid pending report')
        return body

    def write(self, body):
        if not self.valid(body):
            raise ValueError('Invalid pending report')
        raw = json.dumps(body, separators=(',', ':')).encode()
        if len(raw) > MAX_BYTES:
            raise ValueError('Invalid pending report size')
        path = self.path()
        if path is None:
            return
        fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def clear(self):
        path = self.path()
        if path is not None:
            path.unlink(missing_ok=True)
