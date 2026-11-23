"""Bound optional friend-profile reads without consuming the gateway's whole budget."""
from collections import OrderedDict, deque
from copy import deepcopy
import hashlib
import logging
import threading
import time

import profile_relay

MAX_STARTS = 8
WINDOW = 10
MAX_INFLIGHT = 2
MAX_CACHED = 32
FOUND_TTL = 2
MISSING_TTL = 5
RECENT_TTL = 30
MAX_DIAGNOSTICS = 12
NAME_TTL = 300


class ProfileReads:
    def __init__(self):
        self.lock = threading.Lock()
        self.entries = OrderedDict()
        self.pending = set()
        self.starts = deque()
        self.recent = OrderedDict()
        self.names = OrderedDict()
        self.diagnostics = deque()

    def fallback(self, key, now, reason):
        """Called under lock; never turn a brief refresh failure into a guest."""
        recent = self.recent.get(key)
        value = recent[1] if recent and now - recent[0] < RECENT_TTL else None
        while self.diagnostics and self.diagnostics[0] <= now - WINDOW:
            self.diagnostics.popleft()
        if len(self.diagnostics) < MAX_DIAGNOSTICS:
            self.diagnostics.append(now)
            logging.getLogger('roi-profile').warning(
                'PEER_PROFILE fallback=%s reason=%s recent_rank=%s',
                'verified-recent' if value is not None else 'guest', reason,
                value.get('sv_rankId', 'unknown') if value is not None else 'unknown')
        return deepcopy(value)

    def verified_name(self, url, token, uid):
        key = (url, hashlib.sha256(token.encode()).digest(), str(uid))
        now = time.monotonic()
        with self.lock:
            entry = self.names.get(key)
            if entry is None:
                return None
            if now-entry[0] >= NAME_TTL:
                self.names.pop(key, None)
                return None
            return entry[1]

    def get(self, url, token, uid):
        key = (url, hashlib.sha256(token.encode()).digest(), str(uid))
        now = time.monotonic()
        with self.lock:
            for old, (deadline, _) in list(self.entries.items()):
                if deadline <= now:
                    self.entries.pop(old)
            for old, (observed, _) in list(self.recent.items()):
                if now - observed >= RECENT_TTL:
                    self.recent.pop(old)
            if key in self.entries:
                self.entries.move_to_end(key)
                value = self.entries[key][1]
                return (deepcopy(value) if value is not None
                        else self.fallback(key, now, 'negative-cache'))
            while self.starts and self.starts[0] <= now-WINDOW:
                self.starts.popleft()
            # These are optional appearance reads. Never queue a long wait in
            # the native menu or relax TLS admission limits to serve a burst.
            if key in self.pending or len(self.pending) >= MAX_INFLIGHT or len(self.starts) >= MAX_STARTS:
                return self.fallback(key, now, 'admission')
            self.pending.add(key)
            self.starts.append(now)
        value = None
        try:
            result = profile_relay.request(url, token)
            if profile_relay.valid_profile(result, uid):
                value = result
        except Exception:
            pass
        finally:
            with self.lock:
                self.pending.discard(key)
                self.entries[key] = (time.monotonic()+(FOUND_TTL if value is not None else MISSING_TTL),
                                     deepcopy(value))
                if value is not None:
                    name = value.get('sv_userName')
                    if (isinstance(name, str) and name.strip() and len(name.encode('utf-8')) <= 255
                            and not any(ord(c) < 32 or ord(c) == 127 for c in name)):
                        self.names[key] = (time.monotonic(), name)
                        self.names.move_to_end(key)
                        while len(self.names) > MAX_CACHED:
                            self.names.popitem(last=False)
                    else:
                        self.names.pop(key, None)
                    self.recent[key] = (time.monotonic(), deepcopy(value))
                    self.recent.move_to_end(key)
                    while len(self.recent) > MAX_CACHED:
                        self.recent.popitem(last=False)
                self.entries.move_to_end(key)
                while len(self.entries) > MAX_CACHED:
                    self.entries.popitem(last=False)
        if value is None:
            with self.lock:
                return self.fallback(key, time.monotonic(), 'refresh')
        return deepcopy(value)


CACHE = ProfileReads()
