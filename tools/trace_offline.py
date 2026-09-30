"""Execute an original decision in copied RAM and record its helper results."""
import argparse,json,struct,time
from mips_vm import VM,signed
from ai_lab import ROOT
HOOKS={0x3434:'material_probe',0x5FC4:'board_references',0x2730:'card_gate',
       0x23D98:'legal_mask',0x38AC:'tribute_count',0x350C:'choose_tribute',
       0xCC660:'double_tribute',0x1DA4:'forced_position',0xAFAC:'candidate_stats',
       0xB278:'preferred_card',0x15A0:'position_heuristic'}
BASE=0x09E65C00
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('trial',default='trial4_selector',nargs='?')
    a=p.parse_args();metadata=json.loads((ROOT/'traces'/f'{a.trial}.json').read_text())
    entry=metadata['entry'];regs=entry['registers']
    vm=VM(Path(entry['memory']['file']).read_bytes())
    frames=[];events=[]
    def observe(v,pc):
        if frames and pc==frames[-1]['return_address']:
            f=frames.pop();f['result']=signed(v.r[2])
            if f['name']=='board_references':f['outputs']=[v.load(f['args'][1],4,True),v.load(f['args'][2],4,True)]
            if f['name']=='candidate_stats':f['outputs']=[v.load(f['args'][4]+i*4,4,True) for i in range(8)]
            events.append(f)
        if pc-BASE in HOOKS:
            frames.append(dict(name=HOOKS[pc-BASE],relative=f'0x{pc-BASE:06X}',args=v.r[4:12].copy(),return_address=v.r[31]))
    vm.observer=observe
    begun=time.perf_counter();value=vm.call(BASE+0xB3B4,regs['a0'],regs['a1'],sp=regs['sp'])
    output=vm.load(regs['a1'],4)
    actual=signed(metadata['return']['registers']['v0']);actual_output=int.from_bytes(bytes.fromhex(metadata['return']['output_hex']),'little')
    result=dict(trial=a.trial,original_live_return=actual,interpreter_return=value,
        original_live_output=actual_output,interpreter_output=output,
        matches_live=value==actual and output==actual_output,
        instructions=vm.steps,seconds=round(time.perf_counter()-begun,4),helpers=events)
    (ROOT/'reports'/f'{a.trial}_offline.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    if not result['matches_live']:raise SystemExit(1)
from pathlib import Path
if __name__=='__main__':main()
