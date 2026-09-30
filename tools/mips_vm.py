"""Small, deterministic MIPS/Allegrex integer interpreter for local AI experiments.

This is analysis code, not a PSP emulator: no graphics, audio, kernel, timing,
network, or syscall implementations. Unsupported instructions fail explicitly.
All stores modify a private bytearray copied from a local RAM capture.
The captured game code is supplied by the user; it is not embedded in this file.
"""
from __future__ import annotations
import struct
from collections import Counter

MASK=0xFFFFFFFF
def signed(v):
    v &= MASK
    return v-0x100000000 if v & 0x80000000 else v
def sx(v,bits):
    return v-(1<<bits) if v & (1<<(bits-1)) else v

class UnsupportedInstruction(RuntimeError): pass

class VM:
    def __init__(self,ram:bytes,start=0x08800000):
        self.mem=bytearray(ram); self.start=start
        self.r=[0]*32; self.f=[0]*32; self.hi=self.lo=0; self.pc=0; self.pending=None
        self.steps=0; self.visits=Counter(); self.observer=None
        self.callbacks={}; self.recent=[]

    def offset(self,a,n=1):
        a &= 0x3FFFFFFF
        i=a-self.start
        if i<0 or i+n>len(self.mem):
            raise RuntimeError(f'Unmapped memory 0x{a:08X} ({n} bytes), PC 0x{self.pc:08X}')
        return i
    def read(self,a,n):
        i=self.offset(a,n); return bytes(self.mem[i:i+n])
    def load(self,a,n,sign=False):
        i=self.offset(a,n)
        v=int.from_bytes(self.mem[i:i+n],'little')
        return sx(v,n*8) if sign else v
    def store(self,a,v,n=4):
        i=self.offset(a,n); self.mem[i:i+n]=(v & ((1<<(n*8))-1)).to_bytes(n,'little')
    def set(self,i,v):
        if i:self.r[i]=v & MASK

    def call(self,address,*args,sp=0x09FFE000,max_steps=2000000,registers=None,special_registers=None):
        self.r=[0]*32; self.f=[0]*32; self.hi=self.lo=0; self.pending=None
        if special_registers:
            self.hi=special_registers.get('hi',0)&MASK;self.lo=special_registers.get('lo',0)&MASK
            for i,v in enumerate(special_registers.get('f',[])):self.f[i]=v&MASK
        self.r[29]=sp; self.r[31]=0
        if registers:
            for i,v in registers.items():self.set(i,v)
        for i,a in enumerate(args):
            if i<8:self.set(4+i,a)
            else:self.store(sp+4*(i-8),a)
        self.pc=address; before=self.steps
        while self.pc:
            if self.steps-before>=max_steps:
                raise RuntimeError(f'Instruction budget exceeded at 0x{self.pc:08X}')
            self.step()
        return signed(self.r[2])

    def step(self):
        pc=self.pc
        if self.observer:self.observer(self,pc)
        if pc in self.callbacks:
            if self.pending is not None:raise RuntimeError('Callback in a delay slot')
            self.callbacks[pc](self)
            self.pc=self.r[31]; return
        w=self.load(pc,4); self.steps+=1; self.visits[pc]+=1
        self.recent.append(pc)
        if len(self.recent)>32:self.recent.pop(0)
        op=w>>26; rs=(w>>21)&31; rt=(w>>16)&31; rd=(w>>11)&31; sa=(w>>6)&31; fn=w&63
        imm=w&65535; simm=sx(imm,16); r=self.r
        prior=self.pending; self.pending=None
        next_pc=(pc+4)&MASK
        def branch(condition,likely=False,link=False):
            nonlocal next_pc
            if link:self.set(31,pc+8)
            if condition:self.pending=(pc+4+simm*4)&MASK
            elif likely:next_pc=pc+8
        if op==0:
            if fn==0:self.set(rd,r[rt]<<sa)
            elif fn==2:
                self.set(rd,((r[rt]>>sa)|(r[rt]<<(32-sa))) if rs==1 and sa else r[rt]>>sa)
            elif fn==3:self.set(rd,signed(r[rt])>>sa)
            elif fn==4:self.set(rd,r[rt]<<(r[rs]&31))
            elif fn==6:
                n=r[rs]&31
                self.set(rd,((r[rt]>>n)|(r[rt]<<(32-n))) if sa==1 and n else r[rt]>>n)
            elif fn==7:self.set(rd,signed(r[rt])>>(r[rs]&31))
            elif fn in (8,9):
                target=r[rs]
                if fn==9:self.set(rd,pc+8)
                self.pending=target
            elif fn==10:
                if r[rt]==0:self.set(rd,r[rs])
            elif fn==11:
                if r[rt]!=0:self.set(rd,r[rs])
            elif fn==16:self.set(rd,self.hi)
            elif fn==17:self.hi=r[rs]
            elif fn==18:self.set(rd,self.lo)
            elif fn==19:self.lo=r[rs]
            elif fn in (22,23):
                v=r[rs] if fn==22 else (~r[rs]&MASK)
                self.set(rd,32-v.bit_length())
            elif fn in (24,25):
                v=(signed(r[rs])*signed(r[rt])) if fn==24 else r[rs]*r[rt]
                self.lo=v&MASK; self.hi=(v>>32)&MASK
            elif fn in (26,27):
                a,b=(signed(r[rs]),signed(r[rt])) if fn==26 else (r[rs],r[rt])
                if fn==26 and a==-0x80000000 and b==-1:
                    self.lo=0x80000000;self.hi=MASK
                elif b:
                    q=abs(a)//abs(b)
                    if (a<0)!=(b<0):q=-q
                    self.lo=q&MASK; self.hi=(a-q*b)&MASK
                else:
                    self.lo=(1 if a<0 else MASK) if fn==26 else (0xFFFF if a<=0xFFFF else MASK); self.hi=a&MASK
            elif fn in (28,29,46,47):
                v=(signed(r[rs])*signed(r[rt])) if fn in (28,46) else r[rs]*r[rt]
                x=((self.hi<<32)|self.lo)+(v if fn in (28,29) else -v)
                self.lo=x&MASK;self.hi=(x>>32)&MASK
            elif fn in (32,33):self.set(rd,r[rs]+r[rt])
            elif fn in (34,35):self.set(rd,r[rs]-r[rt])
            elif fn==36:self.set(rd,r[rs]&r[rt])
            elif fn==37:self.set(rd,r[rs]|r[rt])
            elif fn==38:self.set(rd,r[rs]^r[rt])
            elif fn==39:self.set(rd,~(r[rs]|r[rt]))
            elif fn==42:self.set(rd,int(signed(r[rs])<signed(r[rt])))
            elif fn==43:self.set(rd,int(r[rs]<r[rt]))
            elif fn in (44,45):self.set(rd,max(signed(r[rs]),signed(r[rt])) if fn==44 else min(signed(r[rs]),signed(r[rt])))
            elif fn in (12,13):raise UnsupportedInstruction(f'Syscall/break at {pc:08X}: {w:08X}')
            elif fn==15:pass # sync, irrelevant in single-thread analysis
            else:raise UnsupportedInstruction(f'SPECIAL {fn} at {pc:08X}: {w:08X}')
        elif op==1:
            if rt in (0,2,16,18):branch(signed(r[rs])<0,rt in (2,18),rt in (16,18))
            elif rt in (1,3,17,19):branch(signed(r[rs])>=0,rt in (3,19),rt in (17,19))
            else:raise UnsupportedInstruction(f'REGIMM {rt} at {pc:08X}')
        elif op in (2,3):
            self.pending=((pc+4)&0xF0000000)|((w&0x3FFFFFF)<<2)
            if op==3:self.set(31,pc+8)
        elif op in (4,20):branch(r[rs]==r[rt],op==20)
        elif op in (5,21):branch(r[rs]!=r[rt],op==21)
        elif op in (6,22):branch(signed(r[rs])<=0,op==22)
        elif op in (7,23):branch(signed(r[rs])>0,op==23)
        elif op in (8,9):self.set(rt,r[rs]+simm)
        elif op==10:self.set(rt,int(signed(r[rs])<simm))
        elif op==11:self.set(rt,int(r[rs]<(simm&MASK)))
        elif op==12:self.set(rt,r[rs]&imm)
        elif op==13:self.set(rt,r[rs]|imm)
        elif op==14:self.set(rt,r[rs]^imm)
        elif op==15:self.set(rt,imm<<16)
        elif op==28:
            raise UnsupportedInstruction(f'Allegrex SPECIAL2 hardware operation {fn} at {pc:08X}')
        elif op==31:
            if fn==0:self.set(rt,(r[rs]>>sa)&((1<<(rd+1))-1)) # ext
            elif fn==4:
                mask=((1<<(rd-sa+1))-1)<<sa
                self.set(rt,(r[rt]&~mask)|((r[rs]<<sa)&mask))
            elif fn==32:
                v=r[rt]
                if sa==2:self.set(rd,((v&0xFF00FF)<<8)|((v&0xFF00FF00)>>8))
                elif sa==3:self.set(rd,int.from_bytes(v.to_bytes(4,'little'),'big'))
                elif sa==16:self.set(rd,sx(v&255,8))
                elif sa==20:self.set(rd,int(f'{v:032b}'[::-1],2))
                elif sa==24:self.set(rd,sx(v&65535,16))
                else:raise UnsupportedInstruction(f'BSHFL {sa} at {pc:08X}')
            else:raise UnsupportedInstruction(f'SPECIAL3 {fn} at {pc:08X}')
        elif op in (32,33,35,36,37):
            size={32:1,33:2,35:4,36:1,37:2}[op]
            self.set(rt,self.load((r[rs]+simm)&MASK,size,op in (32,33)))
        elif op in (40,41,43):self.store((r[rs]+simm)&MASK,r[rt],{40:1,41:2,43:4}[op])
        elif op in (34,38): # lwl/lwr, little endian merge
            a=(r[rs]+simm)&MASK; n=a&3; v=self.load(a&~3,4)
            if op==34:self.set(rt,(r[rt]&((1<<(24-8*n))-1))|(v<<(24-8*n)))
            else:self.set(rt,(r[rt]&(~((1<<(32-8*n))-1)&MASK))|(v>>(8*n)))
        elif op in (42,46):
            a=(r[rs]+simm)&MASK;n=a&3;v=self.load(a&~3,4)
            if op==42:
                bits=8*(n+1); self.store(a&~3,(v&(~((1<<bits)-1)&MASK))|(r[rt]>>(24-8*n)))
            else:
                bits=8*n; self.store(a&~3,(v&((1<<bits)-1))|(r[rt]<<bits))
        elif op==49:self.f[rt]=self.load((r[rs]+simm)&MASK,4)
        elif op==57:self.store((r[rs]+simm)&MASK,self.f[rt],4)
        elif op==17 and rs==0:self.set(rt,self.f[rd])
        elif op==17 and rs==4:self.f[rd]=r[rt]
        elif op==47:pass # cache hint
        else:raise UnsupportedInstruction(f'Opcode {op} at {pc:08X}: {w:08X}')
        if prior is not None:
            if self.pending is not None:raise RuntimeError(f'Branch in delay slot {pc:08X}')
            next_pc=prior
        self.pc=next_pc&MASK; self.r[0]=0
