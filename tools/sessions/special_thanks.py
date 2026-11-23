"""Display the static Records credits page and permit only explicit web links.

The original Twitch panel treats a click on its entire surface as a request to
open the current page externally. Disable that panel callback, but leave the
native URL helper intact so links carrying target="_blank" can still open in
the user's default browser.
"""
import struct

INITIALIZED=0x1403EB103
EXPECTED=bytes.fromhex('488bc34883c4205bc3')
WINDOW_VTABLE=0x1418CDC20
WEB_VTABLE=0x1418EB0D0
URL=b'https://riseofincarnates.artificerreviver.com/thanks'
PANEL_HANDLER=0x1403EB300
PANEL_HANDLER_CODE=bytes.fromhex('48895c240848896c24104889742418574883ec20')
URL_HELPER=0x1403EB260
URL_HELPER_CODE=bytes.fromhex('40534881ec40020000')


def disable_panel_open(debugger):
    """Suppress only the old full-panel Twitch click callback."""
    previous=debugger.read(PANEL_HANDLER,len(PANEL_HANDLER_CODE))
    if previous not in (PANEL_HANDLER_CODE,b'\xc3'+PANEL_HANDLER_CODE[1:]):
        raise ValueError('Unknown Records panel-open handler')
    if debugger.read(URL_HELPER,len(URL_HELPER_CODE))!=URL_HELPER_CODE:
        raise ValueError('Unknown Records URL helper')
    if previous[0]==0xc3:return
    if not debugger.write(PANEL_HANDLER,b'\xc3') or debugger.read(PANEL_HANDLER,1)!=b'\xc3':
        debugger.write(PANEL_HANDLER,previous[:1])
        raise OSError('Panel-open suppression failed')


def panel_browser(debugger,window):
    def pointer(at):
        value=debugger.read(at,8)
        return struct.unpack('<Q',value)[0] if len(value)==8 else 0
    if pointer(window)!=WINDOW_VTABLE:return 0
    controls=pointer(window+0x50)
    browser=pointer(controls+0x18) if controls else 0
    if not browser or pointer(browser)!=WEB_VTABLE:return 0
    string=pointer(browser+0x140) if browser else 0
    return browser if string and debugger.read(string,len(URL)+1)==URL+b'\0' else 0


def enable_exact_browser(debugger,window):
    browser=panel_browser(debugger,window)
    if not browser:return False
    input_at,counter_at=browser+0x1c8,browser+0x1cc
    previous_input=debugger.read(input_at,1)
    previous_counter=debugger.read(counter_at,4)
    if previous_input not in (b'\0',b'\1') or len(previous_counter)!=4:return False
    seen=struct.unpack('<I',previous_counter)[0]
    writes=[]
    try:
        if previous_input!=b'\1':
            if not debugger.write(input_at,b'\1') or debugger.read(input_at,1)!=b'\1':
                raise OSError('Browser interaction verification failed')
            writes.append((input_at,previous_input))
        if seen<4:
            primed=struct.pack('<I',4)
            if not debugger.write(counter_at,primed) or debugger.read(counter_at,4)!=primed:
                raise OSError('First-paint counter verification failed')
            writes.append((counter_at,previous_counter))
    except BaseException:
        for at,previous in reversed(writes):debugger.write(at,previous)
        raise
    return True


def setup(d):
    notices=0
    def initialized(hit):
        nonlocal notices
        if notices>=8:return
        notices+=1
        try:
            enabled=enable_exact_browser(d,hit.rbx)
            d.log('SPECIAL_THANKS exact Records credits panel interaction and first-paint compatibility='+str(enabled))
        except (OSError,ValueError,RuntimeError,struct.error):
            d.log('SPECIAL_THANKS first-paint compatibility unavailable; other browser panels unchanged')
    def arm(debugger):
        try:
            disable_panel_open(debugger)
            debugger.log('SPECIAL_THANKS background launch disabled; explicit external links enabled')
        except (OSError,ValueError,RuntimeError):
            debugger.log('SPECIAL_THANKS link policy unavailable: unknown native code')
        if debugger.read(INITIALIZED,len(EXPECTED))!=EXPECTED:
            debugger.log('SPECIAL_THANKS first-paint fix disabled: unknown Records panel code')
            return
        debugger.bp(INITIALIZED,initialized)
        debugger.log('SPECIAL_THANKS exact-panel first-paint fix armed; Gallery and other browser panels unchanged')
    d.on_start(arm)

