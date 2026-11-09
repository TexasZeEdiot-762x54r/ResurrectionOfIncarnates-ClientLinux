"""Run the existing Win32 hook backend inside Steam's selected Proton prefix."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def preflight():
    if os.name != 'nt' or struct.calcsize('P') != 8:
        raise RuntimeError('The hook backend requires 64-bit Windows Python under Proton')
    import paths
    sys.path.insert(0, str(paths.LIBS))
    import capstone
    import pefile
    import dbg
    for name in ('CreateProcessW', 'WaitForDebugEvent', 'ContinueDebugEvent',
                 'GetThreadContext', 'SetThreadContext', 'ReadProcessMemory',
                 'WriteProcessMemory', 'VirtualProtectEx', 'FlushInstructionCache'):
        if not getattr(dbg.k32, name, None):
            raise RuntimeError('Missing Win32 debugger API: ' + name)
    if not paths.GAME_EXE.is_file():
        raise RuntimeError('Proton cannot see the generated game through its Z: mapping')
    print(json.dumps({'windows_python_64bit': True, 'win32_api_exports_present': True,
                      'game_visible': True, 'game_started': False}), flush=True)
    return dbg, paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--launch', action='store_true')
    parser.add_argument('--enroll', action='store_true')
    args = parser.parse_args()
    if sum((args.preflight,args.launch,args.enroll)) != 1:
        raise ValueError('Select exactly one entry mode')
    dbg, paths = preflight()
    if args.preflight:
        return 0
    if args.enroll:
        import roi_account_client
        return roi_account_client.main()
    if os.environ.get('SteamAppId') != '258160':
        raise RuntimeError('Launch from the actual Rise of Incarnates Steam library item')
    raw = json.loads(os.environ.get('ROI_GAME_ARGUMENTS_JSON', '[]'))
    if not isinstance(raw, list) or any(not isinstance(arg, str) or '\0' in arg for arg in raw):
        raise ValueError('Invalid game arguments')
    from sessions import play
    log = HERE / 'logs' / 'play.log'
    log.parent.mkdir(exist_ok=True)
    debugger = dbg.Debugger(paths.GAME_EXE, log)
    play.setup(debugger)
    if os.environ.get('ROI_STARTUP_DIAGNOSTICS') == '1':
        import startup_diagnostics
        startup_diagnostics.setup(debugger)
    result = debugger.run(86400, subprocess.list2cmdline(raw))
    print('Game exit:', result, flush=True)
    return 0 if result == 0 else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(type(error).__name__ + ': ' + str(error), file=sys.stderr, flush=True)
        raise SystemExit(1)
