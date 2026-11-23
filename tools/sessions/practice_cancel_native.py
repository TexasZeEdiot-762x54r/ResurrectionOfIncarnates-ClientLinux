"""Consume marked practice cancellation at the native scene's exit check."""
import struct

SETTINGS = 0x141B87440
CHECKS = {'Briefing': 0x140145755, 'Battle': 0x1401DD075}
GUARDS = {
    0x14014574E: '488b05eb1c a401',
    0x140145755: '397058740f',
    0x1401DD06E: '488b05cba3 9a01',
    0x1401DD075: '397058740e',
    # Native Yes callback ends by setting this same exit request.
    0x14049DC59: '488b05e0976e01c7405801000000',
}


def ranked_active(d):
    driver = getattr(d, 'ranked_disconnect', None)
    return bool(getattr(getattr(d, 'ranked_search_status', None), 'found', False)
                or driver and (driver.pending or driver.context))


class NativePracticeCancel:
    def __init__(self, trace):
        self.trace = trace
        self.armed = set()

    def verify(self):
        d = self.trace.d
        for address, text in GUARDS.items():
            guard = bytes.fromhex(text)
            if d.read(address, len(guard)) != guard:
                raise RuntimeError('Unexpected native practice exit check')
        if any(address in d.bps for address in CHECKS.values()):
            raise RuntimeError('Native practice exit breakpoint conflict')

    def poll(self):
        t, p = self.trace, self.trace.practice
        if ranked_active(t.d):
            p.clear()
            return
        if (not p.active or not p.pending or p.pending != p.peer
                or p.native_exit_done or p.phase not in CHECKS):
            return
        address = CHECKS[p.phase]
        if address in self.armed:
            return
        if address in t.d.bps:
            raise RuntimeError('Native practice exit check already owned')
        token, phase = (p.generation, p.peer), p.phase
        self.armed.add(address)

        def consume(hit):
            self.armed.discard(address)
            p = t.practice
            if (ranked_active(t.d) or (p.generation, p.peer) != token
                    or not p.active or p.pending != p.peer or not p.pending
                    or p.native_exit_done or p.phase != phase):
                return 'remove'
            settings = t.d.q(SETTINGS)
            # RAX belongs to this scene's native compare, not a cached object.
            # The Yes/No VS Bot HUD itself accepts only modes 2 and 3.
            if (not settings or hit.rax != settings or hit.rsi != 0
                    or t.d.d32(settings + 0x28) not in (2, 3)
                    or t.d.d32(settings + 0x58) not in (0, 1)):
                t.d.log('PRACTICE_EXIT native check declined: scene state changed')
                return 'remove'
            if not t.d.write(settings + 0x58, struct.pack('<I', 1)):
                raise OSError('Could not request native practice exit')
            p.native_exit_done = True
            t.d.log(f'PRACTICE_EXIT native {phase} exit requested; duo retained')
            return 'remove'

        t.d.bp(address, consume)
