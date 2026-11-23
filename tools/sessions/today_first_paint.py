"""Allow the first changed frame of the exact Today pages to reach their texture."""
import struct

PAINT_GATE=0x1404E57EB
EXPECTED=bytes.fromhex('83bbcc010000030f8683000000')
WEB_VTABLE=0x1418EB0D0
URLS=(b'http://127.0.0.1:52710/today/news',b'http://127.0.0.1:52710/today/support',
      b'https://riseofincarnates.artificerreviver.com/news',
      b'https://riseofincarnates.artificerreviver.com/support')


def prime(debugger,browser):
    if not browser or debugger.read(browser,8)!=struct.pack('<Q',WEB_VTABLE):return False
    raw=debugger.read(browser+0x140,8)
    if len(raw)!=8:return False
    address=struct.unpack('<Q',raw)[0]
    if not address or not any(debugger.read(address,len(url)+1)==url+b'\0' for url in URLS):return False
    counter=debugger.read(browser+0x1cc,4)
    if len(counter)!=4:return False
    if struct.unpack('<I',counter)[0]>=4:return False
    value=struct.pack('<I',4)
    if not debugger.write(browser+0x1cc,value) or debugger.read(browser+0x1cc,4)!=value:
        debugger.write(browser+0x1cc,counter)
        raise OSError('Could not prime the Today first-paint counter')
    return True


def setup(d):
    from sessions import browser_texture_copy
    browser_texture_copy.setup(d)
    def paint(hit):
        try:
            if prime(d,hit.rbx):d.log('TODAY first changed frame enabled for exact News/Support page')
        except (OSError,ValueError,struct.error):d.log('TODAY first-paint correction unavailable')
    def arm(debugger):
        if debugger.read(PAINT_GATE,len(EXPECTED))!=EXPECTED or PAINT_GATE in debugger.bps:
            debugger.log('TODAY first-paint correction disabled: unknown or occupied paint gate')
            return
        debugger.bp(PAINT_GATE,paint)
        debugger.log('TODAY exact-page first-paint correction armed')
    d.on_start(arm)
