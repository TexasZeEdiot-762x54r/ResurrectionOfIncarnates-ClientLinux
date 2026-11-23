"""Bounded read-only trace for the native team-name display pipeline."""
import time


TEAM_NAME_ROW_PARSE = 0x140091F80
GLOBAL_STATE_PTR = 0x141B87458
GLOBAL_TEAM_NAME_OFFSET = 0x3CE04
PROFILE_TAG_PARTNER_OFFSET = 0xF240
TRACE_DAYS = 7
TRACE_SECONDS = TRACE_DAYS * 24 * 60 * 60


def bounded_text(raw):
    return raw.split(b'\0', 1)[0].decode('utf-8', 'replace')


class TeamNameDisplayTrace:
    def __init__(self, debugger, clock=time.monotonic):
        self.d = debugger
        self.clock = clock
        self.started = clock()
        self.last = None

    def active(self):
        return self.clock() - self.started < TRACE_SECONDS

    def parsed(self, hit):
        if self.active():
            # Native mode 0x2f copies the selected name to the battle display
            # buffer; 0x30 retains it on the parsed per-player row.
            self.d.log(f'TEAM_NAME_TRACE row parser mode={hit.r8 & 0xffffffff:#x}')

    def snapshot(self):
        state = self.d.q(GLOBAL_STATE_PTR)
        if not state:
            return {'state': 0, 'profile_partner': False, 'display_name': ''}
        return {
            'state': state,
            'profile_partner': self.d.q(state + PROFILE_TAG_PARTNER_OFFSET) not in (0, 0xF0F0F0F0F0F0F0F0),
            'display_name': bounded_text(self.d.read(state + GLOBAL_TEAM_NAME_OFFSET, 64)),
        }

    def poll(self, debugger):
        if not self.active():
            return
        current = self.snapshot()
        if current != self.last:
            self.last = current
            debugger.log(f'TEAM_NAME_TRACE state {current!r}')

    def arm(self, debugger):
        if TEAM_NAME_ROW_PARSE in debugger.bps:
            raise RuntimeError(f'Team-name trace breakpoint conflict at {TEAM_NAME_ROW_PARSE:#x}')
        debugger.bp(TEAM_NAME_ROW_PARSE, self.parsed)
        debugger.log(f'TEAM_NAME_TRACE armed read-only for {TRACE_DAYS} days')


def setup(d):
    trace = TeamNameDisplayTrace(d)
    d.on_start(trace.arm)
    d.poll(trace.poll, every=0.25)
