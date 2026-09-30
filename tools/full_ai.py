"""Use the complete compiled TF1 decision/duel-engine port on private state.

Examples:
    python tools/full_ai.py demo
    python tools/full_ai.py choose --player 1
    python tools/full_ai.py tick --state examples/native/full_tick_trial1.tfstate \
        --context examples/native/full_tick_trial1.json

The prepared states contain initialized game data, with executable instructions
erased. The Windows DLL executes generated C, not a PSP instruction interpreter.
"""
from __future__ import annotations
import argparse,hashlib,json,struct,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'tools/native_port'))
from native_ai import NativePort,BASE,START,load_state

REG_NAMES=['zero','at','v0','v1','a0','a1','a2','a3','t0','t1','t2','t3','t4','t5','t6','t7',
    's0','s1','s2','s3','s4','s5','s6','s7','t8','t9','k0','k1','gp','sp','fp','ra']
ENGINE_SIZE=1139456

def inspect_state(port):
    players=[]
    for player in range(2):
        ptr=BASE+0x111E5C+player*0xFAC
        count=port.load(ptr+0xC)
        if count>256:raise ValueError('Invalid initialized hand count')
        players.append(dict(player=player,life_points=port.load(ptr),hand_count=count,
            hand_card_ids=[port.load(ptr+0x120+i*4)&0x1FFF for i in range(count)]))
    count=port.load(BASE+0x111238)
    if count>256:raise ValueError('Invalid initialized action-queue count')
    fields={'main_step':0x115600,'phase_outer':0x10FCF8,'phase_inner':0x10FCFC,
        'battle_outer':0x11560C,'battle_inner':0x115610}
    return dict(displayed_turn=port.load(BASE+0x1155D4)+1,actor=port.load(BASE+0x1155D0),
        phase=port.load(BASE+0x1155DC),stages={k:port.load(BASE+v) for k,v in fields.items()},
        players=players,current_action=list(struct.unpack('<4H',port.read(BASE+0x110A30,8))),
        queued_actions=[list(struct.unpack('<4H',port.read(BASE+0x110A38+i*8,8))) for i in range(count)])

def run_tick(state,context):
    data=load_state(state);meta=json.loads(Path(context).read_text(encoding='utf-8'))
    port=NativePort(data);before=inspect_state(port)
    regs={int(i):v for i,v in meta['registers'].items()}
    stop=regs.get(31,0)
    if stop:port.callbacks[stop]=lambda p:p.set(31,0)
    result=port.call(meta['entry_pc'],registers=regs,sp=regs.get(29,0x09FFE000),
        special_registers=meta.get('special_registers'),max_steps=12000000)
    actual=hashlib.sha256(port.read(BASE,ENGINE_SIZE)).hexdigest()
    expected=meta.get('expected_engine_sha256')
    return dict(case=meta['name'],return_value=result,instructions=port.steps,
        before=before,after=inspect_state(port),engine_sha256=actual,
        expected_engine_sha256=expected,engine_matches_saved_live_return=actual==expected if expected else None,
        host_services=dict(port.services)),port

def choose(state,player):
    port=NativePort(load_state(state));out=0x09FFF400
    index=port.call(BASE+0xB3B4,player,out,registers={26:0x09FFFB00,28:0x088F8CB0},max_steps=12000000)
    position=port.load(out) if index>=0 else None
    cid=port.load(BASE+0x111E5C+player*0xFAC+0x120+index*4)&0x1FFF if index>=0 else None
    return dict(hand_index=index,card_id=cid,position=position,
        position_label={0:'attack',1:'defense'}.get(position),instructions=port.steps,
        host_services=dict(port.services))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('demo',help='Replay prepared full AI ticks without an emulator.')
    selection=commands.add_parser('choose',help='Run the full hand selector and original rule-helper source.')
    selection.add_argument('--state',type=Path,default=ROOT/'examples/native/trial4_selector.tfstate')
    selection.add_argument('--player',type=int,choices=[0,1],default=1)
    tick=commands.add_parser('tick',help='Run one complete AI update with captured caller context.')
    tick.add_argument('--state',type=Path,required=True);tick.add_argument('--context',type=Path,required=True)
    tick.add_argument('--output-state',type=Path)
    args=parser.parse_args()
    if args.command=='choose':result=choose(args.state,args.player)
    elif args.command=='tick':
        result,port=run_tick(args.state,args.context)
        if args.output_state:
            import zlib
            args.output_state.parent.mkdir(parents=True,exist_ok=True)
            args.output_state.write_bytes(b'TFSTATE1'+zlib.compress(port.bytes(),9))
    else:
        result=[]
        for name in ['full_tick_trial1','full_tick_trial2','full_tick_trial3']:
            item,_=run_tick(ROOT/f'examples/native/{name}.tfstate',ROOT/f'examples/native/{name}.json')
            result.append(item)
        if not all(r['engine_matches_saved_live_return'] for r in result):
            print(json.dumps(result,indent=2));raise SystemExit('A prepared live-state replay did not match')
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
