"""Offline bridge from a TF1 RAM snapshot to the readable AI reconstruction.

The choice and general position decisions use ai_model.py. Untranslated rule,
tribute, card override and modified-stat helpers execute in mips_vm.py against
private copied memory. This is a hybrid analysis tool, not a full duel engine.
It never connects to PPSSPP and cannot write to the running game or source ISO.
"""
from pathlib import Path
from dataclasses import asdict
from ai_model import *
from mips_vm import VM

BASE=0x09E65C00
SCRATCH=0x09FFF000

class EngineAdapter:
    base=BASE
    def __init__(self,ram):
        self.vm=VM(ram);self.inputs=None;self.last_choice=None
    def load(self,relative):return self.vm.load(BASE+relative,4)
    def store(self,relative,value):self.vm.store(BASE+relative,value)
    def call(self,relative,*args):return self.vm.call(BASE+relative,*args,sp=0x09FFD000)
    def field_address(self,player,slot):return BASE+0x111E5C+(player&1)*0xFAC+0x30+slot*20
    def card_id(self,player,slot):return self.vm.load(self.field_address(player,slot),2)&0x1FFF
    def flags(self,player,slot):return self.vm.load(self.field_address(player,slot)+16,4)
    def level(self,cid):return self.call(0xFFA88,cid)
    def context_flag(self):return self.vm.load(self.load(0x1107DC)+12,2)

    def position_context(self,player):
        opponent=1-player
        threat=self.call(0x14A8,opponent,-1,0)
        own_a=self.call(0x617C,player,player,-1,1,0)
        own_b=self.call(0x617C,player,player,-1,0,1)
        has_monsters=self.call(0xE5988,opponent)!=0
        opponent_life=self.load(0x111E5C+opponent*0xFAC)
        damage=self.call(0x68AC,player,player) if not has_monsters else 0
        battle=(self.call(0x32868,player)!=0
            if not has_monsters and damage>=opponent_life else False)
        return PositionContext(threat,own_a,own_b,self.load(0x111E5C+player*0xFAC),
            has_monsters,opponent_life,damage,battle,
            self.call(0xE43C8,player,0x13F9,-1)!=0,
            bool(self.call(0xE71A8,player) and self.call(0xED950,player,22)),
            self.call(0xE43C8,opponent,0x130C,-1)!=0)

    def choose_hand(self,player):
        probe=self.call(0x3434,player,1)!=-1
        self.call(0x5FC4,1-player,SCRATCH,SCRATCH+4,0)
        opponent_ref=self.vm.load(SCRATCH,4,True)
        self.call(0x5FC4,player,SCRATCH+8,SCRATCH+12,0)
        context=SelectionContext(probe,opponent_ref,
            self.vm.load(SCRATCH+8,4,True),self.vm.load(SCRATCH+12,4,True))
        address=BASE+0x111E5C+(player&1)*0xFAC
        count=self.vm.load(address+12,4)
        if count>80:raise ValueError('Hand record layout does not match TF1')
        records=[self.vm.load(address+0x120+i*4,4) for i in range(count)]
        hand=[]
        for record in records:
            cid=record&0x1FFF;handle=instance_handle(record)
            card=Candidate(cid,is_monster=self.call(0xFFAD8,cid)!=0)
            hand.append(card)
            if not card.is_monster:continue
            card.rejected=self.call(0x2730,player,2,cid)!=0
            if card.rejected:continue
            card.legal_mask=self.call(0x23D98,player,handle)
            if not card.legal_mask:continue
            card.tributes_required=self.call(0x38AC,player,handle,1)
            material_pointer=SCRATCH+0x40;stats_pointer=SCRATCH+0x80
            used=0
            while used<card.tributes_required:
                selected=self.call(0x350C,player,cid,0,used,material_pointer)
                if selected<0:card.tributes_available=False;break
                if self.call(0xE6660,player,cid,selected&0xFF,(selected>>8)&0xFF)!=0:
                    self.vm.store(material_pointer+used*2,selected,2);used+=1
                self.vm.store(material_pointer+used*2,selected,2);used+=1
            if not card.tributes_available:continue
            card.forced_position=self.call(0x1DA4,player,cid,0,0)
            self.call(0xAFAC,player,cid,card.tributes_required,material_pointer,stats_pointer)
            card.attack=self.vm.load(stats_pointer+20,4,True)
            card.defense=self.vm.load(stats_pointer+24,4,True)
            if card.forced_position==-1:
                card.heuristic_position=choose_position(card.attack,card.defense,self.position_context(player))
            card.preferred=self.call(0xB278,player,cid)!=0
        choice=choose_hand_monster(hand,context)
        if choice.index<0 and self.call(0xE6184,player)!=0:
            context.fallback_allowed=True
            mode=1 if self.call(0x114D8,player)!=0 else 2
            for card,record in zip(hand,records):
                if not card.is_monster:continue
                handle=instance_handle(record)
                card.fallback_level=self.call(0xED74C,player,handle)
                if card.fallback_level>=5:continue
                card.fallback_rejected=self.call(0x2730,player,mode,card.card_id)!=0
                if not card.fallback_rejected:
                    card.legal_mask=self.call(0x23D98,player,handle)
                    if card.legal_mask:break
            choice=choose_fallback(hand)
        self.inputs=dict(context=asdict(context),hand=[asdict(c) for c in hand])
        self.last_choice=choice
        return choice.index,choice.position

def main():
    import argparse,json,itertools,struct
    root=Path(__file__).resolve().parent.parent
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ram',type=Path,default=root/'work/trial4_selector_entry.bin')
    p.add_argument('--output',type=Path,default=root/'reports/hybrid_validation.json')
    args=p.parse_args();ram=args.ram.read_bytes();results=[]
    address=BASE+0x111E5C+0xFAC+0x120
    records=struct.unpack_from('<3I',ram,address-0x08800000)
    for order in itertools.permutations(range(3)):
        original=VM(ram);adapter=EngineAdapter(ram)
        for slot,source in enumerate(order):
            original.store(address+slot*4,records[source]);adapter.vm.store(address+slot*4,records[source])
        actual=original.call(BASE+0xB3B4,1,SCRATCH+0x200)
        actual_position=original.load(SCRATCH+0x200,4) if actual>=0 else None
        index,position=adapter.choose_hand(1)
        results.append(dict(order=list(order),original=[actual,actual_position],
            recreated=[index,position],card_id=adapter.last_choice.card_id,
            matches=index==actual and position==actual_position,
            inputs=adapter.inputs))
    result=dict(case_count=len(results),mismatch_count=sum(not r['matches'] for r in results),
        cases=results,method='Readable selection and position models with original game-rule helpers in private RAM; compared with the complete original selector on six hand orders.')
    args.output.write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='cases'},indent=2))
    if result['mismatch_count']:raise SystemExit(1)
if __name__=='__main__':main()
