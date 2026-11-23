"""Bounded read-only trace for Ranked Team Start Battle enablement."""
import time


TEAM_SCREEN_CREATE = 0x1403AE640
START_CLICK = 0x1403AF5F0
START_DIALOG = 0x1403AF570
PARTNER_SEND = 0x1400E6C80
PARTNER_RECEIVE = 0x1400EDFC0
PARTY_ROOT = 0x141B94028
BACKEND_ROOT = 0x141B87450
TRACE_DAYS = 7
TRACE_SECONDS = TRACE_DAYS * 24 * 60 * 60
BUTTON_OFFSET = 0x440
BUTTON_ENABLED_OFFSET = 0x228
PARTNER_EVENT_LIMIT = 64


"""Handle only the server's marked post-results timeout, on the Ranked menu thread."""
TICK = 0x1403AF020
LEAVE = 0x1403AF940
MARK = b'R45END'
"""R45 Ranked duo timeout. Only the menu thread invokes the bound Leave action."""
import struct

EXPIRY_PROLOGUE = bytes.fromhex("48896c2420564883ec4033ed488bf1")


def expiry_jump(target):
    return bytes.fromhex("ff2500000000") + struct.pack("<Q", target)


def expiry_update(mail, root_slot, leave, ordinary):
    code = bytearray()
    edges = []
    labels = {}

    def emit(value):
        code.extend(bytes.fromhex(value))

    def branch(cc, label):
        emit("0f" + cc)
        edges.append((len(code), label))
        code.extend(bytes(4))

    # Preserve all borrowed registers and entry flags on both tail exits.
    emit("9c 50 52 4150 49b8")
    code.extend(struct.pack("<Q", mail))
    emit("49833800")
    branch("84", "normal")
    emit("48a1")
    code.extend(struct.pack("<Q", root_slot))
    for deref in ("488b4078", "488b5010"):
        emit("4885c0")
        branch("84", "discard")
        emit(deref)
    emit("493b5008")  # Current model is the one observed on receipt.
    branch("85", "discard")
    emit("488b5208 493b5010")
    branch("85", "discard")
    emit("83b89806000000")
    branch("84", "discard")
    emit("488b5010 83ba5801000003")
    branch("85", "discard")
    emit("83ba5c01000007")
    branch("85", "discard")
    emit("488b8238010000 4883c040 483b8240010000")
    branch("85", "discard")
    # Resolve the current peer through the current container again.
    emit("48a1")
    code.extend(struct.pack("<Q", root_slot))
    emit("488b4078 488b4008 4885c0")
    branch("84", "discard")
    emit("488b4008 4885c0")
    branch("84", "discard")
    emit("488b8078010000 493b00")
    branch("85", "discard")
    for field in ("70040000", "74040000"):
        emit("83b9" + field + "01")
        branch("85", "normal")
    emit("488b8148040000 4885c0")
    branch("84", "normal")
    emit("48398868020000")
    branch("85", "normal")
    emit("48ba")
    code.extend(struct.pack("<Q", leave))
    emit("48399070020000")
    branch("85", "normal")
    emit("49c70000000000 4158 5a 58 9d")
    code.extend(expiry_jump(leave))
    labels["discard"] = len(code)
    emit("49c70000000000")
    labels["normal"] = len(code)
    emit("4158 5a 58 9d")
    code.extend(ordinary)
    for pos, label in edges:
        struct.pack_into("<i", code, pos, labels[label] - (pos + 4))
    return bytes(code)


def install_expiry_menu(d):
    if d.read(TICK, len(EXPIRY_PROLOGUE)) != EXPIRY_PROLOGUE or TICK in d.bps:
        raise RuntimeError("Unexpected Ranked menu update")
    mail = d.alloc(4096)
    if not mail:
        raise OSError("Could not reserve Ranked menu timeout state")
    normal = EXPIRY_PROLOGUE + expiry_jump(TICK + len(EXPIRY_PROLOGUE))
    body = expiry_update(mail, PARTY_ROOT, LEAVE, normal)
    if not d.write(mail, bytes(32) + body):
        raise OSError("Could not prepare Ranked menu timeout")
    entry = expiry_jump(mail + 32).ljust(len(EXPIRY_PROLOGUE), b"\x90")
    if not d.write(TICK, entry):
        raise OSError("Could not arm Ranked menu timeout")
    return mail



"""Dismiss an obsolete tag wait on its own UI update, not on the network thread."""
WAIT_BRANCH = 0x1403C16A3
LOCAL_CLOSE = 0x1403C167C
WAIT_MARK = b'R45MNU'


class CancelledWait:
    def __init__(self, trace):
        self.trace = trace
        self.pending = None

    def receive(self, raw):
        if raw[:2] in (b'\0\0', b'\x03\0'):
            self.pending = None
        if len(raw) != 22 or raw[:2] != b'\x06\0' or raw[10:] != bytes(6) + WAIT_MARK:
            return
        uid = int.from_bytes(raw[2:10], 'little')
        self.pending = self.trace.expiry.identity(uid) if uid else None
        d = self.trace.d
        if self.pending and WAIT_BRANCH not in d.bps:
            d.bp(WAIT_BRANCH, self.close)

    def close(self, hit):
        t = self.trace
        proof, self.pending = self.pending, None
        if (proof and t.expiry.identity(proof[0]) == proof
                and t.d.d32(hit.rbx + 0x348) in (1, 2)
                and not t.d.d32(hit.rbx + 0x338)
                and not getattr(getattr(t.d, 'ranked_search_status', None), 'found', False)):
            hit.setreg('rip', LOCAL_CLOSE)
            t.d.log('RANKED_DUO closed cancelled partner wait locally')
        return 'remove'


class RankedExpiry:
    def __init__(self, trace):
        self.d = trace.d
        self.pending = None

    def identity(self, uid):
        d = self.d
        root = d.q(0x141B94028)
        container = d.q(root + 0x78) if root else 0
        model = d.q(container + 0x10) if container else 0
        owner = d.q(container + 8) if container else 0
        profile = d.q(owner + 8) if owner else 0
        if (not model or not profile or d.q(profile + 0x178) != uid
                or d.d32(model + 0x15c) != 7 or d.d32(model + 0x158) != 3
                or not d.d32(container + 0x698)
                or d.q(model + 0x140) - d.q(model + 0x138) != 64):
            return None
        lobby = d.q(model + 8)
        return (uid, model, lobby) if lobby else None

    def receive(self, hit):
        pos = self.d.d32(hit.rcx + 0xd0)
        raw = self.d.read(hit.rdx + pos, 22)
        self.practice.receive(raw)
        self.cancel_wait.receive(raw)
        if raw[:2] == bytes(2):
            self.pending = None
        if len(raw) != 22 or raw[:2] != b'\x03\x00' or raw[10:] != bytes(6) + MARK:
            return
        uid = int.from_bytes(raw[2:10], 'little')
        self.pending = self.identity(uid) if uid else None

    @property
    def pending(self):
        return self._pending

    @pending.setter
    def pending(self, value):
        self._pending = value
        mail = getattr(self, 'mail', 0)
        if mail:
            if not self.d.write(mail, bytes(8)):
                raise OSError('Could not clear Ranked menu timeout')
            if value:
                uid, model, lobby = value
                if not self.d.write(mail + 8, struct.pack('<QQ', model, lobby)):
                    raise OSError('Could not store Ranked lobby identity')
                if not self.d.write(mail, struct.pack('<Q', uid)):
                    raise OSError('Could not notify Ranked menu')


"""Handle a server-confirmed cancellation of this partner's waiting practice."""
import ctypes
from ctypes import wintypes as wt
import time


def practice_f3(pid, pressed, expected=None):
    # Address only this client's game window, including on Proton. Never send
    # global input or activate a window on the player's desktop.
    u = ctypes.windll.user32
    callback = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    windows = []

    @callback
    def visit(hwnd, unused):
        owner = wt.DWORD()
        u.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(owner))
        if owner.value == pid and u.IsWindowVisible(wt.HWND(hwnd)):
            title = ctypes.create_unicode_buffer(128)
            u.GetWindowTextW(wt.HWND(hwnd), title, len(title))
            if title.value.strip().upper() == "RISE OF INCARNATES":
                windows.append(hwnd)
        return True

    u.EnumWindows(visit, 0)
    if len(windows) != 1:
        return False
    hwnd = wt.HWND(windows[0])
    scan = u.MapVirtualKeyW(0x72, 0) << 16
    if expected is not None and hwnd.value != expected:
        return False
    msg, flags = (0x100, 1) if pressed else (0x101, 0xc0000001)
    return hwnd.value if u.PostMessageW(hwnd, msg, 0x72, wt.LPARAM(scan | flags)) else False


class PracticePulse:
    def __init__(self, key, clock):
        self.key, self.clock = key, clock
        self.window = None
        self.release_at = 0

    def press(self):
        if self.window:
            return False
        self.window = self.key(True, None)
        self.release_at = self.clock() + 0.18
        return bool(self.window)

    def tick(self, abort=False):
        if self.window and (abort or self.clock() >= self.release_at):
            self.key(False, self.window)
            self.window = None


class PracticeExit:
    def __init__(self, log, clock=time.monotonic):
        self.log, self.clock = log, clock
        self.clear()

    def clear(self):
        self.generation = getattr(self, "generation", 0) + 1
        self.native_exit_done = False
        self.shared = False
        self.peer = self.pending = 0
        self.active = False
        self.phase = None
        self.next_attempt = 0
        self.attempts = 0

    def send(self, op, uid):
        if op in (0, 2, 3, 6, 9, 0x80):
            self.clear()
            if op == 0x80:
                self.peer = uid

    def receive(self, raw):
        if len(raw) != 22:
            return
        op, uid = int.from_bytes(raw[:2], 'little'), int.from_bytes(raw[2:10], 'little')
        if not self.peer or uid != self.peer:
            return
        if op in (0x81, 0x82) or (op == 0x83 and raw[10:] == bytes(6)+b'R49BOT'):
            self.active = True
            self.shared = op == 0x81 and raw[10:] == bytes(6)+b'R48BOT'
        elif op == 6 and raw[10:] == bytes(6) + b'R46CAN':
            if self.active and not self.pending:
                self.pending = uid
                self.log('PRACTICE_EXIT partner cancelled; awaiting playable practice')
        elif op in (0, 3):
            self.clear()

    def scene(self, name):
        if name in ('InitialBattle', 'Briefing', 'Battle') and self.active:
            if name != self.phase:
                self.phase = name
                self.attempts = 0
                self.next_attempt = self.clock() + 0.25
        elif name in ('Initial', 'SequencePlay', 'Title', 'ErrorPopup', 'Closed'):
            if self.pending and name in ('Initial','SequencePlay'):
                self.log('PRACTICE_EXIT observed return to menu')
            self.clear()

    def poll(self, ranked, exit_action):
        if ranked:
            self.clear()
        elif self.pending and self.phase and self.attempts < 3 and self.clock() >= self.next_attempt:
            self.attempts += 1
            self.next_attempt = self.clock() + 2.0
            posted = exit_action()
            self.log(f'PRACTICE_EXIT phase={self.phase} request={self.attempts} posted={posted}; awaiting scene')


class RankedTeamStartTrace:
    def __init__(self, debugger, clock=time.monotonic):
        self.d = debugger
        self.clock = clock
        self.started = clock()
        self.practice = debugger.practice_exit = PracticeExit(debugger.log)
        from sessions.practice_cancel_native import NativePracticeCancel
        self.native_cancel = NativePracticeCancel(self)
        self.expiry = RankedExpiry(self)
        self.expiry.practice = self.practice
        self.cancel_wait = CancelledWait(self)
        self.expiry.cancel_wait = self.cancel_wait
        self.screen = 0
        self.last = None
        self.clicks = 0
        self.partner_events = 0

    def active(self):
        return self.clock() - self.started < TRACE_SECONDS

    def created(self, hit):
        if not self.active():
            return
        self.screen = hit.rcx
        self.last = None
        self.d.log(f'RANKED_TEAM_TRACE screen create owner={self.screen:x}')

    def snapshot(self, owner):
        button = self.d.q(owner + BUTTON_OFFSET)
        party_root = self.d.q(PARTY_ROOT)
        party_context = party_root + 0x40 if party_root else 0
        party_container = self.d.q(party_context + 0x38) if party_context else 0
        party_model = self.d.q(party_container + 0x10) if party_container else 0
        roster_begin = self.d.q(party_model + 0x138) if party_model else 0
        roster_end = self.d.q(party_model + 0x140) if party_model else 0
        roster_bytes = roster_end - roster_begin if roster_end >= roster_begin else -1
        roster_count = roster_bytes // 0x20 if roster_bytes >= 0 and roster_bytes % 0x20 == 0 else -1
        backend_root = self.d.q(BACKEND_ROOT)
        backend_transport = self.d.q(backend_root + 0x150) if backend_root else 0
        backend_ready = self.d.d32(backend_transport + 0x40E4) if backend_transport else -1
        party_state = self.d.d32(party_model + 0x15C) if party_model else -1
        party_live = self.d.d32(party_container + 0x698) if party_container else -1
        return {
            'mode_470': self.d.d32(owner + 0x470),
            'state_474': self.d.d32(owner + 0x474),
            'state_478': self.d.d32(owner + 0x478),
            'state_47c': self.d.d32(owner + 0x47C),
            'state_480': self.d.d32(owner + 0x480),
            'start_button': button,
            'button_enabled': self.d.read(button + BUTTON_ENABLED_OFFSET, 1).hex() if button else '',
            'button_state_2c4': self.d.d32(button + 0x2C4) if button else -1,
            'button_state_2c8': self.d.d32(button + 0x2C8) if button else -1,
            'party_root': party_root,
            'party_container': party_container,
            'party_model': party_model,
            'backend_root': backend_root,
            'backend_transport': backend_transport,
            'backend_ready': backend_ready,
            'party_state': party_state,
            'party_live': party_live,
            'party_pending_694': self.d.d32(party_container + 0x694) if party_container else -1,
            'party_pending_69c': self.d.d32(party_container + 0x69C) if party_container else -1,
            'party_model_kind': self.d.d32(party_model + 0x158) if party_model else -1,
            'roster_bytes': roster_bytes,
            'roster_count': roster_count,
            'native_prerequisites_met': party_state == 7 and party_live != 0 and roster_count >= 2,
        }

    def clicked(self, hit):
        if not self.active():
            return
        self.clicks += 1
        self.d.log(f'RANKED_TEAM_TRACE start click #{self.clicks} {self.snapshot(hit.rcx)!r}')

    def dialog(self, hit):
        if self.active():
            self.d.log(f'RANKED_TEAM_TRACE start dialog {self.snapshot(hit.rcx)!r}')

    def shared_ready(self, hit):
        p, d = self.practice, self.d
        if not p.shared or not p.active or p.pending or not p.peer:
            return
        p.shared = False
        driver = getattr(d, 'ranked_disconnect', None)
        if (getattr(getattr(d,'ranked_search_status',None),'found',False)
                or driver and (driver.pending or driver.context)):
            return
        # Same tag-partner wait object as the ordinary Team Vs Bot launch.
        # Change selectors only after native readiness and before closing its popup.
        obj = hit.rbx
        if (not obj or hit.rcx != obj or d.d32(obj+0x348)!=2 or d.d32(obj+0x350)!=0
                or not d.q(obj+0x340) or not self.expiry.identity(p.peer)):
            d.log('R48_SHARED declined: tag wait or partner changed')
            return
        if not d.write(obj+0x348,struct.pack('<II',3,1)):
            raise OSError('Could not select Team Vs Bot tag wait')
        # Finish the current state-2 tick at its own epilogue. On the next
        # tick the native dispatcher runs state 3, including its role checks.
        hit.setreg('rip',0x1403c1871)
        d.log('R48_SHARED entered native Team Vs Bot tag wait; Ranked admission retained')

    def partner_send(self, hit):
        op = hit.rdx & 0xffff
        self.practice.send(op, hit.r8)
        if op in (0, 2, 3, 6, 9, 0x80):
            self.expiry.pending = None
            self.cancel_wait.pending = None
        if op in (2, 0x80):
            game = self.d.q(BACKEND_ROOT)
            nrt = self.d.q(game + 0x150) if game else 0
            if nrt and not self.d.write(nrt + 0x40e8, bytes(8)):
                raise OSError('Could not reset stale partner readiness')
        if not self.active() or self.partner_events >= PARTNER_EVENT_LIMIT:
            return
        self.partner_events += 1
        operation = hit.rdx & 0xFFFF
        self.d.log('RANKED_TEAM_TRACE partner send '
                   f'operation={operation} target_present={bool(hit.r8)}')

    def partner_receive(self, hit):
        self.expiry.receive(hit)
        if not self.active() or self.partner_events >= PARTNER_EVENT_LIMIT:
            return
        try:
            cursor = self.d.d32(hit.rcx + 0xD0)
            raw = self.d.read(hit.rdx + cursor, 10)
            if len(raw) != 10:
                return
            operation = int.from_bytes(raw[:2], 'little')
            target_present = any(raw[2:10])
        except (OSError, RuntimeError, ValueError):
            return
        self.partner_events += 1
        self.d.log('RANKED_TEAM_TRACE partner receive '
                   f'operation={operation} target_present={target_present}')

    def poll(self, debugger):
        driver = getattr(debugger, 'ranked_disconnect', None)
        found = getattr(getattr(debugger, 'ranked_search_status', None), 'found', False)
        ranked = found or (driver and (driver.pending or driver.context))
        self.native_cancel.poll()
        if not self.active() or not self.screen:
            return
        current = self.snapshot(self.screen)
        if current != self.last:
            self.last = current
            debugger.log(f'RANKED_TEAM_TRACE state {current!r}')

    def arm(self, debugger):
        self.native_cancel.verify()
        for addr, guard in ((WAIT_BRANCH, '8b8348030000'), (LOCAL_CLOSE, '488b8b40030000'), (TICK, '48896c2420564883ec40'), (LEAVE, '4883ec284885c97455'),
                            (PARTNER_SEND, '48895c2410574883ec20')):
            if debugger.read(addr, len(bytes.fromhex(guard))) != bytes.fromhex(guard):
                raise RuntimeError('Unexpected Ranked Team native callback')
        for address in (TEAM_SCREEN_CREATE, START_CLICK, START_DIALOG,
                        PARTNER_SEND, PARTNER_RECEIVE):
            if address in debugger.bps:
                raise RuntimeError(f'Ranked Team trace breakpoint conflict at {address:#x}')
        branch = 0x1403c1841
        guards = {branch:'488b8940030000',0x1403c1871:'4883c4205bc3',
                  0x1403c15da:'c7874c03000001000000',0x1403c16e8:'e873020000',
                  0x1403c1b8e:'c78150f6050006000000'}
        if branch in debugger.bps or any(debugger.read(a,len(bytes.fromhex(h)))!=bytes.fromhex(h)
                                         for a,h in guards.items()):
            raise RuntimeError('Unexpected Team Vs Bot tag-wait dispatcher')
        debugger.bp(branch,self.shared_ready)
        self.expiry.mail = install_expiry_menu(debugger)
        debugger.bp(TEAM_SCREEN_CREATE, self.created)
        debugger.bp(START_CLICK, self.clicked)
        debugger.bp(START_DIALOG, self.dialog)
        debugger.bp(PARTNER_SEND, self.partner_send)
        debugger.bp(PARTNER_RECEIVE, self.partner_receive)
        debugger.log(f'RANKED_TEAM_TRACE armed read-only for {TRACE_DAYS} days')


def setup(d):
    trace = RankedTeamStartTrace(d)
    d.on_start(trace.arm)
    d.poll(trace.poll, every=0.25)
    from ranked_intent_native import setup as setup_intent
    setup_intent(d)
