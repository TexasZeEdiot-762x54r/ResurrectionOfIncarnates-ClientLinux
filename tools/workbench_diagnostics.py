"""Bounded authenticated Workbench diagnostics; never upload raw payloads."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
FILES=('server.log','play.log','public-tunnel.err.log')
PROTON_FILES=('proton-game.log','proton-preflight.log','public-tunnel.log',
              'server-process.log','launcher.log','diagnostics-process.log')
ALL_FILES=FILES+PROTON_FILES
MAX_WIRE=15000  # Account transport and receiver retain their 16 KiB cap.
UPLOAD_SECONDS=10
SAFE_STAGES={'server-config','request-size','response-headers','response-framing','http-response'}


def sanitize(text, *, tail=True):
    lines=[]
    for line in text.splitlines():
        if '"sv_' in line and 'body[' not in line and 'body=' not in line:
            line='[payload fragment omitted]'
        if 'body[' in line or 'body=' in line:
            call=re.search(r'"call"\s*:\s*"([A-Za-z0-9_]{1,60})"',line)
            result=re.search(r'"result"\s*:\s*"(OK|ERROR|NG)"',line)
            line=('request call='+call[1] if call else
                  'response result='+result[1] if result else '[payload omitted]')
        if any(x in line.lower() for x in ('authorization','password','ticket','token','credential','cookie','bearer','api_key','secret')):
            line='[sensitive line omitted]'
        if re.search(r'(?i)(?:environment\s*[:=]|\b(?:USER|USERNAME|LOGNAME|SteamAppUser|SteamUser)\s*=)',line):
            line='[environment omitted]'
        line=re.sub(r'[A-Za-z]:[\\/][^"\n]*','[local path]',line)
        line=re.sub(r'/(?:home|mnt|media|run/user)/[^\s"<>]+','[local path]',line)
        line=re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b','[IP]',line)
        line=re.sub(r'(?i)\b(?:[0-9a-f]{0,4}:){2,}[0-9a-f:]+\b','[IP]',line)
        line=re.sub(r'\b[0-9a-fA-F]{32,}\b|\b\d{17}\b','[identifier]',line)
        line=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}','[email]',line)
        lines.append(line[:400] if tail else line)
    result = '\n'.join(lines).encode('ascii','replace').decode()
    return result[-2600:] if tail else result+('\n' if text.endswith('\n') else '')


def proton_used():
    return sys.platform.startswith('linux') or any(os.environ.get(key) for key in
        ('STEAM_COMPAT_DATA_PATH','ROI_PROTON_PRIVATE','WINEPREFIX'))


def snapshot():
    result={}
    try:since=float(os.environ.get('ROI_DIAGNOSTIC_SINCE','0'))
    except ValueError:return result
    for name in ALL_FILES if proton_used() else FILES:
        path=HERE/'logs'/name
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_mtime<since:continue
            with path.open('rb') as stream:
                offset=max(0,path.stat().st_size-6000);stream.seek(offset);raw=stream.read(6000)
            if offset:raw=raw.partition(b'\n')[2]
            result[name]=sanitize(raw.decode('utf-8','replace'))
        except OSError:continue
    return result


def bounded(body):
    body=dict(body,logs=dict(body['logs']))
    while len(json.dumps(body,separators=(',',':')).encode())>MAX_WIRE:
        name=max(body['logs'],key=lambda key:len(body['logs'][key]))
        value=body['logs'][name]
        if not value:raise ValueError('Diagnostic metadata exceeds wire bound')
        body['logs'][name]=value[-max(0,len(value)-300):] if len(value)>300 else ''
    return body


class Reporter:
    def __init__(self):
        self.uid=os.environ.get('ROI_DIAGNOSTIC_UID')
        self.session=os.environ.get('ROI_DIAGNOSTIC_SESSION') or uuid.uuid4().hex
        self.previous=None;self.last_status=None;self.archive=None

    def status(self,state):
        if state!=self.last_status:
            print('DIAGNOSTICS '+state,flush=True);self.last_status=state

    def send(self,final=False):
        stage='release-config'
        pending=None;current=None;queued=None
        try:
            base=json.loads((HERE.parent/'roi-update-base.json').read_bytes())
            if base.get('stream') not in ('dev/workbench-windows','dev/workbench-linux'):return None
            # The native Linux launcher confirms this identity after enrollment.
            if sys.platform.startswith('linux') and not self.uid:
                self.status('waiting for current-session account confirmation');return None
            from roi_account_client import read_credential
            from diagnostic_loopback import exchange
            from public_tunnel import settings
            from client_version import label
            from match_build import fingerprint
            stage='account-file'
            credential=read_credential(HERE/'account-auth.json')
            if not credential:
                self.status('waiting for account authentication');return None
            if self.uid and credential['uid']!=self.uid:
                self.status('identity changed; upload withheld');return None
            self.uid=credential['uid']
            stage='pending-report'
            pending=None;queued=None
            if sys.platform.startswith('linux'):
                from diagnostic_pending import Pending
                pending=Pending(HERE,self.uid,ALL_FILES)
                try:queued=pending.read()
                except (OSError,ValueError):
                    self.status('pending report unavailable; current capture continues')
            stage='build-metadata'
            try:
                from battle_log_archive import Recorder
                if self.archive is None:
                    self.archive = Recorder(HERE,self.uid,self.session,label(),fingerprint().hex(),
                        ALL_FILES if proton_used() else FILES,
                        since=float(os.environ.get('ROI_DIAGNOSTIC_SINCE','0')))
                try:
                    self.archive.capture(final=final)
                except Exception as error:
                    self.status('archive capture incomplete: '+type(error).__name__)
                archive_deadline=time.monotonic()+(15 if final else 2)
                def archive_upload(body):
                    remaining=archive_deadline-time.monotonic()
                    if remaining<=0:raise TimeoutError('Archive time slice used')
                    async def upload_chunk():
                        return await asyncio.wait_for(exchange(settings(),'POST','/diagnostics/'+self.uid,
                            credential['token'],body),min(UPLOAD_SECONDS,remaining))
                    return asyncio.run(upload_chunk())
                # One archive request per pass, including final exit. The durable queue
                # resumes on later passes/launches; never burst to catch up.
                self.archive.drain(archive_upload,limit=1)
                count=self.archive.pending()
                if count:self.status('archive capture saved; upload pending chunks='+str(count))
            except Exception as error:
                self.status('archive pending: '+type(error).__name__)
                # Do not immediately add a snapshot request to a failing transport.
                return False
            logs=snapshot()
            if not logs and queued is None:return None
            digest=hashlib.sha256(json.dumps(logs,sort_keys=True).encode()).hexdigest()
            if digest==self.previous and not final and queued is None:return True
            current=(bounded(dict(session=self.session,version=label(),build=fingerprint().hex(),logs=logs))
                     if logs else None)
            # Finish the older failed session before replacing it with a new capture.
            body=queued or current
            body=bounded(dict(body,logs={key:sanitize(value) for key,value in body['logs'].items()}))
            if pending:
                try:pending.write(body)
                except (OSError,ValueError):self.status('pending report save unavailable; upload continues')
            stage='transport-config'
            options=settings()
            stage='upload'
            async def upload(report):
                return await asyncio.wait_for(exchange(options,'POST','/diagnostics/'+self.uid,
                    credential['token'],report),UPLOAD_SECONDS)
            reply=asyncio.run(upload(body))
            if reply is None:
                self.status('authentication rejected; upload withheld');return False
            if not isinstance(reply,dict) or type(reply.get('accepted')) is not bool:
                raise ValueError('Invalid diagnostic acknowledgement')
            if reply['accepted'] is True:
                if pending:
                    try:pending.clear()
                    except OSError:self.status('pending report cleanup unavailable')
                if queued is not None and current is not None and queued!=current:
                    if pending:
                        try:pending.write(current)
                        except (OSError,ValueError):self.status('pending report save unavailable')
                    self.status('pending upload accepted; current capture retry pending')
                    return False
                self.previous=digest;self.status('upload accepted');return True
            self.status('upload rate-limited; retry pending')
            return False
        except Exception as error:
            # Only allowlisted stages and numeric HTTP status; never exception text.
            detail=getattr(error,'stage',None)
            if detail in SAFE_STAGES:stage=detail
            code=getattr(error,'http_status',None)
            suffix=(' http='+str(code)) if type(code) is int and 100<=code<=599 else ''
            # A permanently rejected old build must not block the new build's report.
            if code in (400,404,409,413) and queued is not None and current is not None and pending:
                try:pending.write(current)
                except (OSError,ValueError):pass
            self.status('upload unavailable: '+type(error).__name__+' stage='+stage+suffix)
            return False


def main():
    print('Public release: automatic diagnostic upload is disabled. Run Report-Bug after closing the game.')
    return
    reporter=Reporter()
    if '--once' in sys.argv:
        result=reporter.send(final='--final' in sys.argv)
        if result is False and '--final' in sys.argv:
            time.sleep(5);reporter.send(final=True)
        return
    while True:
        reporter.send();time.sleep(5)


if __name__=='__main__':main()
