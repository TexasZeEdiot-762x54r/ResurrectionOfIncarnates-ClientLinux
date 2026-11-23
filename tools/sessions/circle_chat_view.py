"""Render server-owned Circle history pages through the native Circle chat UI."""
import json
import struct

CONTROL='ROI_HISTORY_V1'
PAGE_SIZE=8
WINDOW_UPDATE=0x140384340
NOTIFICATION=0x1400EEF40
REMOVE_ROW=0x1402EA7D0
ADD_ROW=0x140384D50
GUARDS={WINDOW_UPDATE:bytes.fromhex('48895c2410'),
        NOTIFICATION:bytes.fromhex('48895c240848896c2410'),
        REMOVE_ROW:bytes.fromhex('48895c2410564883ec20'),
        ADD_ROW:bytes.fromhex('48895c24084889742410')}


def decode(reader,address):
    prefix=reader.read(address,12)
    if len(prefix)!=12:raise ValueError('Incomplete Circle chat prefix')
    uid,reserved,size=struct.unpack('<QHH',prefix)
    if reserved or size>127:raise ValueError('Invalid Circle chat sender')
    name=reader.read(address+12,size)
    raw=reader.read(address+12+size,2)
    if len(raw)!=2:raise ValueError('Incomplete Circle chat size')
    length=struct.unpack('<H',raw)[0]
    if length>511:raise ValueError('Invalid Circle chat body')
    body=reader.read(address+14+size,length)
    if len(name)!=size or len(body)!=length or b'\0' in name+body:raise ValueError('Incomplete Circle chat text')
    return uid,name.decode('utf-8'),body.decode('utf-8'),14+size+length


class Assembler:
    def __init__(self):self.code=bytearray();self.labels={};self.jumps=[]
    def emit(self,data):self.code.extend(data)
    def label(self,name):self.labels[name]=len(self.code)
    def jump(self,op,target):
        self.emit(op);self.jumps.append((len(self.code),target));self.emit(bytes(4))
    def mov(self,register,value):
        self.emit({'rax':b'\x48\xb8','rcx':b'\x48\xb9','rdx':b'\x48\xba',
                   'rbx':b'\x48\xbb','r9':b'\x49\xb9'}[register]+struct.pack('<Q',value))
    def call(self,target):self.mov('rax',target);self.emit(b'\xff\xd0')
    def finish(self):
        for at,target in self.jumps:struct.pack_into('<i',self.code,at,self.labels[target]-(at+4))
        return bytes(self.code)


def thunk(window,previous_tab,limit,circle_list,rows,buffer,uid):
    """Preserve the interrupted update frame; native routines own UI allocation."""
    a=Assembler();data=bytearray()
    def string(value):
        raw=value.encode('utf-8')
        if b'\0' in raw or len(raw)>1024:raise ValueError('Invalid native chat string')
        address=buffer+0x4000+len(data);data.extend(raw+b'\0');return address
    pushes=bytes.fromhex('5051525355565741504151415241534154415541564157')
    a.emit(b'\x9c'+pushes+bytes.fromhex('4881ec98000000'))
    for i in range(6):a.emit(bytes((0xf3,0x0f,0x7f,0x84+(i<<3),0x24))+struct.pack('<I',0x30+i*16))
    a.mov('rbx',window)
    def tab(index):
        a.emit(bytes.fromhex('4889d9ba')+struct.pack('<I',index)+bytes.fromhex('488b01ff9018010000'))
    tab(0)
    a.emit(bytes.fromhex('c783400400000001000041bc00010000'))
    a.label('clear')
    a.emit(bytes.fromhex('488b8b20030000488b5158483b5160'))
    a.jump(b'\x0f\x84','empty')
    a.emit(bytes.fromhex('488b124889d9'));a.call(REMOVE_ROW)
    a.emit(bytes.fromhex('41ffcc'));a.jump(b'\x0f\x85','clear')
    a.label('empty')
    for sender,name,body in rows:
        a.emit(bytes.fromhex('4889d9'));a.mov('rdx',string(name))
        a.emit(bytes.fromhex('4531c0'));a.mov('r9',string(body))
        a.emit(bytes.fromhex('c6442420')+bytes((int(sender==uid and uid!=0),)))
        a.call(ADD_ROW)
    a.mov('rcx',circle_list);a.emit(bytes.fromhex('c7411400000000'))
    a.emit(bytes.fromhex('c78340040000')+struct.pack('<I',limit))
    tab(previous_tab)
    for i in range(6):a.emit(bytes((0xf3,0x0f,0x6f,0x84+(i<<3),0x24))+struct.pack('<I',0x30+i*16))
    a.emit(bytes.fromhex('4881c498000000415f415e415d415c415b415a415941585f5e5d5b5a59589d'))
    a.emit(GUARDS[WINDOW_UPDATE])
    a.emit(bytes.fromhex('ff2500000000')+struct.pack('<Q',WINDOW_UPDATE+len(GUARDS[WINDOW_UPDATE])))
    code=a.finish()
    if len(code)>0x4000 or len(data)>0x8000:raise ValueError('Circle page exceeds its native buffer')
    return code,bytes(data)


class CircleChatView:
    def __init__(self,debugger):
        self.d=debugger;self.staging=None;self.page=None;self.version=0
        self.rendered=None;self.buffer=0

    def notification(self,hit):
        offset=int.from_bytes(self.d.read(hit.rcx+0xd0,4),'little')
        try:sender,name,body,consumed=decode(self.d,hit.rdx+offset)
        except (ValueError,UnicodeError,OSError):return
        handled=False
        if sender==0 and name==CONTROL:
            handled=True
            try:
                meta=json.loads(body)
                if (not isinstance(meta,dict) or meta.get('phase') not in ('begin','end')
                        or type(meta.get('count')) is not int or not 0<=meta['count']<=PAGE_SIZE
                        or type(meta.get('circle')) is not int or meta['circle']<0
                        or type(meta.get('page')) is not int or type(meta.get('pages')) is not int
                        or not 1<=meta['page']<=meta['pages']):raise ValueError('Invalid Circle page')
                if meta['phase']=='begin':self.staging=(meta,[])
                elif self.staging:
                    first,rows=self.staging
                    if ({k:v for k,v in first.items() if k!='phase'}!={k:v for k,v in meta.items() if k!='phase'}
                            or len(rows)!=meta['count']):raise ValueError('Incomplete Circle page')
                    self.page=(meta,rows);self.version+=1;self.staging=None
            except (ValueError,TypeError):
                self.staging=None;self.d.log('CIRCLE_CHAT malformed history page ignored')
        elif self.staging:
            handled=True
            if sender and len(self.staging[1])<self.staging[0]['count']:
                self.staging[1].append((sender,name,body))
            else:self.staging=None
        if handled:
            self.d.write(hit.rcx+0xd0,struct.pack('<I',offset+consumed))
            hit.setreg('rip',self.d.q(hit.rsp));hit.setreg('rsp',hit.rsp+8)

    def update(self,hit):
        window=hit.rcx
        if not self.page or self.rendered==(window,self.version):return
        start,end=self.d.q(window+0x2f8),self.d.q(window+0x300)
        if not start or end-start!=16:return
        lists=[self.d.q(start+i*8) for i in range(2)]
        active=self.d.q(window+0x320)
        if not all(lists) or active not in lists:return
        for view in lists:
            begin,finish=self.d.q(view+0x58),self.d.q(view+0x60)
            if finish<begin or (finish-begin)%8 or (finish-begin)//8>256:return
        limit=int.from_bytes(self.d.read(window+0x440,4),'little')
        if not 1<=limit<=256:return
        meta,rows=self.page
        status=f"Page {meta['page']}/{meta['pages']}"
        if meta.get('newer'):status+=' | New messages available'
        render=[(0,'Circle History',status),*rows] if meta['circle'] else []
        game=self.d.q(0x141B87450);account=self.d.q(game+0x158) if game else 0
        uid=self.d.q(account+0x10) if account else 0
        code,data=thunk(window,lists.index(active),limit,lists[0],render,self.buffer,uid)
        if not self.d.write(self.buffer,code) or not self.d.write(self.buffer+0x4000,data):
            raise OSError('Could not prepare native Circle history page')
        self.rendered=(window,self.version)
        hit.setreg('rip',self.buffer)
        self.d.log(f"CIRCLE_CHAT page {meta['page']}/{meta['pages']} prepared; {len(rows)} messages")

    def arm(self,debugger):
        for address,expected in GUARDS.items():
            if debugger.read(address,len(expected))!=expected:raise RuntimeError('Unexpected native Circle chat code')
        self.buffer=debugger.alloc(0x10000)
        if not self.buffer:raise OSError('Could not allocate Circle history view')
        debugger.bp(NOTIFICATION,self.notification);debugger.bp(WINDOW_UPDATE,self.update)
        debugger.log('CIRCLE_CHAT server history view armed')


def setup(d):
    view=CircleChatView(d);d.on_start(view.arm)
