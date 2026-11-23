"""Receipt-driven native Circle rename UI; no network work on the game thread."""
import json
import queue
import struct
import threading
import time
import urllib.request

SUBMIT = 0x140325050
UPDATE = 0x140324720
CHOICE = 0x1403255F6
POPUP = 0x140324BC0
DISMISS = 0x140325790
STORE = 0x14015F060
GUARDS = {SUBMIT: bytes.fromhex('48895c2410'),
          UPDATE: bytes.fromhex('48895c2410'),
          CHOICE: bytes.fromhex('4585c0'),
          POPUP: bytes.fromhex('48895c24104889742418'),
          DISMISS: bytes.fromhex('4885c97446'),
          STORE: bytes.fromhex('40534883ec70')}

UPDATE_PROLOGUE = bytes.fromhex('48895c2410488974241848897c2420')
CHOICE_PROLOGUE = bytes.fromhex('4585c0740a41ffc8745041ffc875d3')
GUARDS[UPDATE] = UPDATE_PROLOGUE
GUARDS[CHOICE] = CHOICE_PROLOGUE
# Native StoreTabWindow consumes this once, then restores -1. Other is category 8; Product Code is 9.
STORE_SELECTOR = 0x5f604
CIRCLE_REFRESH = 0x14008d2f3
TAG_PARSE = 0x1400911b0
HAS_FIELD = 0x1400f7c10
GUARDS[CIRCLE_REFRESH] = bytes.fromhex('488b0d5ea1af01498d5620e8ad5ef8ff')
GUARDS[TAG_PARSE] = bytes.fromhex('48895c2408')


def jump(address):
    return bytes.fromhex('ff2500000000')+struct.pack('<Q', address)


def choice_thunk():
    # No debugger trap, allocation or Python callback on the name-render path.
    code = bytes.fromhex('4183f801750a41807a4c0074034531c0')
    code += bytes.fromhex('4585c0750e')+jump(0x140325605)
    code += bytes.fromhex('41ffc8750e')+jump(0x140325650)
    code += bytes.fromhex('41ffc8740e')+jump(0x1403255d8)
    return code+jump(0x140325605)


def update_gate(base, flag):
    # A different instance must not consume this dialog's receipt notification.
    owner_slot = flag+8
    code = bytearray(b'\x9c')
    code += b'\x80\x3d'+struct.pack('<i',flag-(base+len(code)+7))+b'\0'
    code += b'\x74\0'
    branches = [len(code)-1]
    code += b'\x50'
    code += b'\x48\x8b\x05'+struct.pack('<i',owner_slot-(base+len(code)+7))
    code += bytes.fromhex('4839c158')
    code += b'\x75\0'
    branches.append(len(code)-1)
    code += b'\xc6\x05'+struct.pack('<i',flag-(base+len(code)+7))+b'\0\x9d'
    callback = base+len(code)
    code += b'\x90'+UPDATE_PROLOGUE+jump(UPDATE+15)
    idle = len(code)
    for offset in branches:
        distance=idle-(offset+1)
        if not 0 <= distance < 128:raise ValueError('Circle gate branch exceeds bound')
        code[offset]=distance
    code += b'\x9d'+UPDATE_PROLOGUE+jump(UPDATE+15)
    return bytes(code), callback


def circle_refresh_thunk():
    # Mid-function RSP is aligned. The decoded response object is at entry RSP+60h.
    # Reuse the native getTagName parser, which merges rows through 0x140013520.
    body = bytes.fromhex('488d8c2470010000')+mov('rdx',0x14184eff0)+call(HAS_FIELD)
    body += bytes.fromhex('84c07414')
    body += bytes.fromhex('488d8c2470010000')+call(TAG_PARSE)
    code = frame(body, b'').replace(bytes.fromhex('4881ec98000000'), bytes.fromhex('4881ec90000000'))
    code = code.replace(bytes.fromhex('4881c498000000'), bytes.fromhex('4881c490000000'))
    original = mov('rcx', 0x141B87458)+bytes.fromhex('488b09498d5620')
    # An indirect absolute call avoids clobbering a register before the original call.
    original += bytes.fromhex('ff1502000000eb08')+struct.pack('<Q',0x1400131b0)
    return code+original+jump(CIRCLE_REFRESH+16)



MEMBER_NAME = 0x14031517d
MEMBER_NAME_END = 0x1403151bd
GUARDS[MEMBER_NAME] = bytes.fromhex('488b470c48894507488b47144889450f')


def member_name_thunk():
    # Prefer a nonempty paid name only for this member-list presentation path.
    # Leave the actual free/paid pair records and RDI untouched.
    def copy_name(offset):
        code = b''
        for index in range(8):
            code += bytes.fromhex('488b87')+struct.pack('<I', offset+index*8)
            code += bytes.fromhex('488945')+bytes([7+index*8])
        return code+jump(MEMBER_NAME_END)
    paid = b'\x9d'+copy_name(0x4c)
    if len(paid)>127:raise ValueError('Member name branch exceeds bound')
    return bytes.fromhex('9c807f4c0074')+bytes([len(paid)])+paid+b'\x9d'+copy_name(0xc)


def display_state(reply, partner, requested):
    if not isinstance(reply, dict) or reply.get('result') != 'OK':
        return 'failed'
    data = reply.get('data')
    if not isinstance(data, dict) or data.get('sv_partnerUid') != partner:
        return 'failed'
    status = data.get('sv_teamNameStatus')
    if status == 'ticket-required':
        return 'store-required'
    if status not in ('applied', 'unchanged'):
        return 'failed'
    free, paid = data.get('sv_freeTagName'), data.get('sv_paidTagName')
    if not all(isinstance(s, str) and len(s.encode('utf-8')) <= 63 and '\0' not in s
               for s in (free, paid)) or (paid or free) != requested:
        return 'failed'
    return status


def frame(body, continuation=b'\xc3'):
    # Entry RSP is 8 mod 16; preserve flags, every GPR and the volatile XMM set.
    code = bytearray(bytes.fromhex('9c50515253555657415041514152415341544155415641574881ec98000000'))
    for i in range(6):
        code += bytes((0xf3, 0x0f, 0x7f, 0x84+(i << 3), 0x24))+struct.pack('<I', 0x30+i*16)
    code += body
    for i in range(6):
        code += bytes((0xf3, 0x0f, 0x6f, 0x84+(i << 3), 0x24))+struct.pack('<I', 0x30+i*16)
    code += bytes.fromhex('4881c498000000415f415e415d415c415b415a415941585f5e5d5b5a59589d')
    return bytes(code)+continuation


def mov(register, value):
    return {'rax': b'\x48\xb8', 'rcx': b'\x48\xb9', 'rdx': b'\x48\xba',
            'r8': b'\x49\xb8', 'r9': b'\x49\xb9'}[register]+struct.pack('<Q', value)


def call(address):
    return mov('rax', address)+b'\xff\xd0'


def popup_thunk(owner, text, confirm=0, yes=0, no=0, resume=False):
    body = mov('rcx', owner)+mov('rdx', text)+mov('r8', confirm)+mov('r9', yes)
    body += mov('rax', no)+bytes.fromhex('4889442420')+call(POPUP)
    tail = UPDATE_PROLOGUE+jump(UPDATE+15) if resume else b'\xc3'
    return frame(body, tail)


def store_thunk():
    # The native menu callback owns CanChangeScene, cleanup and Store construction.
    body = call(DISMISS)+mov('rax', 0x141B87458)+bytes.fromhex('488b00')
    body += bytes.fromhex('c780')+struct.pack('<I',STORE_SELECTOR)+struct.pack('<I',8)
    body += bytes.fromhex('31c9ba01000000')+call(STORE)
    return frame(body)


def exchange(actor, partner, name, mode):
    payload = dict(version=0.8, call='circleTeamNameUI', data={
        'actor': str(actor), 'sv_partnerUid': str(partner), 'sv_tagName': name,
        'sv_isCircle': str(mode)})
    request = urllib.request.Request('http://127.0.0.1:52710/Dragon/game',
        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=18) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError('Circle response too large')
    return json.loads(raw)


class Driver:
    def __init__(self, debugger, send=exchange, clock=time.monotonic):
        self.d, self.send, self.clock = debugger, send, clock
        self.pending = None
        self.results = queue.Queue(maxsize=4)
        self.buffer = 0
        self.serial = 0
        self.ready = None

    def actor(self):
        game = self.d.q(0x141B87450)
        account = self.d.q(game+0x158) if game else 0
        return self.d.q(account+0x10) if account else 0

    def show(self, hit, owner, text, resume=False, store=False):
        raw = text.encode('utf-8')+b'\0'
        if len(raw) > 512:
            raise ValueError('Circle notice too large')
        code = popup_thunk(owner, self.buffer+2048, 0 if store else DISMISS,
                           self.buffer+1024 if store else 0, DISMISS if store else 0, resume)
        if not self.d.write(self.buffer+2048, raw) or not self.d.write(self.buffer, code):
            raise OSError('Could not prepare native Circle notice')
        hit.setreg('rip', self.buffer)

    def work(self, operation):
        serial, owner, actor, partner, name, mode, started = operation
        try:
            reply = self.send(actor, partner, name, mode)
        except (OSError, ValueError, RuntimeError):
            reply = None
        try:
            self.results.put_nowait((operation, reply))
        except queue.Full:
            pass

    def submit(self, hit):
        owner = hit.rcx
        actor, partner = self.actor(), self.d.q(owner+0x2b0)
        raw = self.d.read(hit.rdx, 64)
        try:
            if b'\0' not in raw:
                raise ValueError('Unterminated native name')
            name = raw.split(b'\0', 1)[0].decode('utf-8')
            mode = self.d.d32(owner+0xf598)
            if not actor or not partner or actor == partner or mode not in (0, 1, 2):
                raise ValueError('Invalid native Circle identity')
            mode = int(mode == 1)
            if not name.strip() or any(ord(c) < 32 or c in '<>' for c in name):
                raise ValueError('Invalid native name')
        except (ValueError, UnicodeError):
            self.show(hit, owner, 'The team name could not be verified. Please reopen the name selector.')
            return
        if self.pending is not None and (self.pending[1] != owner or self.pending[2] != actor
                                         or self.clock()-self.pending[-1] >= 20):
            self.pending = None
        if self.pending is not None:
            hit.setreg('rip', self.d.q(hit.rsp))
            hit.setreg('rsp', hit.rsp+8)
            return
        self.serial += 1
        operation = (self.serial, owner, actor, partner, name, mode, self.clock())
        self.pending = operation
        self.ready = None
        self.d.log('CIRCLE_TEAM_UI request queued serial='+str(self.serial))
        # A modal without buttons prevents duplicate submissions while the worker waits.
        code = popup_thunk(owner, self.buffer+2048)
        if not self.d.write(self.buffer+2048, b'Updating team name...\0') or not self.d.write(self.buffer, code):
            raise OSError('Could not prepare native Circle loading notice')
        hit.setreg('rip', self.buffer)
        threading.Thread(target=self.work, args=(operation,), daemon=True).start()

    def poll(self, debugger):
        if self.pending is None:
            return
        if self.ready is None:
            try:
                while True:
                    operation, reply = self.results.get_nowait()
                    if operation == self.pending:
                        self.ready = (operation, reply)
                        break
            except queue.Empty:
                if self.clock()-self.pending[-1] >= 20:
                    self.ready = (self.pending, None)
        if self.ready is not None:
            # Publish the owner before the flag. Idle/wrong-instance frames stay native.
            if not debugger.write(self.buffer+3080, struct.pack('<Q', self.pending[1])):
                raise OSError('Could not publish Circle receipt owner')
            if not debugger.write(self.buffer+3072, b'\x01'):
                raise OSError('Could not publish Circle receipt readiness')

    def choice(self, hit):
        if hit.r8 == 1 and self.d.read(hit.r10+0x4c, 1) not in (b'', b'\0'):
            hit.setreg('r8', 0)

    def update(self, hit):
        if self.pending is None or hit.rcx != self.pending[1]:
            return
        operation = self.pending
        _, owner, actor, partner, name, mode, started = operation
        if self.actor() != actor or self.d.q(owner+0x2b0) != partner:
            self.pending = None
            return
        if self.ready is None or self.ready[0] != operation:
            return
        _, reply = self.ready
        self.ready = None
        self.pending = None
        state = display_state(reply, str(partner), name)
        if state in ('applied', 'unchanged', 'store-required'):
            data = reply['data']
            manager = self.d.q(0x141B87458)
            begin, end = self.d.q(manager+0x53f10), self.d.q(manager+0x53f18)
            if begin and 0 <= end-begin <= 128*0xd0 and (end-begin) % 0xd0 == 0:
                for row in range(begin, end, 0xd0):
                    if self.d.q(row) == partner:
                        for offset, key in ((0xc, 'sv_freeTagName'), (0x4c, 'sv_paidTagName')):
                            value = data.get(key)
                            if isinstance(value, str) and '\0' not in value and len(value.encode()) <= 63:
                                self.d.write(row+offset, value.encode().ljust(64, b'\0'))
                        break
            # Native update rebuilds its bound text from the verified pair cache.
            self.d.write(owner+0xf518, bytes(64))
        text = {'applied': 'Team name changed. One Team Name Ticket was used.',
                'unchanged': 'This team name is already set. No ticket was used.',
                'store-required': 'You need a Team Name Ticket to change this team name. Buy Team Name Tickets from the Store first. Open Store now?',
                'failed': 'Could not confirm the team name change. Check the current name before trying again.'}[state]
        self.show(hit, owner, text, resume=True, store=state == 'store-required')
        self.d.log('CIRCLE_TEAM_UI receipt outcome='+state)

    def arm(self, debugger):
        for address, expected in GUARDS.items():
            if debugger.read(address, len(expected)) != expected:
                raise RuntimeError('Unexpected native Circle code')
        for address in (SUBMIT, UPDATE, CHOICE, CIRCLE_REFRESH, MEMBER_NAME):
            if address in debugger.bps:
                raise RuntimeError('Occupied Circle rename hook')
        self.buffer = debugger.alloc(8192)
        if not self.buffer or not debugger.write(self.buffer+1024, store_thunk()):
            raise OSError('Could not prepare native Circle Store callback')
        debugger.bp(SUBMIT, self.submit)
        gate, callback = update_gate(self.buffer+4096, self.buffer+3072)
        patches = ((self.buffer+4096, gate), (self.buffer+5120, choice_thunk()),
                   (self.buffer+6144, circle_refresh_thunk()), (self.buffer+3072, b'\0'),
                   (self.buffer+3080, bytes(8)), (self.buffer+7168, member_name_thunk()))
        for address, code in patches:
            if not debugger.write(address, code):
                raise OSError('Could not prepare native Circle fast path')
        debugger.bp(callback, self.update)
        if not debugger.write(UPDATE, jump(self.buffer+4096)+b'\x90'):
            raise OSError('Could not install Circle receipt gate')
        if not debugger.write(CHOICE, jump(self.buffer+5120)+b'\x90'):
            raise OSError('Could not install Circle name fast path')
        if not debugger.write(CIRCLE_REFRESH, jump(self.buffer+6144)+b'\x90\x90'):
            raise OSError('Could not install Circle Information name refresh')
        if not debugger.write(MEMBER_NAME, jump(self.buffer+7168)+b'\x90\x90'):
            raise OSError('Could not install Circle member paid-name presentation')
        debugger.poll(self.poll, every=min(debugger.poll_every, 1.0))
        debugger.log('CIRCLE_TEAM_UI receipt-driven rename armed')


def setup(debugger):
    driver = Driver(debugger)
    debugger.on_start(driver.arm)
