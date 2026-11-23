"""Attach the installation credential only to a matching native login frame."""
import asyncio
import struct

KEEPALIVE_SECONDS = 30


def keepalive_frame(uid):
    body = struct.pack('<QHHQ', 0, 0, 0xE605, int(uid))
    return struct.pack('<HH', len(body)+4, len(body))+body+bytes(2)


def login_frame(frame, uid, token, build=b''):
    if len(frame) < 26:
        raise ValueError('Incomplete native login')
    length, body_size = struct.unpack_from('<HH', frame)
    if length+2 != len(frame) or body_size != length-4 or not 24 <= length <= 16382:
        raise ValueError('Invalid native login framing')
    if struct.unpack_from('<H', frame, 14)[0] != 0x9001 or struct.unpack_from('<Q', frame, 16)[0] != int(uid):
        raise ValueError('Native login does not match the authenticated Steam account')
    if len(token)!=64 or any(ch not in '0123456789abcdef' for ch in token):
        raise ValueError('Invalid native credential')
    if type(build) is not bytes or len(build) not in (0, 32):
        raise ValueError('Invalid build fingerprint')
    body = frame[4:24]+bytes.fromhex(token)+build
    return struct.pack('<HH',len(body)+4,len(body))+body+frame[-2:]


class Reader:
    def __init__(self, reader, uid, token, build=b''):
        self.reader, self.uid, self.token = reader, uid, token
        self.pending = b''
        self.build = build
        self.started = False

    async def read(self, size):
        if not self.started:
            self.started = True
            header = await asyncio.wait_for(self.reader.readexactly(2), 10)
            length = struct.unpack('<H',header)[0]
            if not 24 <= length <= 16382:
                raise ValueError('Invalid native frame size')
            frame = header+await asyncio.wait_for(self.reader.readexactly(length), 5)
            self.pending = login_frame(frame,self.uid,self.token,self.build)
            self.token = None
        if not self.pending:
            # Buffer complete native frames so a keepalive never splits game data.
            try:
                first = await asyncio.wait_for(self.reader.read(1), KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                self.pending = keepalive_frame(self.uid)
            else:
                if not first:
                    return b''
                header = first+await asyncio.wait_for(self.reader.readexactly(1), 5)
                length = struct.unpack('<H', header)[0]
                if not 24 <= length <= 16382:
                    raise ValueError('Invalid native frame size')
                self.pending = header+await asyncio.wait_for(self.reader.readexactly(length), 5)
        if self.pending:
            data,self.pending = self.pending[:size],self.pending[size:]
            return data
