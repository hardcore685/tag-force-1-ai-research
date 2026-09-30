"""Readable reconstruction of TF1's effect-target dispatcher (fn_014C34).

Engine helpers stay explicit by relative address because their original names
were stripped. The dispatcher, ordering, masks and special card cases are
translated here. An adapter supplies legality, field data and selector helpers.
"""

FILTER_132F0={0x1A9A,0x192A,0x1928}
PREFER_11548={0x148A,0x140A,0x1476}
OWN_11548={0x151E,0x1391}
PREFER_1216C_0={0x1AA6,0x17EC,0x1719,0x1623,0x1327}
PREFER_1216C_1={0x1A35,0x192E,0x1436,0x17EA,0x11F0,0x1878,0x1147,
    0x17F2,0x1711,0x12E3,0x1254,0x1738,0x15DF,0x1470}
PREFER_1216C_OWN1257C={0x19A5,0x19B5,0x188C,0x187E,0x1855,0x179B,
    0x1768,0x1753,0x1715,0x1713,0x1708,0x1702,0x169B,0x167D,0x158C,
    0x14E4,0x14C5,0x13FC,0x12C5,0x123B,0x0FFF,0x18CA,0x180E,0x17E1,
    0x16A0,0x164B,0x13A8,0x1835,1}
SPECIAL_OWN={0x1927,0x140B,0x15A3,0x1957}

def select_effect_target(player:int,card_id:int,mask:int,api)->int:
    """Run the recovered routing logic using an explicit engine interface.

    api.call(relative,*args), api.card_id(player,slot), api.flags(player,slot),
    api.level(card_id), api.context_flag() are required. Return values are the
    engine's raw status, not a made-up success boolean.
    """
    other=1-player
    def finish(p,slot):return api.call(0x31F34,p,slot,0,11)
    def generic():
        slot=api.call(0x11E20,other,mask)
        if slot>=0:return finish(other,slot)
        slot=api.call(0x11E20,player,mask)
        return finish(player,slot) if slot>=0 else slot
    def opponent_then_own(mode,own_helper):
        slot=api.call(0x1216C,other,mask,mode)
        if slot>=0:return finish(other,slot)
        slot=api.call(own_helper,player,mask,0)
        return finish(player,slot) if slot>=0 else generic()
    def filtered(callback):
        slot=api.call(0x119C8,player,mask,api.base+callback)
        return finish(player,slot) if slot>=0 else generic()

    if card_id in FILTER_132F0:return filtered(0x132F0)
    if card_id==0x1929:return filtered(0x131C8)
    if card_id==0x1A98:
        if api.context_flag()!=0:return api.call(0x32028)
        return filtered(0x134E0)
    if card_id==0x17F5:return filtered(0x13220)
    if card_id in PREFER_11548:
        slot=api.call(0x11548,other,mask,-1,-1)
        if slot>=0:return finish(other,slot)
        slot=api.call(0x11548,player,mask,1,1)
        return finish(player,slot) if slot>=0 else generic()
    if card_id in OWN_11548:
        slot=api.call(0x11548,player,mask,1,0)
        return finish(player,slot) if slot>=0 else generic()
    if card_id in PREFER_1216C_0:return opponent_then_own(0,0x123E4)
    if card_id in PREFER_1216C_1:return opponent_then_own(1,0x123E4)
    if card_id in SPECIAL_OWN:
        slots=range(5,10) if card_id==0x1957 else range(5)
        for slot in slots:
            if not mask & (1 << ((player*16+slot)&31)):continue
            if card_id==0x1927:
                flags=api.flags(player,slot)
                if not ((flags>>22)&1 or (flags>>23)&1):continue
                if api.level(api.card_id(player,slot))>=5:continue
            elif card_id==0x140B:
                value=api.call(0xF2380,player,slot)
                if api.call(0xBBFD8,player,card_id,value)==0:continue
            elif card_id==0x15A3:
                if api.call(0xFFB50,api.card_id(player,slot))!=0:continue
            return finish(player,slot)
        return opponent_then_own(0,0x1257C)
    if card_id in PREFER_1216C_OWN1257C:return opponent_then_own(0,0x1257C)
    return generic()

ALL_SPECIAL_IDS=(FILTER_132F0|PREFER_11548|OWN_11548|PREFER_1216C_0|
    PREFER_1216C_1|PREFER_1216C_OWN1257C|SPECIAL_OWN|{0x1929,0x1A98,0x17F5})
