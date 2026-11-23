"""Allow the explicit 20x diagnostic cube past the ordinary attack-bonus cap.

Native 0x14065c070 multiplies attack type 13 by the attacker's effect 3,
then caps the combined multiplier using the defender's +0x11d60 field.
Only the diagnostic cube bypasses that cap; defense and later damage rules
still run. No actor fields, saves, inventory or on-disk executable are changed.
"""
import math
from pathlib import Path
import struct

import native_lp_input as native

FLAG = Path(__file__).resolve().parent.parent / 'diagnostic-melee.enabled'
HOOK = 0x14065C5C3
RESUME = 0x14065C5D5
GUARDS = {
    HOOK: bytes.fromhex('f3410f1086601d01000f2f037304f30f1103'),
    RESUME: bytes.fromhex('f3410f1086841d01000f2f077604f30f1107'),
    0x14065C1C3: bytes.fromhex('418b8da000000083e90a7449ffc9743183e9027418ffc9'),
    0x14065C1FA: bytes.fromhex('f30f108ee8140100'),
}


ROSTER, PLAYBACK = 0x141B87458, 0x141B874D0


def battle_mode(reader):
    """Return the battle mode, or -1 for a replay. Used for logging only."""
    if native.number(reader, native.pointer(reader, PLAYBACK) + 8, '<B'):
        return -1
    return native.number(reader, native.pointer(reader, ROSTER) + 0x5F650)


def eligible(reader, hit, kept=None):
    """Values for a hit the cube uncaps, else None. Every peer runs this for every attacker, so the
    decision depends only on the attacker's own loadout and stats. When the attacker carries the cube
    but the hit stays capped, the reason is appended to `kept` so peers' logs can be compared."""
    if native.number(reader, hit.r13 + 0xA0) != 13:
        return None
    # Special attacks take a different native multiplier branch, even if type 13.
    if native.number(reader, hit.r13 + 0x554, '<B') & 0x40:
        return None
    slot = native.number(reader, hit.r13 + 0x20, '<b')
    if not 0 <= slot < 4 or native.number(reader, hit.rsi + 0xC71, '<b') != slot:
        return None
    registry = native.pointer(reader, 0x141B87490)
    if native.pointer(reader, registry + slot * 0x25D0 + 0x18) != hit.rsi:
        return None
    manager = native.pointer(reader, 0x141B874D0)
    row = manager + slot * 0x22D8
    grid, count = struct.unpack('<ii', native.read(reader, row + 0x20CC, 8))
    if not 0 <= grid < 5 or not 1 <= count <= 7:
        return None
    definitions = struct.unpack('<7Q', native.read(reader, row + 0x22D0, 56))
    if 1 not in definitions[:count]:
        return None
    baseline = native.number(reader, hit.rsi + 0x10C64, '<f')
    melee = native.number(reader, hit.rsi + 0x114E8, '<f')
    value = native.number(reader, hit.rbx, '<f')
    cap = native.number(reader, hit.r14 + 0x11D60, '<f')
    values = dict(slot=slot, baseline=baseline, melee=melee, before_cap=value, ordinary_cap=cap)
    if not all(math.isfinite(v) for v in (baseline, melee, value, cap)):
        if kept is not None:
            kept.append(('non-finite', values))
        return None
    # A depleted/disabled cube has no +19 static effect. Do not infer activity
    # from the retained inventory ID alone or alter older 5x diagnostic replays.
    if baseline < 19 or melee <= 1:
        if kept is not None:
            kept.append(('cube effect inactive', values))
        return None
    if not 0 < cap < value:
        return None  # Under the cap already: nothing to uncap.
    values['grid'] = grid
    return values


def setup(d, enabled=None):
    if enabled is None:
        enabled = FLAG.is_file()
    if not enabled:
        return

    def arm(debugger):
        try:
            if any(debugger.read(a, len(raw)) != raw for a, raw in GUARDS.items()):
                raise ValueError('Damage code changed')
        except (OSError, ValueError):
            debugger.log('DIAGNOSTIC_MELEE disabled: code guard mismatch')
            return
        if HOOK in getattr(debugger, 'bps', {}):
            debugger.log('DIAGNOSTIC_MELEE disabled: breakpoint already owned')
            return
        logged = 0
        kept_logged = 0
        modes = set()

        def before_cap(hit):
            nonlocal logged, kept_logged
            try:
                try:
                    mode = battle_mode(debugger)
                except (OSError, ValueError, TypeError, AttributeError, struct.error):
                    mode = None
                if mode not in modes:
                    # Applies in every mode; every peer must run this same release so they agree.
                    modes.add(mode)
                    debugger.log(f'DIAGNOSTIC_MELEE active (mode {mode})')
                kept = []
                values = eligible(debugger, hit, kept)
                if values is None:
                    if kept and kept_logged < 12:
                        reason, detail = kept[0]
                        debugger.log(f'DIAGNOSTIC_MELEE kept cap ({reason}) mode {mode} {detail}')
                        kept_logged += 1
                    return
                hit.setreg('rip', RESUME)
                if logged < 12:
                    debugger.log(f'DIAGNOSTIC_MELEE uncapped mode {mode} {values}')
                    logged += 1
            except (OSError, ValueError, TypeError, AttributeError, struct.error):
                return  # Incomplete/unknown state retains native behavior.

        debugger.bp(HOOK, before_cap)
        debugger.log('DIAGNOSTIC_MELEE guarded cube-only cap exception armed')

    d.on_start(arm)
