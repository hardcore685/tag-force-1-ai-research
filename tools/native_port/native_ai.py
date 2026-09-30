"""Run the native source port against private copied TF1 state memory.

No instruction interpreter or PSP emulator is used. All game code is compiled
from the generated C. Unknown PSP hardware services fail explicitly.
"""
from __future__ import annotations
import ctypes as C,json,struct,zlib
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[2]
BASE=0x09E65C00;START=0x08800000
_DELAY_SLOTS=None

class Context(C.Structure):
    _fields_=[('r',C.c_uint32*32),('f',C.c_uint32*32),('hi',C.c_uint32),('lo',C.c_uint32),
        ('pc',C.c_uint32),('fcr31',C.c_uint32),('error',C.c_uint32),('fault_pc',C.c_uint32),
        ('fault_word',C.c_uint32),('fault_address',C.c_uint32),('resume_pc',C.c_uint32),
        ('steps',C.c_uint64),('max_steps',C.c_uint64),('mem',C.POINTER(C.c_uint8)),
        ('mem_start',C.c_uint32),('mem_size',C.c_uint32),('coverage',C.POINTER(C.c_uint32)),
        ('coverage_size',C.c_uint32),('hooks',C.POINTER(C.c_uint8)),('hook_size',C.c_uint32)]

def signed(v):return v-0x100000000 if v&0x80000000 else v
class NativePort:
    def __init__(self,ram,library=None,coverage=False):
        lib=Path(library) if library else Path(__file__).resolve().parent/'build/tagforce_ai.dll'
        self.lib=C.CDLL(str(lib));self.lib.tf_run.argtypes=[C.POINTER(Context)];self.lib.tf_run.restype=C.c_uint32
        self.lib.tf_abi_version.restype=C.c_uint32
        if self.lib.tf_abi_version()!=2:raise ValueError('Native ABI mismatch')
        self.mem=(C.c_uint8*len(ram)).from_buffer_copy(ram);self.ctx=Context()
        self.ctx.mem=C.cast(self.mem,C.POINTER(C.c_uint8));self.ctx.mem_start=START;self.ctx.mem_size=len(ram)
        self.services=Counter();self.callbacks={};self.visits=None;self.irq_enabled=1;self._hook_keys=set();self._hooks=None
        if coverage:
            self.visits=(C.c_uint32*(len(ram)//4))();self.ctx.coverage=self.visits;self.ctx.coverage_size=len(self.visits)
        self.imports={}
        hsys=json.loads((ROOT/'reports/modehsys_analysis.json').read_text())
        for i in hsys['imports']:
            stub=hsys['base_address']+i['stub_address']
            self.imports[stub+4]=dict(i,stub=stub)
    def offset(self,a,n):
        i=(a&0x3fffffff)-START
        if i<0 or n<0 or i+n>len(self.mem):raise ValueError(f'Unmapped state memory {a:08X} length {n}')
        return i
    def read(self,a,n):return bytes(self.mem[self.offset(a,n):self.offset(a,n)+n])
    def load(self,a,n=4,sign=False):return int.from_bytes(self.read(a,n),'little',signed=sign)
    def store(self,a,v,n=4):
        i=self.offset(a,n);self.mem[i:i+n]=(v&((1<<(n*8))-1)).to_bytes(n,'little')
    def write(self,a,data):
        i=self.offset(a,len(data));self.mem[i:i+len(data)]=data
    def set(self,i,v):
        if i:self.ctx.r[i]=v&0xffffffff
    @property
    def pc(self):return self.ctx.pc
    def bytes(self):return bytes(self.mem)
    @property
    def r(self):return self.ctx.r
    @property
    def steps(self):return self.ctx.steps
    def kernel_service(self):
        info=self.imports.get(self.ctx.fault_pc)
        if not info:return False
        key=(info['library'],info['nid']);r=self.ctx.r
        if key==('Kernel_Library','0x092968F4'):
            r[2]=self.irq_enabled;self.irq_enabled=0
        elif key==('Kernel_Library','0x5F10D406'):
            self.irq_enabled=1 if r[4] else 0
        else:return False
        self.services[':'.join(key)]+=1
        self.ctx.error=0;self.ctx.pc=self.ctx.resume_pc
        return True
    def call(self,address,*args,sp=0x09FFE000,max_steps=5000000,registers=None,special_registers=None):
        global _DELAY_SLOTS
        c=self.ctx
        keys=set(self.callbacks)
        if keys!=self._hook_keys:
            if _DELAY_SLOTS is None:
                contract=Path(__file__).resolve().parent/'generated/callback_contract.json'
                if not contract.exists():raise RuntimeError('Missing callback-entry contract metadata')
                _DELAY_SLOTS=set(json.loads(contract.read_text())['delay_slot_addresses'])
            misplaced=keys&_DELAY_SLOTS
            if misplaced:raise ValueError('Callbacks must be function entries, not branch delay slots: '+','.join(f'{p:08X}' for p in sorted(misplaced)))
            if keys and self._hooks is None:
                self._hooks=(C.c_uint8*(len(self.mem)//4))();c.hooks=self._hooks;c.hook_size=len(self._hooks)
            if self._hooks is not None:
                for pc in self._hook_keys:self._hooks[self.offset(pc,4)//4]=0
                for pc in keys:self._hooks[self.offset(pc,4)//4]=1
            self._hook_keys=keys
        for i in range(32):c.r[i]=0;c.f[i]=0
        c.hi=c.lo=c.fcr31=c.error=c.fault_pc=c.fault_word=c.fault_address=c.resume_pc=0
        if special_registers:
            c.hi=special_registers.get('hi',0)&0xffffffff;c.lo=special_registers.get('lo',0)&0xffffffff
            c.fcr31=special_registers.get('fcr31',0)&0xffffffff
            for i,v in enumerate(special_registers.get('f',[])):c.f[i]=v&0xffffffff
        c.pc=address;c.r[29]=sp;c.r[31]=0
        if registers:
            for i,v in registers.items():c.r[i]=v&0xffffffff
        for i,a in enumerate(args):
            if i<8:c.r[4+i]=a&0xffffffff
            else:self.store(sp+4*(i-8),a)
        c.max_steps=c.steps+max_steps
        while True:
            error=self.lib.tf_run(C.byref(c))
            if error==0:return signed(c.r[2])
            if error==7 and c.fault_pc in self.callbacks:
                self.callbacks[c.fault_pc](self);c.pc=c.r[31];c.error=0;continue
            if error==3 and self.kernel_service():continue
            raise RuntimeError(f'Native port error {error} PC {c.fault_pc:08X} word {c.fault_word:08X} address {c.fault_address:08X}; service {self.imports.get(c.fault_pc)}')

def load_state(path):
    p=Path(path);data=p.read_bytes()
    return zlib.decompress(data[8:]) if data.startswith(b'TFSTATE1') else data
if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--state',type=Path,default=ROOT/'work/trial4_selector_entry.bin')
    p.add_argument('--player',type=int,choices=[0,1],default=1);p.add_argument('--routine',type=lambda x:int(x,0),default=0xB3B4)
    args=p.parse_args();port=NativePort(load_state(args.state));out=0x09FFF000
    result=port.call(BASE+args.routine,args.player,out)
    print(json.dumps(dict(result=result,output_word=port.load(out),instructions=port.steps,services=dict(port.services)),indent=2))
