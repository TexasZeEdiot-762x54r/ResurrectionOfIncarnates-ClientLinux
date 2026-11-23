"""R21 candidate: retain full Circle history text for native row wrapping."""
if __package__:
    from . import circle_chat_view as base
else:
    import circle_chat_view as base


ELLIPSIS = 0x140384E55
AFTER_ELLIPSIS = 0x140384E81
ELLIPSIS_GUARD = bytes.fromhex('f30f101de7596701f30f10157f274b01')
RETURN_OFFSET = 0x298


class CircleChatView(base.CircleChatView):
    def full_text(self, hit):
        # ADD_ROW saves five registers and reserves 0x270 bytes. Only calls
        # originating in our history thunk may bypass its body-only elision.
        caller = self.d.q(hit.rsp + RETURN_OFFSET)
        if self.buffer and self.buffer <= caller < self.buffer + 0x4000:
            hit.setreg('rip', AFTER_ELLIPSIS)

    def arm(self, debugger):
        if (debugger.read(ELLIPSIS, len(ELLIPSIS_GUARD)) != ELLIPSIS_GUARD
                or ELLIPSIS in debugger.bps):
            raise RuntimeError('Unexpected native Circle chat ellipsis code')
        super().arm(debugger)
        debugger.bp(ELLIPSIS, self.full_text)
        debugger.log('CIRCLE_CHAT full history text enabled; native row wrapping retained')


def setup(debugger):
    view = CircleChatView(debugger)
    debugger.on_start(view.arm)
