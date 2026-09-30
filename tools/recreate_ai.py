"""Run the readable TF1 AI core without PPSSPP or a game binary.

python tools/recreate_ai.py demo
python tools/recreate_ai.py choose examples/turn26.json
python tools/recreate_ai.py position examples/position.json
"""
import argparse,json
from dataclasses import asdict
from pathlib import Path
from ai_model import Candidate,SelectionContext,PositionContext,choose_hand_monster,choose_position,fixture_demo

def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('demo')
    for name in ('choose','position'):
        part=sub.add_parser(name);part.add_argument('input',type=Path)
    args=p.parse_args()
    if args.command=='demo':result=fixture_demo()
    else:
        data=json.loads(args.input.read_text(encoding='utf-8'))
        if args.command=='choose':
            result=asdict(choose_hand_monster([Candidate(**c) for c in data['hand']],SelectionContext(**data['context'])))
        else:
            mode=choose_position(data['attack'],data['defense'],PositionContext(**data['context']))
            result=dict(position=mode,label={0:'attack',1:'defense',-1:'defer'}[mode])
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
