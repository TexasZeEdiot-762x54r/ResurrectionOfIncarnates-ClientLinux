"""Keep the native battle team-name cache synchronized with the shared server."""
import json
import queue
import threading
import time


GLOBAL_STATE_PTR = 0x141B87458
GLOBAL_TEAM_NAME_OFFSET = 0x3CE04
PROFILE_TAG_PARTNER_OFFSET = 0xF240
INVALID_UID = 0xF0F0F0F0F0F0F0F0
BUFFER_SIZE = 64
RETRY_SECONDS = 5.0


def response_name(reply, partner):
    rows = reply.get('sv_tagNames') if isinstance(reply, dict) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError('Invalid team-name response')
    row = rows[0]
    if str(row.get('sv_uid')) != str(partner):
        raise ValueError('Team-name response identity mismatch')
    paid = row.get('sv_paidTagName')
    free = row.get('sv_freeTagName')
    if not isinstance(paid, str) or not isinstance(free, str):
        raise ValueError('Invalid team-name response fields')
    name = paid or free
    raw = name.encode('utf-8')
    if len(raw) >= BUFFER_SIZE or b'\0' in raw:
        raise ValueError('Team name exceeds the native display field')
    return name


def account_identity():
    import roi_progression_client

    try:
        with roi_progression_client.KEY_FILE.open('r', encoding='utf-8') as stream:
            value = json.load(stream)
    except (FileNotFoundError, OSError, ValueError):
        return None, None
    uid = str(value.get('uid', '')) if isinstance(value, dict) else ''
    token = roi_progression_client.credential(uid) if uid else None
    return (uid, token) if token is not None else (None, None)


def current_pair(reader, with_side=False):
    """Validate the same native team rows used by the shared CPU initializer."""
    import native_lp_input as native
    if native.is_replay(reader):
        return (0, 0)
    state = native.pointer(reader, 0x141B87440)
    game = native.pointer(reader, 0x141B87450)
    account = native.pointer(reader, game+0x158)
    actor = native.number(reader, account+0x10, '<Q')
    if not actor:
        return None
    seats, humans = set(), {}
    for side in range(2):
        team = state+side*0x3CB58
        if native.number(reader, team+0x94, '<I') != 2:
            return None
        for index in range(2):
            row = team+0x98+index*0xF2C8
            flags = native.read(reader, row, 16)
            slot = flags[2]
            if slot > 3 or slot in seats or flags[9] != side:
                return None
            seats.add(slot)
            if flags[1] < 128:
                uid = native.number(reader, row+0x18, '<Q')
                if not uid or uid in humans:
                    return None
                humans[uid] = side
    if actor not in humans:
        return None
    partners = [uid for uid, side in humans.items() if uid != actor and side == humans[actor]]
    if with_side and len(partners)==1: return actor, partners[0], humans[actor]
    return (actor, partners[0]) if len(partners) == 1 else (0, 0)


def native_pair(reader):
    try:
        pair = current_pair(reader)
    except (OSError, ValueError, TypeError):
        pair = None
    return retained_pair(reader) if pair is None else pair


def retained_pair(reader, with_side=False):
    """Read the retained battle roster, never a last-renamed global partner."""
    import native_lp_input as native
    play=native.pointer(reader,0x141B874D0)
    if native.is_replay(reader) or native.number(reader,play+0x10,'<I')!=0x150305F0:
        return 0,0
    own=native.number(reader,play+0x14,'<B')
    if own>3:return 0,0
    rows=[]
    for slot in range(4):
        base=play+0x30+slot*0x22D8
        side=native.number(reader,base+9,'<B')
        if side not in (0,1):return 0,0
        uid = native.number(reader,base+0x18,'<Q') if native.number(reader,base+1,'<B') < 128 else 0
        rows.append((uid,side))
    partners=[uid for slot,(uid,side) in enumerate(rows) if slot!=own and side==rows[own][1]]
    if len(partners)!=1 or not rows[own][0] or not partners[0] or partners[0]==rows[own][0]:return 0,0
    if with_side: return rows[own][0],partners[0],rows[own][1]
    return rows[own][0],partners[0]


def server_selection(pair):
    """Query the exact current teammate; never use progression's global selection."""
    import roi_server_data
    import roi_team_names

    uid, token = account_identity()
    actor,partner=pair
    if uid is None or str(actor)!=str(uid) or not partner:
        return 0, ''
    reply = roi_team_names.request(roi_server_data.relay_url(f'/team/{uid}'), token,
                                   'getTagName', {'sv_uids': [str(partner)]})
    return int(partner), response_name(reply, partner)


class TeamNameDisplay:
    def __init__(self, debugger, fetch=server_selection, clock=time.monotonic, threaded=True):
        self.d = debugger
        self.fetch = fetch
        self.clock = clock
        self.requests = queue.Queue(maxsize=1)
        self.results = queue.Queue()
        self.last_attempt = float('-inf')
        self.applied = None
        self.selection = (0, '')
        self.context = (0,0)
        if threaded:
            threading.Thread(target=self.worker, name='roi-team-name', daemon=True).start()

    def worker(self):
        while True:
            pair=self.requests.get()
            try:
                partner, name = self.fetch(pair)
                self.results.put((pair, partner, name, None))
            except (OSError, RuntimeError, ValueError, TypeError) as exc:
                self.results.put((pair, None, None, type(exc).__name__))

    def state(self):
        state = self.d.q(GLOBAL_STATE_PTR)
        return state or 0

    def request(self):
        now = self.clock()
        if now - self.last_attempt < RETRY_SECONDS:
            return
        try:
            self.requests.put_nowait(self.context)
        except queue.Full:
            return
        self.last_attempt = now

    def apply(self, state, name):
        raw = name.encode('utf-8')
        payload = raw + bytes(BUFFER_SIZE - len(raw))
        if (self.applied == (state, payload)
                and self.d.read(state + GLOBAL_TEAM_NAME_OFFSET, BUFFER_SIZE) == payload):
            return
        if not self.d.write(state + GLOBAL_TEAM_NAME_OFFSET, payload):
            raise OSError('Could not update native team-name display cache')
        self.applied = (state, payload)
        self.d.log(f'TEAM_NAME display synchronized name={name!r}')

    def poll(self, debugger):
        state = self.state()
        if not state:
            return
        try:pair=native_pair(debugger)
        except (OSError,ValueError,TypeError):pair=(0,0)
        if pair!=self.context:
            self.context=pair
            self.last_attempt=float('-inf')
            self.selection=(0,'')
            self.apply(state,'')
        while True:
            try:
                context, partner, name, error = self.results.get_nowait()
            except queue.Empty:
                break
            if context!=self.context:
                continue
            if error is not None:
                debugger.log(f'TEAM_NAME synchronization deferred error={error}')
            else:
                self.selection = (partner, name)
                self.apply(state, name)
        if self.context != (0, 0) and self.selection[0] == self.context[1]:
            # Native scene setup can clear the same display buffer between replies.
            self.apply(state, self.selection[1])
        if self.context != (0, 0):
            self.request()


def label_bytes(pair, selection, side, text):
    partner, name = selection
    if (side not in (0, 1) or not pair[0] or not partner or pair[1] != partner
            or pair[0] == partner or not name
            or text.split(b'\0', 1)[0] != (b'Team A', b'Team B')[side]):
        return None
    raw = name.encode('utf-8')
    return raw.ljust(64, b'\0') if len(raw) < 64 and b'\0' not in raw else None


# Both briefing fields and both stock fields are initialized 64-byte stack
# strings at these instructions. Do not retain stack addresses after the call.
LABEL_SITES = {
    0x140301CD8: ('498b442450', (0x60,)),
    0x140301F00: ('498b442450', (0x20,)),
    0x1404E36D5: ('488bb424e0000000', (0x20, 0x60)),
}


def arm_labels(d, bridge):
    def visit(hit, offsets):
        try:
            resolved = current_pair(d, with_side=True)
            if resolved is None:
                resolved = retained_pair(d, with_side=True)
            if not resolved or resolved[:2] != bridge.context or len(resolved) != 3:
                return
            for offset in offsets:
                addr = hit.rsp + offset
                replacement = label_bytes(bridge.context, bridge.selection,
                                          resolved[2], d.read(addr, 64))
                if replacement is not None:
                    d.write(addr, replacement)
        except (ValueError, TypeError, OSError):
            return
    for addr, (guard, offsets) in LABEL_SITES.items():
        expected = bytes.fromhex(guard)
        if addr not in d.bps and d.read(addr, len(expected)) == expected:
            d.bp(addr, lambda hit, offsets=offsets: visit(hit, offsets))


def setup(d):
    bridge = TeamNameDisplay(d)
    def header(hit):
        try:
            partner, name = bridge.selection
            if not partner or bridge.context != native_pair(d) or partner != bridge.context[1]:
                return
            raw = name.encode('utf-8')
            if hit.rax and len(raw) < 64 and b'\0' not in raw:
                d.write(hit.rax + 0x118, raw.ljust(64, b'\0'))
        except (ValueError, OSError, TypeError):
            return
    def arm(reader):
        arm_labels(reader, bridge)
        site = 0x1404c0715
        if site not in reader.bps and reader.read(site,7) == bytes.fromhex('0f108018010000'):
            reader.bp(site,header)
    d.on_start(arm)
    d.poll(bridge.poll, every=0.25)
    d.log('TEAM_NAME server-backed battle display synchronization enabled')
