"""Optional public Circle labels: bounded verified cache, never membership authority."""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import http.client
import logging
import threading
import time
from urllib.error import HTTPError


def request_data(data):
    """Zero means no Circle, not a label identity to send to the shared service."""
    if (not isinstance(data,dict) or 'sv_circleIds' not in data
            or not set(data)<={'sv_circleIds','sv_enableMaxMember'}
            or ('sv_enableMaxMember' in data and
                (type(data['sv_enableMaxMember']) is not int or data['sv_enableMaxMember'] not in (0,1)))
            or not isinstance(data['sv_circleIds'],list) or len(data['sv_circleIds'])>100):
        raise ValueError('Invalid Circle name lookup')
    ids=[]
    for value in data['sv_circleIds']:
        if (type(value) is int and value==0) or (isinstance(value,str) and value=='0'):
            continue
        if type(value) is int and 0<value<2**63:uid=str(value)
        elif (isinstance(value,str) and value.isascii() and value.isdecimal()
                and 0<int(value)<2**63 and str(int(value))==value):uid=value
        else:raise ValueError('Invalid Circle label identity')
        if uid not in ids:ids.append(uid)
    return {'sv_circleIds':ids}


class NameReads:
    def __init__(self, clock=time.monotonic):
        self.clock=clock;self.lock=threading.Lock()
        self.cache=OrderedDict();self.pending=set();self.cooldowns=OrderedDict()

    def lookup(self, actor, token, data, fetch):
        if (not isinstance(actor,str) or not actor.isascii() or not actor.isdecimal()
                or not 0<int(actor)<2**64 or str(int(actor))!=actor or not isinstance(token,str)
                ):
            raise ValueError('Invalid Circle name lookup')
        ids=request_data(data)['sv_circleIds']
        if not ids:return {'sv_circleNames':[]}
        owner=(actor,hashlib.sha256(token.encode()).digest())
        key=(owner,tuple(sorted(ids)));now=self.clock()
        with self.lock:
            for entry,(stamp,_) in list(self.cache.items()):
                if now-stamp>=300:self.cache.pop(entry)
            for entry,deadline in list(self.cooldowns.items()):
                if deadline<=now:self.cooldowns.pop(entry)
            known={uid:self.cache[(owner,uid)][1] for uid in ids if (owner,uid) in self.cache}
            fresh=all((owner,uid) in self.cache and now-self.cache[(owner,uid)][0]<30 for uid in ids)
            deferred=key in self.pending or key in self.cooldowns or len(self.pending)>=2
            if fresh or deferred:
                return {'sv_circleNames':[dict(sv_circleId=uid,sv_circleName=known[uid]) for uid in ids if uid in known]}
            self.pending.add(key)
        try:
            result=fetch()
            rows=result['data']['sv_circleNames'] if isinstance(result,dict) and isinstance(result.get('data'),dict) else None
            if not isinstance(rows,list) or len(rows)>len(ids):raise ValueError('Invalid Circle label response')
            verified={}
            for row in rows:
                if not isinstance(row,dict) or set(row)!={'sv_circleId','sv_circleName'}:
                    raise ValueError('Invalid Circle label row')
                uid,name=row['sv_circleId'],row['sv_circleName']
                if (not isinstance(uid,str) or uid not in ids or uid in verified
                        or not isinstance(name,str) or not name or len(name.encode('utf-8'))>255
                        or any(ord(c)<32 or ord(c)==127 for c in name)):
                    raise ValueError('Invalid verified Circle label')
                verified[uid]=name
            with self.lock:
                for uid in ids:
                    self.cache.pop((owner,uid),None)
                    if uid in verified:self.cache[(owner,uid)]=(self.clock(),verified[uid])
                while len(self.cache)>256:self.cache.popitem(last=False)
                if len(verified)<len(ids):
                    self.cooldowns[key]=self.clock()+5
                    while len(self.cooldowns)>128:self.cooldowns.popitem(last=False)
            return {'sv_circleNames':deepcopy(rows)}
        except HTTPError as error:
            if not 500<=error.code<=599:raise
        except (OSError,http.client.HTTPException):
            pass
        finally:
            with self.lock:self.pending.discard(key)
        # Only transport/5xx failure degrades this cosmetic lookup; malformed replies
        # and authentication errors propagate. No membership or mutation is invented.
        with self.lock:
            self.cooldowns[key]=self.clock()+5
            while len(self.cooldowns)>128:self.cooldowns.popitem(last=False)
        logging.getLogger('roi-profile').warning('CIRCLE_NAME optional display lookup unavailable; cached=%d missing=%d',len(known),len(ids)-len(known))
        return {'sv_circleNames':[dict(sv_circleId=uid,sv_circleName=known[uid]) for uid in ids if uid in known]}


CACHE=NameReads()
