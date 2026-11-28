"""Observe results-ready, mail parsing and 9008 without changing state."""
HOOKS = {
    'ready_entry': (0x140103620, bytes.fromhex('40534883ec20')),
    'collect_entry': (0x140107EF0, bytes.fromhex('48895c2408')),
    'mails_entry': (0x1400CF370, bytes.fromhex('48894c2408')),
    'mails_complete': (0x1400D0DAC, bytes.fromhex('83e904')),
    'mails_exit': (0x1400D11D1, bytes.fromhex('4881c4e0150000')),
    'mail_entry': (0x1400DBD90, bytes.fromhex('48894c2408')),
    'mail_ok': (0x1400DC2B2, bytes.fromhex('b901000000')),
    'mail_exit': (0x1400DD750, bytes.fromhex('4881c418060000')),
    'controller_error': (0x140100DE0, bytes.fromhex('48895c2408')),
    'central_error': (0x140014300, bytes.fromhex('48895c24104889742418')),
}


def snapshot(d):
    game=d.q(0x141b87450)
    api=d.q(game+0x170) if game else 0
    nrt=d.q(game+0x150) if game else 0
    return dict(api_state=d.d32(api+0x128) if api else None,
                api_mode=d.d32(api+0x12c) if api else None,
                nrt_state=d.d32(nrt+0x3fd0) if nrt else None,
                nrt_wait=d.d32(nrt+0x40c8) if nrt else None)


SOLO_PROMPT_SITE = 0x1404C855C
SOLO_PROMPT_STAGE_CHECK = 0x1404C856A
SOLO_PROMPT_NO = 0x1404D2ED0
SOLO_PROMPT_YES = 0x1404D2E50
SOLO_PROMPT_YES_RETURN = 0x1404D2E5A
SOLO_PROMPT_TEXT = 0x1404D2ADD
SOLO_PROMPT_GUARDS = {
    0x1404D2E42: bytes.fromhex('488b050f466b0144888252030000'),
    SOLO_PROMPT_YES: bytes.fromhex('c78084f60500010000004883c428c3'),
    0x1404D2AC6: bytes.fromhex('488d9424900000004883c130c7842490000000c9b60600'),
    SOLO_PROMPT_TEXT: bytes.fromhex('e88e830b00'),
    0x1404D3197: bytes.fromhex('8b422c83f801740cc74520c9b6060083f8047507c74520d8b60600'),
    0x1404C8557: bytes.fromhex('83f9057559418b422c83f801745083f804744b'),
    SOLO_PROMPT_STAGE_CHECK: bytes.fromhex('418b8180f60500ffc03bc17f3e'),
    0x1404C8585: bytes.fromhex('4c8d0d346d6e01e8bf02310048898788000000'),
    0x1404D2EC9: bytes.fromhex('44888252030000898884f60500'),
}


def solo_win_with_next_stage(d):
    state, roster, stages, playback = (d.q(address) for address in (
        0x141B87440, 0x141B87458, 0x141B87470, 0x141B874D0))
    if not all((state, roster, stages, playback)):
        return None
    if (d.read(playback+8, 1) != b'\0' or d.d32(roster+0x5F650) != 5
            or d.d32(roster+0x5F67C) not in (0, 1)
            or d.d32(state+0x2C) not in (1, 4) or d.d32(state+0x58) != 0
            or d.d32(roster+0x5F688) != 0):
        return None
    stage = d.d32(roster+0x5F680)
    phase_count = d.d32(stages+0x10)
    if not 1 <= phase_count <= 100:
        return None
    if d.d32(roster+0x5F67C) == 0:
        phase_count = min(3, phase_count)
    if not 0 <= stage or stage+1 >= phase_count:
        return None
    return state, roster, stage


def setup_solo_prompt(d):
    offered = None

    def arm(debugger):
        if (any(site in getattr(debugger, 'bps', {}) for site in (SOLO_PROMPT_SITE, SOLO_PROMPT_NO, SOLO_PROMPT_TEXT, SOLO_PROMPT_YES))
                or any(debugger.read(address, len(raw)) != raw
                       for address, raw in SOLO_PROMPT_GUARDS.items())):
            debugger.log('SOLO_BOT_PROMPT disabled: native contract unavailable')
            return

        def offer(hit):
            nonlocal offered
            offered = None
            round_info = solo_win_with_next_stage(debugger)
            if round_info is None or (hit.r10, hit.r9) != round_info[:2]:
                return
            offered = round_info
            # Rejoin the existing constructor path at its stage-limit check.
            hit.setreg('rip', SOLO_PROMPT_STAGE_CHECK)
            debugger.log('SOLO_BOT_PROMPT offering native continue or main menu')

        def leave(hit):
            nonlocal offered
            if offered is None or hit.rax != offered[1]:
                return
            if offered != solo_win_with_next_stage(debugger):
                return
            # The No callback's original store clears the retry flag; wins
            # still qualify for advance unless the native end-run flag is set.
            if debugger.write(hit.rax+0x5F688, b'\x01\0\0\0'):
                offered = None
                debugger.log('SOLO_BOT_PROMPT main menu selected; next stage skipped')

        def label(hit):
            if offered is None or offered != solo_win_with_next_stage(debugger):
                return
            if hit.rdx != hit.rsp+0x90 or debugger.d32(hit.rdx) != 0x6B6C9:
                return
            # The team popup already uses this localized next-stage question on wins.
            debugger.write(hit.rdx, (0x6B6D8).to_bytes(4, 'little'))

        def advance(hit):
            nonlocal offered
            if offered is None or hit.rax != offered[1]:
                return
            if offered != solo_win_with_next_stage(debugger):
                return
            # A win already qualifies for native stage advance. Do not mark it as a retry.
            hit.setreg('rip', SOLO_PROMPT_YES_RETURN)
            offered = None
            debugger.log('SOLO_BOT_PROMPT next stage selected; using normal win progression')

        debugger.bp(SOLO_PROMPT_YES, advance)
        debugger.bp(SOLO_PROMPT_TEXT, label)
        debugger.bp(SOLO_PROMPT_SITE, offer)
        debugger.bp(SOLO_PROMPT_NO, leave)

    d.on_start(arm)


def setup(d):
    setup_solo_prompt(d)
    budget=[30]
    legacy_budget=[6]
    active_mail=[False]

    def log(message, legacy=False):
        remaining=legacy_budget if legacy else budget
        if remaining[0]>0:
            remaining[0]-=1
            d.log('RESULT_FLOW '+message)

    def ready_entry(h):
        log(f'validated results-ready event received state={snapshot(d)}')

    def collect_entry(h):
        log(f'collecting getItemMail queued state={snapshot(d)}')

    def mail_entry(h):
        log(f'mail callback entered request={h.rcx:x} status={h.rdx & 0xffffffff} state={snapshot(d)}',legacy=True)

    def mails_entry(h):
        kind=h.rdx & 0xffffffff
        active_mail[0]=kind in (17,18)
        if active_mail[0]:
            log(f'getItemMail native variant={kind} callback entered state={snapshot(d)}')

    def mails_complete(h):
        if active_mail[0]:
            log(f'getItemMail collecting variant marked complete state={snapshot(d)}')

    def mails_exit(h):
        if active_mail[0]:
            log(f'getItemMail native callback exited state={snapshot(d)}')
            active_mail[0]=False

    def mail_ok(h):
        log('mail JSON result accepted as OK',legacy=True)

    def mail_exit(h):
        request=d.q(h.rbp+0x560)
        status=d.d32(request+0x40) if request else None
        log(f'mail callback completed request={request:x} status={status} state={snapshot(d)}',legacy=True)

    def controller_error(h):
        if h.rdx & 0xffffffff != 9008:
            return
        fields={hex(off):d.d32(h.rcx+off) for off in (0x284,0x288,0x28c,0x290,0x2a0)}
        log(f'controller raised 9008 fields={fields} state={snapshot(d)}')
        for offset,call,op in d.call_chain(h.rsp,0x1000)[:12]:
            log(f'controller caller stack+{offset:x} at {call:x} -> {op}')

    def central_error(h):
        if h.r8 & 0xffffffff != 9008:
            return
        log(f'central 9008 category={h.rdx & 0xffffffff} state={snapshot(d)}')
        for offset,call,op in d.call_chain(h.rsp,0x1000)[:12]:
            log(f'error caller stack+{offset:x} at {call:x} -> {op}')

    handlers=locals()

    def arm(debugger):
        for name,(address,expected) in HOOKS.items():
            if debugger.read(address,len(expected))!=expected:
                raise RuntimeError('Unexpected results-flow code: '+name)
        for name,(address,_) in HOOKS.items():
            debugger.bp(address,handlers[name])
        debugger.log('RESULT_FLOW observational completion/error tracing armed')

    d.on_start(arm)
