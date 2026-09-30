"""Generate native C from all engine instructions, with PSP register semantics.

Generated code executes expressions and C branches; it never fetches or decodes
MIPS instructions at runtime. The input snapshot supplies relocated constants.
Unknown hardware instructions stop explicitly. This is a low-level source port,
not a claim that the decompiler recovered Konami's original source.
"""
from __future__ import annotations
import json,struct,hashlib,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
OUT=HERE/'generated'
START=0x08800000
PAGE=0x1000

HEADER=r'''#ifndef TF_NATIVE_H
#define TF_NATIVE_H
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <math.h>
#ifdef _WIN32
#define TF_EXPORT __declspec(dllexport)
#else
#define TF_EXPORT __attribute__((visibility("default")))
#endif
typedef struct TFContext {
    uint32_t r[32], f[32], hi, lo, pc, fcr31;
    uint32_t error, fault_pc, fault_word, fault_address, resume_pc;
    uint64_t steps, max_steps;
    uint8_t *mem;
    uint32_t mem_start, mem_size;
    uint32_t *coverage;
    uint32_t coverage_size;
    uint8_t *hooks;
    uint32_t hook_size;
} TFContext;
enum { TF_OK=0, TF_MEMORY=1, TF_UNSUPPORTED=2, TF_SYSCALL=3,
       TF_BUDGET=4, TF_BRANCH_SLOT=5, TF_CODE_ADDRESS=6, TF_HOOK=7 };
static inline void tf_fail(TFContext*c,uint32_t error,uint32_t word,uint32_t address){
    if(!c->error){c->error=error;c->fault_pc=c->pc;c->fault_word=word;c->fault_address=address;}
}
static inline uint32_t tf_offset(TFContext*c,uint32_t a,uint32_t n){
    a &= 0x3fffffffu;
    if(a<c->mem_start || n>c->mem_size || a-c->mem_start>c->mem_size-n){
        tf_fail(c,TF_MEMORY,0,a); return UINT32_MAX;
    }
    return a-c->mem_start;
}
static inline uint32_t tf_load(TFContext*c,uint32_t a,unsigned n){
    uint32_t i=tf_offset(c,a,n),v=0;
    if(i==UINT32_MAX)return 0;
    for(unsigned j=0;j<n;j++)v|=(uint32_t)c->mem[i+j]<<(j*8);
    return v;
}
static inline void tf_store(TFContext*c,uint32_t a,uint32_t v,unsigned n){
    uint32_t i=tf_offset(c,a,n);if(i==UINT32_MAX)return;
    for(unsigned j=0;j<n;j++)c->mem[i+j]=(uint8_t)(v>>(j*8));
}
static inline int tf_tick(TFContext*c,uint32_t pc){
    c->pc=pc;
    if(c->error)return 1;
    if(c->hooks){uint32_t i=(pc-c->mem_start)/4;if(i<c->hook_size&&c->hooks[i]){tf_fail(c,TF_HOOK,0,pc);return 1;}}
    if(c->steps>=c->max_steps){tf_fail(c,TF_BUDGET,0,pc);return 1;}
    c->steps++;
    if(c->coverage){uint32_t i=(pc-c->mem_start)/4;if(i<c->coverage_size)c->coverage[i]++;}
    return 0;
}
static inline uint32_t tf_rot(uint32_t v,unsigned s){return s?(v>>s)|(v<<(32-s)):v;}
static inline uint32_t tf_clz(uint32_t v){uint32_t n=0;if(!v)return 32;while(!(v&0x80000000u)){n++;v<<=1;}return n;}
static inline uint32_t tf_swap16(uint32_t v){return ((v&0x00ff00ffu)<<8)|((v&0xff00ff00u)>>8);}
static inline uint32_t tf_swap32(uint32_t v){return tf_rot(tf_swap16(v),16);}
static inline uint32_t tf_bitrev(uint32_t v){uint32_t out=0;for(unsigned i=0;i<32;i++){out=(out<<1)|(v&1u);v>>=1;}return out;}
static inline void tf_div(TFContext*c,uint32_t ua,uint32_t ub,int sign){
    if(sign){int64_t a=(int32_t)ua,b=(int32_t)ub;
        if(a==INT32_MIN&&b== -1){c->lo=0x80000000u;c->hi=UINT32_MAX;}
        else if(b){int64_t q=a/b;c->lo=(uint32_t)q;c->hi=(uint32_t)(a-q*b);}
        else{c->lo=a<0?1u:UINT32_MAX;c->hi=ua;}
    }else if(ub){c->lo=ua/ub;c->hi=ua%ub;}else{c->lo=ua<=65535u?65535u:UINT32_MAX;c->hi=ua;}
}
static inline float tf_float(uint32_t v){float f;memcpy(&f,&v,4);return f;}
static inline uint32_t tf_bits(float f){uint32_t v;memcpy(&v,&f,4);return v;}
static inline uint32_t tf_cvt(float x,unsigned mode){
    double y=mode==1?trunc((double)x):mode==2?ceil((double)x):mode==3?floor((double)x):nearbyint((double)x);
    if(!isfinite(y)||y>2147483647.0||y< -2147483648.0)return 0x80000000u;
    return (uint32_t)(int32_t)y;
}
TF_EXPORT uint32_t tf_run(TFContext *ctx);
TF_EXPORT uint32_t tf_abi_version(void);
#endif
'''

def fields(w):return w>>26,(w>>21)&31,(w>>16)&31,(w>>11)&31,(w>>6)&31,w&63,w&65535
def sx(v,n):return v-(1<<n) if v&(1<<(n-1)) else v
def rr(i):return '0u' if i==0 else f'c->r[{i}]'
def assign(i,v):return f'c->r[{i}]=(uint32_t)({v});' if i else '(void)0;'
def constant(v):return f'0x{v&0xffffffff:08X}u'
def branch_kind(w):
    op,rs,rt,rd,sa,fn,imm=fields(w)
    if op==0 and fn in (8,9):return 'jr'
    if op in (2,3):return 'j'
    if op in (1,4,5,6,7,20,21,22,23):return 'b'
    if op==17 and rs==8:return 'fb'
    return None

def emit_plain(w,pc,unsupported):
    op,rs,rt,rd,sa,fn,imm=fields(w);simm=sx(imm,16)
    a,b=rr(rs),rr(rt);addr=f'({a}+{constant(simm)})'
    fail=f'tf_fail(c,TF_UNSUPPORTED,{constant(w)},0);return;'
    s=None
    if op==0:
        if fn==0:s=assign(rd,f'{b}<<{sa}')
        elif fn==2:s=assign(rd,f'tf_rot({b},{sa})' if rs==1 else f'{b}>>{sa}')
        elif fn==3:s=assign(rd,f'(int32_t){b}>>{sa}')
        elif fn==4:s=assign(rd,f'{b}<<({a}&31u)')
        elif fn==6:s=assign(rd,f'tf_rot({b},{a}&31u)' if sa==1 else f'{b}>>({a}&31u)')
        elif fn==7:s=assign(rd,f'(int32_t){b}>>({a}&31u)')
        elif fn in (10,11):s=f'if({b}{"==" if fn==10 else "!="}0u){{{assign(rd,a)}}}'
        elif fn==16:s=assign(rd,'c->hi')
        elif fn==17:s=f'c->hi={a};'
        elif fn==18:s=assign(rd,'c->lo')
        elif fn==19:s=f'c->lo={a};'
        elif fn in (22,23):s=assign(rd,f'tf_clz({a if fn==22 else "~"+a})')
        elif fn in (24,25):
            v=f'(uint64_t)((int64_t)(int32_t){a}*(int64_t)(int32_t){b})' if fn==24 else f'(uint64_t){a}*{b}'
            s=f'{{uint64_t v={v};c->lo=(uint32_t)v;c->hi=(uint32_t)(v>>32);}}'
        elif fn in (26,27):s=f'tf_div(c,{a},{b},{int(fn==26)});'
        elif fn in (28,29,46,47):
            v=f'(uint64_t)((int64_t)(int32_t){a}*(int64_t)(int32_t){b})' if fn in (28,46) else f'(uint64_t){a}*{b}'
            s=f'{{uint64_t v=((uint64_t)c->hi<<32|c->lo){"+" if fn in (28,29) else "-"}({v});c->lo=(uint32_t)v;c->hi=(uint32_t)(v>>32);}}'
        elif fn in (32,33):s=assign(rd,f'{a}+{b}')
        elif fn in (34,35):s=assign(rd,f'{a}-{b}')
        elif fn in (36,37,38):s=assign(rd,f'{a}'+{36:'&',37:'|',38:'^'}[fn]+b)
        elif fn==39:s=assign(rd,f'~({a}|{b})')
        elif fn==42:s=assign(rd,f'(int32_t){a}<(int32_t){b}')
        elif fn==43:s=assign(rd,f'{a}<{b}')
        elif fn in (44,45):s=assign(rd,f'((int32_t){a}{">" if fn==44 else "<"}(int32_t){b})?{a}:{b}')
        elif fn in (12,13):s=f'tf_fail(c,TF_SYSCALL,{constant(w)},0);return;'
        elif fn==15:s='(void)0; /* Single-thread memory barrier. */'
    elif op in (8,9):s=assign(rt,f'{a}+{constant(simm)}')
    elif op==10:s=assign(rt,f'(int32_t){a}<{simm}')
    elif op==11:s=assign(rt,f'{a}<{constant(simm)}')
    elif op in (12,13,14):s=assign(rt,f'{a}'+{12:'&',13:'|',14:'^'}[op]+constant(imm))
    elif op==15:s=assign(rt,constant(imm<<16))
    elif op==28:
        # Allegrex SPECIAL2 is HALT/MFIC/MTIC, not generic MIPS MUL/CLZ.
        # No SPECIAL2 instruction exists in this game's engine/helper text.
        s=None
    elif op==31:
        if fn==0:s=assign(rt,f'({a}>>{sa})&{constant((1<<(rd+1))-1)}')
        elif fn==4:
            mask=((1<<(rd-sa+1))-1)<<sa if rd>=sa else 0
            s=assign(rt,f'({b}&{constant(~mask)})|(({a}<<{sa})&{constant(mask)})')
        elif fn==32:
            if sa==2:s=assign(rd,f'tf_swap16({b})')
            elif sa==3:s=assign(rd,f'tf_swap32({b})')
            elif sa==16:s=assign(rd,f'(int8_t)({b}&255u)')
            elif sa==20:s=assign(rd,f'tf_bitrev({b})')
            elif sa==24:s=assign(rd,f'(int16_t)({b}&65535u)')
    elif op in (32,33,35,36,37):
        n={32:1,33:2,35:4,36:1,37:2}[op]
        cast={32:'(int8_t)',33:'(int16_t)'}.get(op,'')
        s=assign(rt,f'{cast}tf_load(c,{addr},{n})')
    elif op in (40,41,43):s=f'tf_store(c,{addr},{b},{dict([(40,1),(41,2),(43,4)])[op]});'
    elif op in (34,38):
        if op==34:v=f'({b}&(n==3?0u:(UINT32_MAX>>(8+8*n))))|(v<<(24-8*n))'
        else:v=f'({b}&(n?(UINT32_MAX<<(32-8*n)):0u))|(v>>(8*n))'
        s='{uint32_t a='+addr+',n=a&3u,v=tf_load(c,a&~3u,4);'+assign(rt,v)+'}'
    elif op in (42,46):
        if op==42:v=f'(v&(n==3?0u:(UINT32_MAX<<(8+8*n))))|({b}>>(24-8*n))'
        else:v=f'(v&(n?(UINT32_MAX>>(32-8*n)):0u))|({b}<<(8*n))'
        s='{uint32_t a='+addr+',n=a&3u,v=tf_load(c,a&~3u,4);tf_store(c,a&~3u,'+v+',4);}'
    elif op==49:s=f'c->f[{rt}]=tf_load(c,{addr},4);'
    elif op==57:s=f'tf_store(c,{addr},c->f[{rt}],4);'
    elif op==47:s='(void)0; /* Cache hint: no guest cache in this native port. */'
    elif op==17:
        fs=rd;ft=rt;fd=sa
        x=f'tf_float(c->f[{fs}])';y=f'tf_float(c->f[{ft}])'
        if rs==0:s=assign(rt,f'c->f[{fs}]')
        elif rs==4:s=f'c->f[{fs}]={b};'
        elif rs==2:s=assign(rt,'c->fcr31' if fs==31 else '0x00003351u' if fs==0 else '0u')
        elif rs==6:s=f'c->fcr31={b}&0x0181FFFFu;' if fs==31 else '(void)0;'
        # Scalar floating arithmetic is outside the audited AI closure. Fail
        # explicitly rather than substituting host rounding/NaN semantics.
    if s is None:
        unsupported.append(dict(address=pc,word=w,opcode=op,rs=rs,rt=rt,function=fn))
        s=fail
    return s

def generate(ram):
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'tf_native.h').write_text(HEADER)
    modules=[('eng',0x09E65C00,'modduel_eng'),('hsys',0x08804000,'modehsys')]
    analyses=json.loads((ROOT/'reports/module_analysis.json').read_text())
    analyses.append(json.loads((ROOT/'reports/modehsys_analysis.json').read_text()))
    banks={};ranges=[]
    for prefix,base,name in modules:
        m=next(x for x in analyses if x['name']==name)
        intervals=[(base,base+m['text_size'])]
        intervals.extend((base+i['stub_address'],base+i['stub_address']+8) for i in m['imports'])
        intervals.sort()
        for lo,hi in intervals:
            ranges.append(dict(module=name,start=lo,end=hi))
            for pc in range(lo,hi,4):
                word=struct.unpack_from('<I',ram,pc-START)[0]
                banks.setdefault(pc&~(PAGE-1),{})[pc]=word
    unsupported=[];count=0;files=[];decl=[];switch=[]
    for group in range(0,len(banks),16):
        selected=sorted(banks)[group:group+16]
        path=OUT/f'bank_group_{group//16:03d}.c';files.append(path.name)
        lines=['/* Automatically translated native source; see generation_manifest.json. */','#include "tf_native.h"']
        for page in selected:
            name=f'tf_bank_{page:08X}';decl.append(f'void {name}(TFContext*c);')
            switch.append(f'case {constant(page)}:{name}(c);break;')
            words=banks[page];lines.extend([f'void {name}(TFContext*c){{','switch(c->pc){'])
            for pc in sorted(words):lines.append(f'case {constant(pc)}:goto L_{pc:08X};')
            lines.append('default:tf_fail(c,TF_CODE_ADDRESS,0,c->pc);return;}')
            for pc,w in sorted(words.items()):
                count+=1;lines.append(f'L_{pc:08X}: if(tf_tick(c,{constant(pc)}))return;')
                kind=branch_kind(w)
                if not kind:
                    lines.append(emit_plain(w,pc,unsupported))
                    if pc+4 not in words:lines.append(f'c->pc={constant(pc+4)};return;')
                    continue
                op,rs,rt,rd,sa,fn,imm=fields(w)
                likely=False;condition=None;link=None
                if kind=='jr':
                    target=rr(rs)
                    if fn==9:link=assign(rd,constant(pc+8))
                elif kind=='j':
                    target=constant(((pc+4)&0xf0000000)|((w&0x3ffffff)<<2))
                    if op==3:link=assign(31,constant(pc+8))
                else:
                    target=constant(pc+4+sx(imm,16)*4)
                    if op in (4,20):condition=f'{rr(rs)}=={rr(rt)}'
                    elif op in (5,21):condition=f'{rr(rs)}!={rr(rt)}'
                    elif op in (6,22):condition=f'(int32_t){rr(rs)}<=0'
                    elif op in (7,23):condition=f'(int32_t){rr(rs)}>0'
                    elif op==1:
                        if rt in (0,2,16,18):condition=f'(int32_t){rr(rs)}<0'
                        elif rt in (1,3,17,19):condition=f'(int32_t){rr(rs)}>=0'
                        else:condition=None
                        likely=rt in (2,3,18,19)
                        if rt in (16,17,18,19):link=assign(31,constant(pc+8))
                    elif kind=='fb':condition=f'((c->fcr31>>23)&1u){"!=" if rt&1 else "=="}0u';likely=bool(rt&2)
                    if op in (20,21,22,23):likely=True
                if kind in ('b','fb') and condition is None:
                    unsupported.append(dict(address=pc,word=w,opcode=op,reason='unsupported branch'))
                    lines.append(f'tf_fail(c,TF_UNSUPPORTED,{constant(w)},0);return;');continue
                lines.append('{')
                if condition:lines.append(f'uint32_t taken=({condition});uint32_t dest=taken?{target}:{constant(pc+8)};')
                else:lines.append(f'uint32_t dest={target};')
                if link:lines.append(link)
                lines.append('c->resume_pc=dest;')
                if likely:lines.append('if(taken){')
                dw=struct.unpack_from('<I',ram,pc+4-START)[0]
                lines.append(f'if(tf_tick(c,{constant(pc+4)}))return;')
                if branch_kind(dw):lines.append(f'tf_fail(c,TF_BRANCH_SLOT,{constant(dw)},0);return;')
                else:lines.append(emit_plain(dw,pc+4,unsupported))
                if likely:lines.append('}')
                lines.extend(['c->pc=dest;return;','}'])
            lines.append('}')
        path.write_text('\n'.join(lines)+'\n')
    dispatcher='\n'.join(['#include "tf_native.h"',*decl,
        'TF_EXPORT uint32_t tf_abi_version(void){return 2;}',
        'TF_EXPORT uint32_t tf_run(TFContext*c){',
        'while(c->pc && !c->error){c->r[0]=0;',
        'if(c->hooks){uint32_t i=(c->pc-c->mem_start)/4;if(i<c->hook_size&&c->hooks[i]){tf_fail(c,TF_HOOK,0,c->pc);break;}}',
        'switch(c->pc&0xFFFFF000u){',*switch,
        'default:tf_fail(c,TF_CODE_ADDRESS,0,c->pc);break;}}return c->error;}'])
    (OUT/'tf_dispatch.c').write_text(dispatcher)
    unsupported={x['address']:x for x in unsupported}
    manifest=dict(input_ram_sha256=hashlib.sha256(ram).hexdigest(),input_ram_start=START,
        ranges=ranges,translated_instruction_count=count,bank_count=len(banks),
        source_files=files+['tf_dispatch.c'],unsupported_instructions=list(unsupported.values()),
        method='All engine instructions and import stubs translated to native C; hsys helper code included. Runtime does not fetch or decode instruction words. Unsupported hardware/kernel calls stop explicitly.',
        status='Generated; compilation and behavioral validation required.')
    (OUT/'generation_manifest.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps({k:manifest[k] for k in ('translated_instruction_count','bank_count','input_ram_sha256')},indent=2))
    print('Unsupported instruction locations',len(unsupported))
if __name__=='__main__':
    p=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'work/ram_turn25.bin'
    generate(p.read_bytes())
