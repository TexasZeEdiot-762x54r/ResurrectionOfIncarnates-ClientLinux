"""Show elapsed time and the live server criteria in the Ranked search popup."""
import re
import struct
import time

CREATE_LABELS_RETURN = 0x1403B8E6F
TIMER_LABEL_CALL = 0x1403B8E2A
TIMER_LABEL_RETURN = 0x1403B8E2F
CRITERIA_LABEL_CALL = 0x1403B8E63
CRITERIA_LABEL_RETURN = 0x1403B8E68
DESTROY = 0x1403B9010
UPDATE = 0x1403B9090
UPDATE_RESUME = 0x1403B90A1
POPUP_VTABLE = 0x1418C68C0
TIMER_LABEL_OFFSET = 0x348
CRITERIA_LABEL_OFFSET = 0x350
LAYOUT_Y = 0x1419FDEC0
HIDE_CRITERIA = 0x1403B93A2
HIDE_TIMER = 0x1403B93BF
UPDATE_PROLOGUE = bytes.fromhex('40 53 48 83 ec 20 48 8b d9 c7 44 24 30 00 00 00 00')
CREATE_GUARD = bytes.fromhex('0f 28 74 24 30')
TIMER_CALL_GUARD = bytes.fromhex('e8 d1 05 00 00')
TIMER_RETURN_GUARD = bytes.fromhex('48 8b 0d 2a e6 7c 01')
CRITERIA_CALL_GUARD = bytes.fromhex('e8 98 05 00 00')
CRITERIA_RETURN_GUARD = bytes.fromhex('48 89 87 50 03 00 00')
HIDE_GUARD = bytes.fromhex('c6 81 28 02 00 00 00')
DESTROY_GUARD = bytes.fromhex('40 53 48 83 ec 20')
STATE_SIZE = 512
DIRTY_OFFSET = 24
FOUND_OFFSET = 28
CANCEL = 0x1403B92E0
CANCEL_GUARD = bytes.fromhex('4885c97426')
TIMER_TEXT_OFFSET = 32
CRITERIA_TEXT_OFFSET = 96


def _revision_tag():
    from client_version import label

    match = re.search(r'R([1-9][0-9]?)', label() or '')
    return f'R{match.group(1)}W' if match else 'same build'


def search_lines(elapsed, revision=None):
    """Return the timer and criteria for the popup's two native labels."""
    elapsed = max(0, int(elapsed))
    minutes, seconds = divmod(elapsed, 60)
    timer = f'{minutes:02d}:{seconds:02d}'
    if elapsed < 15:
        region = 'Local region'
    elif elapsed < 30:
        region = 'Continent'
    else:
        region = 'Worldwide'
    roster = '4 players' if elapsed < 120 else '2-4 + bots'
    return (f'Searching {timer}',
            f'{region} | {roster} | {revision or _revision_tag()}')


def _update_thunk(state):
    """Run in the popup's native update method and consume a prepared text buffer."""
    code = bytearray(UPDATE_PROLOGUE)
    code.extend(b'\x49\xba' + struct.pack('<Q', state))                 # mov r10, state
    jumps = []

    def skip_if(raw):
        code.extend(raw)
        code.extend(b'\x0f\x84' + bytes(4))
        jumps.append(len(code) - 4)

    code.extend(bytes.fromhex('49 39 1a'))                              # cmp [r10], rbx
    code.extend(b'\x0f\x85' + bytes(4))
    jumps.append(len(code) - 4)
    skip_if(bytes.fromhex('41 83 7a 18 00'))                            # dirty == 0
    code.extend(bytes.fromhex('48 8b 8b 48 03 00 00'))                 # popup label
    code.extend(bytes.fromhex('49 3b 4a 08'))                           # expected label
    code.extend(b'\x0f\x85' + bytes(4))
    jumps.append(len(code) - 4)
    code.extend(bytes.fromhex('48 8b 83 50 03 00 00'))                 # criteria label
    code.extend(bytes.fromhex('49 3b 42 10'))                           # expected criteria label
    code.extend(b'\x0f\x85' + bytes(4))
    jumps.append(len(code) - 4)
    # r10 is volatile across native calls. Consume the update first, then reload
    # the state address before setting the second label.
    code.extend(bytes.fromhex('41 c7 42 18 00 00 00 00'))              # dirty = 0
    code.extend(b'\x49\x8d\x52' + bytes((TIMER_TEXT_OFFSET,)))         # rdx = timer text
    code.extend(bytes.fromhex('48 8b 01 ff 90 18 01 00 00'))           # label->setText
    code.extend(b'\x49\xba' + struct.pack('<Q', state))               # reload state
    code.extend(bytes.fromhex('49 8b 4a 10'))                           # criteria label
    code.extend(b'\x49\x8d\x52' + bytes((CRITERIA_TEXT_OFFSET,)))      # criteria text
    code.extend(bytes.fromhex('48 8b 01 ff 90 18 01 00 00'))           # label->setText
    # Same native setEnabled(false) call used by the popup's own handoff.
    code.extend(b'\x49\xba' + struct.pack('<Q', state))
    skip_if(bytes.fromhex('41 83 7a 1c 00'))
    code.extend(bytes.fromhex('48 8b 8b 60 03 00 00'))
    skip_if(bytes.fromhex('48 85 c9'))
    code.extend(bytes.fromhex('48 8b 01 33 d2 ff 90 e0 00 00 00'))
    tail = len(code)
    code.extend(bytes.fromhex('48 8b cb'))                              # original rcx
    code.extend(b'\x48\xb8' + struct.pack('<Q', UPDATE_RESUME) + b'\xff\xe0')
    for pos in jumps:
        struct.pack_into('<i', code, pos, tail - pos - 4)
    return bytes(code)


class RankedSearchStatus:
    def __init__(self, debugger):
        self.d = debugger
        self.state = 0
        self.started = None
        self.last_second = None
        self.revision = _revision_tag()
        self.layout_y = None
        self.found = False
        self.handoff_last = None
        self.handoff_samples = 0

    def _begin_layout(self, delta):
        if self.layout_y is None:
            self.layout_y = self.d.read(LAYOUT_Y, 4)
        if len(self.layout_y) != 4:
            raise OSError('Could not read Ranked search label position')
        y = struct.unpack('<f', self.layout_y)[0]
        if not self.d.write(LAYOUT_Y, struct.pack('<f', y + delta)):
            raise OSError('Could not position Ranked search label')

    def timer_layout(self, _hit):
        self._begin_layout(-14.0)

    def criteria_layout(self, _hit):
        self._begin_layout(14.0)

    def restore_layout(self, _hit):
        if self.layout_y is not None and not self.d.write(LAYOUT_Y, self.layout_y):
            raise OSError('Could not restore Ranked search label position')

    def _write_state(self, owner, timer_label, criteria_label, lines, dirty=1):
        timer_raw = lines[0].encode('ascii') + b'\0'
        criteria_raw = lines[1].encode('ascii') + b'\0'
        if len(timer_raw) > CRITERIA_TEXT_OFFSET - TIMER_TEXT_OFFSET:
            raise ValueError('Ranked search timer is too long')
        if len(criteria_raw) > STATE_SIZE - CRITERIA_TEXT_OFFSET:
            raise ValueError('Ranked search criteria is too long')
        payload = struct.pack('<QQQII', owner, timer_label, criteria_label, dirty, int(self.found))
        payload += timer_raw.ljust(CRITERIA_TEXT_OFFSET - TIMER_TEXT_OFFSET, b'\0')
        payload += criteria_raw.ljust(STATE_SIZE - CRITERIA_TEXT_OFFSET, b'\0')
        if not self.d.write(self.state, payload):
            raise OSError('Could not write Ranked search state')

    def created(self, hit):
        owner, criteria_label = hit.rdi, hit.rax
        timer_label = self.d.q(owner + TIMER_LABEL_OFFSET)
        if self.d.q(owner) != POPUP_VTABLE or not timer_label or not criteria_label:
            return
        self.found = False
        self.handoff_last = None
        self.handoff_samples = 0
        self.started = time.monotonic()
        self.last_second = 0
        lines = search_lines(0, self.revision)
        self._write_state(owner, timer_label, criteria_label, lines)
        self.d.log(f'RANKED_SEARCH popup active lines={lines!r}')

    def destroyed(self, hit):
        if self.state and self.d.q(self.state) == hit.rcx:
            self.d.write(self.state, bytes(32))
            self.started = self.last_second = None
            self.found = False
            self.d.log('RANKED_SEARCH popup closed')

    def match_found(self):
        if self.started is None or not self.state or self.found:
            return False
        owner, timer_label, criteria_label = struct.unpack('<QQQ', self.d.read(self.state, 24))
        if (not owner or self.d.q(owner) != POPUP_VTABLE
                or self.d.q(owner + TIMER_LABEL_OFFSET) != timer_label
                or self.d.q(owner + CRITERIA_LABEL_OFFSET) != criteria_label):
            return False
        self.found = True
        self._write_state(owner, timer_label, criteria_label,
                          ('Match found', 'Connecting to players...'))
        self.d.log('RANKED_SEARCH confirmed roster; handoff shown and search Cancel locked')
        return True

    def cancel(self, hit):
        if (self.found and self.state and self.d.q(self.state) == hit.rcx
                and self.d.q(hit.rcx) == POPUP_VTABLE):
            hit.setreg('rip', self.d.q(hit.rsp))
            hit.setreg('rsp', hit.rsp + 8)
            self.d.log('RANKED_SEARCH ignored stale search Cancel after confirmed roster')

    def trace_handoff(self, debugger):
        def word(root, offset):
            raw=debugger.read(root+offset,4) if root else b''
            return int.from_bytes(raw,'little') if len(raw)==4 else None
        game=debugger.q(0x141B87450)
        nrt=debugger.q(game+0x150) if game else 0
        matching=debugger.q(0x141B874C8)
        manager=debugger.q(0x141B94028)
        backend=debugger.q(manager+0x48) if manager else 0
        value=(word(matching,0xc),word(nrt,0x40c8),word(nrt,0x40d0),
               word(backend,0x120),word(backend,0x14c),word(backend,0x150),word(backend,0x154))
        if value!=self.handoff_last and self.handoff_samples<20:
            self.handoff_last=value;self.handoff_samples+=1
            debugger.log(f'RANKED_SEARCH handoff raw states={value}; sample={self.handoff_samples}/20')

    def poll(self, debugger):
        if self.started is None or not self.state:
            return
        if self.found:
            self.trace_handoff(debugger)
            return
        snapshot = debugger.read(self.state, 28)
        if len(snapshot) != 28:
            return
        owner, timer_label, criteria_label, dirty = struct.unpack('<QQQI', snapshot)
        if not owner or not timer_label or not criteria_label or dirty:
            return
        if (debugger.q(owner) != POPUP_VTABLE or
                debugger.q(owner + TIMER_LABEL_OFFSET) != timer_label or
                debugger.q(owner + CRITERIA_LABEL_OFFSET) != criteria_label):
            return
        elapsed = int(time.monotonic() - self.started)
        if elapsed == self.last_second:
            return
        self.last_second = elapsed
        timer, criteria = search_lines(elapsed, self.revision)
        timer_raw = (timer.encode('ascii') + b'\0').ljust(
            CRITERIA_TEXT_OFFSET - TIMER_TEXT_OFFSET, b'\0')
        criteria_raw = (criteria.encode('ascii') + b'\0').ljust(
            STATE_SIZE - CRITERIA_TEXT_OFFSET, b'\0')
        if (debugger.write(self.state + TIMER_TEXT_OFFSET, timer_raw) and
                debugger.write(self.state + CRITERIA_TEXT_OFFSET, criteria_raw)):
            debugger.write(self.state + DIRTY_OFFSET, struct.pack('<I', 1))

    def arm(self, debugger):
        guards = ((CANCEL, CANCEL_GUARD), (TIMER_LABEL_CALL, TIMER_CALL_GUARD),
                  (TIMER_LABEL_RETURN, TIMER_RETURN_GUARD),
                  (CRITERIA_LABEL_CALL, CRITERIA_CALL_GUARD),
                  (CRITERIA_LABEL_RETURN, CRITERIA_RETURN_GUARD),
                  (CREATE_LABELS_RETURN, CREATE_GUARD),
                  (HIDE_CRITERIA, HIDE_GUARD), (HIDE_TIMER, HIDE_GUARD),
                  (DESTROY, DESTROY_GUARD),
                  (UPDATE, UPDATE_PROLOGUE))
        for address, expected in guards:
            if debugger.read(address, len(expected)) != expected:
                raise RuntimeError(f'Ranked search native signature mismatch at {address:#x}')
        callbacks = (CANCEL, TIMER_LABEL_CALL, TIMER_LABEL_RETURN, CRITERIA_LABEL_CALL,
                     CRITERIA_LABEL_RETURN, CREATE_LABELS_RETURN, DESTROY)
        if any(address in debugger.bps for address in callbacks):
            raise RuntimeError('Ranked search breakpoint conflict')
        self.state = debugger.alloc(STATE_SIZE + 256)
        if not self.state:
            raise RuntimeError('Could not allocate Ranked search status state')
        thunk = self.state + STATE_SIZE
        code = _update_thunk(self.state)
        if not debugger.write(self.state, bytes(STATE_SIZE)) or not debugger.write(thunk, code):
            raise OSError('Could not install Ranked search status memory')
        replacement = bytes.fromhex('ff 25 00 00 00 00') + struct.pack('<Q', thunk)
        replacement += b'\xcc' * (len(UPDATE_PROLOGUE) - len(replacement))
        if not debugger.write(UPDATE, replacement):
            raise OSError('Could not install Ranked search update hook')
        # The two recovered labels are alternate native states at the same
        # position. Keep both visible and offset their factory layout Y values.
        if (not debugger.write(HIDE_CRITERIA + 6, b'\x01') or
                not debugger.write(HIDE_TIMER + 6, b'\x01')):
            raise OSError('Could not enable both Ranked search labels')
        debugger.bp(TIMER_LABEL_CALL, self.timer_layout)
        debugger.bp(TIMER_LABEL_RETURN, self.restore_layout)
        debugger.bp(CRITERIA_LABEL_CALL, self.criteria_layout)
        debugger.bp(CRITERIA_LABEL_RETURN, self.restore_layout)
        debugger.bp(CREATE_LABELS_RETURN, self.created)
        debugger.bp(DESTROY, self.destroyed)
        debugger.bp(CANCEL, self.cancel)
        debugger.log('RANKED_SEARCH count-up and live criteria armed')


def setup(d):
    session = RankedSearchStatus(d)
    d.ranked_search_status = session
    d.on_start(session.arm)
    d.poll(session.poll, every=1.0)

