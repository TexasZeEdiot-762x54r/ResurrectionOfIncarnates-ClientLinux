"""Recognize the two optional catalogue reads at the end of a battle."""
import re
import threading
import time

LOCK = threading.Lock()
state = None


def observe(uid, name, data):
    global state
    now = time.monotonic()
    with LOCK:
        if state and (state[0] != uid or now >= state[1]):
            state = None
        if name in ('authorize', 'getMyCart', 'buyItem', 'addToCart', 'updateCart', 'removeFromCart'):
            state = None
        if not isinstance(data, dict):
            return False
        if name == 'logClientAction':
            logs = data.get('sv_logs')
            if isinstance(logs, list) and any(
                isinstance(r, dict) and r.get('sv_actionId') == 1000
                and isinstance(r.get('sv_remark'), dict)
                and re.fullmatch('[0-9a-fA-F]{32}', str(r['sv_remark'].get('battleId')))
                for r in logs
            ):
                state = (uid, now + 8, False, {0, 1})
        elif state and name == 'getItemMail' and data == {'sv_isReadOnly': 0}:
            state = (*state[:2], True, state[3])
        elif state and state[2] and name == 'getStoreItemList' and set(data) == {'sv_type'}:
            kind = data['sv_type']
            if type(kind) is int and kind in state[3]:
                state[3].remove(kind)
                if not state[3]:
                    state = None
                return True
    return False
