"""Candidate resume gate; deliberately not armed by any published session.

The caller must supply fresh, authenticated proof for this exact native round.
No socket threads, AI presets, control masks or team assignments are rewritten.
"""
from dataclasses import dataclass
import time

HOOK = 0x1401329DA
COUNT_GATE = 0x140132A21
RESUME = 0x140132A00
GUARD = bytes.fromhex('488b4308488b9058080000448b4210418d40fa83f801')


@dataclass(frozen=True)
class Proof:
    match: str
    owner: int
    initial_humans: tuple
    expires: float
    ready_mask: int = 15


class ResumeGate:
    def __init__(self, debugger, proof, native_context, clock=time.monotonic, hold_last=None):
        self.d, self.proof, self.native_context, self.clock = debugger, proof, native_context, clock
        self.hold_last = hold_last

    def hit(self, hit):
        proof = self.proof()
        if not isinstance(proof, Proof) or self.clock() >= proof.expires:
            return
        if len(proof.initial_humans) not in (2,3,4) or proof.owner not in proof.initial_humans:
            return
        try:
            match, owner, initial_humans = self.native_context(self.d)
        except (ValueError, OSError):
            return
        if (match, owner, initial_humans) != (proof.match, proof.owner, proof.initial_humans):
            return
        count, mask = hit.rcx & 0xffffffff, hit.r9 & 0xffffffff
        if type(proof.ready_mask) is not int or not 0<proof.ready_mask<16 or mask & ~proof.ready_mask:
            return
        if count == 1 and mask & ~15 == 0 and mask.bit_count() == 1 and self.hold_last:
            if self.hold_last():
                hit.setreg('rip', RESUME)
            return
        if len(proof.initial_humans) == 2:
            return
        if not 2 <= count <= len(proof.initial_humans) or mask & ~15 or mask.bit_count() != count:
            return
        # Reuse the native Custom-equivalent surviving-human policy branch;
        # original code propagates the actual disconnect mask and CPU control.
        hit.setreg('rip', COUNT_GATE)
        self.d.log(f'RANKED_TAKEOVER native surviving-human gate count={count}')

    def arm(self):
        if self.d.read(HOOK,len(GUARD)) != GUARD or HOOK in self.d.bps:
            raise RuntimeError('Unknown or occupied native Ranked resume gate')
        self.d.bp(HOOK,self.hit)
