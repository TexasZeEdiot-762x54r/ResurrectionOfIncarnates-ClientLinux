"""Seat two Solo Ranked humans and initialize the padded bots from shared rows."""
import struct
import time


BEFORE_SIDE_ASSIGNMENT = 0x1401388D4
RANKED_CALLERS = frozenset((0x140137E6F, 0x140137F82))
PADDED_HOST_CALLER = 0x140138202
ROSTER_WINDOW = 45.0
GUARD = bytes.fromhex('48 8b 4f 10 e8 6c 4e 00 00 44 0f b6 4f')
PARSED_SIDE = 0x1400ECF05
PARSED_SIDE_GUARD = bytes.fromhex('42 0f b6 04 28 42 88 84 26 e2 0b 00 00')
BOT_MODE_BRANCH, CUSTOM_BOT_INIT = 0x14055982B, 0x140559832
BOT_INIT_GUARD = bytes.fromhex(
    '41 8b 86 50 f6 05 00 85 c0 74 3c 41 3b c1 74 37 0f b6 42 0f 84 c0 '
    '0f 8e 54 01 00 00 4c 8b 74 24 28 0f be c0 41 bb 0a 00 00 00 41 3b '
    'c3 44 0f 4c d8 45 3b d9 45 0f 4f cb 44 89 89 bc c2 00 00 48 83 c4 38 41 5d 5f c3')


def shared_bot_row(hit, roster):
    state = hit.q(0x141B87440)
    if not state or not hit.rdx or not hit.rcx:
        return None
    current = hit.read(hit.rdx, 16)
    if (len(current) != 16 or current[1] < 128 or current[2] > 3
            or hit.read(hit.rcx+0xC71,1) != current[2:3]):
        return None
    seats, humans, bot_sides = set(), {}, set()
    selected = None
    for side in range(2):
        team = state + side*0x3CB58
        if hit.read(team+0x94,4) != b'\x02\0\0\0':
            return None
        for index in range(2):
            row = team + 0x98 + index*0xF2C8
            data = hit.read(row, 16)
            if len(data) != 16 or data[2] > 3 or data[2] in seats or data[9] != side:
                return None
            seats.add(data[2])
            if data[1] < 128:
                uid = hit.q(row+0x18)
                if not uid or uid in humans:
                    return None
                humans[uid] = side
            else:
                bot_sides.add(side)
                if data == current:
                    selected = (data[2], side, struct.unpack_from('b', data, 15)[0])
    needed = {side for side in (0,1) if list(roster.values()).count(side)==1}
    return selected if humans == roster and bot_sides == needed else None


def opposing_roster(values, sides):
    """The native assignment writes side = final vector index & 1."""
    if len(values) != 4:
        return None
    humans = [value for value in values if 0 <= value < 8]
    bots = [value for value in values if value == -1]
    if (len(humans) not in (2,3,4) or len(set(humans)) != len(humans)
            or len(bots) != 4-len(humans) or len(humans)+len(bots)!=4
            or set(sides) != set(humans) or set(sides.values()) != {0, 1}
            or any(list(sides.values()).count(side)>2 for side in (0,1))):
        return None
    if all(i % 2 == sides[h] for i, h in enumerate(values) if h >= 0):
        return None
    queues={side:[h for h in humans if sides[h]==side] for side in (0,1)}
    return tuple(queues[i%2].pop(0) if queues[i%2] else -1 for i in range(4))


class RankedPartialTeams:
    def __init__(self, debugger, clock=time.monotonic):
        self.d = debugger
        self.clock = clock
        self.seen = set()
        self.first_row = None
        self.valid_until = None
        self.roster = None
        self.bot_proof = None
        self.bot_logged = set()
        self.decoded = {}

    def parsed(self, hit):
        row = hit.rsi
        if row == 0:
            self.seen.clear()
            self.decoded.clear()
        tag = hit.rax & 255
        count = hit.read(hit.r12 + 0xBE0, 2)
        now = self.clock()
        first, self.first_row = self.first_row, None
        self.valid_until = None
        self.roster = None
        self.bot_proof = None
        self.bot_logged.clear()
        n=int.from_bytes(count,'little') if len(count)==2 else 0
        if n in (2,3,4) and tag in (0,1) and 0<=row<n:
            uid=hit.q(hit.r12+0xBF0+row*8)
            if row==0:
                self.decode_start=(hit.r12,n,now)
            start=getattr(self,'decode_start',None)
            if uid and start and start[:2]==(hit.r12,n) and now-start[2]<ROSTER_WINDOW:
                self.decoded[row]=(uid,tag)
            if len(self.decoded)==n and set(self.decoded)==set(range(n)):
                roster=dict(self.decoded.values())
                tags=list(roster.values())
                if len(roster)!=n or set(tags)!={0,1} or max(tags.count(s) for s in (0,1))>2:
                    return
                self.roster = roster
                self.valid_until = now + ROSTER_WINDOW
                # Seating consumes its own proof on the host. Bot creation also
                # runs on the guest, later in briefing, and again at respawn.
                self.bot_proof = (dict(self.roster), now+180)
                status = getattr(self.d, 'ranked_search_status', None)
                if status is not None:
                    status.match_found()
        if 0 <= row < 4:
            self.d.log(f'RANKED_TEAMS decoded roster row={row} tag={hit.rax & 255}')

    def skipped(self, reason, detail):
        if reason not in self.seen:
            self.seen.add(reason)
            self.d.log(f'RANKED_TEAMS seating check skipped: {reason} {detail}')

    def bot_init(self, hit):
        if hit.rax not in (0,1) or not self.bot_proof:
            return
        roster, deadline = self.bot_proof
        if self.clock() >= deadline:
            self.bot_proof = None
            return
        row = shared_bot_row(hit, roster)
        if row is None:
            self.skipped('CPU initializer does not match confirmed Solo Ranked seats', '')
            return
        # Only redirect this actor's conditional branch. The native Custom
        # path reads the same RDX row and handles zero, clamp and return itself.
        hit.setreg('rip', CUSTOM_BOT_INIT)
        if not self.bot_logged:
            self.bot_proof = (roster, self.clock()+900)
        if row not in self.bot_logged:
            self.bot_logged.add(row)
            self.d.log(f'RANKED_BOT_PRESET shared initializer slot={row[0]} side={row[1]} roster_difficulty={row[2]} mode={hit.rax}')

    def padded(self, hit):
        # 0x1401385e0 keeps its return address at rbp+0x20 after four pushes.
        caller = hit.q(hit.rbp + 0x20)
        deadline, self.valid_until = self.valid_until, None
        roster, self.roster = self.roster, None
        if caller == PADDED_HOST_CALLER:
            if deadline is None or self.clock() >= deadline:
                self.skipped('host without fresh opposing Ranked roster', hex(caller))
                return
        elif caller not in RANKED_CALLERS:
            self.skipped('caller', hex(caller or 0))
            return
        vector = hit.rbx
        begin, end = hit.q(vector), hit.q(vector + 8)
        if not begin or end - begin != 16:
            self.skipped('vector', (end - begin) if begin and end else None)
            return
        raw = hit.read(begin, 16)
        if len(raw) != 16:
            self.skipped('read', len(raw))
            return
        values = struct.unpack('<4i', raw)
        profiles = hit.q(hit.rdi + 0x10)
        if not profiles or values.count(-1) not in (0,1,2):
            self.skipped('padding', (bool(profiles), values))
            return
        sides = {}
        for h in values:
            if 0 <= h < 8:
                tag = hit.read(profiles + h * 0x30 + 0x0D, 1)
                if len(tag) != 1:
                    self.skipped('side tag', h)
                    return
                sides[h] = tag[0]
        if caller == PADDED_HOST_CALLER:
            transport = hit.q(hit.rdi + 8)
            lobby = hit.q(transport + 0x278) if transport else 0
            resolved = {}
            for h in sides:
                # Native 0x140137650 joins Steam endpoints to these per-slot
                # account IDs; +0x220 is the partner, not the player's own ID.
                own = hit.q(lobby + 0x218 + h * 16) if lobby else 0
                endpoint = transport + 0x60 + h * 0x30 if transport else 0
                steam = hit.q(endpoint + 8) if endpoint and hit.read(endpoint, 1) == b'S' else 0
                matches = {uid for uid in (own, steam) if uid in roster}
                if len(matches) == 1:
                    resolved[h] = roster[matches.pop()]
            self.d.log(f'RANKED_TEAMS identity sides={resolved} profile tags={sides}')
            if (set(resolved)!=set(sides) or len(resolved)!=len(roster)
                    or set(resolved.values()) != {0, 1}):
                self.skipped('identity mismatch', len(resolved))
                return
            sides = resolved
        arranged = opposing_roster(values, sides)
        self.d.log(f'RANKED_TEAMS final seats={values} side_tags={sides} corrected={arranged}')
        if arranged is None:
            return
        if not self.d.write(begin, struct.pack('<4i', *arranged)):
            raise OSError('Could not correct partial Ranked team roster')
        self.d.log(f'RANKED_TEAMS authoritative partial roster {values} -> {arranged}')

    def arm(self, debugger):
        if debugger.read(BEFORE_SIDE_ASSIGNMENT - 9, len(GUARD)) != GUARD:
            raise RuntimeError('Ranked partial-team native signature mismatch')
        if BEFORE_SIDE_ASSIGNMENT in debugger.bps:
            raise RuntimeError('Ranked partial-team breakpoint conflict')
        debugger.bp(BEFORE_SIDE_ASSIGNMENT, self.padded)
        if (PARSED_SIDE not in debugger.bps
                and debugger.read(PARSED_SIDE - 5, len(PARSED_SIDE_GUARD)) == PARSED_SIDE_GUARD):
            debugger.bp(PARSED_SIDE, self.parsed)
            debugger.log('RANKED_TEAMS roster decode trace armed')
        if BOT_MODE_BRANCH in debugger.bps or debugger.read(BOT_MODE_BRANCH-9,len(BOT_INIT_GUARD)) != BOT_INIT_GUARD:
            raise RuntimeError('Ranked CPU initializer signature mismatch or breakpoint conflict')
        debugger.bp(BOT_MODE_BRANCH, self.bot_init)
        debugger.log('RANKED_BOT_PRESET ready: native shared-row initializer for opposing two-human Solo Ranked')
        debugger.log('RANKED_TEAMS two-human opposing-side correction armed; identity-bound host seating enabled')


def setup(d):
    session = RankedPartialTeams(d)
    d.on_start(session.arm)
