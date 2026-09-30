"""Readable reconstruction of TF1's monster choice and position heuristic.

Addresses are module-relative offsets in ULUS10136 version 1.03. These names
are ours, not recovered source symbols. The decision functions below do not
load game binaries. Card effects, legality and modified battle statistics are
explicit inputs, so a separate duel rules engine can supply them.
"""
from dataclasses import dataclass,field,asdict
from typing import Optional

@dataclass
class PositionContext:
    opponent_threat:int
    own_reference_a:int
    own_reference_b:int
    own_life:int
    opponent_has_monsters:bool=True
    opponent_life:int=8000
    current_damage:int=0
    battle_allowed:bool=False
    own_fairy_box:bool=False
    margin_300_rule:bool=False
    opponent_robbin_goblin:bool=False

def choose_position(attack:int,defense:int,c:PositionContext)->int:
    """Translate fn_0015A0. 0=attack, 1=defense, -1=defer.

    Card-specific overrides (fn_001DA4) run before this general heuristic.
    Reference values are the outputs of the original board helpers, not raw
    database ATK/DEF. Keep the exact thresholds and strict comparisons.
    """
    if (not c.opponent_has_monsters and c.current_damage>=c.opponent_life
            and c.battle_allowed and attack>0):return 0
    if attack==0:return 1
    if c.own_fairy_box:return 0
    threat=max(c.opponent_threat,1500) # 0016B8..0016C8: branch-likely delay slot
    gap=threat-attack
    if gap>=1000:return 1
    margin=300 if c.margin_300_rule else 100
    if c.opponent_robbin_goblin:margin=0
    if attack>=defense:
        if gap<=margin and c.own_life>margin:return 0
    else:
        if attack>=threat and attack>=1700:return 0
        if defense>=max(c.own_reference_a,c.own_reference_b) and defense>1500:return 1
    if attack>=threat:return 0
    if gap>margin:return 1
    return -1

@dataclass
class Candidate:
    card_id:int
    attack:int=0
    defense:int=0
    is_monster:bool=True
    rejected:bool=False
    legal_mask:int=0x50
    tributes_required:int=0
    tributes_available:bool=True
    forced_position:int=-1
    heuristic_position:int=-1
    preferred:bool=False
    fallback_level:int=0
    fallback_rejected:bool=False

@dataclass
class SelectionContext:
    material_probe_succeeded:bool=False
    opponent_reference:int=0
    own_reference_a:int=0
    own_reference_b:int=0
    fallback_allowed:bool=False

@dataclass
class Choice:
    index:int=-1
    position:Optional[int]=None
    card_id:Optional[int]=None
    reasons:list=field(default_factory=list)

def choose_hand_monster(hand:list[Candidate],c:SelectionContext)->Choice:
    """Translate fn_00B3B4's selection logic, including its fallback.

    The normal scan overwrites its chosen index whenever a candidate passes.
    Its comparison targets come from the board and never become the score of
    the previously chosen hand card. Hand order therefore matters.
    """
    choice=Choice()
    own_reference=max(c.own_reference_a,c.own_reference_b)
    for index,card in enumerate(hand):
        if not card.is_monster or card.rejected or card.legal_mask==0:continue
        if card.tributes_required>0 and not card.tributes_available:continue
        position=card.forced_position
        if position==-1:position=card.heuristic_position
        if position==-1:position=int(card.defense>=card.attack)
        wants_attack=int(position==0)
        # These are equality checks on the whole mask, not generic bit tests.
        if card.legal_mask==0x10 and not wants_attack:continue
        if card.legal_mask==0x40 and wants_attack:continue
        strength=max(card.attack,card.defense)
        reasons=[]
        if c.material_probe_succeeded and card.tributes_required!=0:reasons.append('material probe and tribute candidate')
        if c.opponent_reference<strength:reasons.append('beats opponent reference')
        if card.preferred:reasons.append('card-specific preference')
        if own_reference<strength:reasons.append('beats own reference')
        if card.tributes_required==0:reasons.append('needs no tribute')
        if reasons:choice=Choice(index,int(not wants_attack),card.card_id,reasons)
    if choice.index>=0:return choice
    if c.fallback_allowed:return choose_fallback(hand)
    return choice

def choose_fallback(hand:list[Candidate])->Choice:
    """Separate first-eligible fallback after the normal scan fails."""
    for index,card in enumerate(hand):
        if (card.is_monster and card.fallback_level<5 and not card.fallback_rejected
                and card.legal_mask!=0):
            return Choice(index,int(bool(card.legal_mask&0x40)),card.card_id,['first legal fallback'])
    return Choice()

def instance_handle(hand_record:int)->int:
    """Decode the instance handle used by legality/tribute helpers.

    This is separate from the card ID. A duplicate card can have another handle.
    """
    return ((hand_record>>13)&1) | (((hand_record>>22)&0xFF)<<1)

def pack_action(a0:int,a1:int,a2:int,a3:int)->bytes:
    """fn_0D0C00's action payload: four little-endian unsigned halfwords."""
    import struct
    return struct.pack('<4H',*(v&0xFFFF for v in (a0,a1,a2,a3)))

def hidden_monster_estimate(level:int,known:bool,actual_attack:int,actual_defense:int):
    """fn_001378's unknown face-down monster estimates.

    The game's helper reads the recorded level to choose the two buckets.
    This small helper does not recreate the surrounding visibility flags.
    """
    if known:return actual_attack,actual_defense
    return (1200,1000) if level<5 else (2100,1800)

def fixture_demo():
    hand=[Candidate(5803,1300,2000,forced_position=1),
          Candidate(4095,1200,2200,heuristic_position=1),
          Candidate(4530,1250,700,heuristic_position=1)]
    ctx=SelectionContext(False,2600,1850,2200)
    return dict(original_order=asdict(choose_hand_monster(hand,ctx)),
        swapped_order=asdict(choose_hand_monster(list(reversed(hand)),ctx)))

if __name__=='__main__':
    import json
    print(json.dumps(fixture_demo(),indent=2))
