"""Differential tests: readable AI versus original MIPS decision routines.

Synthetic cases replace engine helpers with explicit inputs, then execute the
original routine. They validate the decision branches, not the full duel rules.
Live comparisons and real-state permutations are reported separately.
"""
import json,random,time,itertools,struct
from dataclasses import asdict
from collections import Counter
from ai_lab import ROOT
from mips_vm import VM,signed
from ai_model import Candidate,SelectionContext,PositionContext,choose_hand_monster,choose_position
BASE=0x09E65C00
PLAYER=BASE+0x111E5C+0xFAC
OUT=0x09FFF400

def fixed(value):
    return lambda vm:vm.set(2,value)

def selector_callbacks(vm,hand,context):
    by_id={c.card_id:c for c in hand}
    by_handle={2*(i+1)+1:c for i,c in enumerate(hand)}
    vm.store(PLAYER+0xC,len(hand))
    for i,c in enumerate(hand):vm.store(PLAYER+0x120+4*i,c.card_id|0x2000|((i+1)<<22))
    cb=vm.callbacks
    cb[BASE+0x3434]=fixed(0 if context.material_probe_succeeded else -1)
    def references(v):
        xs=(context.opponent_reference,0) if v.r[4]==0 else (context.own_reference_a,context.own_reference_b)
        v.store(v.r[5],xs[0]);v.store(v.r[6],xs[1]);v.set(2,0)
    cb[BASE+0x5FC4]=references
    cb[0x0884E424]=lambda v:v.set(2,int(by_id[v.r[4]].is_monster))
    cb[BASE+0x2730]=lambda v:v.set(2,int(by_id[v.r[6]].rejected if v.r[5]==2 else by_id[v.r[6]].fallback_rejected))
    cb[BASE+0x23D98]=lambda v:v.set(2,by_handle[v.r[5]].legal_mask)
    cb[BASE+0x38AC]=lambda v:v.set(2,by_handle[v.r[5]].tributes_required)
    cb[BASE+0x350C]=lambda v:v.set(2,0x100+v.r[7] if by_id[v.r[5]].tributes_available else -1)
    cb[BASE+0xE6660]=fixed(0)
    cb[BASE+0x1DA4]=lambda v:v.set(2,by_id[v.r[5]].forced_position)
    def stats(v):
        c=by_id[v.r[5]]
        for i,value in enumerate([c.card_id,0,0,0,0,c.attack,c.defense,0]):v.store(v.r[8]+i*4,value)
        v.set(2,0)
    cb[BASE+0xAFAC]=stats
    # Candidate ID is s3 in the original selector at this call site.
    cb[BASE+0x15A0]=lambda v:v.set(2,by_id[v.r[19]].heuristic_position)
    cb[BASE+0xB278]=lambda v:v.set(2,int(by_id[v.r[5]].preferred))
    cb[BASE+0xE6184]=fixed(int(context.fallback_allowed))
    cb[BASE+0x114D8]=fixed(1) # distinct fallback gate mode 1
    cb[BASE+0xED74C]=lambda v:v.set(2,by_handle[v.r[5]].fallback_level)

def position_callbacks(vm,c):
    cb=vm.callbacks
    cb[BASE+0x14A8]=fixed(c.opponent_threat)
    cb[BASE+0x617C]=lambda v:v.set(2,c.own_reference_a if v.r[7]==1 else c.own_reference_b)
    cb[BASE+0xE5988]=fixed(int(c.opponent_has_monsters))
    cb[BASE+0x68AC]=fixed(c.current_damage)
    cb[BASE+0x32868]=fixed(int(c.battle_allowed))
    cb[BASE+0xE43C8]=lambda v:v.set(2,int(c.own_fairy_box if v.r[5]==0x13F9 else c.opponent_robbin_goblin))
    cb[BASE+0xE71A8]=fixed(int(c.margin_300_rule))
    cb[BASE+0xED950]=fixed(int(c.margin_300_rule))
    vm.store(BASE+0x111E5C,c.opponent_life)
    vm.store(PLAYER,c.own_life)

def main():
    started=time.perf_counter();rng=random.Random(10136)
    ram=(ROOT/'work/trial4_selector_entry.bin').read_bytes()
    vm=VM(ram); selector_cases=1200;position_cases=1600
    failures=[];choice_counts=Counter();position_counts=Counter()
    for case in range(selector_cases):
        hand=[Candidate(4007+i,
            attack=rng.choice([0,100,500,1200,1500,1700,2200,3000]),
            defense=rng.choice([0,500,1000,1500,2200,3000]),
            is_monster=rng.random()>.15,rejected=rng.random()<.15,
            legal_mask=rng.choice([0,0x10,0x40,0x50,0x90]),
            tributes_required=rng.randrange(3),tributes_available=rng.random()>.2,
            forced_position=rng.choice([-1,-1,0,1]),heuristic_position=rng.choice([-1,0,1]),
            preferred=rng.random()<.15,fallback_level=rng.randrange(1,9),
            fallback_rejected=rng.random()<.15) for i in range(rng.randrange(9))]
        context=SelectionContext(bool(rng.randrange(2)),rng.choice([0,1200,1500,2600,3000]),
            rng.choice([0,1200,1850,3000]),rng.choice([0,1000,2200,3000]),bool(rng.randrange(2)))
        selector_callbacks(vm,hand,context);vm.store(OUT,0xDEADBEEF)
        actual=vm.call(BASE+0xB3B4,1,OUT);actual_position=vm.load(OUT,4)
        expected=choose_hand_monster(hand,context)
        choice_counts[str(actual)]+=1
        if actual!=expected.index or (actual>=0 and actual_position!=expected.position):
            failures.append(dict(kind='selector',case=case,hand=[asdict(x) for x in hand],context=asdict(context),actual=[actual,actual_position],expected=asdict(expected)))
    vm.callbacks.clear()
    for case in range(position_cases):
        attack=rng.choice([0,100,499,500,501,1100,1200,1400,1499,1500,1501,1699,1700,2200,3000])
        defense=rng.choice([0,1000,1499,1500,1700,2200,3000])
        c=PositionContext(rng.choice([0,1200,1500,1501,1800,2600]),rng.choice([0,1000,2200]),
            rng.choice([0,1500,2600]),rng.choice([0,100,101,300,301,8000]),
            bool(rng.randrange(2)),rng.choice([100,1000,8000]),rng.choice([0,1000,8000]),
            bool(rng.randrange(2)),rng.random()<.1,rng.random()<.3,rng.random()<.1)
        position_callbacks(vm,c)
        actual=vm.call(BASE+0x15A0,1,attack,defense)
        expected=choose_position(attack,defense,c)
        position_counts[str(actual)]+=1
        if actual!=expected:failures.append(dict(kind='position',case=case,attack=attack,defense=defense,context=asdict(c),actual=actual,expected=expected))
    live=json.loads((ROOT/'reports/trial4_selector_offline.json').read_text())
    # Real board: all six orders of the exact three captured hand records.
    # No new card IDs, records, LP, or field cards are introduced.
    real_results=[]
    original=list(struct.unpack_from('<3I',ram,PLAYER+0x120-0x08800000))
    for order in itertools.permutations(range(3)):
        real=VM(ram)
        for slot,source in enumerate(order):real.store(PLAYER+0x120+slot*4,original[source])
        result=real.call(BASE+0xB3B4,1,OUT)
        cid=(original[order[result]]&0x1FFF) if result>=0 else None
        real_results.append(dict(order=list(order),return_index=result,card_id=cid,position=real.load(OUT,4)))
    result=dict(seed=10136,synthetic_selector_cases=selector_cases,synthetic_position_cases=position_cases,
        mismatch_count=len(failures),failures=failures[:20],
        selector_return_counts=dict(choice_counts),position_return_counts=dict(position_counts),
        selector_instructions_visited=sum(BASE+0xB3B4<=pc<BASE+0xB8C4 for pc in vm.visits),
        position_instructions_visited=sum(BASE+0x15A0<=pc<BASE+0x1810 for pc in vm.visits),
        live_comparison_matches=live['matches_live'],real_state_permutations=real_results,
        seconds=round(time.perf_counter()-started,3),
        limitations='Synthetic engine helpers validate decision branches only. Six real-state permutations are offline; original/swapped orders were also observed in live trials 1-3.')
    (ROOT/'reports/ai_validation.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    if failures:raise SystemExit(1)
if __name__=='__main__':main()
