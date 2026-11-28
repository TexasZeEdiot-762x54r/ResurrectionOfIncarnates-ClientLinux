"""Give the native Lamina command a recorded, frame-bound replay fallback."""
import struct

COMMAND = 0x1406985F0
STOLEN = bytes.fromhex('48895c24084889742418574883ec20')
COMMAND_ID = 0x11
FRAME_LIMIT = 0x4000


def event_masks(events):
    masks = bytearray(FRAME_LIMIT)
    for frame, slot, _, _ in events:
        if not 0 <= frame < FRAME_LIMIT or not 0 <= slot < 4:
            raise ValueError('Invalid replay summon frame or slot')
        masks[frame] |= 1 << slot
    return bytes(masks)


def command_thunk(track, events, state, gate, native):
    """Win64 command-entry adapter. Preserve native behavior off recorded frames."""
    code = bytearray()
    fallback = []

    def emit(raw):
        code.extend(bytes.fromhex(raw))

    def imm(raw, address):
        if not 0x10000 <= address < 0x7FFFFFF00000:
            raise ValueError('Invalid replay command address')
        emit(raw)
        code.extend(struct.pack('<Q', address))

    def skip(condition):
        emit('0f '+condition)
        fallback.append(len(code))
        code.extend(bytes(4))

    emit('83 fa 11'); skip('85')
    imm('48 b8', track)
    emit('80 38 02'); skip('85')
    emit('48 85 c9'); skip('84')
    emit('4c 8b 81 f8 0e 00 00 4d 85 c0'); skip('84')  # wrapper -> actor
    emit('45 0f b6 80 71 0c 00 00 41 83 f8 03'); skip('87')
    imm('48 b8', state)
    emit('48 8b 00 48 85 c0'); skip('84')
    emit('8b 40 18 3d 00 40 00 00'); skip('83')
    imm('49 ba', events)
    emit('41 0f b6 04 02 44 0f a3 c0'); skip('83')   # bit: confirmed slot

    # At entry RSP is 8 mod 16. Save the two original arguments above shadow space.
    emit('48 83 ec 38 48 89 4c 24 20 89 54 24 28')
    imm('48 b8', native)
    emit('ff d0 84 c0 75 11')                       # keep native success
    emit('48 8b 4c 24 20')
    imm('48 b8', gate)
    emit('ff d0')
    emit('48 83 c4 38 c3')

    for at in fallback:
        struct.pack_into('<i', code, at, len(code)-at-4)
    imm('48 b8', native)
    emit('ff e0')
    return bytes(code)
