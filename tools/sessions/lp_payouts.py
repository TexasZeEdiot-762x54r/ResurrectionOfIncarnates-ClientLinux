"""Normal-launch receipt bridge; LP/XP/cube presentation never grants inventory."""
import json
from http.client import IncompleteRead
from pathlib import Path
import re
import struct
import threading
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from urllib.error import HTTPError
from uuid import uuid4

import native_lp_input as native
from sessions.result_cube_display import CubeDisplay
from sessions.level_reward_notice import state as notice_state

ROOT = Path(__file__).resolve().parent.parent
FLAG = ROOT / 'lp-payouts.enabled'
TOKEN_FILE = ROOT / 'lp-bridge-token.txt'
URL = 'http://127.0.0.1:52710/Dragon/game'
HOOKS = {
    'bot': (0x140079680, bytes.fromhex('48895c24104c894c24204c89')),
    'start': (0x1400E8160, bytes.fromhex('4c8bdc49895b1049896b1849')),
    'custom': (0x1400EA140, bytes.fromhex('48895c240848897424104889')),
    'hud': (0x1404C8030, bytes.fromhex('488bc456574881ece8000000')),
}
LP_FIELDS = ((0x84, '2'), (0xA8, '5'), (0xAC, '6'))
# The native breakdown draws +A8, +AC, +84 in Score, Team, Battle order.
RANKED_LP_FIELDS = ((0xA8, '2'), (0xAC, '5'), (0x84, '6'))
RANKED_STAT_WORD = 0x1400EADAB
STAT_GUARD = bytes.fromhex('4a8b9c2ee83d050033c0488945c04889')
RPC_ACTIONS = frozenset(('open', 'begin_custom', 'stage_bot', 'settle_custom', 'receipt', 'ranked_wear'))
RPC_TIMEOUTS = (1.0, 3.0)


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate bridge response field')
        result[key] = value
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TransportFailure(OSError):
    def __init__(self, action):
        super().__init__('Local reward bridge transport failed')
        self.action = action


class RPC:
    def __init__(self, token, *, deferred_bot=False, deferred_custom=False):
        if type(token) is not str or not re.fullmatch('[0-9a-f]{64}', token):
            raise ValueError('Invalid local bridge credential')
        self.token = token
        self.launch = uuid4().hex
        self.opened = False
        self.deferred_bot = deferred_bot
        self.deferred_custom = deferred_custom
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def send(self, action, *, deferred=False, **fields):
        if action not in RPC_ACTIONS:
            raise ValueError('Unknown local reward operation')
        body = json.dumps(dict(call='nativeLPDeferred' if deferred else 'nativeLP', data=dict(sv_token=self.token, action=action,
                          launch=self.launch, **fields)), allow_nan=False).encode('ascii')
        request = Request(URL, body, {'Content-Type': 'application/json'}, method='POST')
        # A timed-out reply can follow a durable write. Reuse the exact request;
        # these operations are idempotent under the same launch/round identifiers.
        for attempt, timeout in enumerate(RPC_TIMEOUTS):
            try:
                with self.opener.open(request, timeout=timeout) as response:
                    raw = response.read(16385)
                break
            except HTTPError:
                raise
            except (OSError, IncompleteRead):
                if attempt == len(RPC_TIMEOUTS) - 1:
                    raise TransportFailure(action) from None
        if len(raw) > 16384:
            raise ValueError('Bridge response exceeds limit')
        value = json.loads(raw, object_pairs_hook=strict_object)
        if (not isinstance(value, dict) or value.get('result') != 'OK'
                or not isinstance(value.get('data'), dict) or value['data'].get('enabled') is not True):
            raise ValueError('Local LP bridge unavailable')
        return value['data']

    def __call__(self, action, **fields):
        if ((action == 'stage_bot' and self.deferred_bot)
                or (action == 'settle_custom' and self.deferred_custom)):
            reply = self.send(action, deferred=True, **fields)
            if reply.get('queued') is not True:
                raise ValueError('Deferred staging was not acknowledged')
            self.opened = True  # The HTTP worker opens before staging this launch.
            return reply
        if not self.opened:
            self.send('open')
            self.opened = True
        return self.send(action, **fields)


def xp_display_fields(reader, xp, api):
    if not isinstance(xp, dict) or type(xp.get('character')) is not int or not 1 <= xp['character'] <= 21:
        raise ValueError('Invalid XP receipt character')
    cid = xp['character']
    if native.local_character(reader) != cid:
        raise ValueError('XP receipt character does not match the retained battle roster')
    roster = native.pointer(reader, 0x141B87458)
    fields = []
    for label, gain_offset, total_address in (
            ('player', 0x28, roster + 0x3CCFC),
            ('character_xp', 0x2C + cid * 4, roster + 0x3CEBC + cid * 0x138)):
        values = xp.get(label)
        keys = {'stored_before', 'before', 'after', 'gained', 'level_before', 'level_after', 'baseline_adjustment'}
        if (not isinstance(values, dict) or set(values) != keys
                or any(type(value) is not int or not 0 <= value <= native.MAX_INT for value in values.values())
                or values['before'] + values['gained'] != values['after']
                or values['before'] - values['stored_before'] != values['baseline_adjustment']
                or values['level_after'] < values['level_before']):
            raise ValueError('Invalid committed XP totals')
        if native.number(reader, total_address) not in (values['stored_before'], values['before'], values['after']):
            raise ValueError('Cached XP changed since this result')
        fields.extend(((api + gain_offset, values['gained']), (total_address, values['after'])))
    return fields


def display(reader, row, context, current, *, hud=None):
    if (not isinstance(row, dict) or row.get('enabled') is not True
            or row.get('battle_id') != context['battle_id'] or row.get('mode') != context['mode']
            or row.get('summary') != current['summary'] or current['summary'] != context['summary']):
        raise ValueError('Committed LP receipt and native result disagree')
    values = row.get('components')
    if (not isinstance(values, dict) or set(values) != {'2', '5', '6'}
            or any(type(value) is not int or not 0 <= value <= native.MAX_INT for value in values.values())
            or type(row.get('total')) is not int or row['total'] != sum(values.values())
            or not 0 <= row['total'] <= native.MAX_INT):
        raise ValueError('Invalid committed LP totals')
    api = native.pointer(reader, native.pointer(reader, 0x141B87450) + 0x170)
    layout = RANKED_LP_FIELDS if context['mode'] == 'ranked' else LP_FIELDS
    fields = [(api + offset, values[reason]) for offset, reason in layout]
    if 'xp' in row:
        fields.extend(xp_display_fields(reader, row['xp'], api))
    if context['mode'] == 'ranked':
        # HUD 0x1404c8030 copies API+A4 to HUD+AC, the local Rank Point delta.
        fields.append((api + 0xA4, row['point_delta']))
    originals = [(address, native.read(reader, address, 4)) for address, _ in fields]
    try:
        for address, amount in fields:
            value = struct.pack('<i', amount)
            if not reader.write(address, value) or native.read(reader, address, 4) != value:
                raise OSError('Native award display write failed')
    except (OSError, ValueError):
        restored = [reader.write(address, raw) for address, raw in originals]
        if not all(restored):
            reader.log('LP_PAYOUT display rollback incomplete; persistent receipt retained')
        raise


def ranked_identity(reader):
    play = native.pointer(reader, 0x141B874D0)
    roster = native.pointer(reader, 0x141B87458)
    if (native.number(reader, roster + 0x5F650) not in (0,1) or native.is_replay(reader)
            or native.number(reader, play + 0x10, '<I') != 0x150305F0):
        return None
    own = native.number(reader, play + 0x14, '<B')
    if own > 3:
        return None
    uid = native.number(reader, play + 0x48 + own * 0x22D8, '<Q')
    return str(uid) if uid else None


def observe_lamina_round(reader, stage, hit, start=None):
    # Presentation is optional. A missing module or roster must never cancel settlement.
    try:
        import match_lamina
        if stage=='custom':
            match_lamina.presentation_round(native.custom_key(reader,hit))
        elif start is not None:
            match_lamina.presentation_round(start['match_key'])
        else:
            match_lamina.ranked_start(reader,hit.rdx)
    except (ImportError,AttributeError,ValueError,OSError,struct.error):
        pass


def ranked_row(receipt, context):
    if (not isinstance(receipt, dict) or receipt.get('match') != context['battle_id']
            or receipt.get('uid') != context['uid']):
        return None
    for field in ('lp', 'points_before', 'points_after'):
        value = receipt[field]
        if type(value) != int or not 0 <= value <= native.MAX_INT:
            raise ValueError('Invalid ranked receipt')
    delta = receipt['points_after'] - receipt['points_before']
    if not -32 <= delta <= 32:
        raise ValueError('Invalid ranked point delta')
    return dict(enabled=True, battle_id=receipt['match'], mode='ranked',
                summary=context['summary'], components=receipt.get('components', {'2': 0, '5': 0, '6': receipt['lp']}),
                total=receipt['lp'], xp=receipt['xp'], point_delta=delta,
                result_items=receipt.get('result_items', []), drop_events=receipt.get('drop_events', []))


def ranked_summary_words(reader):
    play = native.pointer(reader, 0x141B874D0)
    scores = native.pointer(reader, 0x141B87490)
    seats = []
    for slot in range(4):
        row = play + 0x30 + slot * 0x22D8
        uid = native.number(reader, row + 0x18, '<Q')
        side = native.number(reader, row + 9, '<B')
        block = native.read(reader, scores + 0xF120 + 0x90 * slot, 0x90)
        score, = struct.unpack_from('<i', block, 0x24)
        counts = tuple(struct.unpack_from('<i', block, pos)[0] for pos in (0x78, 0x8C, 0x30))
        if side not in (0, 1):
            return {}
        seats.append((uid, side, score, counts if min(counts) >= 0 else None))
    words = {}
    for side in (0, 1):
        pair = [seat for seat in seats if seat[1] == side]
        if len(pair) != 2:
            return {}
        if any(seat[3] is None for seat in pair):
            continue
        grade = native.grade(*(a+b for a,b in zip(pair[0][3], pair[1][3])))
        for uid, _, score, _ in pair:
            if uid and 0 <= score <= 1_000_000:
                words[uid] = int.from_bytes(struct.pack('<IB3s',score,'SABC'.index(grade),b'RS1'),'little')
    return words


def fetch_ranked(context, cancelled, rpc=None):
    import roi_progression_client as api
    uid = context['uid']
    try:
        token = api.credential(uid)
        if not token:
            return
        deadline = time.monotonic() + 30
        wear_sent = False
        while not cancelled.is_set() and time.monotonic() < deadline:
            try:
                if not wear_sent and rpc is not None and 'cube_usage' in context:
                    try:
                        rpc('ranked_wear', match_key=context['battle_id'], cube_usage=context['cube_usage'])
                        wear_sent = True
                    except (OSError, ValueError, IncompleteRead):
                        pass
                doc = api.request('http://127.0.0.1:52713/progress/' + uid, uid, token)
                receipt = doc['fields'].get('awardPipeline', {}).get('ranked_display')
                row = ranked_row(receipt, context)
                if row is not None:
                    if rpc is not None and 'cube_usage' in context and 'cube_usage' not in receipt:
                        try:
                            wear = rpc('ranked_wear', match_key=context['battle_id'], cube_usage=context['cube_usage'])
                            row['cube_wear'] = wear.get('cube_wear')
                        except (OSError, ValueError, IncompleteRead):
                            # Keep reward presentation independent of a retryable wear request.
                            context['row'] = row
                            cancelled.wait(0.6)
                            continue
                    else:
                        row['cube_wear'] = receipt.get('cube_wear')
                    context['row'] = row
                    return
            except api.Unavailable:
                pass
            cancelled.wait(0.6)
    except (KeyError, OSError, TypeError, ValueError, api.Unavailable):
        return


class Bridge:
    def report_word(self, hit):
        ctx = self.context
        if (not ctx or ctx['mode'] != 'ranked' or hit.rbx != 0 or hit.rsi not in (0,88,176,264)):
            return
        try:
            if (native.read(self.d, hit.r12 + 0x1ED8, 16).hex() != ctx['battle_id']
                    or hit.r13 != native.pointer(self.d, 0x141B87458)):
                return
            if not ctx.get('stat_words'):
                # Retry at the guarded native serializer, after result setup.
                ctx['stat_words'] = ranked_summary_words(self.d)
            uid = native.number(self.d, hit.r13 + hit.rsi + 0x53DA8, '<Q')
            word = ctx['stat_words'].get(uid)
            if word is not None:
                # This register is copied into the outgoing row only, not the roster.
                hit.setreg('rbx', word)
        except (ValueError, OSError, struct.error):
            return

    def __init__(self, debugger, rpc):
        self.d, self.rpc = debugger, rpc
        self.context = None
        self.custom = None
        self.ranked_cancel = threading.Event()
        self.budget = 30
        self.timing_budget = 24
        self.cubes = CubeDisplay(debugger)
        self.notices = notice_state(debugger)

    def log(self, text):
        if self.budget:
            self.budget -= 1
            self.d.log('LP_PAYOUT ' + text)

    def xp_fields(self):
        try:
            return {'xp_summary': native.experience_summary(self.d)}
        except (OSError, ValueError, struct.error):
            return {}  # Keep the existing LP-only adapter independent of extra XP reads.

    def cube_fields(self):
        try:
            return {'cube_usage': native.battle_cubes(self.d)}
        except (OSError, ValueError, struct.error):
            self.log('battle cube snapshot unavailable; durability unchanged')
            return {}

    def teammate_fields(self):
        try:
            return {'teammate': native.teammate(self.d)}
        except (OSError, ValueError, struct.error):
            self.log('teammate character unavailable; partner counts unchanged')
            return {}

    def event(self, name, hit):
        started, cpu_started = time.perf_counter(), time.thread_time()
        if name in ('custom', 'hud') and getattr(self, '_round_samples', 0) < 40:
            self._round_samples = getattr(self, '_round_samples', 0) + 1
            try:
                state = native.pointer(self.d, 0x141B87458)
                manager = native.pointer(self.d, 0x141B874D0)
                reg = native.pointer(self.d, 0x141B87490)
                if native.number(self.d, manager+0x10, '<I') == 0x150305F0:
                    rows = []
                    for slot in range(4):
                        row = manager+0x30+slot*0x22D8
                        uid = native.number(self.d, row+0x18, '<Q')
                        actor = native.number(self.d, reg+slot*0x25D0+0x18, '<Q')
                        valid = bool(actor and native.number(self.d, actor+0xC58, '<Q') == actor)
                        rows.append(dict(slot=slot, player={76561198094893006:'desktop',
                            76561198376595203:'laptop'}.get(uid,'other'),
                            side=native.number(self.d,row+9,'<B'),
                            lamina=native.number(self.d,actor+0xC78) if valid else None,
                            model=bool(native.number(self.d,actor+0xC288,'<Q')) if valid else None))
                    self.log('ROUND_TRACE '+json.dumps(dict(event=name, seats=rows,
                        mode=native.number(self.d,state+0x5F650),
                        registered=bool(self.custom and 'battle_id' in self.custom),
                        context=bool(self.context),
                        team_name_cached=bool(native.read(self.d,state+0x3CE04,1)[0]))))
            except (AttributeError, OSError, KeyError, TypeError, ValueError, struct.error):
                self.log('ROUND_TRACE unavailable stage='+name)
        try:
            return self._event(name, hit)
        finally:
            if self.timing_budget and name in HOOKS:
                self.timing_budget -= 1
                self.d.log('LP_PAYOUT_TIMING '+name
                           +' wall_ms='+format(1000*(time.perf_counter()-started), '.3f')
                           +' thread_cpu_ms='+format(1000*(time.thread_time()-cpu_started), '.3f'))

    def _event(self, name, hit):
        try:
            playing = native.is_replay(self.d)
        except (OSError, ValueError, struct.error):
            playing = True
            self.log('playback state unavailable; reward hook skipped')
        if playing:
            # Only discard Python-side context; never write into replay state.
            self.context = self.custom = None
            self.cubes.clear()
            self.notices.clear()
            return
        try:
            stage = name
            if name == 'start':
                # This hook sees incoming replies too, including results-ready (E60D).
                # Only a validated E608 for a different match supersedes a ranked receipt.
                value = native.custom_start(self.d, hit.rdx)
                observe_lamina_round(self.d,name,hit,value)
                if value is None:
                    return
                if self.context and self.context['mode'] == 'ranked':
                    if value['match_key'] == self.context['battle_id']:
                        return
                    self.ranked_cancel.set()
                    self.context = None
                if self.custom and value['sequence'] < self.custom['sequence']:
                    return
                if (self.custom and 'battle_id' in self.custom
                        and all(self.custom[key] == item for key, item in value.items())):
                    return
                self.context = None
                self.cubes.clear()
                self.notices.clear()
                self.custom = dict(value)
                row = self.rpc('begin_custom', **value)
                self.custom = dict(value, battle_id=row['battle_id'])
                self.log('Custom round registered')
            elif name == 'bot':
                self.context = None
                self.cubes.clear()
                self.notices.clear()
                self.custom = None
                value = native.bot_input(self.d, hit)
                self.context = dict(mode='bot', battle_id=value['battle_id'], summary=value['summary'])
                self.rpc('stage_bot', **value, **self.xp_fields(), **self.cube_fields(), **self.teammate_fields())
                self.log('Vs. Bot final score and Team Rank submitted')
            elif name == 'custom':
                observe_lamina_round(self.d,name,hit)
                try:
                    uid = ranked_identity(self.d)
                except (ValueError, OSError, struct.error):
                    uid = None
                if uid:
                    key = native.custom_key(self.d, hit)
                    if not key.strip('0'):
                        return
                    self.ranked_cancel.set()
                    self.ranked_cancel = threading.Event()
                    self.custom = None
                    self.cubes.clear()
                    self.notices.clear()
                    self.context = dict(mode='ranked', battle_id=key, uid=uid,
                                        expires=time.monotonic() + 45, summary=None, **self.cube_fields())
                    threading.Thread(target=fetch_ranked, args=(self.context, self.ranked_cancel, self.rpc),
                                     daemon=True).start()
                    roster = native.pointer(self.d, 0x141B87458)
                    outcome = native.number(self.d, roster + 0x53d6a, '<H') if roster else None
                    self.d.r46_ranked_result = (int(uid), key) if outcome == 3 else None
                    self.log('Ranked receipt requested match=' + key)
                    self.context['stat_words'] = ranked_summary_words(self.d)
                    return
                if not self.custom:
                    self.log('result observed without a registered Custom start')
                    return  # Vs. Bot may use other NRT notifications; retain its HTTP context.
                self.context = None
                self.cubes.clear()
                self.notices.clear()
                stage = 'custom-key'
                if native.custom_key(self.d, hit) != self.custom['match_key']:
                    raise ValueError('Custom round context missing')
                if 'battle_id' not in self.custom:
                    row = self.rpc('begin_custom', **self.custom)
                    self.custom['battle_id'] = row['battle_id']
                stage = 'custom-score'
                value = native.snapshot(self.d)
                self.context = dict(mode='custom', battle_id=self.custom['battle_id'], summary=value['summary'])
                stage = 'custom-settle'
                row = self.rpc('settle_custom', **self.custom, **value, **self.xp_fields(), **self.cube_fields(), **self.teammate_fields())
                self.log('Custom settlement queued' if row.get('queued') is True
                         else 'Custom LP committed total=' + str(row['total']))
            elif name == 'hud' and self.context is not None:
                self.cubes.clear()
                self.notices.clear()
                if self.context['mode'] == 'ranked':
                    if (time.monotonic() > self.context['expires']
                            or ranked_identity(self.d) != self.context['uid']):
                        return
                    row = self.context.get('row')
                    if row is None:
                        self.log('Ranked receipt not ready at HUD construction')
                        return
                else:
                    row = self.rpc('receipt', mode=self.context['mode'], battle_id=self.context['battle_id'])
                # Ranked settlement is keyed by the E60C round and account; it does
                # not depend on the local Custom score/grade calculation.
                current = {'summary': None} if self.context['mode'] == 'ranked' else native.snapshot(self.d)
                display(self.d, row, self.context, current, hud=getattr(hit, 'rcx', None))
                values = row['components']
                self.log('Displayed Score=' + str(values['2']) + ' Team=' + str(values['5'])
                         + ' Battle=' + str(values['6']) + ' Total=' + str(row['total']))
                if 'xp' in row:
                    self.log('Displayed player XP=' + str(row['xp']['player']['gained'])
                             + ' character XP=' + str(row['xp']['character_xp']['gained']))
                if self.context['mode'] == 'ranked':
                    self.log('Displayed committed Ranked points=' + str(row['point_delta']))
                    self.context = None
                self.cubes.prepare(row, hit)
                self.notices.prepare(row)
                if isinstance(row.get('cube_wear'), dict):
                    wear = row['cube_wear']
                    self.log('Cube durability committed grid=' + str(wear['grid_id'])
                             + ' cubes=' + str(len(wear['cubes'])))
        except (OSError, ValueError, TypeError, KeyError, AttributeError, struct.error) as error:
            # Never expose credentials/pointers, or alter native control flow on a bridge failure.
            detail = (' transport during ' + error.action if isinstance(error, TransportFailure)
                      else ' ' + type(error).__name__)
            self.log(stage + ' unavailable;' + detail + '; normal match completion preserved')


def setup(d, *, enabled=None, rpc=None):
    if not (FLAG.is_file() if enabled is None else enabled):
        return False
    if rpc is None:
        try:
            if TOKEN_FILE.stat().st_size > 128:
                raise ValueError('Invalid bridge credential file')
            rpc = RPC(TOKEN_FILE.read_text(encoding='ascii').strip(),
                      deferred_bot=True, deferred_custom=True)
        except (OSError, ValueError):
            d.log('LP_PAYOUT disabled: local bridge credential unavailable')
            return True
    bridge = Bridge(d, rpc)

    def arm(debugger):
        for address, expected in HOOKS.values():
            if debugger.read(address, len(expected)) != expected:
                debugger.log('LP_PAYOUT disabled: native code guard mismatch')
                return
        for name, (address, _) in HOOKS.items():
            debugger.bp(address, lambda hit, name=name: bridge.event(name, hit))
        if (debugger.read(RANKED_STAT_WORD-8, len(STAT_GUARD)) == STAT_GUARD
                and RANKED_STAT_WORD not in debugger.bps):
            debugger.bp(RANKED_STAT_WORD, bridge.report_word)
            debugger.log('LP_PAYOUT Ranked score/grade report armed')
        bridge.cubes.arm()
        debugger.log('LP_PAYOUT guarded committed-reward presentation armed')

    d.on_start(arm)
    return True
