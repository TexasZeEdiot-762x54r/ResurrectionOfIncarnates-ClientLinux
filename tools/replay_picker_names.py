"""Refresh picker display names using bounded, authenticated profile reads."""
from copy import deepcopy

MAX_LOOKUPS = 8


def name(uid, fetch):
    try:
        profile = fetch(uid)
        if not isinstance(profile, dict) or str(profile.get('_id')) != uid:
            return None
        if any(str(profile.get(k, uid)) != uid for k in ('sv_uid', 'sv_onlineId')):
            return None
        value = profile.get('sv_userName')
        if (not isinstance(value, str) or not value.strip()
                or len(value.encode('utf-8')) > 255
                or any(ord(c) < 32 or ord(c) == 127 for c in value)
                or value == 'Player '+uid[-4:]):
            return None
        return value
    except Exception:
        return None


def enrich(data, fetch):
    result = deepcopy(data)
    rows = result.get('sv_members', [])
    circle = result.get('sv_circle')
    if isinstance(circle, dict):
        rows = circle.get('sv_members', rows)
    if not isinstance(rows, list):
        return result
    resolved = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        uid = row.get('sv_uid')
        if not isinstance(uid, str) or not uid.isascii() or not uid.isdecimal() or not 0 < int(uid) < 2**64:
            continue
        if uid not in resolved:
            if len(resolved) >= MAX_LOOKUPS:
                continue
            resolved[uid] = name(uid, fetch)
        if resolved[uid] is not None:
            row['sv_userName'] = resolved[uid]
            if isinstance(circle, dict) and circle.get('sv_leaderUid') == uid:
                circle['sv_leaderName'] = resolved[uid]
    return result
