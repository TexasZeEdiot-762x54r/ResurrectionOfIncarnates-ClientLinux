"""Open visual-only Lamina eligibility at the validated Ranked final report."""
import struct

REPORT=0x1400EA140
GUARD=bytes.fromhex('48895c240848897424104889')


def ranked_report(reader, hit, active_key):
    import native_lp_input as native
    try:
        if not active_key or native.is_replay(reader):
            return False
        if native.custom_key(reader,hit)!=active_key:
            return False
        roster=native.pointer(reader,0x141B87458)
        play=native.pointer(reader,0x141B874D0)
        if (native.number(reader,roster+0x5F650) not in (0,1)
                or native.number(reader,play+8,'<B')
                or native.number(reader,play+0x10)!=0x150305F0):
            return False
        slot=native.number(reader,play+0x14,'<B')
        if not 0<=slot<4:
            return False
        game=native.pointer(reader,0x141B87450)
        account=native.pointer(reader,game+0x158)
        actor=native.number(reader,account+0x10,'<Q')
        return bool(actor and native.number(reader,play+0x48+slot*0x22D8,'<Q')==actor)
    except (OSError,ValueError,TypeError,AttributeError,struct.error):
        return False


def setup(debugger):
    def arm(d):
        entry=d.bps.get(REPORT)
        callback=getattr(d,'lamina_results_report_ready',None)
        if entry is None or callback is None:
            d.log('LAMINA_RESULT Ranked report observer unavailable; native readiness retained')
            return
        first=entry[0] if entry[0] is not None else d.read(REPORT,1)
        if first+d.read(REPORT+1,len(GUARD)-1)!=GUARD:
            raise RuntimeError('Unexpected Ranked final-report code')
        previous=entry[1]
        def report(hit):
            import match_lamina
            with match_lamina.LOCK:
                key=match_lamina.PRESENTATION_KEY
            if ranked_report(d,hit,key):
                callback()
            return previous(hit)
        entry[1]=report
        d.log('LAMINA_RESULT round-bound Ranked final-report observer armed')
    debugger.on_start(arm)
