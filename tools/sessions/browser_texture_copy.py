"""Copy native browser pixels without reading texture-row padding as pixels."""
import struct

COPY = 0x1404E584F
RESUME = 0x1404E5854
COPY_SETUP = 0x1404E583A
WEB_VTABLE = 0x1418EB0D0
EXPECTED = bytes.fromhex('448b8338010000488b4c2430488bd7440faf442438e89c587e00')
URLS = (b'http://127.0.0.1:52710/today/news', b'http://127.0.0.1:52710/today/support',
        b'https://riseofincarnates.artificerreviver.com/news',
        b'https://riseofincarnates.artificerreviver.com/support')
MAX_SIDE = 8192
MAX_BYTES = 64 * 1024 * 1024


def integer(d, address, size):
    raw = d.read(address, size)
    if len(raw) != size:
        raise ValueError('Incomplete browser metadata')
    return int.from_bytes(raw, 'little')


def dimensions(d, hit):
    browser = hit.rbx
    if not browser or integer(d, browser, 8) != WEB_VTABLE:
        return None
    width = integer(d, browser + 0x130, 4)
    height = integer(d, browser + 0x134, 4)
    rows = integer(d, browser + 0x138, 4)
    pitch = integer(d, hit.rsp + 0x38, 4)
    mapped = integer(d, hit.rsp + 0x30, 8)
    shared = integer(d, browser + 0x128, 8)
    row_bytes = width * 4
    if (not 1 <= width <= MAX_SIDE or not 1 <= height <= MAX_SIDE
            or rows != height or not row_bytes <= pitch <= row_bytes + 65536
            or height * pitch > MAX_BYTES or not mapped or not shared
            or hit.rcx != mapped or hit.rdx != shared + 0x860
            or hit.rdi != hit.rdx or hit.r8 != height * pitch):
        raise ValueError('Unexpected browser copy layout')
    return width, height, pitch


def row_copy():
    # Entry is a redirected call site, not a called function: no return address.
    # r9=row bytes, r10=destination pitch, r11=height. Keep native Map/Unmap.
    code = bytearray.fromhex('56579c4889cf4889d64889f8fc')
    loop = len(code)
    code += bytes.fromhex('4c89c9f3a44c01d74c29cf49ffcb')
    code += b'\x75' + struct.pack('b', loop - len(code) - 2)
    code += bytes.fromhex('9d5f5eff2500000000') + struct.pack('<Q', RESUME)
    return bytes(code)


def setup(d):
    def arm(debugger):
        if (debugger.read(COPY_SETUP, len(EXPECTED)) != EXPECTED
                or COPY in debugger.bps):
            debugger.log('BROWSER_COPY disabled: unknown or occupied pixel-copy site')
            return
        blob = row_copy()
        entry = debugger.alloc(len(blob))
        if not entry or not debugger.write(entry, blob) or debugger.read(entry, len(blob)) != blob:
            raise OSError('Could not install bounded browser row copy')
        seen = set()
        failures = 0

        def copy(hit):
            nonlocal failures
            try:
                size = dimensions(debugger, hit)
                if size is None:
                    return
                width, height, pitch = size
                if size not in seen and len(seen) < 8:
                    seen.add(size)
                    debugger.log(f'BROWSER_COPY native-widget width={width} height={height} '
                                 f'row_bytes={width*4} pitch={pitch} padded={pitch!=width*4}')
                if pitch != width * 4:
                    hit.setreg('r9', width * 4)
                    hit.setreg('r10', pitch)
                    hit.setreg('r11', height)
                    hit.setreg('rip', entry)
            except (OSError, ValueError, struct.error):
                if failures < 3:
                    failures += 1
                    debugger.log('BROWSER_COPY native-widget layout unverified; native copy unchanged')

        debugger.bp(COPY, copy)
        debugger.log('BROWSER_COPY validated native-widget padded-row correction and bounded dimensions trace armed')

    d.on_start(arm)
