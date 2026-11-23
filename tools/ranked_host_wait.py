"""Bounded Ranked host setup grace; no changes to joins, auth or packet handling."""
import struct

SITE = 0x140127AF1
RESUME = 0x140127AFE
TIMEOUT = 0x140128060
GUARD = bytes.fromhex('ba10270000488bcfe862050000')
FAILURE = 0x1401280B5
FAILURE_GUARD = bytes.fromhex('488bcbe8137affff')


def thunk():
    # State 1 is the host setup wait. Its task already has a 30-second budget.
    code = bytearray(bytes.fromhex('ba10270000'))
    exits = []
    for offset, value in ((0xf0, 4), (0x120, 1), (0x128, 1), (0x130, 0)):
        code += b'\x83\xbf' + struct.pack('<I', offset) + bytes([value])
        code += b'\x75\x00'
        exits.append(len(code)-1)
    code += bytes.fromhex('ba30750000')
    target = len(code)
    for pos in exits:
        code[pos] = target-pos-1
    code += bytes.fromhex('488bcf48b8') + struct.pack('<Q', TIMEOUT) + b'\xff\xd0'
    code += b'\x49\xbb' + struct.pack('<Q', RESUME) + b'\x41\xff\xe3'
    return bytes(code)


def setup(debugger):
    budget = [4]

    def expired(hit):
        if not budget[0]:
            return
        def word(pointer, offset):
            if not 0x10000 <= pointer < 0x7fffffff0000:
                return None
            raw = debugger.read(pointer+offset, 4)
            return int.from_bytes(raw, 'little') if len(raw) == 4 else None
        try:
            owner = hit.rbx
            if word(owner, 0xf0) != 4 or word(owner, 0x120) != 1:
                return
            budget[0] -= 1
            task = debugger.q(owner+0x180)
            values = {hex(off): word(owner, off) for off in
                      (0xf0, 0x120, 0x124, 0x128, 0x130, 0x14c, 0x150, 0x154)}
            task_values = {hex(off): word(task, off) for off in (0x1c, 0x54, 0x58, 0x68)}
            debugger.log(f'RANKED_HOST_WAIT expired backend={values} task_present={bool(task)} task={task_values}')
        except (OSError, ValueError, TypeError, struct.error):
            debugger.log('RANKED_HOST_WAIT expired; bounded snapshot incomplete')

    def arm(d):
        if d.read(SITE, len(GUARD)) != GUARD:
            raise RuntimeError('Ranked host wait signature mismatch')
        if d.read(FAILURE, len(FAILURE_GUARD)) != FAILURE_GUARD or FAILURE in d.bps:
            raise RuntimeError('Ranked host timeout snapshot signature/conflict')
        target = d.alloc(256)
        if not target:
            raise OSError('Could not allocate Ranked host wait thunk')
        replacement = b'\x48\xb8' + struct.pack('<Q', target) + b'\xff\xe0\x90'
        if not d.write(target, thunk()) or not d.write(SITE, replacement):
            raise OSError('Could not install Ranked host wait grace')
        d.bp(FAILURE, expired)
        d.log('RANKED_HOST_WAIT host setup budget 30s; other waits unchanged; failure-only snapshot armed')

    debugger.on_start(arm)
