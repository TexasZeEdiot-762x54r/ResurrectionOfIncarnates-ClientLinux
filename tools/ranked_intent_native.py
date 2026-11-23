"""Carry confirmed native Solo/Team selection in one reserved E620 byte."""
CREATE=0x1403AE640
DIALOG=0x1403AF570
WRITE=0x1400E973F
RESUME=0x1400E9742
GUARDS={CREATE:bytes.fromhex('488bc448895810'),
        DIALOG:bytes.fromhex('4883ec2883b97004000000'),WRITE:bytes.fromhex('448830')}


class Intent:
    def __init__(self,d):self.d=d;self.owners={};self.value=None;self.logs=0
    def created(self,hit):
        self.value=None
        if len(self.owners)>=8:self.owners.clear()
        self.owners[hit.rcx]=self.d.q(hit.rcx)
    def selected(self,hit):
        owner=hit.rcx
        if owner not in self.owners or self.d.q(owner)!=self.owners[owner]:
            self.value=None;return
        mode=self.d.d32(owner+0x470)
        self.value=b'S' if mode==0 else b'T' if mode==1 else None
        if self.logs<16:
            self.logs+=1;self.d.log('RANKED_INTENT explicit '+('solo' if self.value==b'S' else 'team' if self.value==b'T' else 'unknown'))
    def write(self,hit):
        if self.value is None:return
        if not 0x10000<=hit.rax<0x7fffffff0000:raise ValueError('Invalid native search buffer')
        if not self.d.write(hit.rax,self.value):raise OSError('Could not encode Ranked intent')
        hit.setreg('rip',RESUME)


def setup(d):
    state=Intent(d)
    def arm(debugger):
        for address,guard in GUARDS.items():
            entry=debugger.bps.get(address)
            actual=(entry[0]+debugger.read(address+1,len(guard)-1)) if entry and entry[0] else debugger.read(address,len(guard))
            if actual!=guard:raise RuntimeError('Ranked queue intent native signature mismatch')
        for address,callback in ((CREATE,state.created),(DIALOG,state.selected),(WRITE,state.write)):
            entry=debugger.bps.get(address)
            if entry:
                previous=entry[1]
                def chained(hit,first=callback,second=previous):
                    first(hit)
                    if second:second(hit)
                entry[1]=chained
            else:debugger.bp(address,callback)
        debugger.log('RANKED_INTENT native Solo/Team search marker armed; no callback network calls')
    d.on_start(arm)
    return state
