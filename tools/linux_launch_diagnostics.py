"""Private Linux startup capture and bounded local-only final capture."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

ACTIVE=None


class Capture:
    def __init__(self,root):
        self.root=Path(root);self.here=self.root/'tools';self.logs=self.here/'logs'
        self.started=time.time();self.session=uuid.uuid4().hex
        self.uid=None;self.child=None;self.stream=None;self.handlers={};self.ready=False

    def note(self,text):
        if not self.ready:return
        from workbench_diagnostics import sanitize
        path=self.logs/'launcher.log'
        if path.is_symlink():return
        with path.open('a',encoding='ascii') as stream:stream.write(sanitize(text)+'\n')

    def begin(self):
        if os.name!='posix' or os.geteuid()==0:return
        if self.here.is_symlink() or self.here.stat().st_uid!=os.getuid():return
        if self.logs.is_symlink():return
        self.logs.mkdir(mode=0o700,exist_ok=True)
        if self.logs.stat().st_uid!=os.getuid():return
        self.logs.chmod(0o700);self.ready=True
        from public_session import begin
        self.public_session=begin(self.root)
        path=self.logs/'launcher.log'
        if not path.is_symlink():
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600);os.close(fd)
        self.note('LAUNCHER startup capture started; logs local only')
        def interrupted(signum,frame):raise KeyboardInterrupt('Launcher interrupted')
        for sig in (signal.SIGINT,signal.SIGTERM):
            self.handlers[sig]=signal.signal(sig,interrupted)

    def environment(self):
        return dict(os.environ,ROI_DIAGNOSTIC_UID=self.uid,ROI_DIAGNOSTIC_SESSION=self.session,
                    ROI_DIAGNOSTIC_SINCE=str(self.started),ROI_PROTON_PRIVATE='1',
                    ROI_DEPLOYMENT_FILE=str(self.root/'deployment.local.json'))

    def enrolled(self):
        if not self.ready:return
        try:
            from roi_account_client import read_credential
            credential=read_credential(self.here/'account-auth.json')
            if not credential:return
            self.uid=credential['uid'];self.note('LAUNCHER current account authenticated')
        except Exception as error:self.note('LAUNCHER diagnostic startup unavailable: '+type(error).__name__)

    def finish(self):
        try:
            if self.child and self.child.poll() is None:
                self.child.terminate()
                try:self.child.wait(timeout=2)
                except subprocess.TimeoutExpired:self.child.kill();self.child.wait(timeout=2)
            if self.stream:self.stream.close()
            self.note('LAUNCHER exit; final diagnostic capture')
            from public_session import finish
            finish(self.root,self.public_session)
        except Exception as error:self.note('LAUNCHER local capture unavailable: '+type(error).__name__)
        finally:
            for sig,handler in self.handlers.items():signal.signal(sig,handler)


def enrolled():
    if ACTIVE:ACTIVE.enrolled()


def run(root,operation):
    global ACTIVE
    capture=Capture(root);ACTIVE=capture
    try:
        try:capture.begin()
        except Exception:pass  # Diagnostic failure cannot block launch.
        return operation()
    except BaseException as error:
        try:capture.note('LAUNCHER failed: '+type(error).__name__+': '+str(error))
        except Exception:pass
        raise
    finally:
        try:capture.finish()
        except Exception:pass
        ACTIVE=None
