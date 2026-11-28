"""Fresh Steam sessions, atomic protected storage, no plaintext retry or backup files."""
import asyncio
import json
import os
from pathlib import Path
import secrets


def ensure(identity,options,directory,api,revoke=False):
    from account_switch import launch_lock
    from battle_log_archive import linked,private_directory
    import credential_store as store
    directory=Path(directory)
    with launch_lock(directory):
        cache=directory/'account-credentials';private_directory(cache)
        final=directory/'account-auth.json'
        if linked(final):raise store.StorageError('Linked credential refused')
        try:
            with final.open('rb') as stream:original=stream.read(store.MAX+1)
        except FileNotFoundError:original=None
        if original is not None and len(original)>store.MAX:raise store.StorageError('Oversized credential file')
        candidate=dict(schema=1,uid=identity.uid,issuer=api.ISSUER,token=secrets.token_hex(32))
        pending=cache/('session-'+secrets.token_hex(16)+'.pending.json')
        api.private_create(pending,candidate)
        try:
            if api.read_credential(pending)!=candidate:raise store.StorageError('Protected credential verification failed')
            request=dict(uid=identity.uid,token=candidate['token'],ticket=identity.ticket(),session_login=True)
            if revoke:request['revoke_devices']=True
            result=api.validate_status(asyncio.run(api.exchange(options,'POST','/registration',body=request)),identity.uid)
            if result['progression']!='server':raise ValueError('Progression approval required')
            current=final.read_bytes() if final.exists() else None
            if current!=original:raise ValueError('Another launcher changed the active account')
            os.replace(pending,final)
            if original is not None:
                try:store.release(json.loads(original),final)
                except (OSError,ValueError):
                    print('Previous protected credential cleanup deferred; no plaintext backup was created.')
            from credential_cleanup import purge
            purge(directory)
            return result
        finally:
            if pending.exists():store.remove(pending)
