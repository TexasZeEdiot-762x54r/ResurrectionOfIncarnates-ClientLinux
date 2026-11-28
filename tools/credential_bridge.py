"""Ephemeral, capability-authenticated loopback bridge for Proton and native keyring."""
from contextlib import contextmanager
import hmac
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading


@contextmanager
def running(directory):
    import credential_store as store
    if os.name!='posix':raise store.StorageError('Linux credential bridge requires the native launcher')
    path=Path(directory)/'account-auth.json';cap=secrets.token_hex(32);slots=threading.BoundedSemaphore(4)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            if not slots.acquire(blocking=False):
                self.send_response(503);self.send_header('Content-Length','0');self.end_headers();return
            try:self.handle_credential()
            finally:slots.release()
        def handle_credential(self):
            self.connection.settimeout(5)
            if len(self.headers.get_all('Authorization',[]))!=1 or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+cap):
                self.send_error(403);return
            try:
                lengths=self.headers.get_all('Content-Length',[])
                if len(lengths)!=1 or self.headers.get('Transfer-Encoding'):raise ValueError()
                size=int(lengths[0])
                if not 0<size<=store.MAX:raise ValueError()
                raw=self.rfile.read(size)
                if len(raw)!=size:raise ValueError()
                value=json.loads(raw)
                if self.path=='/protect':
                    if not isinstance(value,dict) or set(value)!={'schema','uid','issuer','token'}:raise ValueError()
                    result=store.protect(value,path)
                elif self.path=='/unprotect':result=store.unprotect(value,path)
                elif self.path=='/release':store.release(value,path);result={}
                else:raise ValueError()
                body=json.dumps(result).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            except Exception:
                self.send_response(503);self.send_header('Content-Length','0');self.end_headers()
    class Server(ThreadingHTTPServer):
        daemon_threads=True
    server=Server(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield dict(ROI_CREDENTIAL_BRIDGE='127.0.0.1:'+str(server.server_port),ROI_CREDENTIAL_CAP=cap)
    finally:server.shutdown();server.server_close();thread.join(timeout=3)
