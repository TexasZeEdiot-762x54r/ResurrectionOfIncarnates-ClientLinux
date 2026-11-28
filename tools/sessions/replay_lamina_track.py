"""Optional summon track stored after the native slot-zero input sentinel."""
import struct
import zlib

RAW_SIZE = 0xB1780
STREAM_START = 0x8B80
STREAM_END = STREAM_START + 0x3840 * 12
RESERVE = 0x4200
OFFSET = STREAM_END - RESERVE
FRAME_LIMIT = 0x4000
EVENT_LIMIT = 64
MAGIC = b'ROILAM64'
HEADER = struct.Struct('<8sHHII')
EVENT = struct.Struct('<IBBB')
STATE = 0x141B87440


def native_tail_isolated(raw):
    return any(struct.unpack_from('<I', raw, offset)[0] == 0xFFFFFFFF
               for offset in range(STREAM_START, OFFSET, 12))


def slot_is_human(raw, slot):
    offset = 0x20 + slot * 0x22D8
    return raw[offset+1] < 128 and bool(struct.unpack_from('<Q', raw, offset+0x18)[0])


def validate(raw, last, gates, events):
    if (len(raw) != RAW_SIZE or not native_tail_isolated(raw)
            or struct.unpack_from('<i', raw, 0x14)[0] != 5
            or not 0 < last < FRAME_LIMIT or len(gates) != last+1
            or len(events) > EVENT_LIMIT):
        raise ValueError('Unsupported Lamina replay track')
    previous = (-1, -1)
    human = sum(0x11 << slot for slot in range(4) if slot_is_human(raw, slot))
    if any(bits & ~human for bits in gates):
        raise ValueError('Replay gate track includes a CPU')
    for frame, slot, lamina, variant in events:
        if (not 0 <= slot < 4 or not slot_is_human(raw, slot)
                or not 0 <= frame <= last or (frame, slot) <= previous
                or not 1 <= lamina <= 15 or not 0 <= variant <= 2):
            raise ValueError('Invalid Lamina replay event')
        previous = (frame, slot)


def embed(raw, last, gates, events):
    events = sorted(events)
    validate(raw, last, gates, events)
    body = gates + b''.join(EVENT.pack(*event) for event in events)
    header = HEADER.pack(MAGIC, last, len(events), len(body), zlib.crc32(body))
    if len(header) + len(body) > RESERVE:
        raise ValueError('Lamina track exceeds reserved tail')
    return raw[:OFFSET] + (header+body).ljust(RESERVE, b'\0') + raw[STREAM_END:]


def extract(raw):
    if len(raw) != RAW_SIZE or raw[OFFSET:OFFSET+len(MAGIC)] != MAGIC:
        return None
    _, last, count, size, check = HEADER.unpack_from(raw, OFFSET)
    if (last >= FRAME_LIMIT or count > EVENT_LIMIT or size != last+1+count*EVENT.size
            or HEADER.size+size > RESERVE):
        raise ValueError('Invalid Lamina track length')
    start = OFFSET + HEADER.size
    body = raw[start:start+size]
    if zlib.crc32(body) != check:
        raise ValueError('Lamina replay track checksum mismatch')
    gates = body[:last+1]
    events = tuple(EVENT.iter_unpack(body[last+1:]))
    validate(raw, last, gates, events)
    return last, gates, events


class RoundTrack:
    def __init__(self):
        self.reset()

    def reset(self):
        self.mode = 0
        self.manager = 0
        self.expected = set()
        self.confirmed = []
        self.complete = True

    def admit(self, manager, event):
        if self.mode != 2:
            return True
        if manager != self.manager or event not in self.expected:
            return False
        self.expected.remove(event)
        return True

    def confirm(self, manager, event):
        if self.mode != 1 or manager != self.manager:
            return
        if len(self.confirmed) >= EVENT_LIMIT or event in self.confirmed:
            self.complete = False
        else:
            self.confirmed.append(event)
