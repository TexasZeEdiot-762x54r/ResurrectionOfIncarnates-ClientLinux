"""Offer native Solo waiting practice and retain independent duo practice choices."""
import struct

HOOK = 0x1403C18B6
GUARD = bytes.fromhex('c78050f6050001000000')
RESUME = 0x1403C1930
SOLO_PROMPT_GATE = 0x1403AF5AF
SOLO_PROMPT_GUARD = bytes.fromhex('83b800cd030003721b')


def solo_thunk():
    # Same initialization as CPlayMatchingBotPopup's Solo path (1403b6f0e).
    code = bytearray(b'\x48\xa1' + struct.pack('<Q', 0x141B874C8))
    code += b'\x48\x89\xc1\x31\xd2'
    code += b'\x48\xb8' + struct.pack('<Q', 0x140553D20) + b'\xff\xd0'
    code += b'\x48\xa1' + struct.pack('<Q', 0x141B87458)
    code += bytes.fromhex('c78050f6050000000000')
    code += b'\x48\xb8' + struct.pack('<Q', RESUME) + b'\xff\xe0'
    return bytes(code)


def setup(d):
    def arm(debugger):
        if (debugger.read(HOOK, len(GUARD)) != GUARD
                or debugger.read(SOLO_PROMPT_GATE,len(SOLO_PROMPT_GUARD)) != SOLO_PROMPT_GUARD):
            raise RuntimeError('Unexpected ranked waiting-practice transition')
        code = solo_thunk()
        entry = debugger.alloc(len(code))
        if not debugger.write(entry, code):
            raise OSError('Could not prepare independent waiting practice')
        # Skip only the native eligibility branch that bypasses the Solo yes/no popup.
        # Both choices still run their original native queue/practice transitions.
        if not debugger.write(SOLO_PROMPT_GATE+7,b'\x90\x90'):
            raise OSError('Could not enable the native Solo waiting-practice prompt')

        def transition(hit):
            game = debugger.q(0x141B87450)
            nrt = debugger.q(game + 0x150) if game else 0
            if nrt and debugger.d32(nrt + 0x4104) == 0x82:
                hit.setreg('rip', entry)
                debugger.log('RANKED_WAIT independent solo practice; ranked duo retained')

        debugger.bp(HOOK, transition)
        debugger.log('RANKED_WAIT independent waiting-practice transition armed')
        debugger.log('RANKED_WAIT native Solo practice choice enabled; queue eligibility unchanged')

    d.on_start(arm)
