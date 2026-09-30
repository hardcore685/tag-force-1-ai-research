"""Independent primitive/branch checks for the Tag Force native port.

The integer oracle uses mathematical operations, and unaligned memory uses
byte-slice merging instead of the generator's bit-mask formulas. Allegrex DIV
edge results follow PPSSPP v1.20.4's verified PSP behavior, independently of
mips_vm.py. All state is synthetic private RAM; no emulator or saves are used.
"""
from __future__ import annotations
import argparse
import ctypes as C
import hashlib
import json
import random
import shutil
import struct
import subprocess
from collections import Counter
from pathlib import Path
import bisect

from generate_native_port import HEADER, emit_plain, fields, branch_kind
from native_ai import Context, NativePort

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
START=0x08800000
MASK=0xFFFFFFFF
SCRATCH=START+0x100


def s32(v):
    v &= MASK
    return v-(1<<32) if v & 0x80000000 else v


def rot(v,n):
    n %= 32
    return v if n==0 else ((v>>n)|(v<<(32-n)))&MASK


def rword(fn,rs=4,rt=5,rd=3,sa=0,op=0):
    return (op<<26)|(rs<<21)|(rt<<16)|(rd<<11)|(sa<<6)|fn


def iword(op,rs=4,rt=5,imm=0):
    return (op<<26)|(rs<<21)|(rt<<16)|(imm&0xFFFF)


def oracle(word,r,f,hi,lo,fcr,mem):
    """Execute one non-branch instruction, using an independent byte oracle."""
    op,rs,rt,rd,sa,fn,imm=fields(word)
    a,b=r[rs],r[rt]
    simm=imm-(1<<16) if imm&0x8000 else imm
    addr=(a+simm)&MASK
    result=None
    dest=rd
    if op==0:
        if fn==0:result=b<<sa
        elif fn==2:result=rot(b,sa) if rs==1 else b>>sa
        elif fn==3:result=s32(b)>>sa
        elif fn==4:result=b<<(a&31)
        elif fn==6:result=rot(b,a) if sa==1 else b>>(a&31)
        elif fn==7:result=s32(b)>>(a&31)
        elif fn in (10,11):
            if (b==0) == (fn==10):result=a
        elif fn==16:result=hi
        elif fn==17:hi=a
        elif fn==18:result=lo
        elif fn==19:lo=a
        elif fn in (22,23):
            value=a if fn==22 else (~a&MASK)
            result=32-value.bit_length()
        elif fn in (24,25,28,29,46,47):
            product=s32(a)*s32(b) if fn in (24,28,46) else a*b
            accum=(hi<<32)|lo
            value=product if fn in (24,25) else accum+product if fn in (28,29) else accum-product
            hi=(value>>32)&MASK
            lo=value&MASK
        elif fn in (26,27):
            if fn==26:
                x,y=s32(a),s32(b)
                if x==-(1<<31) and y==-1:lo=0x80000000;hi=MASK
                elif y==0:lo=1 if x<0 else MASK;hi=a
                else:
                    quotient=abs(x)//abs(y)
                    if (x<0)!=(y<0):quotient=-quotient
                    lo=quotient&MASK
                    hi=(x-quotient*y)&MASK
            elif b==0:lo=0xFFFF if a<=0xFFFF else MASK;hi=a
            else:lo=a//b;hi=a%b
        elif fn in (32,33):result=a+b
        elif fn in (34,35):result=a-b
        elif fn==36:result=a&b
        elif fn==37:result=a|b
        elif fn==38:result=a^b
        elif fn==39:result=~(a|b)
        elif fn==42:result=int(s32(a)<s32(b))
        elif fn==43:result=int(a<b)
        elif fn==44:result=max(s32(a),s32(b))
        elif fn==45:result=min(s32(a),s32(b))
        elif fn==15:pass
        else:raise ValueError(f"Unimplemented oracle SPECIAL {fn}")
    elif op in (8,9):dest=rt;result=a+simm
    elif op==10:dest=rt;result=int(s32(a)<simm)
    elif op==11:dest=rt;result=int(a<(simm&MASK))
    elif op in (12,13,14):
        dest=rt
        result={12:lambda:a&imm,13:lambda:a|imm,14:lambda:a^imm}[op]()
    elif op==15:dest=rt;result=imm<<16
    elif op==31:
        if fn==0:dest=rt;result=(a>>sa)&((1<<(rd+1))-1)
        elif fn==4:
            dest=rt
            width=rd-sa+1
            old=list(f"{b:032b}"[::-1])
            source=list(f"{a:032b}"[::-1])
            old[sa:sa+width]=source[:width]
            result=int(''.join(old[::-1]),2)
        elif fn==32:
            if sa==2:result=int.from_bytes(bytes([b>>8&255,b&255,b>>24&255,b>>16&255]),'little')
            elif sa==3:result=int.from_bytes(b.to_bytes(4,'little'),'big')
            elif sa==16:result=(b&255)-256 if b&128 else b&255
            elif sa==24:result=(b&65535)-65536 if b&32768 else b&65535
            elif sa==20:result=int(f"{b:032b}"[::-1],2)
            else:raise ValueError(f"BSHFL {sa}")
        else:raise ValueError(f"SPECIAL3 {fn}")
    elif op in (32,33,35,36,37,40,41,43,49,57,34,38,42,46):
        off=(addr&0x3FFFFFFF)-START
        if op in (34,38,42,46):
            aligned=off&~3;n=off&3
            mb=bytearray(mem[aligned:aligned+4])
            rb=bytearray(b.to_bytes(4,'little'))
            if op==34:rb[3-n:4]=mb[:n+1];dest=rt;result=int.from_bytes(rb,'little')
            elif op==38:rb[:4-n]=mb[n:4];dest=rt;result=int.from_bytes(rb,'little')
            elif op==42:mb[:n+1]=rb[3-n:4];mem[aligned:aligned+4]=mb
            else:mb[n:4]=rb[:4-n];mem[aligned:aligned+4]=mb
        elif op in (32,33,35,36,37):
            size={32:1,33:2,35:4,36:1,37:2}[op]
            dest=rt;result=int.from_bytes(mem[off:off+size],'little',signed=op in (32,33))
        elif op in (40,41,43):
            size={40:1,41:2,43:4}[op]
            mem[off:off+size]=(b&((1<<(8*size))-1)).to_bytes(size,'little')
        elif op==49:f[rt]=int.from_bytes(mem[off:off+4],'little')
        elif op==57:mem[off:off+4]=f[rt].to_bytes(4,'little')
    elif op==17:
        if rs==0:dest=rt;result=f[rd]
        elif rs==4:f[rd]=b
        elif rs==2:dest=rt;result=fcr if rd==31 else 0x3351 if rd==0 else 0
        elif rs==6:
            if rd==31:fcr=b&0x0181FFFF
        else:raise ValueError(f"Oracle only models FPU copies/control, rs={rs}")
    elif op==47:pass
    else:raise ValueError(f"Unimplemented oracle opcode {op}")
    if result is not None and dest:r[dest]=result&MASK
    r[0]=0
    return hi&MASK,lo&MASK,fcr


def cases():
    rows=[]
    for fn in (0,2,3,4,6,7,10,11,16,17,18,19,22,23,24,25,26,27,28,29,32,33,34,35,36,37,38,39,42,43,44,45,46,47):
        variants=(0,1,7,16,31) if fn in (0,2,3) else (0,)
        for sa in variants:rows.append((f"special_{fn}_sa{sa}",rword(fn,sa=sa)))
    for sa in (0,1,7,16,31):rows.append((f"rotr_{sa}",rword(2,rs=1,sa=sa)))
    rows.append(("rotrv",rword(6,sa=1)))
    for op in range(8,16):
        for imm in (0,1,0x7FFF,0x8000,0xFFFF):rows.append((f"immediate_{op}_{imm}",iword(op,imm=imm)))
    for pos,width in ((0,1),(0,32),(1,31),(7,9),(31,1)):
        rows.append((f"ext_{pos}_{width}",rword(0,rt=5,rd=width-1,sa=pos,op=31)))
        rows.append((f"ins_{pos}_{width}",rword(4,rt=5,rd=pos+width-1,sa=pos,op=31)))
    for sa in (2,3,16,20,24):rows.append((f"bshfl_{sa}",rword(32,sa=sa,op=31)))
    for op in (32,33,34,35,36,37,38,40,41,42,43,46,49,57):
        for imm in (-7,0,9):rows.append((f"memory_{op}_{imm}",iword(op,imm=imm)))
    rows.extend((name,word) for name,word in (
        ("mfc1",rword(0,rs=0,rt=5,rd=3,op=17)),
        ("mtc1",rword(0,rs=4,rt=5,rd=3,op=17)),
        ("cfc1_fir",rword(0,rs=2,rt=5,rd=0,op=17)),
        ("cfc1_fcr31",rword(0,rs=2,rt=5,rd=31,op=17)),
        ("ctc1_fcr31",rword(0,rs=6,rt=5,rd=31,op=17))))
    # Explicit overlap/destination-zero cases are distinct from normal game
    # routines and catch sequencing or zero-register handling mistakes.
    for fn in (0,2,3,4,6,7,10,11,32,35,36,37,38,42,44,45):
        rows.append((f"overlap_rd_rs_{fn}",rword(fn,rd=4)))
        rows.append((f"overlap_rd_rt_{fn}",rword(fn,rd=5)))
        rows.append((f"zero_dest_{fn}",rword(fn,rd=0)))
    return rows


def setup(ctx,mem,rng,word,trial):
    edge=(0,1,2,31,32,0x7FFF,0xFFFF,0x10000,0x7FFFFFFF,0x80000000,0xFFFFFFFE,0xFFFFFFFF)
    rr=[rng.getrandbits(32) for _ in range(32)];rr[0]=0
    rr[4]=edge[trial%len(edge)] if trial<24 else rng.getrandbits(32)
    rr[5]=edge[(trial//2)%len(edge)] if trial<24 else rng.getrandbits(32)
    # Include the two DIV edge cases absent in ordinary duel values.
    if trial==24:rr[4]=0x80000000;rr[5]=0xFFFFFFFF
    if trial==25:rr[4]=1;rr[5]=0
    if trial==26:rr[4]=0xFFFF;rr[5]=0
    if trial==27:rr[4]=0x10000;rr[5]=0
    ff=[rng.getrandbits(32) for _ in range(32)]
    ff[3]=(0x7FA00001,0x80000000,0x7FC00000,0xFFFFFFFF)[trial%4]
    op,rs,rt,rd,sa,fn,imm=fields(word)
    simm=imm-(1<<16) if imm&0x8000 else imm
    if op in (32,33,34,35,36,37,38,40,41,42,43,46,49,57):
        off=trial%4 if op in (34,38,42,46) else 0
        rr[rs]=(SCRATCH+off-simm)&MASK
    for i in range(32):ctx.r[i]=rr[i];ctx.f[i]=ff[i]
    ctx.hi=rng.getrandbits(32);ctx.lo=rng.getrandbits(32);ctx.fcr31=rng.getrandbits(32)
    ctx.pc=START+0x4000;ctx.error=0;ctx.fault_pc=ctx.fault_word=ctx.fault_address=ctx.resume_pc=0
    data=bytes(rng.getrandbits(8) for _ in range(len(mem)))
    mem[:]=data
    return rr,ff,ctx.hi,ctx.lo,ctx.fcr31,bytearray(data)


def diff(ctx,r,f,hi,lo,fcr,mem,expected):
    out={}
    for key,actual,wanted in (("r",list(ctx.r),r),("f",list(ctx.f),f),("hi",ctx.hi,hi),("lo",ctx.lo,lo),("fcr31",ctx.fcr31,fcr),("memory",bytes(mem),bytes(expected))):
        if actual!=wanted:
            if isinstance(actual,list):
                out[key]=[{"index":i,"actual":v,"expected":wanted[i]} for i,v in enumerate(actual) if v!=wanted[i]]
            elif isinstance(actual,bytes):
                changes=[{"offset":i,"actual":v,"expected":wanted[i]} for i,v in enumerate(actual) if v!=wanted[i]]
                out[key]=dict(changed_bytes=len(changes),first_changes=changes[:32])
            else:out[key]=dict(actual=actual,expected=wanted)
    return out


def plain_tests(folder,iterations):
    rows=cases();unsupported=[]
    lines=[HEADER,"TF_EXPORT void tf_semantic_unit(TFContext*c,unsigned index){switch(index){"]
    for index,(name,word) in enumerate(rows):
        missing=[];body=emit_plain(word,START+0x4000,missing)
        lines.append(f"case {index}:{{{body}return;}}")
        if missing:unsupported.append(dict(index=index,name=name,word=f"{word:08X}"))
    lines.append("default:tf_fail(c,TF_UNSUPPORTED,0,index);return;}}")
    source=folder/'semantic_units.c';source.write_text('\n'.join(lines),encoding='utf-8')
    dll=folder/'semantic_units.dll'
    compiler=json.loads((ROOT/'work/toolchain/paths.json').read_text())['zig_executable']
    built=subprocess.run([compiler,'cc','-std=c11','-O2','-fno-strict-aliasing','-ffp-contract=off','-shared',str(source),'-o',str(dll)],capture_output=True,text=True)
    if built.returncode:raise RuntimeError(built.stdout+built.stderr)
    library=C.CDLL(str(dll));library.tf_semantic_unit.argtypes=[C.POINTER(Context),C.c_uint]
    ctx=Context();mem=(C.c_uint8*1024)();ctx.mem=mem;ctx.mem_start=START;ctx.mem_size=len(mem)
    rng=random.Random(0x54464149);failed=[];passed=0;tested=Counter();fail_counts=Counter();missing_indices={x['index'] for x in unsupported}
    for index,(name,word) in enumerate(rows):
        if index in missing_indices:continue
        for trial in range(iterations):
            r,f,hi,lo,fcr,expected=setup(ctx,mem,rng,word,trial)
            hi,lo,fcr=oracle(word,r,f,hi,lo,fcr,expected)
            library.tf_semantic_unit(C.byref(ctx),index)
            differences=diff(ctx,r,f,hi,lo,fcr,mem,expected)
            if ctx.error or differences:
                fail_counts[name]+=1
                if fail_counts[name]<=3:failed.append(dict(name=name,word=f"{word:08X}",trial=trial,error=ctx.error,differences=differences))
            else:passed+=1
            tested[name]+=1
    return dict(test_count=sum(tested.values()),passed=passed,failed_count=sum(tested.values())-passed,
                failures=failed,failure_counts=dict(fail_counts),unsupported_families=unsupported,word_count=len(rows),iterations=iterations,
                source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),dll_sha256=hashlib.sha256(dll.read_bytes()).hexdigest())


def native_library(folder):
    # Read only the capture which generated the source port. A private DLL copy
    # avoids holding the parent's build file open while it rebuilds.
    dll=HERE/'build/tagforce_ai.dll'
    private=folder/'branch_game_code.dll';private.write_bytes(dll.read_bytes())
    return private


def game_data():
    ram=(ROOT/'work/ram_turn25.bin').read_bytes()
    analyses=json.loads((ROOT/'reports/module_analysis.json').read_text())[:1]
    analyses.append(json.loads((ROOT/'reports/modehsys_analysis.json').read_text()))
    return ram,analyses


def family(word):
    op,rs,rt,rd,sa,fn,imm=fields(word)
    if op==0:
        suffix=f"_sa{sa}" if fn in (0,2,3) else f"_rotate{sa==1}" if fn==6 else ''
        return f"special_{fn}"+suffix
    if op==31:return f"special3_{fn}_sa{sa}_rd{rd}"
    if op==17:return f"cop1_{rs}_fs{rd}"
    return f"op{op}"+(f"_negative{bool(imm&0x8000)}" if op in range(8,16) else '')


def instruction_tests(private,iterations):
    """Check actual compiled PCs for every encountered integer/copy family."""
    port=NativePort(bytes(1024),library=private)
    ram,analyses=game_data();candidates={};static_counts=Counter();skipped=Counter()
    owner_starts=[];owners={}
    for analysis in analyses:
        for func in analysis['functions']:owners[func['address']]=func
        for pc in range(analysis['base_address'],analysis['base_address']+analysis['text_size']-4,4):
            word=struct.unpack_from('<I',ram,pc-START)[0]
            if branch_kind(word):continue
            name=family(word);static_counts[name]+=1
            op,rs,rt,rd,sa,fn,imm=fields(word)
            if op==0 and fn in (12,13):skipped[name]+=1;continue
            if op in (32,33,34,35,36,37,38,40,41,42,43,46,49,57) and rs==0:skipped[name]+=1;continue
            try:oracle(word,[0]*32,[0]*32,0,0,0,bytearray(1024))
            except (ValueError,OverflowError):skipped[name]+=1;continue
            candidates.setdefault(name,(pc,word))
    owner_starts=sorted(owners)
    rng=random.Random(0x1AC7A11);passed=0;total=0;failures=[];fail_counts=Counter()
    for name,(pc,word) in candidates.items():
        for trial in range(iterations):
            ctx=port.ctx;r,f,h,l,fcr,mem=setup(ctx,port.mem,rng,word,trial)
            # Check uncached aliases without changing the byte slice oracle.
            op,rs,rt,rd,sa,fn,imm=fields(word)
            if op in (32,33,34,35,36,37,38,40,41,42,43,46,49,57) and trial&4:
                r[rs]|=0x40000000;ctx.r[rs]=r[rs]
            h,l,fcr=oracle(word,r,f,h,l,fcr,mem)
            ctx.pc=pc;ctx.steps=0;ctx.max_steps=1
            port.lib.tf_run(C.byref(ctx));total+=1
            differences=diff(ctx,r,f,h,l,fcr,port.mem,mem)
            if ctx.pc!=pc+4:differences['pc']=dict(actual=ctx.pc,expected=pc+4)
            if ctx.error!=4:differences['error']=ctx.error
            if differences:
                fail_counts[name]+=1
                if fail_counts[name]<=3:failures.append(dict(name=name,pc=f"{pc:08X}",word=f"{word:08X}",trial=trial,differences=differences))
            else:passed+=1
    targets={}
    visited_path=ROOT/'reports/native_covered_instructions.json'
    visited=set(json.loads(visited_path.read_text())) if visited_path.exists() else set()
    for name,(pc,word) in candidates.items():
        index=bisect.bisect_right(owner_starts,pc)-1
        own=owners[owner_starts[index]] if index>=0 else None
        if own and not own['address']<=pc<own['address']+own['size']:own=None
        targets[name]=dict(pc=f"{pc:08X}",word=f"{word:08X}",static_sites=static_counts[name],
            owner=f"{own['address']:08X}" if own else None,covered_in_duel_validation=pc in visited)
    return dict(test_count=total,passed=passed,failed_count=total-passed,failures=failures,
        failure_counts=dict(fail_counts),native_instruction_families=targets,
        skipped_families=dict(skipped),dll_sha256=hashlib.sha256(private.read_bytes()).hexdigest())


def branch_tests(private,iterations):
    port=NativePort(bytes(1024),library=private)
    ram,analyses=game_data()
    candidates={}
    for pc in (pc for analysis in analyses for pc in range(analysis['base_address'],analysis['base_address']+analysis['text_size']-4,4)):
        word=struct.unpack_from('<I',ram,pc-START)[0]
        kind=branch_kind(word)
        if not kind:continue
        op,rs,rt,rd,sa,fn,imm=fields(word)
        if op==1 and rt not in (0,1,2,3,16,17,18,19):continue
        delay=struct.unpack_from('<I',ram,pc+4-START)[0]
        if branch_kind(delay) or (delay>>26==0 and delay&63 in (12,13)):continue
        try:oracle(delay,[0]*32,[0]*32,0,0,0,bytearray(1024))
        except (ValueError,OverflowError):continue
        d=fields(delay)
        if d[0] in (32,33,34,35,36,37,38,40,41,42,43,46,49,57) and d[1]==0:continue
        key=f"op{op}_rt{rt}" if op in (1,17) else f"special_{fn}" if op==0 else f"op{op}"
        candidates.setdefault(key,(pc,word,delay))
    rng=random.Random(0xB12A4C);passed=0;total=0;failures=[];outcomes={}
    for name,(pc,word,delay) in candidates.items():
        for trial in range(iterations):
            ctx=port.ctx;r,f,h,l,fcr,mem=setup(ctx,port.mem,rng,delay,trial)
            op,rs,rt,rd,sa,fn,imm=fields(word)
            # Pure random equality almost never exercises a taken BEQ. Force
            # both outcomes and include signed zero/INT_MIN/INT_MAX boundaries.
            if op in (4,5,20,21) and rs!=rt:
                if rs:
                    r[rs]=r[rt] if trial%2==0 else r[rt]^1
                elif rt:r[rt]=0 if trial%2==0 else 1
            elif op in (1,6,7,22,23) and rs:
                r[rs]=(0,1,0xFFFFFFFF,0x80000000,0x7FFFFFFF)[trial%5]
            elif op==17:
                fcr=(fcr&~(1<<23))|((trial&1)<<23)
                ctx.fcr31=fcr
            for i in range(32):ctx.r[i]=r[i]
            if op==0:
                r[rs]=0;ctx.r[rs]=0;dest=0;likely=False;taken=True
                if fn==9 and rd:r[rd]=(pc+8)&MASK
            elif op in (2,3):
                dest=((pc+4)&0xF0000000)|((word&0x3FFFFFF)<<2);likely=False;taken=True
                if op==3:r[31]=(pc+8)&MASK
            else:
                if op==17:taken=bool(fcr&(1<<23))==bool(rt&1)
                elif op in (4,20):taken=r[rs]==r[rt]
                elif op in (5,21):taken=r[rs]!=r[rt]
                elif op in (6,22):taken=s32(r[rs])<=0
                elif op in (7,23):taken=s32(r[rs])>0
                else:
                    taken=s32(r[rs])<0 if rt in (0,2,16,18) else s32(r[rs])>=0
                    if rt in (16,17,18,19):r[31]=(pc+8)&MASK
                likely=op in (20,21,22,23) or (op==1 and rt in (2,3,18,19)) or (op==17 and bool(rt&2))
                disp=(imm-65536 if imm&0x8000 else imm)*4
                dest=(pc+4+disp)&MASK if taken else pc+8
            execute_delay=taken or not likely
            outcome=outcomes.setdefault(name,Counter())
            outcome['taken' if taken else 'not_taken']+=1
            outcome['delay_executed' if execute_delay else 'delay_annulled']+=1
            if execute_delay:h,l,fcr=oracle(delay,r,f,h,l,fcr,mem)
            ctx.pc=pc;ctx.steps=0;ctx.max_steps=1+int(execute_delay)
            port.lib.tf_run(C.byref(ctx));total+=1
            differences=diff(ctx,r,f,h,l,fcr,port.mem,mem)
            if ctx.pc!=dest:differences['pc']=dict(actual=ctx.pc,expected=dest)
            if ctx.error not in (0,4):differences['error']=ctx.error
            if differences:
                if len(failures)<20:failures.append(dict(name=name,pc=f"{pc:08X}",word=f"{word:08X}",delay=f"{delay:08X}",trial=trial,differences=differences))
            else:passed+=1
    return dict(test_count=total,passed=passed,failed_count=total-passed,failures=failures,
                real_branch_families={k:dict(pc=f"{v[0]:08X}",word=f"{v[1]:08X}",delay=f"{v[2]:08X}") for k,v in candidates.items()},
                outcomes={k:dict(v) for k,v in outcomes.items()},
                dll_sha256=hashlib.sha256(private.read_bytes()).hexdigest())


def wrapper_tests(private):
    """Exercise real calls/import stubs with zeroed private guest code bytes."""
    # The relocated native C must supply code. These private bytes are all zero,
    # so success also verifies that wrappers never fall back to code execution.
    port=NativePort(bytes(24*1024*1024),library=private)
    records=[]
    callback_pc=START+0x1000;stack=START+0x2000;args=tuple(range(11,21));seen=[]
    def synthetic(p):
        values=tuple(p.r[4:12])+tuple(p.load(p.r[29]+4*i) for i in range(2))
        seen.append(dict(args=list(values),ra=p.r[31],pc=p.pc))
        p.set(2,-7)
    port.callbacks={callback_pc:synthetic}
    result=port.call(callback_pc,*args,sp=stack,max_steps=20)
    records.append(dict(name='synthetic_callback_and_ten_arg_EABI',passed=result==-7 and len(seen)==1 and tuple(seen[0]['args'])==args,
        result=result,seen=seen))

    jal_pc=0x09E65C20;ram,_=game_data();word=struct.unpack_from('<I',ram,jal_pc-START)[0]
    target=((jal_pc+4)&0xF0000000)|((word&0x3FFFFFF)<<2);seen=[]
    def callee(p):
        seen.append(dict(stage='callee',pc=p.pc,ra=p.r[31]));p.set(2,77)
    def caller_return(p):
        seen.append(dict(stage='caller_resume',pc=p.pc,ra=p.r[31]));p.set(31,0)
    port.callbacks={target:callee,jal_pc+8:caller_return}
    result=port.call(jal_pc,sp=stack,max_steps=20)
    records.append(dict(name='JAL_callback_then_caller_resume',passed=result==77 and [x['stage'] for x in seen]==['callee','caller_resume'] and seen[0]['ra']==jal_pc+8,
        result=result,target=f'{target:08X}',seen=seen))

    port.callbacks={}
    previous=port.call(0x0886EE4C,sp=stack,max_steps=20)
    irq_after_suspend=port.irq_enabled
    previous2=port.call(0x0886EE4C,sp=stack,max_steps=20)
    port.call(0x0886EE54,1,sp=stack,max_steps=20)
    irq_after_resume=port.irq_enabled
    port.call(0x0886EE54,0,sp=stack,max_steps=20)
    records.append(dict(name='interrupt_stubs_use_captured_delay_return',passed=previous==1 and previous2==0 and irq_after_suspend==0 and irq_after_resume==1 and port.irq_enabled==0,
        first_suspend_return=previous,second_suspend_return=previous2,irq_after_resume=irq_after_resume,services=dict(port.services)))
    for name,pc,args in (('unimplemented_network_fails_explicitly',0x0886F334,(1,0)),('unimplemented_file_read_fails_explicitly',0x0886ED7C,(0,START+0x3000,4))):
        try:port.call(pc,*args,sp=stack,max_steps=20);failure=None
        except RuntimeError as exc:failure=str(exc)
        records.append(dict(name=name,passed=failure is not None and 'Native port error 3' in failure,detail=failure))
    port.callbacks={jal_pc+4:lambda p:p.set(2,123)}
    try:port.call(jal_pc,sp=stack,max_steps=20);failure=None
    except (ValueError,RuntimeError) as exc:failure=str(exc)
    records.append(dict(name='delay_slot_callback_rejected',passed=failure is not None and 'delay' in failure.lower(),detail=failure,
        contract='Native callback APIs accept function entries. Underlying C callers must observe the same contract.'))
    return dict(test_count=len(records),passed=sum(r['passed'] for r in records),failed_count=sum(not r['passed'] for r in records),cases=records)


def memory_copy_tests(private):
    """Whole original libc helper, including unvisited unaligned-copy paths."""
    port=NativePort(bytes(1024),library=private)
    lengths=(0,1,2,3,4,5,7,8,15,16,17,31,32,33,63,64,65,127,128,129,255,256)
    failures=[];total=0;passed=0;steps=set()
    for src_align in range(4):
        for dest_align in range(4):
            for length in lengths:
                for alias in (0,0x40000000):
                    before=bytes((i*13+71)&255 for i in range(1024));port.mem[:]=before
                    src=START+0x80+src_align;dest=START+0x240+dest_align
                    expected=bytearray(before);expected[dest-START:dest-START+length]=before[src-START:src-START+length]
                    count=port.steps;result=port.call(0x0880DD1C,dest|alias,src|alias,length,sp=START+0x3C0,max_steps=10000)
                    steps.add(port.steps-count);total+=1
                    if bytes(port.mem)==expected and result==dest|alias:passed+=1
                    elif len(failures)<12:failures.append(dict(src_alignment=src_align,dest_alignment=dest_align,length=length,alias=f'{alias:08X}',result=result,expected_return=dest|alias,memory_matches=bytes(port.mem)==expected))
    return dict(test_count=total,passed=passed,failed_count=total-passed,failures=failures,
        helper_address='0880DD1C',helper_name='memcpy',lengths=list(lengths),src_alignments=4,dest_alignments=4,
        cached_and_uncached_aliases=True,minimum_instructions=min(steps),maximum_instructions=max(steps),
        oracle='Independent immutable source bytes copied into destination; every byte outside destination must be unchanged. Nonoverlapping buffers only.')


def static_review():
    manifest_path=HERE/'generated/generation_manifest.json'
    manifest=json.loads(manifest_path.read_text())
    closure=json.loads((ROOT/'reports/coverage_hsys_instruction_closure.json').read_text())
    helper_pcs={int(pc,16) for pc in closure['instruction_addresses']}
    unsupported={entry['address'] for entry in manifest['unsupported_instructions']}
    engine_lo=0x09E65C00;engine_hi=engine_lo+0x0FDBA0
    raw,analyses=game_data();delay_branches=[];special2_counts={}
    for module in analyses:
        lo=module['base_address'];hi=lo+module['text_size'];op28=0
        for pc in range(lo,hi,4):
            word=struct.unpack_from('<I',raw,pc-START)[0]
            if word>>26==28:op28+=1
            if not branch_kind(word):continue
            delay=struct.unpack_from('<I',raw,pc+4-START)[0]
            if branch_kind(delay) and (engine_lo<=pc<engine_hi or pc in helper_pcs):delay_branches.append(f'{pc:08X}')
        special2_counts[module['name']]=op28
    return dict(translated_instructions=manifest['translated_instruction_count'],
        generation_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        unsupported_instruction_locations=len(unsupported),
        unsupported_engine_locations=sorted(f'{pc:08X}' for pc in unsupported if engine_lo<=pc<engine_hi),
        audited_helper_instruction_count=len(helper_pcs),
        unsupported_audited_helper_locations=sorted(f'{pc:08X}' for pc in unsupported&helper_pcs),
        branch_in_delay_slot_locations=delay_branches,actual_special2_opcode28_counts=special2_counts,
        corrected_findings=[
            'PSP EABI arguments after the eight argument GPRs start at SP+0.',
            'Allegrex signed DIV(INT_MIN,-1) leaves HI=FFFFFFFF; DIVU by zero produces LO=FFFF for dividends <=FFFF.',
            'Allegrex CLZ/CLO and multiply-accumulate operations use SPECIAL function encodings, not generic SPECIAL2.',
            'BITREV is BSHFL shift field20; CFC1 FIR reads3351; CTC1 FCR31 masks0181FFFF.',
            'Unvalidated scalar FP arithmetic now fails explicitly; audited AI paths use raw FPU copies only.'
        ],
        obligations=[
            'The static helper closure uses captured FILE callbacks; custom callback registrations require separate validation.',
            'Only confirmed interrupt services are implemented; other reachable PSP services fail explicitly unless separately bridged.',
            'Callbacks follow a function-entry-only contract; the Python wrapper rejects compiled delay-slot addresses, and direct C callers must observe the same contract.',
            'Loaded module coverage and primitive equivalence do not prove every card or complete duel state has been behaviorally tested.'
        ])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--iterations',type=int,default=64);parser.add_argument('--skip-branches',action='store_true')
    args=parser.parse_args()
    folder=ROOT/'work/audit_native_semantics';folder.mkdir(exist_ok=True)
    report=dict(oracle_sources=[
        'https://github.com/hrydgard/ppsspp/blob/v1.20.4/Core/MIPS/MIPSInt.cpp',
        'https://github.com/hrydgard/ppsspp/blob/v1.20.4/Core/MIPS/MIPS.h',
        'https://github.com/hrydgard/ppsspp/blob/v1.20.4/Core/MIPS/MIPSTables.cpp',
        'https://github.com/kotcrab/ghidra-allegrex/blob/v21.4/data/languages/allegrex.cspec'],
        primitive_tests=plain_tests(folder,args.iterations),static_review=static_review())
    if not args.skip_branches:
        private=native_library(folder)
        report['native_instruction_tests']=instruction_tests(private,args.iterations)
        report['branch_tests']=branch_tests(private,args.iterations)
        report['wrapper_tests']=wrapper_tests(private)
        report['whole_memcpy_tests']=memory_copy_tests(private)
    report['limitations']=['These checks validate primitive translation and selected real branch encodings, not every card/rule or every full duel.',
                          'Scalar FPU arithmetic, PSP timing, external files, network and dialog services require separate validation. Raw FPU bit copies are checked.']
    path=ROOT/'reports/coverage_native_review.json';path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:{x:v for x,v in value.items() if x not in ('failures','real_branch_families','native_instruction_families','skipped_families','outcomes','cases')} for k,value in report.items() if isinstance(value,dict)},indent=2))
    if any(value.get('failed_count',0) for value in report.values() if isinstance(value,dict)):raise SystemExit(1)


if __name__=='__main__':main()
