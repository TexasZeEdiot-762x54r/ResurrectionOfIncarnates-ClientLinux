"""Bounded outfit documents shared by the Workshop client and server."""
import json
from pathlib import Path

CHARACTERS = frozenset(map(str, (1,2,3,4,5,6,7,8,9,10,13,14,20,21)))
FIELDS = {'customSlots', 'activeCustomSlot'}
MAX_SLOT = 255
MAX_DOCUMENT = 65536
_catalog = None


def integer(value, minimum, maximum):
    if type(value) is str and value.isascii() and value.isdecimal() and len(value) <= 20:
        value = int(value)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('Invalid outfit number')
    return value


def allowed_items():
    global _catalog
    if _catalog is None:
        parts = json.loads(Path(__file__).with_name('customize-parts.json').read_bytes())
        data = {}
        for cid in CHARACTERS:
            row = parts[cid]
            catalog = set()
            for category,key in ((1,'head'),(2,'cos'),(3,'weap')):
                pairs = {tuple(x) for x in row.get(key, [])}
                catalog.update(int(cid)*1000000+category*100000+m*100+t
                               for m,t in pairs if m<=100 and (m,0) in pairs)
            for key,offset in (('acce',0),('headacce',500)):
                catalog.update(int(cid)*1000000+400000+(offset+m)*100
                               for m,_ in row.get(key,[]) if 1<=m<=99)
            data[cid]=frozenset(catalog)
        _catalog = data
    return _catalog


def character(value):
    cid=str(integer(value,1,21))
    if cid not in CHARACTERS:
        raise ValueError('Unknown outfit character')
    return cid


def outfit(cid, values):
    if not isinstance(values,list) or len(values)>9:
        raise ValueError('Invalid outfit parts')
    ids = [integer(v,0,2**64-1) for v in values] + [0]*(9-len(values))
    catalog = allowed_items()[cid]
    for i,item in enumerate(ids):
        if item and (item not in catalog or item//100000%10!=min(i+1,4)):
            raise ValueError('Outfit part does not exist for this character and slot')
    return ids


def blank():
    return dict(customSlots={},activeCustomSlot={})


def normalize(value):
    if not isinstance(value,dict) or set(value)!=FIELDS:
        raise ValueError('Invalid outfit document')
    slots,active=value['customSlots'],value['activeCustomSlot']
    if not isinstance(slots,dict) or not isinstance(active,dict) or len(slots)>14 or len(active)>14:
        raise ValueError('Too many outfit characters')
    result=blank()
    for cid,rows in slots.items():
        cid=character(cid)
        if cid in result['customSlots'] or not isinstance(rows,dict) or len(rows)>MAX_SLOT+1:
            raise ValueError('Invalid outfit slots')
        normalized={}
        for slot,parts in rows.items():
            key=str(integer(slot,0,MAX_SLOT))
            if key in normalized:
                raise ValueError('Duplicate outfit slot')
            normalized[key]=outfit(cid,parts)
        result['customSlots'][cid]=normalized
    for cid,slot in active.items():
        cid=character(cid)
        if cid in result['activeCustomSlot']:
            raise ValueError('Duplicate active outfit slot')
        result['activeCustomSlot'][cid]=integer(slot,0,MAX_SLOT)
    return result


def apply(doc, action, data):
    if not isinstance(data,dict):
        raise ValueError('Invalid outfit request')
    if action=='outfitImport':
        # Import is additive. Existing server presets and active choices win.
        imported=normalize(data)
        for cid,slots in imported['customSlots'].items():
            current=doc['customSlots'].setdefault(cid,{})
            for slot,parts in slots.items():
                current.setdefault(slot,parts)
        for cid,slot in imported['activeCustomSlot'].items():
            doc['activeCustomSlot'].setdefault(cid,slot)
    else:
        cid=character(data.get('sv_charaId'))
        if action=='outfitSave' and set(data)=={'sv_charaId','sv_slotNum','sv_customItemHoldIds'}:
            slot=str(integer(data['sv_slotNum'],0,MAX_SLOT))
            doc['customSlots'].setdefault(cid,{})[slot]=outfit(cid,data['sv_customItemHoldIds'])
        elif action=='outfitSelect' and set(data)=={'sv_charaId','sv_slotNum'}:
            doc['activeCustomSlot'][cid]=integer(data['sv_slotNum'],0,MAX_SLOT)
        else:
            raise ValueError('Unsupported outfit action')
    return normalize(doc)


def equipped(doc, cid):
    """Resolve an active slot into item IDs, keeping Odin's nonexistent head empty."""
    slot=str(doc['activeCustomSlot'].get(cid,0))
    ids=doc['customSlots'].get(cid,{}).get(slot,[0]*9)[:]
    for i in range(3):
        item=int(cid)*1000000+(i+1)*100000
        if not ids[i] and item in allowed_items()[cid]:
            ids[i]=item
    return list(map(str,ids))
