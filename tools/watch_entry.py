"""Set one lab-owned function-entry breakpoint in the isolated game session."""
import argparse
from ai_lab import validated_client,HOOKS
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('relative',type=lambda x:int(x,0))
    p.add_argument('--condition',default='');args=p.parse_args()
    c,b=validated_client()
    try:
        c.pause();ours={b+x for x in [*HOOKS.values(),0x3ED98,0x3B174,0x11288,0x1130C,0x10EC8]}
        for bp in c.request('cpu.breakpoint.list')['breakpoints']:
            if bp['address'] in ours:c.request('cpu.breakpoint.remove',address=bp['address'])
        c.request('cpu.breakpoint.add',address=b+args.relative,enabled=True,log=False,condition=args.condition)
        c.resume();print(f'Watching engine+{args.relative:06X}')
    finally:c.close()
if __name__=='__main__':main()
