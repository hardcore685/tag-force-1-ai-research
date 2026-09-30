"""Check every recovered target-dispatch case against the original routine."""
import json,random,time
from ai_lab import ROOT
from mips_vm import VM,signed
from target_model import ALL_SPECIAL_IDS,select_effect_target
BASE=0x09E65C00
ARITIES={0x11E20:2,0x11548:4,0x119C8:3,0x1216C:3,0x123E4:2,0x1257C:3,
         0xF2380:2,0xBBFD8:3,0xFFB50:1,0x31F34:4,0x32028:0}

class FixtureAPI:
    base=BASE
    def __init__(self,values,cards,flags,levels,context):
        self.values=values;self.cards=cards;self.field_flags=flags;self.levels=levels;self.context=context
        self.selected=None;self.calls=[]
    def call(self,relative,*args):
        args=tuple(signed(a) for a in args[:ARITIES[relative]])
        self.calls.append((relative,args))
        if relative==0x31F34:self.selected=list(args);return 42
        if relative==0x32028:self.selected=['cancel'];return 43
        if relative==0xF2380:return args[1]%4
        if relative==0xBBFD8:return int(args[2]%2==0)
        if relative==0xFFB50:return int(self.levels[args[0]]%2==0)
        return self.values[(relative,args[0])]
    def card_id(self,player,slot):return self.cards[player][slot]
    def flags(self,player,slot):return self.field_flags[player][slot]
    def level(self,cid):return self.levels[cid]
    def context_flag(self):return self.context

def main():
    start=time.perf_counter();rng=random.Random(2147);failures=[];cases=0
    ram=(ROOT/'work/trial4_selector_entry.bin').read_bytes();vm=VM(ram)
    choices=set(ALL_SPECIAL_IDS)|{4007,0x1FFF}
    for cid in sorted(choices):
        for iteration in range(24):
            player=rng.randrange(2);mask=rng.getrandbits(32)
            context=int(rng.random()<.3)
            values={(a,p):rng.choice([-1,-1,0,4,5,9]) for a in ARITIES for p in (0,1)}
            cards=[[4007+p*20+s for s in range(11)] for p in (0,1)]
            flags=[[rng.getrandbits(24) for _ in range(11)] for _ in range(2)]
            levels={c:rng.randrange(1,9) for cs in cards for c in cs}
            actual_api=FixtureAPI(values,cards,flags,levels,context)
            expected_api=FixtureAPI(values,cards,flags,levels,context)
            for p in (0,1):
                address=BASE+0x111E5C+p*0xFAC
                for slot,c in enumerate(cards[p]):
                    vm.store(address+0x30+slot*20,c,2)
                    vm.store(address+0x40+slot*20,flags[p][slot])
            # A safe synthetic effect-context pointer, in copied RAM only.
            vm.store(BASE+0x1107DC,0x09FFF300);vm.store(0x09FFF30C,context,2)
            vm.callbacks={}
            vm.callbacks[BASE+0x4A538]=lambda v:v.set(2,mask)
            vm.callbacks[BASE+0xFFA88]=lambda v:v.set(2,levels[v.r[4]])
            for rel,n in ARITIES.items():
                def callback(v,relative=rel,arity=n):v.set(2,actual_api.call(relative,*v.r[4:4+arity]))
                vm.callbacks[BASE+rel]=callback
            actual=vm.call(BASE+0x14C34,player,cid,0)
            expected=select_effect_target(player,cid,mask,expected_api)
            cases+=1
            if actual!=expected or actual_api.selected!=expected_api.selected:
                failures.append(dict(card_id=cid,iteration=iteration,player=player,mask=f'{mask:08X}',
                    actual=[actual,actual_api.selected],expected=[expected,expected_api.selected],
                    actual_calls=actual_api.calls,expected_calls=expected_api.calls))
    result=dict(seed=2147,dispatch_ids=len(ALL_SPECIAL_IDS),case_count=cases,mismatch_count=len(failures),
        failures=failures[:10],instructions_visited=sum(BASE+0x14C34<=p<BASE+0x154D0 for p in vm.visits),
        total_routine_instructions=(0x154D0-0x14C34)//4,seconds=round(time.perf_counter()-start,3),
        limitations='Original dispatcher executed with synthetic masks, field records and helper returns. No live effect-target trigger or complete card-effect implementation is claimed.')
    (ROOT/'reports/target_validation.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if failures:raise SystemExit(1)
if __name__=='__main__':main()
