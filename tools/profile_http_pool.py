"""Bounded loopback HTTP proxy sharing two certificate-pinned relay streams."""
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import queue
import re
import socket
import ssl
import threading
import time

MAX_BODY=8*1024*1024
IDLE=15
HOP={'connection','proxy-connection','keep-alive','te','trailer','transfer-encoding',
     'upgrade','host','content-length','expect','x-roi-transport','x-roi-broker-error'}


class PinnedHTTP(http.client.HTTPConnection):
    def __init__(self,options):
        super().__init__(options[0],options[1],timeout=7)
        self.options=options

    def connect(self):
        from tls_transport import CHANNELS,client_context
        host,port,pin=self.options
        if not isinstance(pin,str) or re.fullmatch('[0-9a-fA-F]{64}',pin) is None:
            raise ValueError('Invalid certificate pin')
        raw=socket.create_connection((host,port),timeout=7)
        secured=None
        try:
            secured=client_context().wrap_socket(raw,server_hostname=None)
            digest=hashlib.sha256(secured.getpeercert(binary_form=True)).hexdigest()
            if not hmac.compare_digest(digest,pin.lower()):
                raise ssl.SSLError('Public relay certificate mismatch')
            secured.sendall(CHANNELS['profile'])
            self.sock=secured
        except BaseException:
            (secured or raw).close()
            raise


class Pool:
    def __init__(self,options,factory=None,clock=time.monotonic):
        self.options=options;self.factory=factory or (lambda:PinnedHTTP(options));self.clock=clock
        self.slots=queue.LifoQueue(2)
        self.slots.put(None);self.slots.put(None)
        self.lock=threading.Lock();self.closed=False
        self.idle={};self.counters=dict(requests=0,connections=0,failures=0,busy=0,upstream_503=0,upstream_close=0,transport_error=0)

    def count(self,key):
        with self.lock:self.counters[key]+=1

    def exchange(self,method,path,headers,body):
        try:connection=self.slots.get(timeout=0.05 if path.startswith('/diagnostics/') else 1.5)
        except queue.Empty:
            self.count('busy');raise OSError('Profile relay pool busy') from None
        try:
            with self.lock:
                if self.closed:raise OSError('Profile relay stopped')
                stamp=self.idle.pop(connection,None) if connection is not None else None
            if connection is not None and (stamp is None or self.clock()-stamp>=IDLE):
                connection.close();connection=None
            if connection is None:
                connection=self.factory();self.count('connections')
            self.count('requests')
            # No automatic replay: a failed POST may already have committed remotely.
            connection.putrequest(method,path,skip_host=True,skip_accept_encoding=True)
            for key,value in headers:
                if key.lower() not in HOP:connection.putheader(key,value)
            connection.putheader('Host',self.options[0])
            connection.putheader('Connection','keep-alive')
            connection.putheader('X-ROI-Transport','pooled-v1')
            connection.putheader('Content-Length',str(len(body)))
            connection.endheaders(body or None)
            response=connection.getresponse()
            data=response.read(MAX_BODY+1)
            if len(data)>MAX_BODY:raise ValueError('Profile response exceeds cap')
            result=response.status,response.getheaders(),data
            if response.status==503:self.count('upstream_503')
            if response.will_close:
                self.count('upstream_close')
                connection.close();connection=None
            return result
        except BaseException:
            self.count('transport_error');self.count('failures')
            if connection is not None:connection.close()
            connection=None
            raise
        finally:
            with self.lock:
                if connection is not None:
                    if self.closed:connection.close();connection=None
                    else:self.idle[connection]=self.clock()
            self.slots.put(connection)

    def reap(self):
        with self.lock:
            for connection,stamp in list(self.idle.items()):
                if self.clock()-stamp>=IDLE:
                    connection.close();del self.idle[connection]

    def close(self):
        with self.lock:
            self.closed=True
            for connection in self.idle:connection.close()
            self.idle.clear()


class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def setup(self):
        super().setup();self.connection.settimeout(5)
    def log_message(self,*args):pass
    def send_error(self,code,message=None,explain=None):
        self.send_response(code);self.send_header('Content-Length','0')
        if code==503:self.send_header('X-ROI-Broker-Error','1')
        self.send_header('Connection','close');self.end_headers();self.close_connection=True
    def do_GET(self):self.forward()
    def do_POST(self):self.forward()
    def do_PUT(self):self.forward()
    def forward(self):
        if self.client_address[0]!='127.0.0.1':self.send_error(403);return
        raw_target=self.raw_requestline.split()[1]
        lengths=self.headers.get_all('Content-Length',[])
        if (self.headers.get('Transfer-Encoding') or len(lengths)>1
                or len(self.headers.get_all('Authorization',[]))>1
                or not self.path.startswith('/') or self.path.startswith('//')
                or raw_target!=self.path.encode('ascii',errors='replace')
                or len(self.path)>2048 or any(ord(c)<33 or ord(c)>126 for c in self.path)
                or self.headers.get('Upgrade')
                or any(x.strip().lower() not in ('close','keep-alive')
                       for x in self.headers.get('Connection','').split(',') if x.strip())):
            self.send_error(400);return
        try:
            text=lengths[0] if lengths else '0'
            if not text.isascii() or not text.isdecimal():raise ValueError()
            size=int(text)
            if size>MAX_BODY or self.command=='GET' and size:raise ValueError()
            body=self.rfile.read(size)
            if len(body)!=size:raise ValueError()
        except (ValueError,OSError):self.send_error(400);return
        try:status,headers,data=self.server.pool.exchange(self.command,self.path,list(self.headers.items()),body)
        except (OSError,ValueError,http.client.HTTPException):self.send_error(503);return
        # HTTP errors, authentication responses and bytes are passed through unchanged.
        self.send_response_only(status)
        for key,value in headers:
            if key.lower() not in HOP:self.send_header(key,value)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Connection','close' if self.close_connection else 'keep-alive')
        self.end_headers()
        if data:
            try:self.wfile.write(data)
            except OSError:self.close_connection=True


class Server(ThreadingHTTPServer):
    daemon_threads=True
    block_on_close=False
    allow_reuse_address=True
    request_queue_size=32
    def __init__(self,options,address=('127.0.0.1',52713),pool=None):
        self.pool=pool or Pool(options);self.workers=threading.BoundedSemaphore(24)
        self.last_report=time.monotonic()
        super().__init__(address,Handler)
    def process_request(self,request,address):
        if address[0]!='127.0.0.1' or not self.workers.acquire(False):
            self.shutdown_request(request);return
        try:super().process_request(request,address)
        except BaseException:self.workers.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.workers.release()
    def service_actions(self):
        self.pool.reap()
        if time.monotonic()-self.last_report>=30:
            self.last_report=time.monotonic()
            with self.pool.lock:counts=dict(self.pool.counters)
            print('PROFILE_POOL counters '+str(counts),flush=True)
    def server_close(self):
        self.pool.close();super().server_close()
