"""Selected Ranked loadout wear, serialized by the progression account lock."""
from copy import deepcopy
import re

import roi_cube_wear


def stage(saved, ledger, owner, match, usage):
    if not isinstance(match, str) or not re.fullmatch('[0-9a-f]{32}', match):
        raise ValueError('Invalid Ranked wear identity')
    usage = roi_cube_wear.normalize(usage)
    receipt = ledger.get('ranked_display', {})
    if receipt.get('match') == match and receipt.get('uid') == str(owner):
        if 'cube_usage' in receipt:
            if receipt['cube_usage'] != usage:
                raise ValueError('Conflicting Ranked loadout retry')
            return {}, deepcopy(receipt.get('cube_wear'))
        # Only a server-settled normal result can authorize durability loss.
        if receipt.get('operation') != 'ranked_result' or receipt.get('outcome') not in (-1, 0, 1):
            return {}, None
        changes, wear = roi_cube_wear.apply(saved, usage, receipt['outcome'])
        receipt.update(cube_usage=deepcopy(usage), cube_wear=deepcopy(wear))
        ledger.pop('ranked_cube_stage', None)
        return changes, wear
    usage = roi_cube_wear.snapshot(saved, usage)
    previous = ledger.get('ranked_cube_stage')
    value = dict(match=match, owner=str(owner), usage=usage)
    if previous and previous.get('match') == match and previous != value:
        raise ValueError('Conflicting Ranked loadout retry')
    ledger['ranked_cube_stage'] = value
    return {}, None


def settle(saved, ledger, owner, match, outcome, operation):
    staged = ledger.get('ranked_cube_stage', {})
    if (operation != 'ranked_result' or staged.get('match') != match
            or staged.get('owner') != str(owner)):
        return {}, {}
    usage = staged['usage']
    changes, wear = roi_cube_wear.apply(saved, usage, outcome)
    ledger.pop('ranked_cube_stage', None)
    return changes, dict(cube_usage=deepcopy(usage), cube_wear=deepcopy(wear))
