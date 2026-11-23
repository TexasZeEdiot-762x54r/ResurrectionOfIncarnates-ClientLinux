"""Bounded lifecycle transport/state for the pending native adapter.

Calls belong on a background worker, never inside a debugger breakpoint.
The server chooses the survivor, amounts and once-only event; no win request
accepts a caller-supplied winner, reward amount or rank.
"""
import json
import re
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, owner, match, token, opener=None):
        if (type(owner) is not int or not 0 < owner < 2**64 or type(match) is not str
                or not re.fullmatch('[0-9a-f]{32}',match) or type(token) is not str
                or not re.fullmatch('[0-9a-f]{64}',token)):
            raise ValueError('Invalid account-bound Ranked lifecycle context')
        self.owner, self.match, self.token = owner, match, token
        self.opener = opener or build_opener(ProxyHandler({}),NoRedirect())
        self.initial = None
        self.seats = None
        self.receipt = None

    def send(self, action, **fields):
        expected = {'start': {'seats'}, 'poll': set(), 'returned': set(), 'confirm': {'challenge'}}
        if action not in expected or set(fields) != expected[action]:
            raise ValueError('Unexpected lifecycle operation fields')
        request=dict(action=action,match=self.match,**fields)
        if action=='start':request['ready_policy']='battle-quorum-v1'
        body=json.dumps(request,separators=(',',':')).encode()
        if len(body)>2048:
            raise ValueError('Lifecycle request exceeds cap')
        req=Request('http://127.0.0.1:52713/ranked-live/'+str(self.owner),body,
                    {'Authorization':'Bearer '+self.token,'Content-Type':'application/json'},method='POST')
        with self.opener.open(req,timeout=2) as response:
            raw=response.read(16385)
        if len(raw)>16384:
            raise ValueError('Lifecycle response exceeds cap')
        from team_names import strict_object
        value=json.loads(raw,object_pairs_hook=strict_object)
        if not isinstance(value,dict) or value.get('match')!=self.match:
            raise ValueError('Lifecycle reply belongs to another round')
        return value

    def start(self, seats):
        if (not isinstance(seats,list) or len(seats)!=4 or any(not isinstance(r,list) or len(r)!=3
                or any(type(v) is not int for v in r) for r in seats)):
            raise ValueError('Invalid native lifecycle seats')
        initial=tuple(r[0] for r in seats if r[0])
        if (len(initial) not in (2,3,4) or len(set(initial))!=len(initial)
                or self.owner not in initial):
            raise ValueError('Invalid initial human roster')
        value=self.send('start',seats=seats)
        if self.initial is not None and self.initial!=initial:
            raise ValueError('Ranked lifecycle cannot change its original participants')
        self.initial=initial
        self.seats=tuple(map(tuple,seats))
        return self.validate_view(value)

    def validate_view(self,value):
        fields={'match','phase','humans','departed','challenge'}
        if isinstance(value,dict) and value.get('phase')=='transition':fields.add('ready_mask')
        if (self.initial is None or not isinstance(value,dict) or value.get('match')!=self.match
                or set(value)!=fields):
            raise ValueError('Invalid Ranked lifecycle state')
        humans,departed=value['humans'],value['departed']
        if (not isinstance(humans,list) or not isinstance(departed,list)
                or any(type(uid) is not int for uid in humans+departed)
                or len(set(humans+departed))!=len(humans+departed)
                or set(humans+departed)!=set(self.initial) or self.owner not in humans
                or value['phase'] not in ('briefing','transition','battle','completed','forfeit-pending','awarded')):
            raise ValueError('Invalid surviving human partition')
        if value['phase']=='transition':
            mask=value['ready_mask']
            if (type(mask) is not int or not 0<mask<16 or mask.bit_count()<2 or self.seats is None):
                raise ValueError('Invalid native transition quorum')
            ready={row[0] for slot,row in enumerate(self.seats) if mask & (1<<slot)}
            if 0 in ready or self.owner not in ready or not ready<=set(humans):
                raise ValueError('Native transition mask includes an unconfirmed seat')
        if value['phase']=='forfeit-pending':
            if humans!=[self.owner] or type(value['challenge']) is not str or not re.fullmatch('[0-9a-f]{32}',value['challenge']):
                raise ValueError('Forfeit does not belong to the sole survivor')
        elif value['challenge'] is not None:
            raise ValueError('Unexpected forfeit challenge')
        return value

    def poll(self):
        return self.validate_view(self.send('poll'))

    def confirm(self,view):
        self.validate_view(view)
        if view['phase']!='forfeit-pending':
            raise ValueError('No server-admitted forfeit')
        result=self.send('confirm',challenge=view['challenge'])
        event=self.match+':forfeit:'+str(self.owner)
        if (set(result)!={'match','event','outcome','uid','award'} or result['event']!=event
                or result['outcome']!='forfeit-win' or result['uid']!=str(self.owner)
                or not isinstance(result['award'],dict)):
            raise ValueError('Invalid committed forfeit receipt')
        award=result['award']
        if (set(award)!={'lp','xp','points_before','points_after','rank_before','rank_after'}
                or any(type(award[key]) is not int or not 0<=award[key]<=2147483647
                       for key in ('lp','points_before','points_after'))
                or any(type(award[key]) is not int or not 1<=award[key]<=35
                       for key in ('rank_before','rank_after'))
                or not isinstance(award['xp'],dict)):
            raise ValueError('Invalid committed forfeit reward totals')
        self.receipt=result
        return result
