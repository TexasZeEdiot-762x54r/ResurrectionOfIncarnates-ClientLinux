"""Workbench HUD only. Login and matchmaking use the unmodified release receipt."""
import re
import struct


def setup(d):
    from client_version import label

    def layout(hit):
        # Only the base-version constructor: leave both fonts at their native 24px size.
        if hit.read(hit.rdx, 8) == struct.pack('<2f', 860.0, 1040.0):
            if d.write(hit.rdx, struct.pack('<f', 820.0)):
                d.log('VERSION_FOOTER base label shifted left 40px; native font sizes retained')

    def apply(debugger):
        revision = re.fullmatch(r'2\.06 - R([1-9][0-9]?)P', label() or '')
        address = 0x14187A9A0
        # Verified eight-byte string slot; the next byte starts another string.
        slot = b' - %s\0\0\0'
        if (not revision or debugger.read(0x1401E5167, 7) != bytes.fromhex('48 8d 15 32 58 69 01')
                or debugger.read(address, len(slot)) != slot):
            debugger.log('VERSION_FOOTER skipped: unrecognized revision or HUD formatter')
            return
        footer = (' - R' + revision[1] + 'P').encode('ascii').ljust(len(slot), b'\0')
        if not debugger.write(address, footer) or debugger.read(address, len(slot)) != footer:
            debugger.log('VERSION_FOOTER could not install Workbench label')
            return
        if debugger.read(0x1401E4B41, 5) == bytes.fromhex('e81aad0f00'):
            debugger.bp(0x1401E4B41, layout)

    d.on_start(apply)
