"""Two read-only snapshots on the existing nonfatal connection-error callback."""
import json
import struct

SITE=0x1401480A5
GUARD=bytes.fromhex('4489833c030000')


def arm(d):
    entry=d.bps.get(SITE)
    if entry is None:
        d.log('PRACTICE_HANDOFF error snapshot inactive: existing callback unavailable')
        return
    if entry[0]+d.read(SITE+1,len(GUARD)-1)!=GUARD:
        raise RuntimeError('Unknown native connection-error snapshot site')
    remaining=[2];previous=entry[1]
    def snapshot(hit):
        if remaining[0]:
            remaining[0]-=1
            try:
                roster=d.q(0x141B87458);manager=d.q(0x141B874D0);actors=d.q(0x141B87490)
                driver=getattr(d,'ranked_disconnect',None)
                rows=[]
                for slot in range(4):
                    actor=d.q(actors+slot*0x25d0+0x18) if actors else 0
                    rows.append(dict(slot=slot,present=bool(actor),
                        cpu=d.d32(actor+0xc2a4) if actor else None,
                        difficulty=d.d32(actor+0xc2bc) if actor else None,
                        preset=d.d32(actor+0xc2c0) if actor else None))
                data=dict(error_code=hit.r8&0xffffffff,
                    mode=d.d32(roster+0x5f650) if roster else None,
                    recording_version=d.d32(manager+0x10) if manager else None,
                    frame=d.d32(manager+0x28) if manager else None,
                    ranked_pending=bool(driver and driver.pending),
                    ranked_battle=bool(driver and driver.battle),
                    ranked_admitted=bool(driver and driver.context),actors=rows)
                d.log('PRACTICE_HANDOFF error '+json.dumps(data,sort_keys=True))
            except (OSError,ValueError,TypeError,AttributeError,struct.error):
                d.log('PRACTICE_HANDOFF error snapshot incomplete')
        if previous:previous(hit)
    entry[1]=snapshot
    d.log('PRACTICE_HANDOFF bounded error snapshot chained; no extra breakpoints or state writes')
