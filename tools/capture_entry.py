"""Capture a stopped function entry and then observe its original return.

Only debugger breakpoints and reads; no RAM writes. A breakpoint at the caller
return is used instead of guessing how long the game function needs to run.
"""
import argparse,json,hashlib,time
from ai_lab import ROOT,validated_client,snapshot

def registers(client):
    raw=client.request('cpu.getAllRegs')
    cat=raw['categories'][0]
    return raw,dict(zip(cat['registerNames'],cat['uintValues']))

def dump(client,name):
    target=ROOT/'work'/f'{name}.bin'
    with target.open('wb') as out:
        for address in range(0x08800000,0x0A000000,0x80000):
            data=client.read(address,0x80000)
            if len(data)!=0x80000:raise ValueError('Incomplete RAM read')
            out.write(data)
    return dict(file=str(target),memory_start=0x08800000,size=target.stat().st_size,sha256=hashlib.sha256(target.read_bytes()).hexdigest())

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('name'); p.add_argument('--relative',type=lambda x:int(x,0),default=0xB3B4)
    p.add_argument('--observe-return',action='store_true')
    a=p.parse_args();c,b=validated_client()
    try:
        for _ in range(30):
            status=c.request('cpu.status')
            if status['stepping']:break
            time.sleep(.2)
        if not status['stepping'] or status['pc']!=b+a.relative:
            print('Entry not reached',status,snapshot(c,b));return
        raw,regs=registers(c)
        result=dict(entry=dict(cpu=status,registers=regs,raw_registers=raw,state=snapshot(c,b),memory=dump(c,a.name+'_entry')))
        out=ROOT/'traces'/f'{a.name}.json';out.write_text(json.dumps(result,indent=2))
        if a.observe_return:
            if regs['ra']==b+a.relative:raise ValueError('Bad return target')
            c.request('cpu.breakpoint.remove',address=b+a.relative)
            c.request('cpu.breakpoint.add',address=regs['ra'],enabled=True,log=False,condition='')
            c.resume()
            for _ in range(100):
                status=c.request('cpu.status')
                if status['stepping']:break
                time.sleep(.05)
            if not status['stepping'] or status['pc']!=regs['ra']:raise RuntimeError('Return breakpoint not reached')
            raw2,regs2=registers(c)
            result['return']=dict(cpu=status,registers=regs2,raw_registers=raw2,
                output_address=regs['a1'],output_hex=c.read(regs['a1'],4).hex() if 0x08800000<=regs['a1']<0x0A000000-4 else None,
                state=snapshot(c,b),memory=dump(c,a.name+'_return'))
            out.write_text(json.dumps(result,indent=2))
            c.request('cpu.breakpoint.remove',address=regs['ra'])
        print(json.dumps({k:{q:v for q,v in d.items() if q not in ('raw_registers','registers')} for k,d in result.items()},indent=2))
        print('Record',out)
    finally:c.close()
if __name__=='__main__':main()
