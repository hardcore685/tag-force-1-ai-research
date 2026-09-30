"""Recovered TF1 main-phase state machines, expressed as readable Python.

The API exposes load/store at module-relative offsets, call(relative,*args),
and choose_hand(player)->(index,position). Helper names were stripped; offsets
identify the remaining game-rule/effect helpers without inventing semantics.
Only an adapter's private state is changed. No PSP or game process is opened.
"""
ACTOR=0x1155D0
TURN=ACTOR+4
PHASE=ACTOR+12
PERSPECTIVE=0x10FCF4
OUTER=0x10FCF8
INNER=0x10FCFC
AUX=0x10FD00
PENDING=0x115614
HANDLERS=(0x1CE08,0x828,0xEDC,0x10D8,0xBAC,0xEDC,0x10D8,0xFEC,0x1DABC,None)

def advance(api):
    api.store(INNER,api.load(INNER)+1)
    return 0

def normal_available(api,player):
    if not api.call(0x26D0,player):return False
    flags=api.load(0x111E5C+(player&1)*0xFAC+0x11C)
    if (flags>>17)&1:return False
    return bool(api.call(0xDB7EC,player) or api.call(0xDB6B8,player))

def outer_tick(api):
    """fn_001248: one tick of the phase scheduler; returns raw 0/1."""
    player=api.load(ACTOR);api.store(PERSPECTIVE,player)
    if api.load(OUTER)==0 and api.load(PHASE)==4:api.store(OUTER,7)
    step=api.load(OUTER)
    if not 0<=step<len(HANDLERS):raise ValueError('Invalid outer phase step')
    handler=HANDLERS[step]
    if handler is None:return 1
    if api.call(handler,player)==0:return 0
    api.store(INNER,0);api.store(AUX,0);api.store(OUTER,step+1)
    return int(api.load(PENDING)!=0)

def main_phase_tick(api):
    """fn_000828: early summons, effect passes, then the hand selector."""
    player=api.load(ACTOR);step=api.load(INNER)
    if step==0:
        api.store(0x10FEC4,0)
        if api.load(TURN)==0:return 1
        if (not normal_available(api,player)
                or api.call(0xE43C8,1-player,0x1102,-1)>0):return advance(api)
        if api.call(0x4C8,player)!=0:return 0
        if api.call(0xDB7EC,player)!=0:
            index=-1
            if api.call(0x3434,player,1)>=0:index=api.call(0xAC5C,player,1)
            elif api.call(0xAA18,player)!=0:index=api.call(0xAC5C,player,0)
            if index==-1:index=api.call(0xAC5C,player,-1)
            if index>=0 and api.call(0x2D4,player,index,0)==0:return 0
        return advance(api)
    if step==1:
        if api.call(0x5F2C,player)>=0 and api.call(0xAA18,player)!=0:return 1
        api.store(AUX,0)
        return advance(api)
    if step in (2,3):
        if api.call(0x1D124 if step==2 else 0x1CF60,player)!=0:
            api.store(INNER,0);return 0
        return advance(api)
    if step==4:
        if not normal_available(api,player):return advance(api)
        index,position=api.choose_hand(player)
        if index<0 or position!=0:return 1
        result=api.call(0x2D4,player,index,0)
        if result==0:return 0
        if result>0:api.store(INNER,1);return 0
        return 1
    return 1

def summon_tick(api):
    """fn_000BAC: later summon/set pass, honoring the chosen position."""
    player=api.load(ACTOR)
    if api.load(INNER)!=0:return 1
    if not normal_available(api,player):return advance(api)
    index,position=api.choose_hand(player)
    if index<0:return advance(api)
    if api.call(0x2D4,player,index,position)==0:return 0
    return advance(api)

def position_tick(api,after_battle=False):
    """fn_000EDC / fn_000FEC: effect checks and five field slots.

    Unlike main_phase_tick, these stages deliberately fall through within a
    single tick once a helper reports that no action was taken.
    """
    player=api.load(ACTOR);step=api.load(INNER);mode=int(after_battle)
    if step==0:
        if not after_battle and api.load(PHASE)>=3:return 1
        if api.call(0x1C948,player,mode)!=0:return 0
        advance(api);step=1
    if step==1:
        if api.call(0x1D124,player)!=0:return 0
        advance(api);step=2
    if step==2:
        if not after_battle:api.call(0xAA18,player)
        for slot in range(5):
            if api.call(0xCC4,player,slot,mode)!=0:return 0
    return 1

def battle_tick(api):
    """fn_0010D8: battle preparation, planner, then phase-change action."""
    player=api.load(ACTOR);step=api.load(INNER)
    if step==0:
        if not api.call(0x32868,player):return 1
        if api.call(0x329D8,player)!=0 and api.call(0xAA88,player,0)==0:return 1
        if api.call(0x1CFD0,player,1)!=0:return 0
        api.store(0x11560C,0);api.store(0x115610,0)
        return advance(api)
    if step==1:
        api.call(0xABD5C,3,0)
        return advance(api)
    if step==2:
        if api.call(0x3B174,player)==0:return 0
        if api.load(PENDING)!=0:return 1
        return advance(api)
    if step==3:
        api.call(0xD0C00,(0x8000 if player else 0)|0x10,0,0,0)
        return 1
    return 0

RECREATED_HANDLERS={0x828:main_phase_tick,0xBAC:summon_tick,
    0xEDC:position_tick,0xFEC:lambda api:position_tick(api,True),0x10D8:battle_tick}
