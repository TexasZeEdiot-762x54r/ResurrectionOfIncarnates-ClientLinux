"""Use the native nonfatal disconnect dialog and its Return to Menu cleanup."""
import struct
import time

TEXT = 0x140148153
TEXT_GUARD = bytes.fromhex('488b8830030000488b01ff9018010000')
UPDATE = 0x140148280
UPDATE_GUARD = bytes.fromhex('4883ec28')
SCENE_VTABLE = 0x1418534B8
MESSAGE = b'You won by forfeit. Victory rewards have been awarded.\0'
CODE = 0x1401480A5
CODE_GUARD = bytes.fromhex('4489833c030000')


def close_thunk(window):
    # Same register/stack contract as the established native Circle history thunk.
    pushes=bytes.fromhex('5051525355565741504151415241534154415541564157')
    code=bytearray(b'\x9c'+pushes+bytes.fromhex('4881ec98000000'))
    for i in range(6):code+=bytes((0xf3,0x0f,0x7f,0x84+(i<<3),0x24))+struct.pack('<I',0x30+i*16)
    code+=b'\x48\xb9'+struct.pack('<Q',window)+bytes.fromhex('488b01ff9098000000')
    for i in range(6):code+=bytes((0xf3,0x0f,0x6f,0x84+(i<<3),0x24))+struct.pack('<I',0x30+i*16)
    code+=bytes.fromhex('4881c498000000415f415e415d415c415b415a415941585f5e5d5b5a59589d')
    return bytes(code)+UPDATE_GUARD+bytes.fromhex('ff2500000000')+struct.pack('<Q',UPDATE+4)


class Menu:
    def __init__(self, debugger, committed, clock=time.monotonic):
        self.d,self.committed,self.clock=debugger,committed,clock
        self.buffer=0;self.scene=0;self.shown=None;self.closed=False

    def reset(self):
        self.scene=0;self.shown=None;self.closed=False

    def text(self, hit):
        if not self.committed() or self.d.q(hit.rdi)!=SCENE_VTABLE:
            return
        window=self.d.q(hit.rdi+0x10)
        if not window or self.d.d32(window+0x33c)!=0xffffffff:
            return
        self.scene=hit.rdi;self.shown=self.clock();self.closed=False
        hit.setreg('rdx',self.buffer+512)
        self.d.log('RANKED_FORFEIT committed win shown in native Return to Menu dialog')

    def code(self, hit):
        roster=self.d.q(0x141B87458)
        if (self.committed() and self.d.q(hit.rdi)==SCENE_VTABLE and roster
                and self.d.d32(roster+0x5f648)==2
                and self.d.d32(roster+0x5f64c) in (0,0xffffffff)):
            hit.setreg('r8',0xffffffff)

    def update(self, hit):
        if (self.closed or not self.committed() or hit.rcx!=self.scene or self.shown is None
                or self.clock()-self.shown<3 or self.d.q(self.scene)!=SCENE_VTABLE
                or self.d.d32(self.scene+0x18)!=0):
            return
        window=self.d.q(self.scene+0x10)
        if not window or self.d.d32(window+0x1c) not in (0,1):
            return
        target=self.d.q(self.d.q(window)+0x98)
        if not 0x140000000 <= target < 0x141800000:
            return
        if not self.d.write(self.buffer,close_thunk(window)):
            raise OSError('Could not prepare native forfeit dismissal')
        self.closed=True
        hit.setreg('rip',self.buffer)
        self.d.log('RANKED_FORFEIT native dialog dismissal requested; native Return to Menu retained')

    def arm(self):
        for address,guard in ((TEXT,TEXT_GUARD),(UPDATE,UPDATE_GUARD),(CODE,CODE_GUARD)):
            if self.d.read(address,len(guard))!=guard or address in self.d.bps:
                raise RuntimeError('Unknown or occupied native forfeit menu hook')
        self.buffer=self.d.alloc(1024)
        if not self.buffer or not self.d.write(self.buffer+512,MESSAGE):
            raise OSError('Could not prepare native forfeit notice')
        self.d.bp(TEXT,self.text);self.d.bp(UPDATE,self.update);self.d.bp(CODE,self.code)
