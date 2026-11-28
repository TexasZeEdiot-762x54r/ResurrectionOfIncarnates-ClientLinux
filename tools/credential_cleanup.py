"""Remove obsolete launcher credential copies after a fresh Steam session is active."""
import os
from pathlib import Path
import re
import stat

NAME=re.compile(r'(?:[1-9][0-9]{0,19}(?:\.pending)?|(?:session-[0-9a-f]{32}\.pending)|(?:recovery|activate|preserve|recover)-[0-9a-f]{32})\.json')


def purge(directory):
    from battle_log_archive import linked
    directory=Path(directory).resolve()
    cache=directory/'account-credentials'
    if linked(cache) or linked(directory):
        raise ValueError('Linked credential directory refused')
    removed=0
    candidates=[]
    if cache.is_dir():
        candidates.extend(p for p in cache.iterdir() if NAME.fullmatch(p.name))
    pending=directory/'account-auth.pending.json'
    if pending.exists():candidates.append(pending)
    for path in candidates:
        if linked(path) or not path.resolve().is_relative_to(directory):
            raise ValueError('Linked credential copy refused')
        info=path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
            raise ValueError('Non-private credential copy refused')
        from credential_store import remove
        remove(path);removed+=1
    # Never touch active account-auth.json, profiles, logs, or unrelated backup files.
    return removed
