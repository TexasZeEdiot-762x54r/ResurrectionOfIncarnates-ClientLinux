"""OS-protected session storage. Never keep an encryption key beside the file."""
import base64
import ctypes as c
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import threading

MAX=16384
_CACHE={}
_LOCK=threading.Lock()


class StorageError(ValueError):
    pass


def scope(path):
    directory=Path(path).parent
    if directory.name=='account-credentials':directory=directory.parent
    return hashlib.sha256(str(directory.resolve()).encode()).hexdigest()


def dpapi(data,binding,protect):
    if os.name!='nt':raise StorageError('Windows protected storage is unavailable')
    class Blob(c.Structure):
        _fields_=[('size',c.c_uint32),('data',c.POINTER(c.c_ubyte))]
    crypt=c.WinDLL('crypt32',use_last_error=True);kernel=c.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[c.c_void_p];kernel.LocalFree.restype=c.c_void_p
    source=c.create_string_buffer(data);entropy=c.create_string_buffer(('ROI-session-v1:'+binding).encode())
    a=Blob(len(data),c.cast(source,c.POINTER(c.c_ubyte)))
    e=Blob(len(entropy.value),c.cast(entropy,c.POINTER(c.c_ubyte)));out=Blob()
    fn=crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    fn.argtypes=[c.POINTER(Blob),c.c_void_p,c.POINTER(Blob),c.c_void_p,c.c_void_p,c.c_uint32,c.POINTER(Blob)]
    fn.restype=c.c_int
    if not fn(c.byref(a),None,c.byref(e),None,None,1,c.byref(out)):
        raise StorageError('Windows could not unlock this credential; sign in through Steam again')
    try:return c.string_at(out.data,out.size)
    finally:kernel.LocalFree(c.cast(out.data,c.c_void_p))


def keyring(action,reference,binding,value=None):
    tool=Path('/usr/bin/secret-tool')
    try:info=tool.stat()
    except OSError:raise StorageError('Install libsecret tools and unlock a desktop Secret Service keyring') from None
    if info.st_uid!=0 or info.st_mode & 0o022:
        raise StorageError('Untrusted Secret Service helper')
    args=[str(tool),action]
    if action=='store':args+=['--label=ROI Resurrection session']
    args+=['application','roi-resurrection','scope',binding,'reference',reference]
    try:
        result=subprocess.run(args,input=value,capture_output=True,timeout=45)
    except (OSError,subprocess.TimeoutExpired):
        raise StorageError('Desktop keyring is unavailable or locked; unlock it and retry') from None
    if result.returncode:
        raise StorageError('Desktop keyring is unavailable or locked; unlock it and retry')
    if len(result.stdout)>MAX:raise StorageError('Oversized keyring result')
    return result.stdout


def bridge(action,value):
    endpoint=os.environ.get('ROI_CREDENTIAL_BRIDGE','');cap=os.environ.get('ROI_CREDENTIAL_CAP','')
    if not re.fullmatch(r'127\.0\.0\.1:[1-9][0-9]{0,4}',endpoint) or not re.fullmatch('[0-9a-f]{64}',cap):
        raise StorageError('Launch the Linux client through Steam; secure-storage broker is missing')
    host,port=endpoint.split(':');conn=http.client.HTTPConnection(host,int(port),timeout=50)
    try:
        raw=json.dumps(value).encode()
        if len(raw)>MAX:raise StorageError('Oversized credential')
        conn.request('POST','/'+action,body=raw,headers={'Authorization':'Bearer '+cap,'Content-Type':'application/json'})
        response=conn.getresponse();body=response.read(MAX+1)
        if response.status!=200 or len(body)>MAX:raise StorageError('Linux secure-storage broker failed; unlock the keyring and retry')
        return json.loads(body)
    except (OSError,http.client.HTTPException,ValueError):
        raise StorageError('Linux secure-storage broker unavailable; relaunch through Steam') from None
    finally:conn.close()


def proton():
    if os.name!='nt':return False
    if os.environ.get('ROI_PROTON_PRIVATE')=='1':return True
    try:
        getattr(c.WinDLL('ntdll'),'wine_get_version')
        return True
    except (OSError,AttributeError):return False


def protect(value,path):
    if proton():return bridge('protect',value)
    raw=json.dumps(value,separators=(',',':')).encode()
    if len(raw)>4096:raise StorageError('Oversized credential')
    binding=scope(path)
    if os.name=='nt':
        return dict(storage=1,backend='dpapi-user',scope=binding,blob=base64.b64encode(dpapi(raw,binding,True)).decode())
    reference=secrets.token_hex(16)
    keyring('store',reference,binding,raw)
    try:
        if not hmac.compare_digest(keyring('lookup',reference,binding),raw):raise StorageError('Keyring verification failed')
    except BaseException:
        keyring('clear',reference,binding);raise
    return dict(storage=1,backend='secret-service',scope=binding,reference=reference)


def validate_envelope(value,path):
    if not isinstance(value,dict) or value.get('storage')!=1 or value.get('scope')!=scope(path):
        raise StorageError('Credential belongs to a different installation; sign in through Steam again')
    backend=value.get('backend')
    keys={'storage','backend','scope','blob' if backend=='dpapi-user' else 'reference'}
    if set(value)!=keys:raise StorageError('Invalid protected credential')
    if backend=='secret-service' and not re.fullmatch('[0-9a-f]{32}',value.get('reference','')):
        raise StorageError('Invalid keyring reference')
    if backend not in ('dpapi-user','secret-service'):raise StorageError('Unsupported credential store')
    return backend


def unprotect(value,path):
    # Bounded legacy read is migration-only; new session writes are always protected.
    if isinstance(value,dict) and value.get('schema')==1 and 'token' in value:return value
    if proton():return bridge('unprotect',value)
    backend=validate_envelope(value,path)
    if backend=='dpapi-user':
        if os.name!='nt':raise StorageError('Windows credentials cannot be copied to Linux; sign in again')
        raw=dpapi(base64.b64decode(value['blob'],validate=True),value['scope'],False)
    else:
        if os.name=='nt':raise StorageError('Linux keyring requires the native Linux launcher')
        raw=keyring('lookup',value['reference'],value['scope'])
    if len(raw)>4096:raise StorageError('Oversized credential')
    return json.loads(raw)


def read(path):
    from battle_log_archive import linked
    path=Path(path)
    if linked(path) or linked(path.parent):raise StorageError('Linked credential refused')
    try:
        with path.open('rb') as stream:raw=stream.read(MAX+1)
    except FileNotFoundError:return None
    if len(raw)>MAX:raise StorageError('Oversized credential envelope')
    key=(scope(path),hashlib.sha256(raw).digest())
    with _LOCK:
        if key in _CACHE:return dict(_CACHE[key])
        value=unprotect(json.loads(raw),path)
        if not isinstance(value,dict):raise StorageError('Invalid credential value')
        # Process memory only; the file digest changes on every account switch.
        _CACHE.clear();_CACHE[key]=dict(value)
        return value


def release(value,path):
    if not isinstance(value,dict) or value.get('storage')!=1:return
    if proton():bridge('release',value);return
    if validate_envelope(value,path)=='secret-service':
        keyring('clear',value['reference'],value['scope'])


def remove(path):
    path=Path(path)
    from battle_log_archive import linked
    if linked(path) or linked(path.parent):raise StorageError('Linked credential refused')
    with path.open('rb') as stream:raw=stream.read(MAX+1)
    if len(raw)>MAX:raise StorageError('Oversized credential envelope')
    try:value=json.loads(raw)
    except ValueError:value=None
    release(value,path)
    path.unlink()


if __name__=='__main__':
    import sys
    if sys.argv[1:]!=['--uid']:raise SystemExit(2)
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from roi_account_client import read_credential
    value=read_credential(Path(__file__).with_name('account-auth.json'))
    if value is None:raise SystemExit(1)
    print(value['uid'])
