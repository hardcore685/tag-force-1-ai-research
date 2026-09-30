"""Compare the compiled AI with stopped original-game entry/return captures."""
import json,sys,time,struct,hashlib
from pathlib import Path
from native_ai import NativePort,BASE,START
from validate_native_port import REG_NAMES,cleared_code,differences,VM,prepare_reference
ROOT=Path(__file__).resolve().parents[2]
ENGINE_ALLOCATED_SIZE=1139456
MEMCPY_ADDRESS=0x0880DD1C
MEMCPY_SIZE=268
MEMCPY_NORMALIZED_SHA256='673cad7a4a3eac0a64f6061156afe5c8e1e53b20d149aace1ac61514b08fdf92'

def memcpy_signature(ram):
    """Match the exact recognized helper, using PPSSPP's relocation masking."""
    words=[]
    for pc in range(MEMCPY_ADDRESS,MEMCPY_ADDRESS+MEMCPY_SIZE,4):
        word=struct.unpack_from('<I',ram,pc-START)[0];op=word>>26
        if op in (1,4,5,6,7,8,9,10,11,12,13,14,15,20,21,22,23,
                  32,33,34,35,36,37,38,40,41,42,43,46,47,49,57):word&=0xffff0000
        elif op in (2,3):word&=0xfc000000
        words.append(word)
    return hashlib.sha256(struct.pack('<'+'I'*len(words),*words)).hexdigest()

def check_memcpy_exception(ram,expected,t,saved,specials,register_differences):
    """Explain only t1/t2 via the independently identified PPSSPP replacement.

    Native C still executes the original game helper. This extra reference run
    models PPSSPP's libc optimization only to explain its temporary-register
    outputs. Every observed difference remains visible in the report.
    """
    if not register_differences or not set(register_differences)<= {'t1','t2'}:return None
    signature=memcpy_signature(ram)
    if signature!=MEMCPY_NORMALIZED_SHA256:return None
    entry=t['entry']['registers'];end=t['return']['registers'];vm=VM(ram);calls=[]
    prepare_reference(vm)
    def replacement(v):
        dest,src,size=v.r[4:7]
        calls.append(dict(dest=f'{dest:08X}',src=f'{src:08X}',size=size,
                          t1=f'{v.r[9]:08X}',t2=f'{v.r[10]:08X}'))
        # PPSSPP handles overlapping copies in sixteen-byte blocks, then bytes.
        if min(dest,src)+size>max(dest,src):
            blocks=size&~15
            for offset in range(0,blocks,16):
                i=v.offset(dest+offset,16);v.mem[i:i+16]=v.read(src+offset,16)
            for offset in range(blocks,size):v.store(dest+offset,v.load(src+offset,1),1)
        else:
            i=v.offset(dest,size);v.mem[i:i+size]=v.read(src,size)
        v.set(2,dest)
    vm.callbacks[MEMCPY_ADDRESS]=replacement
    vm.callbacks[entry['ra']]=lambda v:v.set(31,0)
    try:
        vm.call(t['entry']['cpu']['pc'],registers=saved,sp=entry['sp'],special_registers=specials,max_steps=12000000)
    except RuntimeError as exc:return dict(established=False,error=str(exc))
    differences_after_replacement={n:[end[n],vm.r[i]] for i,n in enumerate(REG_NAMES) if i!=31 and end[n]!=vm.r[i]}
    lo=BASE-START;hi=lo+ENGINE_ALLOCATED_SIZE
    engine_matches=bytes(vm.mem[lo:hi])==expected[lo:hi]
    special_matches=vm.hi==end['hi'] and vm.lo==end['lo']
    established=bool(calls) and not differences_after_replacement and engine_matches and special_matches
    return dict(established=established,explained_registers=sorted(register_differences) if established else [],
                helper=f'{MEMCPY_ADDRESS:08X}',known_ppsspp_cityhash64='8C3FD997A544D0B1',normalized_signature=signature,
                replacement_calls=len(calls),last_replacement_calls=calls[-3:],
                replacement_oracle_register_differences=differences_after_replacement,
                replacement_oracle_engine_matches=engine_matches,replacement_oracle_hi_lo_matches=special_matches,
                evidence_report='reports/coverage_ppsspp_memcpy_exception.json',
                source='https://github.com/hrydgard/ppsspp/blob/v1.20.4/Core/HLE/ReplaceTables.cpp#L118')

def main():
    records=sys.argv[1:] or ['trial4_selector','full_tick_trial1','full_tick_trial2','full_tick_trial3']
    results=[];start=time.monotonic()
    for name in records:
        t=json.loads((ROOT/'traces'/f'{name}.json').read_text())
        ram=Path(t['entry']['memory']['file']).read_bytes();expected=Path(t['return']['memory']['file']).read_bytes()
        for stage,data in (('entry',ram),('return',expected)):
            digest=t[stage]['memory'].get('sha256')
            if digest and hashlib.sha256(data).hexdigest()!=digest:raise ValueError(f'{name} {stage} capture checksum differs from its trace')
        entry=t['entry']['registers'];end=t['return']['registers'];native=NativePort(ram,coverage=True)
        saved={i:entry[n] for i,n in enumerate(REG_NAMES)}
        floating=t['entry']['raw_registers']['categories'][1]['uintValues']
        specials=dict(hi=entry['hi'],lo=entry['lo'],f=floating[:32])
        native.callbacks[entry['ra']]=lambda v:v.set(31,0)
        result=native.call(t['entry']['cpu']['pc'],registers=saved,sp=entry['sp'],special_registers=specials,max_steps=12000000)
        actual=native.bytes();full_diff=differences(expected,actual)
        # Compare the entire allocated engine, including code, constants, data,
        # and BSS. Other PSP-thread memory is also compared and reported below.
        lo=BASE-START;hi=lo+ENGINE_ALLOCATED_SIZE
        engine_matches=actual[lo:hi]==expected[lo:hi]
        regs_diff={n:[end[n],native.r[i]] for i,n in enumerate(REG_NAMES) if i!=31 and end[n]!=native.r[i]}
        hi_lo_diff={n:[end[n],getattr(native.ctx,n)] for n in ('hi','lo') if end[n]!=getattr(native.ctx,n)}
        replacement=check_memcpy_exception(ram,expected,t,saved,specials,regs_diff)
        explained=set(replacement['explained_registers']) if replacement and replacement.get('established') else set()
        unexplained={n:v for n,v in regs_diff.items() if n not in explained}
        preserved={'s0','s1','s2','s3','s4','s5','s6','s7','sp','fp','gp','k0','k1'}
        return_registers={'v0','v1'}
        expected_f=t['return']['raw_registers']['categories'][1]['uintValues'][:32]
        f_diff={str(i):[v,native.ctx.f[i]] for i,v in enumerate(expected_f) if v!=native.ctx.f[i]}
        erased=NativePort(cleared_code(ram));erased.callbacks[entry['ra']]=lambda v:v.set(31,0)
        erased_result=erased.call(t['entry']['cpu']['pc'],registers=saved,sp=entry['sp'],special_registers=specials,max_steps=12000000)
        erased_matches=erased_result==result and erased.bytes()==cleared_code(actual)
        item=dict(capture=name,relative=f"{t['entry']['cpu']['pc']-BASE:06X}",live_return=end['v0'],native_return=result&0xffffffff,
            engine_allocated_range=dict(start=f'{BASE:08X}',end=f'{BASE+ENGINE_ALLOCATED_SIZE:08X}',byte_count=ENGINE_ALLOCATED_SIZE),
            engine_allocated_matches=engine_matches,engine_arena_matches=engine_matches,
            register_differences=regs_diff,unexplained_register_differences=unexplained,
            preserved_registers_match=not(set(regs_diff)&preserved),return_registers_match=not(set(regs_diff)&return_registers),
            strict_machine_registers_match=not regs_diff and not hi_lo_diff and not f_diff,
            memcpy_reference_exception=replacement,hi_lo_differences=hi_lo_diff,fpu_raw_register_differences=f_diff,
            full_ram_matches=not full_diff,full_ram_first_differences=full_diff,
            erased_instruction_state_matches=erased_matches,instruction_count=native.steps,
            services=dict(native.services))
        item['matches']=engine_matches and not unexplained and not hi_lo_diff and not f_diff and erased_matches
        results.append(item);print(name,'PASS' if item['matches'] else 'FAIL','fullRAM',not full_diff,flush=True)
    report=dict(capture_count=len(results),mismatch_count=sum(not r['matches'] for r in results),captures=results,
        seconds=round(time.monotonic()-start,2),method='Captured original GPR/FPU/HI/LO inputs and caller return breakpoint, versus compiled C on private memory. Source instructions also erased in separate native run.',
        criteria='Entire 1,139,456-byte engine allocation, ABI return/preserved registers, all remaining GPR/HI/LO/raw-FPU state, and erased-code execution. Raw t1/t2 differences are retained and may be explained only by a matching known memcpy signature plus a separate replacement oracle reproducing all captured GPRs and engine bytes.',
        limitations='Live board and captured ticks; not exhaustive games. Full RAM comparison additionally reports memory changes outside the allocated engine. An explained PPSSPP libc replacement difference does not mean exact original-instruction register equality.')
    (ROOT/'reports/native_live_validation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
    if report['mismatch_count']:raise SystemExit(1)
if __name__=='__main__':main()
