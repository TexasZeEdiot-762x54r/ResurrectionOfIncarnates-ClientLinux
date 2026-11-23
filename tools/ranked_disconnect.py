"""Held native Ranked lifecycle integration; network I/O stays off game callbacks."""
from copy import deepcopy
from pathlib import Path
import queue
import struct
import threading
import time

from lifecycle_client import Client
from native_takeover import Proof, ResumeGate
from forfeit_menu import Menu

ROOT=Path(__file__).resolve().parent
PACKET=0x1400E8160
PACKET_GUARD=bytes.fromhex('4c8bdc49895b1049896b1849')
BATTLE=0x1401DAB60
MENU=0x140162BF0
RESULT=0x1400EA140


def packet(reader,address):
    raw=reader.read(address,26)
    if len(raw)!=26:
        return None
    outer,size,_,_,kind,status=struct.unpack_from('<HHQHHH',raw)
    if outer!=size+4 or status!=0:
        return None
    if kind==0xE608:
        return ('custom',None,None)
    if kind!=0xE623 or not 42<=size<=2048:
        return None
    body=address+26
    prefix=reader.read(body,20)
    if len(prefix)!=20:
        return None
    count=struct.unpack_from('<H',prefix,18)[0]
    if not 2<=count<=4 or not prefix[2:18].strip(b'\0'):
        return None
    uids=[];offset=20
    for _ in range(count):
        if offset+16>size-22:
            return None
        row=reader.read(body+offset,16)
        if len(row)!=16:
            return None
        uid,_,side,_,length=struct.unpack('<QBBIH',row)
        if not uid or uid in uids or side not in (0,1) or not 1<=length<=255:
            return None
        offset+=20+length
        if offset>size-22:
            return None
        uids.append(uid)
    return 'ranked',prefix[2:18].hex(),tuple(sorted(uids))


def native_seats(reader):
    import native_lp_input as native
    manager=native.pointer(reader,0x141B874D0)
    if native.is_replay(reader) or native.number(reader,manager+0x10,'<I')!=0x150305F0:
        raise ValueError('Not a native live battle roster')
    own=native.number(reader,manager+0x14,'<B')
    if own>3:
        raise ValueError('Invalid local native seat')
    rows=[]
    for slot in range(4):
        row=manager+0x30+slot*0x22D8
        flags=native.read(reader,row,11)
        side,character=flags[9:11]
        uid=native.number(reader,row+0x18,'<Q') if flags[1]<128 else 0
        if side not in (0,1) or not 1<=character<=21:
            raise ValueError('Invalid native battle seat')
        rows.append([uid,side,character])
    if not rows[own][0]:
        raise ValueError('Local human seat missing')
    return rows[own][0],rows


def credential(owner):
    import roi_account_client
    import roi_progression_client
    account=roi_account_client.read_credential(ROOT/'account-auth.json')
    if not account or account['uid']!=str(owner) or roi_progression_client.credential(str(owner))!=account['token']:
        raise ValueError('No current account credential for this native player')
    return account['token']


def notify_returned(owner, key):
    def work():
        try:
            client = Client(owner, key, credential(owner))
            for attempt in range(3):
                try:
                    client.send('returned')
                    return
                except OSError as exc:
                    if attempt == 2 or getattr(exc, 'code', 409) not in (409,503):
                        return
                    time.sleep(0.75)
        except (OSError, ValueError, RuntimeError):
            pass  # Normal completed rounds have no self-abort to acknowledge.
    threading.Thread(target=work, daemon=True, name='ranked-menu-return').start()


class Driver:
    def __init__(self,debugger,clock=time.monotonic,client_factory=None):
        self.d,self.clock=debugger,clock
        self.client_factory=client_factory or (lambda owner,key:Client(owner,key,credential(owner)))
        self.pending=None;self.battle=False;self.context=None;self.proof=None;self.receipt=None
        self.generation=0;self.abort_address=None;self.hold_until=None;self.return_requested=False
        self.notice_until=0
        self.menu=None
        self.last_failure=None
        self.inbox=queue.Queue(maxsize=1);self.outbox=queue.Queue(maxsize=2)
        self.lock=threading.Lock()

    def reset(self):
        with self.lock:self.generation+=1
        self.pending=None;self.battle=False;self.context=None;self.proof=None;self.receipt=None
        self.abort_address=None;self.hold_until=None;self.return_requested=False
        self.notice_until=0
        self.last_failure=None
        if self.menu:self.menu.reset()

    def observe(self,hit):
        parsed=packet(self.d,hit.rdx)
        if parsed:
            self.reset()
            if parsed[0]=='ranked':self.pending=parsed[1:]

    def context_now(self,reader):
        if not self.battle or not self.context or not self.pending:
            raise ValueError('Ranked native round is no longer active')
        owner,_=native_seats(reader)
        key,expected=self.pending
        if owner!=self.context['owner'] or key!=self.context['match']:
            raise ValueError('Native owner or round changed')
        return key,owner,expected

    def committed(self):
        return (self.receipt is not None and self.context is not None
                and self.receipt.get('match')==self.context['match'] and self.clock()<self.notice_until)

    def menu_entered(self,menu):
        if self.committed() and not menu.scene:
            # Some native error paths construct the underlying menu before its dialog.
            self.battle=False;self.proof=None
            return
        self.reset()

    def hold_last(self):
        if self.hold_until is not None:
            return self.clock()<self.hold_until
        manager=self.d.q(0x141B874A8)
        transport=self.d.q(manager+0x958) if manager else 0
        if not 0x10000<=transport<0x7fffffff0000:
            return False
        self.abort_address=transport+0x48
        self.hold_until=self.clock()+30
        self.d.log('RANKED_FORFEIT last-human error deferred for authenticated settlement')
        return True

    def emit(self,generation,kind,value):
        if self.outbox.full():
            try:self.outbox.get_nowait()
            except queue.Empty:pass
        self.outbox.put_nowait((generation,kind,value))

    def worker(self):
        while True:
            generation,context=self.inbox.get()
            client=None;pending_confirm=None
            while True:
                with self.lock:
                    if generation!=self.generation:break
                try:
                    if client is None:
                        client=self.client_factory(context['owner'],context['match'])
                        view=client.start(context['seats'])
                    elif pending_confirm is not None:
                        receipt=client.confirm(pending_confirm)
                        self.emit(generation,'receipt',receipt)
                        break
                    else:
                        view=client.poll()
                    with self.lock:
                        if generation!=self.generation:break
                        import match_lamina
                        match_lamina.confirmed_departures(context['match'], view['departed'])
                        self.proof=(Proof(context['match'],context['owner'],
                                          tuple(sorted(r[0] for r in context['seats'] if r[0])),self.clock()+6, view.get('ready_mask',15))
                                    if view['phase'] in ('transition','battle','forfeit-pending') else None)
                    self.emit(generation,'view',view)
                    if view['phase']=='forfeit-pending':pending_confirm=view
                    if view['phase']=='completed':break
                except (OSError,ValueError,RuntimeError) as error:
                    status=getattr(error,'code',None)
                    self.emit(generation,'failure',(type(error).__name__,status if type(status) is int and 100<=status<=599 else None))
                    if client is not None and client.initial is None:client=None
                time.sleep(2)

    def poll(self,debugger):
        if self.battle and self.pending and self.context is None:
            try:
                owner,seats=native_seats(debugger)
                humans=tuple(sorted(row[0] for row in seats if row[0]))
                if humans!=self.pending[1]:return
                context=dict(owner=owner,match=self.pending[0],seats=seats)
                self.inbox.put_nowait((self.generation,deepcopy(context)))
                self.context=context
            except (ValueError,OSError,queue.Full):return
        while True:
            try:generation,kind,value=self.outbox.get_nowait()
            except queue.Empty:break
            if generation!=self.generation or self.context is None:continue
            if kind=='receipt':
                self.receipt=value
                self.notice_until=self.clock()+60
                self.d.log('RANKED_FORFEIT server committed victory; native menu return requested')
            elif kind=='failure' and value!=self.last_failure:
                self.last_failure=value
                self.d.log(f'RANKED_LIFECYCLE exchange unavailable type={value[0]} http={value[1]}')
        if self.hold_until is not None and not self.return_requested and (self.committed() or self.clock()>=self.hold_until):
            try:
                self.context_now(debugger)
                manager=debugger.q(0x141B874A8)
                current=debugger.q(manager+0x958) if manager else 0
                if current+0x48!=self.abort_address:return
                # The exact native lone-survivor branch writes this same flag.
                if not debugger.write(self.abort_address,struct.pack('<I',1)):
                    raise OSError('Could not request native disconnect cleanup')
                self.return_requested=True
            except (ValueError,OSError):
                self.d.log('RANKED_FORFEIT cleanup context unavailable; no scene jump attempted')


def wrap(debugger,address,callback):
    entry=debugger.bps.get(address)
    previous=entry[1] if entry else None
    def handler(hit):
        callback(hit)
        if previous:previous(hit)
    if entry:entry[1]=handler
    else:debugger.bp(address,handler)


def setup(debugger, *, enabled=False):
    if not enabled:return False
    driver=Driver(debugger)
    debugger.ranked_disconnect=driver
    gate=ResumeGate(debugger,lambda:driver.proof,driver.context_now,hold_last=driver.hold_last)
    menu=Menu(debugger,driver.committed)
    driver.menu=menu
    def arm(d):
        guards={PACKET:PACKET_GUARD,BATTLE:bytes.fromhex('4883ec28488b0d359c9e018b'),
                MENU:bytes.fromhex('4883ec28488b0da51ba6018b'),RESULT:bytes.fromhex('48895c240848897424104889')}
        for address,guard in guards.items():
            entry=d.bps.get(address)
            original=((entry[0] or b'')+d.read(address+1,len(guard)-1)
                      if entry else d.read(address,len(guard)))
            if original!=guard:
                raise RuntimeError('Unknown native Ranked lifecycle callback')
        gate.arm();menu.arm()
        wrap(d,PACKET,driver.observe)
        wrap(d,BATTLE,lambda _:setattr(driver,'battle',True))
        wrap(d,MENU,lambda _:driver.menu_entered(menu))
        # A genuine native completed result wins over any pending forfeit flow.
        wrap(d,RESULT,lambda _:driver.reset() if driver.hold_until is None else None)
        threading.Thread(target=driver.worker,name='roi-ranked-lifecycle',daemon=True).start()
        d.log('RANKED_TAKEOVER guarded lifecycle enabled; no network calls in native callbacks')
    debugger.on_start(arm)
    debugger.poll(driver.poll,every=getattr(debugger,'poll_every',2.0))
    return True
