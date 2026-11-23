"""Local-only game/relay listeners forwarding to the pinned public TLS gateway."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from tls_transport import CHANNELS,HEALTH,bridge,close,pinned_connection


def settings():
    config=json.loads((HERE/'config.json').read_text(encoding='utf-8-sig'))
    public=json.loads((HERE/'public-lobby.json').read_text(encoding='utf-8'))
    if config.get('lobby_transport')!='tls-public':
        raise ValueError('Public TLS transport is not selected')
    host=config.get('public_lobby_host')
    if host not in (public['internet_host'],public['lan_host']):
        raise ValueError('Unexpected public gateway address')
    port=public['port']
    if type(port) is not int or not 1024<=port<=65535:
        raise ValueError('Invalid gateway port')
    return host,port,public['certificate_sha256']


async def check(options):
    reader,writer=await pinned_connection(*options)
    try:
        writer.write(CHANNELS['health'])
        await writer.drain()
        if await asyncio.wait_for(reader.readline(),timeout=5)!=HEALTH:
            raise ValueError('Unexpected TLS gateway health response')
    finally:
        await close(writer)


async def acquire_admission(admission):
    queued=admission.locked()
    if queued:
        print('public tunnel: local admission limit reached; connection queued',flush=True)
    await admission.acquire()
    return queued


async def run(options, trace_ranked=False):
    await check(options)
    from roi_account_client import read_credential
    from authenticated_native import Reader as AuthenticatedReader
    import roi_progression_client
    account=read_credential(HERE/'account-auth.json')
    if account is None or roi_progression_client.credential(account['uid'])!=account['token']:
        raise ValueError('Run the account registration launcher first')
    build = b''
    if options[0] == 'riseofincarnates.artificerreviver.com':
        from match_build import fingerprint
        build = fingerprint()
    trace = None
    if trace_ranked:
        from ranked_transport_trace import Budget, observe
        trace = Budget(lambda line: print(line, flush=True))
        print('ranked transport trace enabled: numeric shapes only, 10 minutes, 64 records',flush=True)
    # The game can briefly open more than 24 loopback profile connections while
    # rebuilding adjacent menu screens. Preserve the 24-connection upstream cap,
    # but queue overflow locally instead of turning it into RemoteDisconnected.
    admission=asyncio.Semaphore(24)
    async def accept(reader,writer,channel):
        address=writer.get_extra_info('peername')[0]
        if address!='127.0.0.1':
            await close(writer)
            return
        remote_writer=None
        admitted=False
        try:
            await acquire_admission(admission)
            admitted=True
            remote,remote_writer=await pinned_connection(*options)
            remote_writer.write(CHANNELS[channel])
            await remote_writer.drain()
            if channel == 'nrt':
                reader=AuthenticatedReader(reader,account['uid'],account['token'],build)
            if channel == 'nrt' and trace is not None:
                reader=observe(reader,trace,'request')
                remote=observe(remote,trace,'reply')
            result=await bridge(reader,writer,remote,remote_writer)
            if channel=='nrt':
                print('public native stream closed',result,flush=True)
        except (OSError,asyncio.TimeoutError,ValueError):
            print('public tunnel: connection unavailable or certificate check failed',flush=True)
        finally:
            await close(remote_writer)
            await close(writer)
            if admitted:
                admission.release()
    native=await asyncio.start_server(lambda r,w:accept(r,w,'nrt'),'127.0.0.1',52711,limit=65536,backlog=16)
    try:
        from profile_http_pool import Server
        import threading
        profile=Server(options)
    except BaseException:
        native.close()
        await native.wait_closed()
        raise
    thread=threading.Thread(target=profile.serve_forever,daemon=True)
    thread.start()
    try:
        async with native:
            print('public TLS tunnel ready on loopback 52711/52713; profile pool max2',flush=True)
            await native.serve_forever()
    finally:
        await asyncio.to_thread(profile.shutdown)
        profile.server_close()
        thread.join(timeout=2)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    try:
        asyncio.run(check(settings()) if args.check else run(
            settings(), trace_ranked=os.environ.get('ROI_TRACE_RANKED') == '1'))
        if args.check:
            print('Pinned public TLS gateway health check passed')
    except (OSError,ValueError,asyncio.TimeoutError):
        print('Public TLS gateway unavailable, wrong endpoint, or certificate mismatch. Check connectivity and TCP 52714 forwarding.',file=sys.stderr)
        raise SystemExit(1)
