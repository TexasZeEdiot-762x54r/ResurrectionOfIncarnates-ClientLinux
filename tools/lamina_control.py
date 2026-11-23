"""Offline experimental selector; never accesses production services."""
import argparse
import json
from pathlib import Path
import sys
import subprocess

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import profile_store
import roi_lamina


def main():
    if json.loads((HERE.parent/'roi-update-base.json').read_bytes()).get('stream') != 'dev/workbench-windows':
        raise SystemExit('This command is restricted to the experimental copy.')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--select', type=int, choices=range(1, 16))
    parser.add_argument('--variant', type=int, choices=(1, 2, 3), default=1)
    args = parser.parse_args()
    path = HERE / 'lamina_profile.json'
    if args.select:
        processes = subprocess.check_output(['tasklist', '/FI', 'IMAGENAME eq roi.exe', '/FO', 'CSV', '/NH'], text=True)
        if 'roi.exe' in processes.lower():
            raise SystemExit('Close the game before changing offline Lamina preferences.')
        value = roi_lamina.select(path, args.select, args.variant - 1)
    else:
        value = roi_lamina.state(profile_store.read(path))
    selected = value['selected_id']
    print(json.dumps({'selected_id': selected, 'name': roi_lamina.LAMINA_NAMES[selected - 1],
                      'skill_variant': value['laminas'][str(selected)]['variant'] + 1,
                      'experimental_xp': value['laminas'][str(selected)]['xp'],
                      'original_xp_curve_recovered': False,
                      'available': dict(enumerate(roi_lamina.LAMINA_NAMES, 1))}, indent=2))


if __name__ == '__main__':
    main()
