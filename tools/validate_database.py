"""Differentially check the decoder against the game's own property getters.

Uses a local, private RAM copy; never connects to or writes the running game.
"""
import json,struct,time
from pathlib import Path
from mips_vm import VM
from card_database import ROOT,decode_properties,encode_properties

GETTERS={'kind_code':0x0884DB20,'race_code':0x0884DA84,
         'attribute_code':0x0884DAAC,'level':0x0884DAD4,
         'icon_code':0x0884DAF8,'rarity_code':0x0884D8E4,
         'attack':0x0884DBCC,'defense':0x0884DC10}

def main():
    begun=time.perf_counter()
    cards=json.loads((ROOT/'reports/card_database/cards.json').read_text(encoding='utf-8'))
    vm=VM((ROOT/'work/ram_turn25.bin').read_bytes())
    mismatches=[]; checks=0
    root=vm.load(0x088EEE88,4)
    properties=(ROOT/'inputs/card_database/CARD_Prop.bin').read_bytes()
    assert vm.read(vm.load(root+0x30,4),len(properties))==properties
    for card in cards:
        cid=card['card_id']; ix=card['resource_index']
        assert vm.call(0x0884D794,cid)==ix
        for field,addr in GETTERS.items():
            actual=vm.call(addr,cid)
            expected=65535 if card[field] is None else card[field]
            checks+=1
            if actual!=expected:mismatches.append(dict(card=cid,name=card['name'],field=field,expected=expected,actual=actual))
        # The original name/description getters use byte offsets into UTF-16.
        for field,addr in [('name',0x0884D7C4),('description',0x0884D810)]:
            address=vm.call(addr,cid)
            expected=(card[field]+'\0').encode('utf-16-le')
            checks+=1
            if vm.read(address,len(expected))!=expected:mismatches.append(dict(card=cid,field=field))
        raw=properties[ix*8:ix*8+8]
        assert encode_properties(decode_properties(raw))==raw
    result=dict(records=len(cards),getter_comparisons=checks,mismatch_count=len(mismatches),
        mismatches=mismatches,property_table_matches_live_ram=True,
        instruction_count=vm.steps,seconds=round(time.perf_counter()-begun,3),
        method='Decoded archive versus original MIPS getters executed in the lab interpreter; no live writes',
        limitations='Numeric getter results and strings verified. Rarity and genre labels remain unresolved.')
    (ROOT/'reports/database_validation.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    if mismatches:raise SystemExit(1)
if __name__=='__main__':main()
