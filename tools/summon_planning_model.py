"""Readable Tag Force 1 summon planning, execution and position decisions.

The API follows phase_model/battle_model: load(relative), store(relative,u32),
call(relative,*args). select_material(player,cid,mode,used,buffer) represents
350C and returns a packed owner/slot. place_summon(player,cid,position,count,
materials) represents A4F0; modified_attack(player,slot) is word 5 of the
EED70 rule-engine output. Those interfaces expose unchanged engine results.

The planner saves and restores the ten monster records around each what-if
trial. Only an adapter's private state changes. No live emulator, ISO or save
is opened. Address labels identify stripped helpers without invented names.
"""
from battle_model import BASE, PLAYERS, PLAYER_STRIDE, field, half, set_half, signed

CACHE_VALID = 0x10FEC4
OPPONENT_LOSSES = 0x10FECC
OWN_REMAINING = 0x10FED0
OPPONENT_REMAINING = 0x10FED4
SLOT_DAMAGE = 0x10FEDC
SELECTED_INSTANCE = 0x11562C
SELECTED_OWNER = 0x115648
SELECTED_CONTROLLER = 0x11564C
EARLY_SUMMON_IDS = frozenset((
    0x185D, 0x1870, 0x19BC, 0x1905, 0x13B5, 0x154B, 0x147B,
    0x1B2F, 0x1B2E, 0x16EC, 0x1388, 0x1387, 0x1386,
))


def hand_record(api, player, index):
    return api.load(PLAYERS + (player & 1) * PLAYER_STRIDE + 0x120 + index * 4)


def hand_count(api, player):
    return api.load(PLAYERS + (player & 1) * PLAYER_STRIDE + 12)


def instance_handle(record):
    # MIPS LHU +122; (value & 0x3FFF) >> 6 keeps eight instance bits.
    return ((record >> 13) & 1) | (((record >> 22) & 0xFF) << 1)


def _select_instance(api, player, index):
    api.store(SELECTED_INSTANCE, instance_handle(hand_record(api, player, index)))
    api.store(SELECTED_OWNER, player)
    api.store(SELECTED_CONTROLLER, player)


def _remember_monsters(api):
    return [(field(owner, slot) + offset, api.load(field(owner, slot) + offset))
        for owner in range(2) for slot in range(5) for offset in range(0, 20, 4)]


def _restore_monsters(api, saved):
    for address, value in saved:
        api.store(address, value)


def plan_battle_summon(api, player, tribute_filter=-1):
    """fn_00AC5C: compare a summon with the current full attack simulation.

    Filter 1 considers tribute summons, filter 0 considers no-tribute summons,
    and -1 considers both. A trial must finish in attack mode, be legal, and
    have ATK >= 900. It improves predicted damage, or ties damage while
    removing more opposing monsters and leaving at least one own monster.
    Every trial restores the 200 original monster bytes; simulation result
    counters remain available just as they do in the game.
    """
    best_index = -1
    best_score = api.call(0xAA18, player)
    best_losses = api.load(OPPONENT_LOSSES)
    index = 0
    while index < hand_count(api, player):
        record = hand_record(api, player, index)
        cid = record & 0x1FFF
        handle = instance_handle(record)
        if (api.call(0x23D98, player, handle) & 0x10
                and api.call(0x2730, player, 2, cid) == 0
                and api.call(0x1DA4, player, cid, 0, 0) != 1):
            needed = api.call(0x38AC, player, handle, 0)
            if tribute_filter != int(needed == 0):
                materials = [0] * 4
                used = 0
                ready = True
                while used < needed:
                    selected = api.select_material(player, cid, 0, used, materials)
                    if signed(selected) < 0:
                        ready = False
                        break
                    owner, slot = selected & 0xFF, (selected >> 8) & 0xFF
                    # E6660, not the unrelated CC660 routine.
                    if api.call(0xE6660, player, cid, owner, slot) != 0:
                        materials[used] = selected & 0xFFFF
                        used += 1
                    materials[used] = selected & 0xFFFF
                    used += 1
                if ready:
                    saved = _remember_monsters(api)
                    api.call(0xA414, player)
                    slot = api.place_summon(player, cid, 0, needed, materials)
                    attack = api.modified_attack(player, slot) if slot >= 0 else None
                    score = api.call(0xA690, player)
                    api.store(OWN_REMAINING, api.call(0xE5988, player))
                    api.store(OPPONENT_REMAINING, api.call(0xE5988, 1 - player))
                    flags = api.load(field(player, slot) + 16)
                    _restore_monsters(api, saved)
                    if slot >= 0 and attack >= 900 and flags & (1 << 22):
                        improves = best_score < score
                        if (score == best_score and best_losses < api.load(OPPONENT_LOSSES)
                                and api.load(OWN_REMAINING) != 0):
                            improves = True
                        if improves:
                            best_losses = api.load(OPPONENT_LOSSES)
                            best_score = score
                            best_index = index
        index += 1
    return best_index


def early_card_summon(api, player):
    """fn_0004C8: first qualifying hand card from a fixed special-card list."""
    if api.call(0xDB7EC, player) == 0:
        return 0
    index = 0
    while index < hand_count(api, player):
        record = hand_record(api, player, index)
        cid = record & 0x1FFF
        qualifies = False
        if api.call(0x23D98, player, instance_handle(record)) & 0x10:
            if cid == 0x185D:
                qualifies = (api.call(0xE6A1C, player, -1) != 0
                    and api.call(0xBBFD8, player, cid, 0) != 0)
            elif cid == 0x1870:
                qualifies = (api.call(0xE5684, player, 0x1870) != 0
                    or api.call(0xE5684, player, 0x18F6) != 0)
            elif cid == 0x19BC:
                # Both native first E5684 calls retain a1=1870 from dispatch.
                qualifies = (api.call(0xE5684, player, 0x1870) != 0
                    or (api.call(0x6F74, player, 0x18F6) != 0
                        and (api.call(0x6F74, player, 0x12E5) != 0
                            or api.call(0x6F74, player, 0x18FE) != 0)))
            elif cid == 0x1905:
                qualifies = api.call(0xE5684, player, cid) == 0
            elif cid in (0x13B5, 0x154B):
                helper = 0xE6C0C if cid == 0x13B5 else 0xE5988
                qualifies = (api.call(helper, 1 - player) != 0
                    and api.call(0xE6784, player, cid, 1) > 2)
            elif cid == 0x147B:
                qualifies = hand_count(api, player) == 1
            elif cid in EARLY_SUMMON_IDS:
                qualifies = True
        if qualifies:
            _select_instance(api, player, index)
            api.call(0x26F5C, 1, 0, 1)
            return 1
        index += 1
    return 0


def summon_selected_card(api, player, index, position=0):
    """fn_0002D4: validate tributes, flip an eligible material, then summon.

    Returns -1 when the material selector returns its -1 sentinel; 0 means a
    tribute monster was flipped first, and 1 means the summon was queued.
    Double-tribute materials occupy two buffer entries. Normal summons being
    unavailable changes the queued position after the tribute count was read.
    """
    record = hand_record(api, player, index)
    cid = record & 0x1FFF
    needed = api.call(0x38AC, player, instance_handle(record), position)
    if api.call(0xDB7EC, player) == 0:
        position = 1
    materials = [0] * 4
    used = 0
    while used < needed:
        selected = api.select_material(player, cid, 0, used, materials)
        if (selected & 0xFFFFFFFF) == 0xFFFFFFFF:
            return -1
        owner, slot = selected & 0xFF, (selected >> 8) & 0xFF
        if owner == player and api.call(0x8, player, slot) != 0:
            api.call(0x22000, player, slot)
            return 0
        if api.call(0xE6660, player, cid, owner, slot) != 0:
            materials[used] = selected & 0xFFFF
            used += 1
        materials[used] = selected & 0xFFFF
        used += 1
    _select_instance(api, player, index)
    api.call(0x26F5C, int(position == 0), 0, 1)
    api.store(CACHE_VALID, 0)
    return 1


def change_position_if_needed(api, player, slot, after_battle=0):
    """fn_000CC4: apply the attack plan or the card/position heuristic."""
    preferred = -1
    if after_battle == 0 and api.load(SLOT_DAMAGE + slot * 4) != 0:
        preferred = 0
    if preferred == -1:
        preferred = api.call(0x1810, player, slot)
    if api.call(0xE79C8, player, slot) == 0:
        return 0
    if preferred == 0:
        if half(api, field(player, slot) + 8) == 0:
            if api.call(0xE7D4C, player, slot) == 0:
                return 0
            api.call(0x22000, player, slot)
            return 1
        if half(api, field(player, slot) + 6) != 0:
            if api.call(0xE4624, 0x17A6) != 0 and api.call(0xF2380, player, slot) > 3:
                return 0
            api.call(0xC5878, player, slot, 0, 0, 0, 0)
            return 1
    elif preferred == 1 and half(api, field(player, slot) + 6) == 0:
        if api.call(0xE4624, 0x15FB) != 0:
            return 0
        if api.call(0xE4624, 0x197B) != 0 and api.call(0xF2380, player, slot) < 4:
            return 0
        api.call(0xC5878, player, slot, 0, 0, 0, 0)
        return 1
    return 0


def place_hypothetical_summon(api, player, cid, position, count, materials):
    """fn_00A4F0: remove trial tributes and place a face-up trial monster."""
    total_levels = 0
    for packed in materials[:max(0, count)]:
        owner, slot = packed & 0xFF, (packed >> 8) & 0xFF
        total_levels = signed(total_levels + api.call(0xF2320, owner, slot))
        for offset in range(0, 20, 4):
            api.store(field(owner, slot) + offset, 0)
    slot = api.call(0xE62A0, player)
    if slot >= 0:
        set_half(api, field(player, slot), (half(api, field(player, slot)) & 0xE000) | (cid & 0x1FFF))
        set_half(api, field(player, slot) + 8, 1)
        set_half(api, field(player, slot) + 6, position)
        if cid in (0x1654, 0x1388, 0x1688):
            api.store(field(player, slot) + 12, total_levels * (2 if cid == 0x1688 else 1))
    return slot


RECREATED_SUMMON_PLANNING = {
    0xAC5C: plan_battle_summon,
    0x4C8: early_card_summon,
    0x2D4: summon_selected_card,
    0xCC4: change_position_if_needed,
    0xA4F0: place_hypothetical_summon,
}
