"""Failure-branch-only transport snapshot; never retries/bypasses native validation."""
import json
import struct

SITE=0x14012D422
GUARD=bytes.fromhex('488b83c8060000')


def setup(debugger):
    from ranked_host_wait import setup as setup_host_wait
    setup_host_wait(debugger)
    budget=[4]
    def failed(hit):
        driver=getattr(debugger,'ranked_disconnect',None)
        if not budget[0] or driver is None or not driver.pending:return
        budget[0]-=1
        try:
            def q(address):
                if not 0x10000<=address<0x7fffffff0000:raise ValueError('Invalid diagnostic pointer')
                return struct.unpack('<Q',debugger.read(address,8))[0]
            def n(address):
                if not 0x10000<=address<0x7fffffff0000:raise ValueError('Invalid diagnostic pointer')
                return struct.unpack('<I',debugger.read(address,4))[0]
            owner=hit.rbx
            link=q(owner+0x700);adapter=q(link+0x50)
            transport=q(adapter+8);platform=q(transport+0x58);peer=q(platform+0x50)
            data=dict(match=driver.pending[0],state=n(owner+0x54),mode=n(owner+0x58),
                      slot=n(owner+0x4c),tick=n(owner+0x6d0),link_state=n(link+0x1c),
                      adapter_state=n(adapter+0x10),adapter_override=n(adapter+0x12c),
                      peer_present=bool(peer),peer_state=n(peer+0x58) if peer else None,
                      peer_connected=n(peer+0x40) if peer else None)
            debugger.log('RANKED_HANDOFF peer unavailable '+json.dumps(data,sort_keys=True))
        except (OSError,ValueError,TypeError,AttributeError,struct.error):
            debugger.log('RANKED_HANDOFF peer unavailable; bounded snapshot incomplete')
    def arm(d):
        entry=d.bps.get(SITE)
        first=entry[0] if entry and entry[0] is not None else d.read(SITE,1)
        if first+d.read(SITE+1,len(GUARD)-1)!=GUARD:
            raise RuntimeError('Unexpected native transport-failure branch')
        if entry:
            previous=entry[1]
            def chained(hit):
                failed(hit);return previous(hit)
            entry[1]=chained
        else:d.bp(SITE,failed)
        d.log('RANKED_HANDOFF failure-only transport snapshot armed; native state unchanged')
        from practice_failure_trace import arm as arm_practice_trace
        arm_practice_trace(d)
    debugger.on_start(arm)
