"""Read the native recording stream from a worker, independent of game ticks."""
import json
from pathlib import Path
import struct
import time


RINGS = 0x141C002C0  # RIP-relative load at 0x14068db10


def word(reader, address):
    buf = reader.read(address, 8)
    return f'{int.from_bytes(buf, "little"):016x}' if len(buf) == 8 else None


def source(reader, registry, rings, slot):
    """Candidates at 0x14068dbbe, not a packet capture or an atomic CPU trace."""
    actor = reader.q(registry + slot*0x25d0 + 0x18)
    if not actor or reader.read(actor+0xc71, 1) != bytes([slot]):
        return None
    flag = reader.read(actor+0xc2a4, 4)
    kind = ('cpu' if any(flag) else 'ring') if len(flag) == 4 else None
    ring_word = None
    if rings:
        b = reader.read(rings + slot*64 + 0x18, 44)
        if len(b) == 44:
            base, = struct.unpack_from('<Q', b)
            a, c = struct.unpack_from('<II', b, 36)
            index = a & c
            if 0x10000 <= base < 0x7ffffffff000 and index < 0x10000:
                ring_word = word(reader, base + 8*index)
    return [kind, ring_word, word(reader, actor+0xd6f0)]


def committed(reader, manager, slot, first, count):
    # The cell at count is the mutable sentinel; only earlier cells are final.
    if not 0 <= first <= count < 0x3840 or not 0 <= slot < 4:
        return None
    end = min(count, first + 128)
    if first == end:
        return []
    data = reader.read(manager + 0x8B90 + (slot * 0x3840 + first) * 12,
                       (end - first) * 12)
    if len(data) != (end - first) * 12:
        return None
    return [(frame, f'{value:016x}') for frame, value in struct.iter_unpack('<IQ', data)]


def observe(d, folder, *, duration=60, interval=.01):
    stamp = time.time_ns()
    path = Path(folder) / f'ranked-input-{stamp}.jsonl'
    with path.open('x', encoding='ascii', buffering=1) as out:
        def emit(**fields):
            print(json.dumps(dict(ms=(time.time_ns()-stamp)//1000000, **fields)), file=out)
        emit(start=True, version=2, source_columns=['native_branch', 'raw_ring', 'cpu_word'],
             source_slots=[1, 2, 3])
        seen = [0]*4
        origin = None
        previous = None
        last_source = None
        deadline = time.monotonic() + duration
        next_health = 0
        try:
            while time.monotonic() < deadline:
                now = time.monotonic()
                heartbeat = now >= next_health
                if heartbeat:
                    next_health = now + 1
                    roster = d.q(0x141B87458)
                    emit(roster_mode=d.d32(roster+0x5f650) if roster else None)
                manager, registry, game = (d.q(a) for a in
                                           (0x141B874D0, 0x141B87490, 0x141B87440))
                if manager and registry and game and d.read(manager+0x10, 4) == b'\xf0\x05\x03\x15':
                    if origin and manager != origin:
                        emit(stop='manager changed')
                        return
                    origin = manager
                    raw = d.read(manager+0x1A2F30, 16)
                    if len(raw) != 16:
                        time.sleep(interval)
                        continue
                    counts = struct.unpack('<4i', raw)
                    before = d.d32(game+0x18)
                    rings = d.q(RINGS)
                    candidates = [source(d, registry, rings, slot) for slot in (1, 2, 3)]
                    after = d.d32(game+0x18)
                    if heartbeat or candidates != last_source:
                        emit(frame_before=before, frame_after=after, candidates=candidates)
                        last_source = candidates
                    if heartbeat or counts != previous:
                        emit(frame=after, local_hex=d.read(manager+0x14,1).hex(), counts=counts)
                        previous = counts
                    for i, count in enumerate(counts):
                        if 0 <= count < seen[i]:
                            emit(stop='stream reset', slot=i)
                            return
                        rows = committed(d, manager, i, seen[i], count)
                        if rows:
                            emit(slot=i, first=seen[i], inputs=rows)
                            seen[i] += len(rows)
                elif origin:
                    emit(stop='battle recording released')
                    return
                time.sleep(interval)
            emit(stop='window complete', captured=seen)
        except Exception as exc:
            emit(stop='observer error', error=type(exc).__name__)
