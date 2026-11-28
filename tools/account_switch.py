"""Select credentials using the active Steam identity, before any client services start."""
import asyncio
import base64
from contextlib import contextmanager
import os
from pathlib import Path
import re
import secrets
import socket


@contextmanager
def launch_lock(directory):
    from battle_log_archive import linked
    path = directory / 'account-switch.lock'
    if linked(directory) or linked(path):
        raise ValueError('Linked account path refused')
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == 'nt':
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b'0')
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def ensure(identity, options, directory, api, revoke=False):
    from account_switch_secure import ensure as protected_ensure
    return protected_ensure(identity, options, directory, api, revoke)


def check_idle():
    for port in (52711, 52713):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.2):
                raise ValueError('Close the running client and its local services before signing in')
        except OSError:
            pass
