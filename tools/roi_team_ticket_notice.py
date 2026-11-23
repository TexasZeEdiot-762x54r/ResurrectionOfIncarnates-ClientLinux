"""Nonblocking ticket warning for the Windows client and its Wine runtime."""
import ctypes
import logging
import threading

MESSAGE = 'You need a Team Name Ticket to change this team name. Buy Team Name Tickets from the Store first.'
LOCK = threading.Lock()


def show():
    try:
        dialog = ctypes.WinDLL('user32', use_last_error=True).MessageBoxW
        dialog.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        dialog.restype = ctypes.c_int
        dialog(None, MESSAGE, 'Rise of Incarnates - Team Name Ticket', 0x10030)
    except (AttributeError, OSError):
        logging.getLogger('roi-profile').warning(MESSAGE)
    finally:
        LOCK.release()


def present(reply):
    data = reply.get('data') if isinstance(reply, dict) else None
    if (not isinstance(data, dict) or data.get('sv_teamNameStatus') != 'ticket-required'
            or not LOCK.acquire(blocking=False)):
        return False
    try:
        threading.Thread(target=show, daemon=True).start()
    except BaseException:
        LOCK.release()
        raise
    return True
