"""Warm existing profile caches off game callbacks after an admitted Ranked roster."""
import json
import logging
import queue
import re
import threading
import time
import urllib.request


def selection(data, actor):
    if not isinstance(data,dict) or set(data)!={'actor','match','peers'} or data['actor']!=str(actor):
        raise ValueError('Invalid profile warm owner')
    if not re.fullmatch('[0-9a-f]{32}',data['match']) or data['match']=='0'*32:
        raise ValueError('Invalid profile warm round')
    peers=data['peers']
    if (not isinstance(peers,list) or not 1<=len(peers)<=3 or len(set(peers))!=len(peers)
            or any(not isinstance(uid,str) or not uid.isascii() or not uid.isdecimal()
                   or not 0<int(uid)<2**64 or str(int(uid))!=uid or uid==str(actor) for uid in peers)):
        raise ValueError('Invalid profile warm peers')
    return (str(actor),data['match'],tuple(peers))


class Warmer:
    def __init__(self,clock=time.monotonic,sleep=time.sleep):
        self.clock,self.sleep=clock,sleep
        self.lock=threading.Lock();self.jobs=queue.Queue(maxsize=1)
        self.last=None;self.worker=None

    def submit(self,data,actor,fetch,current_actor):
        key=selection(data,actor)
        with self.lock:
            if self.last and self.last[0]==key and self.clock()-self.last[1]<30:
                return False
            if self.jobs.full():
                return False
            self.jobs.put_nowait((key,fetch,current_actor));self.last=(key,self.clock())
            if self.worker is None:
                self.worker=threading.Thread(target=self.run,name='roi-peer-profile-warm',daemon=True)
                self.worker.start()
        return True

    def warm(self,key,fetch,current_actor):
        actor,match,peers=key
        deadline=self.clock()+10
        remaining=list(peers)
        completed=0
        while remaining and self.clock()<deadline and current_actor()==actor:
            uid=remaining.pop(0)
            try:
                value=fetch(uid)
            except (OSError,ValueError,RuntimeError):
                value=None
            if value is None:
                remaining.append(uid)
                self.sleep(0.2)
            else:
                completed+=1
        logging.getLogger('roi-profile').info('PEER_PROFILE warm completed=%d expected=%d',completed,len(peers))

    def run(self):
        while True:
            key,fetch,current_actor=self.jobs.get()
            try:
                self.warm(key,fetch,current_actor)
            except Exception:
                logging.getLogger('roi-profile').warning('PEER_PROFILE warm unavailable')


WARMER=Warmer()


def send(data):
    request=urllib.request.Request('http://127.0.0.1:52710/Dragon/game',
        data=json.dumps(dict(version=0.8,call='warmPeerProfiles',data=data)).encode(),
        headers={'Content-Type':'application/json'})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request,timeout=3) as response:
        raw=response.read(4097)
    if len(raw)>4096:raise ValueError('Invalid profile warm acknowledgement')
    return json.loads(raw)


class Observer:
    def __init__(self,debugger,exchange=send):
        self.d,self.exchange=debugger,exchange
        self.jobs=queue.Queue(maxsize=1);self.last=None

    def observe(self,hit):
        from ranked_disconnect import packet
        parsed=packet(self.d,hit.rdx)
        if not parsed or parsed[0]!='ranked':return
        game=self.d.q(0x141B87450)
        account=self.d.q(game+0x158) if game else 0
        actor=self.d.q(account+0x10) if account else 0
        key,members=parsed[1:]
        if actor not in members or self.last==(actor,key):return
        data=dict(actor=str(actor),match=key,peers=[str(uid) for uid in members if uid!=actor])
        try:self.jobs.put_nowait(data)
        except queue.Full:return
        self.last=(actor,key)

    def run(self):
        while True:
            data=self.jobs.get()
            try:self.exchange(data)
            except (OSError,ValueError,RuntimeError):pass

    def arm(self,debugger):
        from ranked_disconnect import PACKET,PACKET_GUARD,wrap
        entry=debugger.bps.get(PACKET)
        raw=(entry[0]+debugger.read(PACKET+1,len(PACKET_GUARD)-1) if entry else debugger.read(PACKET,len(PACKET_GUARD)))
        if raw!=PACKET_GUARD:raise RuntimeError('Unknown peer roster callback')
        wrap(debugger,PACKET,self.observe)
        threading.Thread(target=self.run,name='roi-peer-roster-warm',daemon=True).start()
        debugger.log('PEER_PROFILE Ranked background prewarm armed')


def setup(debugger):
    observer=Observer(debugger);debugger.on_start(observer.arm)
