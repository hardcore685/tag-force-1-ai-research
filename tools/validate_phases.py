"""Compare recovered phase machines with original MIPS on synthetic inputs."""
import json,random,time
from pathlib import Path
from mips_vm import VM,signed
from phase_model import *
ROOT=Path(__file__).resolve().parent.parent
BASE=0x09E65C00
ARITIES={0x26D0:1,0xDB7EC:1,0xDB6B8:1,0xE43C8:3,0x4C8:1,0x3434:2,
    0xAC5C:2,0xAA18:1,0x2D4:3,0x5F2C:1,0x1D124:1,0x1CF60:1,
    0x1C948:2,0xCC4:3,0x32868:1,0x329D8:1,0xAA88:2,0x1CFD0:2,
    0xABD5C:2,0x3B174:1,0xD0C00:4}
STATE=(ACTOR,TURN,PHASE,PERSPECTIVE,OUTER,INNER,AUX,PENDING,
       0x10FEC4,0x11560C,0x115610)

class Fixture:
    def __init__(self,state,values,choice):
        self.state=state.copy();self.values=values;self.choice=choice;self.actions=[]
    def load(self,rel):return self.state.get(rel,0)
    def store(self,rel,value):self.state[rel]=value&0xFFFFFFFF
    def call(self,rel,*args):
        if rel==0xD0C00:self.actions.append(list(args))
        return self.values[rel]
    def choose_hand(self,player):return self.choice

def main():
    begun=time.perf_counter();rng=random.Random(40828);failures=[];cases=0
    vm=VM((ROOT/'work/trial4_selector_entry.bin').read_bytes())
    models={0x1248:outer_tick,**RECREATED_HANDLERS}
    for relative,model in models.items():
        for iteration in range(800):
            player=rng.randrange(2)
            state={r:rng.randrange(6) for r in STATE}
            state.update({ACTOR:player,TURN:rng.randrange(3),PHASE:rng.choice([2,4]),
                OUTER:rng.randrange(10),INNER:rng.randrange(6),PENDING:rng.randrange(2)})
            for p in (0,1):state[0x111E5C+p*0xFAC+0x11C]=rng.choice([0,1<<17])
            values={r:rng.choice([-1,0,0,1,2]) for r in ARITIES}
            values.update({r:rng.choice([0,1]) for r in HANDLERS if r is not None})
            choice=(rng.choice([-1,0,2]),rng.randrange(2))
            expected_api=Fixture(state,values,choice);actual_actions=[]
            for r,v in state.items():vm.store(BASE+r,v)
            vm.callbacks={}
            for rel,n in ARITIES.items():
                def callback(v,r=rel,n=n):
                    if r==0xD0C00:actual_actions.append([signed(x) for x in v.r[4:4+n]])
                    v.set(2,values[r])
                vm.callbacks[BASE+rel]=callback
            if relative==0x1248:
                for r in HANDLERS:
                    if r is not None:vm.callbacks[BASE+r]=lambda v,r=r:v.set(2,values[r])
            def select(v):v.store(v.r[5],choice[1]);v.set(2,choice[0])
            vm.callbacks[BASE+0xB3B4]=select
            actual=vm.call(BASE+relative)
            expected=model(expected_api)
            actual_state={r:vm.load(BASE+r,4) for r in state}
            cases+=1
            if actual!=expected or actual_state!=expected_api.state or actual_actions!=expected_api.actions:
                failures.append(dict(relative=f'{relative:06X}',iteration=iteration,
                    state=state,values=values,choice=choice,actual=actual,expected=expected,
                    state_difference={f'{r:06X}':[actual_state[r],expected_api.state[r]]
                        for r in state if actual_state[r]!=expected_api.state[r]},
                    actions=[actual_actions,expected_api.actions]))
    result=dict(seed=40828,case_count=cases,routine_count=len(models),
        mismatch_count=len(failures),failures=failures[:10],seconds=round(time.perf_counter()-begun,3),
        limitations='Synthetic helper results validate scheduling, state transitions and the battle phase-change payload. Battle target planning and remaining effect helpers are not rewritten here.')
    (ROOT/'reports/phase_validation.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    if failures:raise SystemExit(1)
if __name__=='__main__':main()
