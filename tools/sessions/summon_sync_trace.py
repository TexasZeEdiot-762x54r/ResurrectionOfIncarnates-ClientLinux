"""Diagnostic only: does each machine's game ask to open a Lamina for *other* players?

Enabled by tools/summon-sync-trace.enabled. Changes no game behaviour and sets no breakpoints in
Steam (an earlier version hooked SteamNetworking/SendP2PPacket; even its one-time hooks made
battles fail to load with error 9009, so Steam is left alone).

lamina_combat's native OPEN gate counts, per player slot, how often the game asks whether that
player may open a Lamina, and how often the answer is yes (only ever the local player). If the
battle is lockstep, the other machine should ask for the summoner's slot when the summoner
presses the button. This logs those counters:
- when the Battle scene is created (baseline),
- whenever they change, on the debugger's existing poll cadence (no new breakpoints),
- when the error popup opens, with the popup call chain and NRT error-state fields.
"""
import json
from pathlib import Path
import struct
import time
from threading import Thread

from sessions import ranked_input_observer

from sessions import lamina_combat

FLAG = Path(__file__).resolve().parent.parent / 'summon-sync-trace.enabled'
DUMPS = Path(__file__).resolve().parent.parent / 'logs' / 'slot-dumps'
PLAYER_DUMP = 0x14000  # covers every player field the gate and summon read (highest: magazine +0x13818)
ERROR_POPUP = 0x140147E40
BATTLE_SCENE = 0x1401DAB60
GAME = 0x141B87450


def setup(d, enabled=None):
    if enabled is None:
        enabled = FLAG.is_file()
    if not enabled:
        return False
    d.log('SUMMON_SYNC hardware bot write watches disabled; counters and crash logging retained')
    state = {'last': None, 'dumps': 0}

    def snapshot(debugger, reason, force=False):
        values = lamina_combat.read_open_counters(debugger)
        if values is None or (values == state['last'] and not force):
            return
        state['last'] = values
        calls, eligible = values
        debugger.log(f'SUMMON_SYNC open-gate {reason}: calls per slot {calls}, eligible per slot {eligible}')

    def wrap(address, before):
        """Run `before` ahead of the handler play.py already registered at `address`."""
        entry = d.bps.get(address)
        original = entry[1] if entry else None

        def handler(h):
            try:
                before(h)
            except Exception as exc:
                d.log(f'SUMMON_SYNC handler error {exc!r}')
            return original(h) if original else None

        if entry:
            entry[1] = handler  # replace in place; a second d.bp() would save 0xCC as the original
        else:
            d.bp(address, handler)

    def players(debugger, reason):
        """Per slot, the fields the OPEN gate reads, so both machines' views can be compared."""
        rows = {}
        try:
            registry = debugger.q(lamina_combat.REGISTRY)
            playback = debugger.q(lamina_combat.PLAYBACK)
            local = debugger.read(playback + 0x14, 1)[0] if playback else None
            for slot in range(4):
                player = debugger.q(registry + slot * 0x25D0 + 0x18) if registry else 0
                if not player:
                    rows[slot] = None
                    continue
                raw = debugger.read(player + 0xC71, 1)
                hp = debugger.read(player + 0xC88, 4)
                rows[slot] = dict(
                    slot_field=struct.unpack('<b', raw)[0] if raw else None,
                    character=debugger.d32(player + 0xC74),
                    lamina=struct.unpack('<i', debugger.read(player + 0xC78, 4) or b'\0\0\0\0')[0],
                    level=struct.unpack('<b', debugger.read(player + 0x5611, 1) or b'\0')[0],
                    hp=round(struct.unpack('<f', hp)[0], 1) if len(hp) == 4 else None,
                    owner_links=debugger.q(player + 0xC58) == player and debugger.q(player + 0xC60) == player + 0xBF0,
                    magazine=bool(debugger.q(player + 0x13818)))
            # HP changes constantly; compare everything else so the log stays short.
            key = (local, {s: r and {k: v for k, v in r.items() if k != 'hp'} for s, r in rows.items()})
            if state.get('players') != key:
                state['players'] = key
                debugger.log(f'SUMMON_SYNC players {reason}: local slot {local}, ' + json.dumps(rows))
            if not state.get('dumped') and all(rows.values()):
                state['dumped'] = True
                dump_slots(debugger, registry, local)
        except Exception as exc:
            debugger.log(f'SUMMON_SYNC players unavailable: {exc!r}')

    def dump_slots(debugger, registry, local):
        """Once per battle: raw player structures and registry entries for all four slots, to find
        a field that marks CPU players (compared offline between human and CPU slots)."""
        folder = DUMPS / time.strftime('%Y%m%d-%H%M%S')
        folder.mkdir(parents=True, exist_ok=True)
        sizes = {}
        for slot in range(4):
            player = debugger.q(registry + slot * 0x25D0 + 0x18)
            blob = b''
            while player and len(blob) < PLAYER_DUMP:  # page-sized reads; stop at the first unreadable page
                chunk = debugger.read(player + len(blob), 0x1000)
                if len(chunk) != 0x1000:
                    break
                blob += chunk
            entry = debugger.read(registry + slot * 0x25D0, 0x25D0)
            (folder / f'slot{slot}-player.bin').write_bytes(blob)
            (folder / f'slot{slot}-registry.bin').write_bytes(entry)
            sizes[slot] = (len(blob), len(entry))
        (folder / 'meta.json').write_text(json.dumps(dict(local_slot=local, sizes=sizes)))
        debugger.log(f'SUMMON_SYNC slot dump written to logs/slot-dumps/{folder.name} {sizes}')

    def battle(h):
        if not state.get('observer') or not state['observer'].is_alive():
            worker = Thread(target=ranked_input_observer.observe,
                            args=(d, DUMPS.parent), daemon=True)
            state['observer'] = worker
            worker.start()
        snapshot(d, 'at battle start', force=True)
        state['players'] = None
        state['dumped'] = False
        players(d, 'at battle start')

    def error_popup(h):
        snapshot(d, 'at error popup', force=True)
        state['players'] = None
        players(d, 'at error popup')
        if state['dumps'] >= 3:
            return
        state['dumps'] += 1
        d.log('SUMMON_SYNC error popup call chain:')
        for offset, call, op in d.call_chain(h.rsp, 0x3000)[:16]:
            d.log(f'  stack+{offset:x}: call at {d.describe(call)} -> {op}')
        game = d.q(GAME)
        nrt = d.q(game + 0x150) if game else 0
        if nrt:
            fields = {hex(off): d.d32(nrt + off) for off in (0x40, 0x44, 0x3FD0, 0x40C8, 0x40D0)}
            d.log(f'SUMMON_SYNC NRT error-state fields {fields}')

    wrap(BATTLE_SCENE, battle)
    wrap(ERROR_POPUP, error_popup)
    def poll(debugger):
        snapshot(debugger, 'changed')
        if state.get('players') is not None:
            players(debugger, 'changed')

    d.poll(poll, every=getattr(d, 'poll_every', 10.0))
    d.log('SUMMON_SYNC trace enabled: native OPEN-gate counters per player slot; no Steam hooks')
    d.log('RANKED_INPUT v2 armed: Battle scene including mode 0; slot 2 CPU/ring sources and applied stream')
    return True
