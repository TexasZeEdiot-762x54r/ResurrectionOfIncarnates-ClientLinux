"""Explicit, consent-based submission of one retained public launch."""
import gzip
import json
import os
from pathlib import Path
import re
import sys
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parent))
ENDPOINT='https://riseofincarnates.artificerreviver.com/bug-reports/v1'


def request(route, code, data=None):
    if route not in ('/identity','/submit'):raise ValueError('Invalid report route')
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}',code):
        raise ValueError('Use the report code returned by LaminaBot /bugreport')
    if os.name=='nt':
        # Schannel uses the Windows certificate chain store, as the updater does.
        # The private capability goes through stdin, never the process command line.
        with tempfile.TemporaryDirectory(prefix='roi-report-') as tmp:
            lines=['url = '+json.dumps(ENDPOINT+route),
                   'header = '+json.dumps('Authorization: Bearer '+code),
                   'header = "Content-Type: application/octet-stream"']
            if data is not None:
                path=Path(tmp)/'report.gz';path.write_bytes(data)
                lines.append('data-binary = '+json.dumps('@'+path.as_posix()))
            result=subprocess.run([str(Path(os.environ['SystemRoot'])/'System32/curl.exe'),
                '-q','--proto','=https','--fail','--silent','--show-error','--max-time','60',
                '--max-filesize','65536','--config','-'],
                input=('\n'.join(lines)+'\n').encode(),capture_output=True,timeout=65)
            if result.returncode:raise OSError('HTTPS report request failed')
            raw=result.stdout
        if len(raw)>65536:raise ValueError('Oversized report response')
        return json.loads(raw)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):
            raise ValueError('Unexpected report redirect')
    req=urllib.request.Request(ENDPOINT+route,data=data,headers={
        'Authorization':'Bearer '+code,'Content-Type':'application/octet-stream',
        'User-Agent':'ROI-Resurrection-Report/R1P'})
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=60) as response:
        raw=response.read(65537)
    if len(raw)>65536:raise ValueError('Oversized report response')
    return json.loads(raw)


def main():
    from public_session import latest, read_json, save, finish
    root=Path(__file__).resolve().parents[1]
    folder=root/'tools/bug-reports'
    print('Nothing is uploaded until you confirm submission.')
    try:
        record=latest(root)
    except ValueError:
        if input('The last launch did not close cleanly. Is the game closed now? [yes/NO] ').strip().lower()!='yes':
            return
        item=read_json(folder/'last-launch.json')
        record=finish(root,item['session'],recovered=True)
    except FileNotFoundError:
        record=dict(session=uuid.uuid4().hex,logs={},warnings=['No last-launch record found'],os={})
    uid=record.get('steam_id') or input('SteamID64 (leave blank if unknown): ').strip()
    if uid and not re.fullmatch(r'[0-9]{17}',uid):raise ValueError('SteamID64 must contain 17 digits')
    os_info=record.get('os',{})
    if not os_info.get('system'):os_info['system']=input('Operating system: ').strip()
    if os_info['system'].lower()=='linux' and not os_info.get('distro'):
        os_info['distro']=input('Linux distribution and version: ').strip()
    print('In the ROI Resurrection Discord, run /bugreport to get a private report code.')
    import getpass
    code=getpass.getpass('LaminaBot report code: ').strip()
    identity=request('/identity',code)
    name=identity.get('display_name') or input('Your ROI Resurrection Discord display name: ').strip()
    summary=input('What were you doing, what happened, and what did you expect?\n> ').strip()
    if not summary:raise ValueError('Please describe the bug before submitting')
    if len(summary)>4000:raise ValueError('Summary exceeds 4000 characters')
    # R1P is the legacy wire-format discriminator, not the reported client version.
    report=dict(schema=1,report_id=uuid.uuid4().hex,release='R1P',
                client_version=record.get('client_version'),
                steam_id=uid or None,discord_name=name,summary=summary,os=os_info,
                launch=record)
    preview=folder/(report['report_id']+'-report.json')
    save(preview,report)
    print('Report preview:',preview)
    print('Includes:',', '.join(record.get('logs',{})) or 'NO LOGS',
          '\nSteamID:',uid or 'missing','\nDiscord:',name,'\nOS:',os_info)
    print('The summary and your mention will appear in a Discord thread. Logs and SteamID stay private to maintainers.')
    if input('Submit this report? [yes/NO] ').strip().lower()!='yes':
        print('Saved locally. Nothing submitted.');return
    body=gzip.compress(json.dumps(report,ensure_ascii=True).encode(),mtime=0)
    if len(body)>16*1024*1024:raise ValueError('Report too large; local preview retained, nothing uploaded')
    reply=request('/submit',code,body)
    save(folder/(report['report_id']+'-receipt.json'),reply)
    print('Report accepted:',reply['report_id'])
    print(reply.get('thread_url') or 'LaminaBot will create the thread and tag you shortly.')
    for missing in reply.get('missing',[]):print('Please add in the thread:',missing)


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,KeyError,urllib.error.URLError) as error:
        print('Report not completed ('+type(error).__name__+'). Your local report was retained; no automatic retry.',file=sys.stderr)
        raise SystemExit(1)
