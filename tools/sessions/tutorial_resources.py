"""Retain live tutorial resources at the mission index; leave full exits native."""
import struct

ADDRESS = 0x1401dbe37
BETWEEN = 0x1401dbe83
FULL_EXIT = 0x1401dbe3d
TUTORIAL_VTABLE = 0x1419c0780
UNLOAD = 0x140ba4fa0
UNLOAD_PROLOGUE = bytes.fromhex('40534883ec20488bd9488bd1')


def number(d, address, size):
    raw = d.read(address, size)
    return int.from_bytes(raw, 'little') if len(raw) == size else None


def tutorial_context(d):
    tutorial = number(d, 0x141b874c0, 8)
    if not tutorial or number(d, tutorial, 8) != TUTORIAL_VTABLE:
        return None
    kind = number(d, tutorial + 0x14, 4)
    character = number(d, tutorial + 0x18, 4)
    script = number(d, tutorial + 8, 8)
    if not script:
        return None
    if kind == 0 and character == 1:
        return 'system'
    ui = number(d, 0x141b87458, 8)
    if (kind == 1 and character is not None and 1 <= character <= 21
            and ui and number(d, ui + 0x5f650, 4) == 7):
        return 'character'
    return None


def keep_tutorial_cache(d, scene):
    if not scene or number(d, scene + 0x43, 1) != 0:
        return False
    bot = number(d, 0x141b874c8, 8)
    loader = number(d, 0x141b941f0, 8)
    if not bot or not loader:
        return False
    # Character scripts remain live at their index just like the system script.
    # Never discard their ready preloader through the injected VS Bot round path.
    return (tutorial_context(d) is not None
            and number(d, bot + 0x18, 4) == 0
            and number(d, loader + 0x80, 4) == 1)


def cleanup_thunk(address):
    def relative(opcode, at, target):
        return opcode + struct.pack('<i', target - (at + len(opcode) + 4))
    return (bytes.fromhex('40387b437405')
            + relative(b'\xe9', address + 6, FULL_EXIT)
            + relative(bytes.fromhex('488b0d'), address + 11, 0x141b874b8)
            + bytes.fromhex('488b4928')
            + relative(b'\xe8', address + 22, 0x1405680e0)
            + relative(b'\xe9', address + 27, BETWEEN))


def setup(d):
    counts = {'exit': 0, 'unload': 0}

    def guard(h):
        context = tutorial_context(d)
        retained = keep_tutorial_cache(d, h.rbx)
        if context and counts['exit'] < 24:
            loader = number(d, 0x141b941f0, 8)
            d.log('TUTORIAL_RESOURCES exit kind={} full_exit={} loader_ready={} retained={}'.format(
                context, number(d, h.rbx + 0x43, 1) if h.rbx else None,
                number(d, loader + 0x80, 4) if loader else None, retained))
            counts['exit'] += 1
        if retained:
            h.setreg('rip', BETWEEN)

    def unload(h):
        if counts['unload'] >= 32 or tutorial_context(d) != 'character':
            return
        counts['unload'] += 1
        # Numeric lifecycle metadata only; do not read asset contents or alter frees.
        d.log('TUTORIAL_RESOURCES sound_unload bank={} data={} caller={}'.format(
            hex(h.rcx), number(d, h.rcx + 0xd8, 8) if h.rcx else None, hex(h.ret())))

    def arm(debugger):
        entry = debugger.read(ADDRESS, 6)
        if entry == bytes.fromhex('40387b437446'):
            debugger.log('TUTORIAL_RESOURCES native between-session retention already present')
            return
        if len(entry) != 6 or entry[0] != 0xe9 or entry[5] != 0x90:
            raise RuntimeError('Unexpected battle-exit cleanup entry')
        target = ADDRESS + 5 + struct.unpack('<i', entry[1:5])[0]
        if not 0x141000000 <= target < 0x142000000:
            raise RuntimeError('Battle-exit cleanup target outside reviewed image range')
        expected = cleanup_thunk(target)
        if debugger.read(target, len(expected)) != expected:
            raise RuntimeError('Unexpected battle-exit cleanup thunk')
        if debugger.read(UNLOAD, len(UNLOAD_PROLOGUE)) != UNLOAD_PROLOGUE:
            raise RuntimeError('Unexpected tutorial sound-bank unload code')
        debugger.bp(ADDRESS, guard)
        debugger.bp(UNLOAD, unload)
        debugger.log('TUTORIAL_RESOURCES guarded system/character index retention and bounded unload trace armed')

    d.on_start(arm)
