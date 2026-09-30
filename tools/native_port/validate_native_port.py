"""Differential-check the native port against original TF1 instruction code.

The integer VM is an independent reference engine for these tests, not part of
the delivered native replica. Native state with instruction bytes erased is
checked separately to establish that the port does not execute original code.
"""
import sys,json,struct,itertools,time,hashlib,collections
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
from mips_vm import VM
from native_ai import NativePort,BASE,START

REG_NAMES=['zero','at','v0','v1','a0','a1','a2','a3','t0','t1','t2','t3','t4','t5','t6','t7',
    's0','s1','s2','s3','s4','s5','s6','s7','t8','t9','k0','k1','gp','sp','fp','ra']

def prepare_reference(vm):
    irq=[1]
    def suspend(v):v.set(2,irq[0]);irq[0]=0
    def resume(v):irq[0]=int(bool(v.r[4]))
    vm.callbacks[0x0886EE4C]=suspend
    vm.callbacks[0x0886EE54]=resume

def cleared_code(ram):
    data=bytearray(ram)
    manifest=json.loads((Path(__file__).parent/'generated/generation_manifest.json').read_text())
    for r in manifest['ranges']:data[r['start']-START:r['end']-START]=b'\0'*(r['end']-r['start'])
    modules=json.loads((ROOT/'reports/module_analysis.json').read_text())
    for m in modules:
        if m['name']=='modduel_eng':continue
        b=m['base_address'];data[b-START:b+m['text_size']-START]=b'\0'*m['text_size']
        for i in m['imports']:
            a=b+i['stub_address']-START;data[a:a+8]=b'\0'*8
    return bytes(data)

def differences(a,b,limit=12):
    if a==b:return []
    result=[]
    for i,(x,y) in enumerate(zip(a,b)):
        if x!=y:
            if len(result)<limit:result.append(dict(address=f'{START+i:08X}',reference=x,native=y))
            else:break
    return result

def main():
    begin=time.monotonic();ram=(ROOT/'work/trial4_selector_entry.bin').read_bytes()
    zeroed=cleared_code(ram);results=[];failures=[];covered=set()
    def run(name,rel,args,changes=(),memory_changes=(),registers=None,sp=0x09FFE000,compare_live=None):
        vm=VM(ram);native=NativePort(ram,coverage=True);erased=NativePort(zeroed)
        prepare_reference(vm)
        for a,v,n in changes:
            vm.store(a,v,n);native.store(a,v,n);erased.store(a,v,n)
        for a,blob in memory_changes:
            i=vm.offset(a,len(blob));vm.mem[i:i+len(blob)]=blob;native.write(a,blob);erased.write(a,blob)
        if registers and registers.get(31):
            stop=registers[31]
            vm.callbacks[stop]=lambda v:v.set(31,0)
            native.callbacks[stop]=lambda v:v.set(31,0)
            erased.callbacks[stop]=lambda v:v.set(31,0)
        item=dict(name=name,relative=f'{rel:06X}',args=list(args))
        try:
            actual=vm.call(BASE+rel,*args,sp=sp,max_steps=12000000,registers=registers)
            output=native.call(BASE+rel,*args,sp=sp,max_steps=12000000,registers=registers)
            no_code=erased.call(BASE+rel,*args,sp=sp,max_steps=12000000,registers=registers)
            native_bytes=native.bytes();reference=bytes(vm.mem)
            diff=differences(reference,native_bytes)
            data_diff=differences(cleared_code(native_bytes),erased.bytes())
            item.update(reference_return=actual,native_return=output,erased_code_return=no_code,
                memory_matches=not diff,memory_difference=diff,erased_code_state_matches=not data_diff,erased_code_difference=data_diff,reference_instructions=vm.steps,
                native_instructions=native.steps,services=dict(native.services))
            item['matches']=actual==output==no_code and not diff and not data_diff
            if compare_live:
                address,value=compare_live;item['live_output_matches']=native.load(address,4)==value
                item['matches'] &= item['live_output_matches']
            covered.update(vm.visits)
        except Exception as e:
            item.update(matches=False,error=str(e))
        results.append(item)
        if not item['matches']:failures.append(item)
        print(name,'PASS' if item['matches'] else 'FAIL',item.get('error',''),flush=True)
    player=BASE+0x111E5C+0xFAC
    records=struct.unpack_from('<3I',ram,player+0x120-START)
    for order in itertools.permutations(range(3)):
        run('real_hand_'+'_'.join(map(str,order)),0xB3B4,(1,0x09FFF400),
            [(player+0x120+i*4,records[k],4) for i,k in enumerate(order)])
    for actor in (0,1):
        for phase in (2,4):
            for rel in (0x1CE08,0x828,0xEDC,0xBAC,0xFEC,0x10D8,0x1DABC,0x1248):
                run(f'phase_{actor}_{phase}_{rel:06X}',rel,(),[(BASE+0x1155D0,actor,4),
                    (BASE+0x1155DC,phase,4),(BASE+0x10FCF8,0,4),(BASE+0x10FCFC,0,4),
                    (BASE+0x10FD00,0,4),(BASE+0x10F9F8+actor*4,1,4)])
    for actor in (0,1):
        for rel,args in [(0xAA88,(actor,1)),(0xAA18,(actor,)),(0x11288,(actor,)),(0x1130C,(actor,0x09FFF800)),
                (0x3B174,(actor,)),(0x3ED98,())]:
            run(f'planning_{actor}_{rel:06X}',rel,args,[(BASE+0x1155D0,actor,4),
                (BASE+0x115600,2,4),(BASE+0x10FCF8,0,4),(BASE+0x10FCFC,0,4),
                (BASE+0x11560C,0,4),(BASE+0x115610,0,4),(BASE+0x10F9F8+actor*4,1,4)],
                memory_changes=[(0x09FFF800,bytes(24))])
    trace=json.loads((ROOT/'traces/trial4_selector.json').read_text());regs=trace['entry']['registers']
    run('captured_live_selector_registers',0xB3B4,(),registers={i:regs[n] for i,n in enumerate(REG_NAMES)},
        sp=regs['sp'],compare_live=(regs['a1'],int(trace['return']['output_hex'],16) if False else int.from_bytes(bytes.fromhex(trace['return']['output_hex']),'little')))
    summary=dict(case_count=len(results),mismatch_count=len(failures),cases=results,
        distinct_original_instructions_visited=len(covered),seconds=round(time.monotonic()-begin,2),
        method='Native C versus independent original-instruction VM on complete real board helpers; also erase source instruction bytes in a separate native state copy.',
        limitations='Real board plus configured actor/phase states; not exhaustive duel-state testing. PSP host services outside the supported offline initialized context stop explicitly.')
    (ROOT/'reports/native_validation.json').write_text(json.dumps(summary,indent=2))
    (ROOT/'reports/native_covered_instructions.json').write_text(json.dumps(sorted(covered)))
    print(json.dumps({k:v for k,v in summary.items() if k!='cases'},indent=2))
    if failures:raise SystemExit(1)
if __name__=='__main__':main()
