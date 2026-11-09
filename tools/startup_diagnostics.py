"""Opt-in bounded HTTP-copy observations; never log payloads or alter game state."""
HOOKS = {
    'response_copy': (0x1400424A4, bytes.fromhex('e82783fdff')),
    'response_copied': (0x1400424A9, bytes.fromhex('49837c241810')),
    'allocation_failed': (0x14001F8B4, bytes.fromhex('488b1585b9b501')),
}
DEFAULT_HEAP = 0x141BC4750
DEFAULT_HEAP_MODE = 0x141BC4758
MAX_RESPONSES = 128


def number(debugger, address, size=8):
    raw = debugger.read(address, size)
    return int.from_bytes(raw, 'little') if len(raw) == size else None


def heap_snapshot(debugger):
    return {'object': number(debugger, DEFAULT_HEAP),
            'mode': number(debugger, DEFAULT_HEAP_MODE, 4)}


def setup(debugger):
    active = {}
    remaining = [MAX_RESPONSES]
    failures = [4]

    def response_copy(hit):
        if remaining[0] <= 0:
            return 'remove'
        remaining[0] -= 1
        # Registers are read at the reviewed call instruction, after native strlen.
        fields = {'type': number(debugger, hit.r13 + 0x178, 4),
                  'bytes': hit.r8,
                  'previous_size': number(debugger, hit.rcx + 0x10),
                  'previous_capacity': number(debugger, hit.rcx + 0x18),
                  'heap': heap_snapshot(debugger)}
        active[hit.tid] = fields
        debugger.log('STARTUP_HTTP copy tid=' + str(hit.tid) + ' ' + repr(fields))

    def response_copied(hit):
        fields = active.pop(hit.tid, None)
        if fields is not None:
            debugger.log('STARTUP_HTTP copied tid=' + str(hit.tid)
                         + ' bytes=' + str(fields['bytes']))
        if remaining[0] <= 0 and not active:
            return 'remove'

    def allocation_failed(hit):
        if failures[0] <= 0:
            return 'remove'
        failures[0] -= 1
        # The fatal branch is entered only after allocation returned NULL.
        # Do not suppress that branch or change the requested size.
        debugger.log('STARTUP_HTTP allocation_failed tid=' + str(hit.tid)
                     + ' active_copy=' + repr(active.get(hit.tid))
                     + ' heap=' + repr(heap_snapshot(debugger)))

    handlers = locals()

    def arm(d):
        for name, (address, expected) in HOOKS.items():
            if d.read(address, len(expected)) != expected:
                raise RuntimeError('Unexpected startup-diagnostic code: ' + name)
        for name, (address, _) in HOOKS.items():
            d.bp(address, handlers[name])
        d.log('STARTUP_HTTP opt-in length/allocation observations armed; payloads omitted')

    debugger.on_start(arm)
