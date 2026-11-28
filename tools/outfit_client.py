"""Use the authenticated progression tunnel for Workshop outfit presets."""
import http.client
import json
import logging
from threading import RLock
from urllib.parse import urlsplit
from uuid import uuid4

import outfit_presets as outfits
import roi_progression_client as progression

LOCK = RLock()
CACHE = {}
IMPORTED = set()
DEFERRED = (OSError, ValueError, http.client.HTTPException, progression.Unavailable,
            progression.ResponseError)


def exchange(url, uid, token, payload):
    target = urlsplit(url)
    if (target.scheme != 'http' or target.netloc not in ('127.0.0.1:52713', 'localhost:52713')
            or target.path != '/progress/' + uid or target.query or target.fragment):
        raise ValueError('Outfits require the pinned loopback tunnel')
    body = json.dumps(payload, separators=(',', ':')).encode()
    if len(body) > outfits.MAX_DOCUMENT:
        raise ValueError('Outfit request too large')
    for attempt in range(2):
        try:
            raw = progression.exchange(target, uid, token, body, timeout=1.25, fresh=bool(attempt))
            break
        except progression.ResponseError as error:
            if attempt or error.code < 500:
                raise
        except (OSError, http.client.HTTPException):
            if attempt:
                raise
    if len(raw) > outfits.MAX_DOCUMENT + 256:
        raise ValueError('Outfit response too large')
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != {'uid', 'revision', 'outfits'}
            or value['uid'] != uid or type(value['revision']) is not int
            or value['revision'] < 0 or (value['outfits'] is None) != (value['revision'] == 0)):
        raise ValueError('Invalid outfit response')
    if value['outfits'] is not None:
        value['outfits'] = outfits.normalize(value['outfits'])
    return value


def request(url, uid, token, payload):
    value = exchange(url, uid, token, payload)
    CACHE.clear()
    CACHE[uid] = value
    return value


def change(url, uid, token, action, data, revision):
    return request(url, uid, token, dict(action=action, data=data, revision=revision,
                                        request_id=uuid4().hex))


def import_local(url, uid, token, local, save_file, state):
    claim = save_file.with_name('outfit-import-owner.txt')
    done = save_file.with_name('outfit-import-complete')
    if claim in IMPORTED or done.is_file():
        return state
    data = outfits.normalize({key: local.get(key, {}) for key in outfits.FIELDS})
    if not any(data.values()):
        return state
    # Claim before contacting the server so a failed upload cannot hand the same
    # legacy file to a different Steam account on the next launch.
    try:
        with claim.open('x', encoding='ascii') as stream:
            stream.write(uid)
    except FileExistsError:
        with claim.open(encoding='ascii') as stream:
            if stream.read(21) != uid:
                return state
    state = change(url, uid, token, 'outfitImport', data, state['revision'])
    done.touch()
    IMPORTED.add(claim)
    return state


def fields(url, uid, local, save_file):
    if not url:
        return {}
    uid = str(uid)
    with LOCK:
        try:
            token = progression.credential(uid)
        except DEFERRED:
            return outfits.blank()
        if not token:
            return outfits.blank()
        try:
            state = request(url, uid, token, dict(action='outfitRead'))
        except DEFERRED:
            state = CACHE.get(uid, {})
        else:
            try:
                state = import_local(url, uid, token, local, save_file, state)
            except DEFERRED as error:
                logging.getLogger('roi-profile').warning(
                    'Local outfit import deferred: %s', type(error).__name__)
                state = CACHE.get(uid, state)
        return state.get('outfits') or outfits.blank()


def mutate(url, uid, action, data):
    uid = str(uid)
    with LOCK:
        token = progression.credential(uid)
        if not token:
            raise ValueError('Outfits require an authenticated account')
        state = request(url, uid, token, dict(action='outfitRead'))
        change(url, uid, token, action, data, state['revision'])
