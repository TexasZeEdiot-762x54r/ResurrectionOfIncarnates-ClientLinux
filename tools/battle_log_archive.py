"""Account-bound, durable incremental diagnostic archive. Never runs in the game."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
import subprocess
import time
from contextlib import contextmanager

CHUNK = 8000
READ = 256 * 1024
LOCAL_LIMIT = 128 * 1024 * 1024


def wire_pieces(text):
    if not text:
        yield ''
    while text:
        length=min(CHUNK,len(text))
        while len(json.dumps(text[:length]).encode('ascii'))>10000:
            length=max(1,length*3//4)
        yield text[:length]
        text=text[length:]


def linked(path):
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def private_directory(path):
    if linked(path) or linked(path.parent):
        raise ValueError('Linked archive directory')
    created = not path.exists()
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    if os.name == 'posix':
        if path.stat().st_uid != os.getuid():
            raise PermissionError('Archive owner differs')
        path.chmod(0o700)
    elif created:
        # Secure the directory before SQLite writes any diagnostic data.
        import csv
        import io
        out = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True)
        sid = next(csv.reader(io.StringIO(out)))[1]
        if not re.fullmatch(r'S-1-[0-9-]+', sid):
            raise PermissionError('Archive SID unavailable')
        subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r',
                        '*'+sid+':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'],
                       check=True, capture_output=True)


class Recorder:
    def __init__(self, here, uid, session, version, build, allowed, since=0):
        if (not re.fullmatch(r'[1-9][0-9]{0,19}', uid) or int(uid) >= 2**64
                or not re.fullmatch('[0-9a-f]{32}', session)):
            raise ValueError('Invalid archive identity')
        self.here, self.uid, self.session = Path(here), uid, session
        self.version, self.build, self.allowed, self.since = version, build, tuple(allowed), since
        directory = self.here/'logs'/'battle-archive-pending'
        private_directory(directory)
        self.path = directory/'queue.sqlite3'
        if linked(self.path):
            raise ValueError('Linked archive queue')
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS streams (
                  uid TEXT, session TEXT, file TEXT, generation INTEGER,
                  identity TEXT, prefix BLOB, raw_offset INTEGER, wire_offset INTEGER,
                  complete INTEGER, PRIMARY KEY(uid,session,file,generation));
                CREATE TABLE IF NOT EXISTS chunks (
                  id INTEGER PRIMARY KEY, uid TEXT, session TEXT, version TEXT, build TEXT,
                  file TEXT, generation INTEGER, offset INTEGER, data TEXT, complete INTEGER,
                  matches TEXT, created REAL, retry_after REAL DEFAULT 0);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            if os.name == 'posix':
                self.path.chmod(0o600)
            with db:
                yield db
        finally:
            db.close()

    def capture(self, final=False):
        # Catch up after busy frames; final capture drains the source, not just its tail.
        for _ in range(256 if final else 4):
            with self.connect() as db:
                before=db.execute('SELECT COALESCE(SUM(raw_offset),0),COUNT(*) FROM streams').fetchone()
                before=tuple(before)
            self._capture(final)
            with self.connect() as db:
                after=tuple(db.execute('SELECT COALESCE(SUM(raw_offset),0),COUNT(*) FROM streams').fetchone())
            if before==after:break

    def _capture(self, final=False):
        from workbench_diagnostics import sanitize
        for name in self.allowed:
            path = self.here/'logs'/name
            if linked(path) or not path.is_file() or path.stat().st_mtime < self.since:
                continue
            with path.open('rb') as source, self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                stat = os.fstat(source.fileno())
                identity = str(stat.st_dev)+':'+str(stat.st_ino)
                row = db.execute('SELECT * FROM streams WHERE uid=? AND session=? AND file=? '
                                 'ORDER BY generation DESC LIMIT 1',
                                 (self.uid, self.session, name)).fetchone()
                generation, raw_offset, offset = 0, 0, 0
                prefix = source.read(128)
                signature = struct.pack('<I',len(prefix))+hashlib.sha256(prefix).digest()
                if row:
                    generation = row['generation']
                    length=struct.unpack('<I',row['prefix'][:4])[0]
                    source.seek(0)
                    same_prefix=hashlib.sha256(source.read(length)).digest()==row['prefix'][4:36]
                    source.seek(max(0,row['raw_offset']-128))
                    same_tail=hashlib.sha256(source.read(min(128,row['raw_offset']))).digest()==row['prefix'][36:68]
                    if (row['identity'] != identity or stat.st_size < row['raw_offset']
                            or not same_prefix or not same_tail):
                        generation += 1
                    else:
                        if row['complete']:
                            continue
                        raw_offset, offset, signature = row['raw_offset'], row['wire_offset'], row['prefix'][:36]
                source.seek(raw_offset)
                raw = source.read(READ)
                eof = raw_offset+len(raw) == stat.st_size
                if not (final and eof):
                    boundary = raw.rfind(b'\n')+1
                    if not boundary and len(raw) == READ:
                        raise ValueError('Archive line exceeds capture limit; source retained locally')
                    raw = raw[:boundary]
                complete = bool(final and eof)
                if not raw and not complete:
                    continue
                text = raw.decode('utf-8', 'replace')
                matches = sorted(set(re.findall(r'(?:match=|"round"\s*:\s*")([0-9a-f]{32})', text)))[:64]
                data = sanitize(text, tail=False)
                pending = db.execute('SELECT COALESCE(SUM(length(data)),0) FROM chunks').fetchone()[0]
                if pending+len(data) > LOCAL_LIMIT:
                    raise ValueError('Archive retry queue full; source retained locally')
                pieces = list(wire_pieces(data))
                for i, piece in enumerate(pieces):
                    db.execute('INSERT INTO chunks(uid,session,version,build,file,generation,offset,data,complete,matches,created) '
                               'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                               (self.uid,self.session,self.version,self.build,name,generation,offset,piece,
                                int(complete and i == len(pieces)-1),json.dumps(matches),time.time()))
                    offset += len(piece.encode('ascii'))
                new_offset=raw_offset+len(raw)
                source.seek(max(0,new_offset-128))
                signature+=hashlib.sha256(source.read(min(128,new_offset))).digest()
                db.execute('INSERT OR REPLACE INTO streams VALUES (?,?,?,?,?,?,?,?,?)',
                           (self.uid,self.session,name,generation,identity,signature,new_offset,offset,int(complete)))

    def drain(self, upload, limit=8):
        acknowledged = 0
        for _ in range(limit):
            with self.connect() as db:
                row = db.execute('SELECT * FROM chunks WHERE uid=? AND retry_after<=? ORDER BY id LIMIT 1',
                                 (self.uid,time.time())).fetchone()
            if row is None:
                break
            archive = dict(file=row['file'], generation=row['generation'], offset=row['offset'],
                           data=row['data'], complete=bool(row['complete']), matches=json.loads(row['matches']),
                           sha256=hashlib.sha256(row['data'].encode('ascii')).hexdigest())
            body = dict(session=row['session'],version=row['version'],build=row['build'],archive=archive)
            if len(json.dumps(body).encode()) > 15000:
                raise ValueError('Archive chunk exceeds transport limit')
            try:
                reply = upload(body)
            except Exception as error:
                if getattr(error,'http_status',None) not in (400,404,409,413):
                    raise
                # Keep the rejected stream for diagnosis without pinning newer sessions.
                with self.connect() as db:
                    db.execute('UPDATE chunks SET retry_after=? WHERE uid=? AND session=? AND file=? AND generation=?',
                               (time.time()+3600,self.uid,row['session'],row['file'],row['generation']))
                continue
            if (not isinstance(reply,dict) or reply.get('accepted') is not True
                    or reply.get('next_offset') != row['offset']+len(row['data'].encode('ascii'))):
                break
            with self.connect() as db:
                db.execute('DELETE FROM chunks WHERE id=? AND uid=?', (row['id'], self.uid))
            acknowledged += 1
        return acknowledged

    def pending(self):
        with self.connect() as db:
            return db.execute('SELECT COUNT(*) FROM chunks WHERE uid=?', (self.uid,)).fetchone()[0]
