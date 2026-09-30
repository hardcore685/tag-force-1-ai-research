"""Readable card-position overrides and card-action priority for Tag Force 1.

These are translations of modduel_eng routines, not invented strategies.
Engine calls stay explicit by relative address where original names were
stripped. An adapter supplies complete legality and card-specific predicates.
"""
from dataclasses import dataclass

BASE = 0x09E65C00
ACTOR = 0x1155D0
INNER = 0x10FCFC
EARLY_TABLE = 0x1025E8
LATE_TABLE = 0x1035E0

ALWAYS_ATTACK = frozenset({0x1A07, 0x18C3, 0x1829, 0x1743, 0x1639, 0x1621, 0x1439, 0x129A})
ALWAYS_DEFENSE = frozenset({0x195A, 0x1815, 0x1813})
RACE_COUNT_ATTACK = frozenset({0x1863, 0x1756, 0x1755, 0x1744})
FIELD_10F4_ATTACK = frozenset({0x1709, 0x1706, 0x12A8, 0x13CD})
MODE_ZERO_ATTACK = frozenset({
    0x1A1E, 0x19BC, 0x1868, 0x183A, 0x180C, 0x180B, 0x1807, 0x172E,
    0x15DA, 0x158B, 0x156B, 0x14CB, 0x148C, 0x144D, 0x13AD, 0x139C,
    0x11A6, 0x101E, 0x0FB6,
})
BOTH_ZERO_ATTACK = frozenset({
    0x19C6, 0x1918, 0x128E, 0x1617, 0x14C6, 0x170C, 0x128A, 0x1293,
    0x1341, 0x1334, 0x119A,
})
OTHER_POSITION_IDS = frozenset({
    0x1914, 0x182D, 0x191C, 0x17DE, 0x16BC, 0x18B6, 0x185D, 0x1821,
    0x189A, 0x1523, 0x1836, 0x17DD, 0x1399, 0x161C, 0x1612, 0x14AC,
    0x118A, 0x147A, 0x14D7, 0x13A7, 0x1284, 0x1281, 0x1694, 0x12BB,
    0x0FE0,
})
POSITION_SPECIAL_IDS = (ALWAYS_ATTACK | ALWAYS_DEFENSE | RACE_COUNT_ATTACK |
                        FIELD_10F4_ATTACK | MODE_ZERO_ATTACK | BOTH_ZERO_ATTACK | OTHER_POSITION_IDS)

# Eng+FFB80 imports modehsys 0x088504A8. Its fixed spell-counter capacity table
# supports Apprentice Magician's special position check. Values are retained
# exactly, including 255 for cards without a smaller built-in cap.
SPELL_COUNTER_LIMITS = {
    0x1983:255, 0x1817:5, 0x186A:255, 0x16DE:255, 0x1695:255,
    0x1624:255, 0x161A:3, 0x1617:1, 0x1615:255, 0x1611:3,
    0x1610:3, 0x128E:1,
}
SET_DEFENSE_EXTRA_IDS = frozenset({0x19BD,0x152E,0x1476,0x13E8,0x1370,0x133D,0x1286})
CREATURE_SWAP_ATTACK_IDS = frozenset({
    0x19C5,0x196A,0x1939,0x18F7,0x18B7,0x1867,0x17E6,0x17E5,0x17D6,
    0x17C3,0x1591,0x156D,0x156C,0x152F,0x14F6,0x14E7,0x14DD,0x14AB,
    0x13C7,0x139E,0x139D,0x1342,0x133F,0x133E,0x133C,0x133A,0x1339,
    0x1335,0x1333,
})


def spell_counter_capacity(cid):
    """Modehsys 0x088504A8: original fixed spell-counter cap, zero otherwise."""
    return SPELL_COUNTER_LIMITS.get(cid, 0)


def prefer_set_defense(cid, api):
    """1C88: imported defense-preference class, then seven explicit special IDs.

    FFB48 imports modehsys 0x0884F504, a fixed 94-card classifier returning 0,
    1 or 2. It includes cards beyond ordinary Flip Effect Monsters, so its raw
    classification is retained rather than assigning a card-type label.
    """
    return int(api.call(0xFFB48,cid) != 0 or cid in SET_DEFENSE_EXTRA_IDS)


def creature_swap_attack_class(cid):
    """31E8: the fixed 29-card class used by 1DA4's Creature Swap attack branch."""
    return int(cid in CREATURE_SWAP_ATTACK_IDS)


def card_position_override(player, cid, context_mode, option_flag, api):
    """1DA4: return 0 attack, 1 defense or -1 to defer to the general heuristic.

    context_mode and option_flag preserve the original parameters without
    assigning unverified meanings. API.phase(), life_points() and call() are
    required. API.call accepts relative addresses and signed integer arguments.
    """
    call = api.call
    if api.phase() == 3 and call(0xDE2F8, player, 11, 0x1669) != 0:
        return 1
    if call(0xE4624, 0x135D) > 0:
        context_mode = 1
    if cid == 0x1914:
        if context_mode == 0:
            if call(0xE5198, 0, 0x1784) != 0:
                return 0
            return int(call(0xE5198, 1, 0x1784) == 0)
    elif cid == 0x182D:
        if call(0xE4624, 0x140E) != 0 or call(0xE4624, 0x17A6) != 0:
            return 0
    elif cid == 0x191C:
        if call(0xE55AC, player, 7) != 0:
            return 0
        if call(0xE43C8, player, 0x1357, -1) != 0 and call(0x6F74, player, 0x198E) != 0:
            return 0
    elif cid in ALWAYS_ATTACK:
        return 0
    elif cid in ALWAYS_DEFENSE:
        return 1
    elif cid == 0x17DE:
        if context_mode == 0 and call(0x6F74, player, 0x12DC) != 0:
            return 0
    elif cid == 0x16BC:
        if context_mode == 0 and call(0xE7360, player, 3) != 0:
            return 1
    elif cid == 0x18B6:
        if context_mode == 0 and call(0xE5C1C, player, 0, 1) != 0:
            return 0
    elif cid == 0x185D:
        if context_mode == 0 and call(0xE5988, player) != 0:
            return 0
    elif cid == 0x1821:
        if context_mode == 0 and call(0xED9FC, player, 0x1820) != 0:
            return 0
    elif cid == 0x189A:
        if context_mode == 0 and call(0xED9FC, player, 0x0FA7) != 0:
            return 0
    elif cid == 0x1523:
        if context_mode == 0:
            score = call(0x6400, player, 1-player, -1, 1, 0)
            if score < 1101 or score > 1899:
                return 0
    elif cid == 0x1836:
        if context_mode == 0 and api.life_points(player) > 3999:
            return 0
    elif cid == 0x17DD:
        if context_mode == 0 and call(0xED9FC, player, 0x0FF8) != 0:
            return 0
    elif cid in RACE_COUNT_ATTACK:
        if context_mode == 0:
            race = call(0xFFA78, cid)
            own_count = call(0xE55AC, player, race)
            opposing_count = call(0xE5988, 1-player)
            if opposing_count <= own_count:
                return 0
    elif cid == 0x1399:
        if context_mode == 0:
            own_count = call(0xE5988, player)
            opposing_count = call(0xE5988, 1-player)
            if opposing_count <= own_count:
                return 0
    elif cid == 0x161C:
        if context_mode == 0 and call(0x707C, player) != 0:
            return 0
    elif cid == 0x1612:
        for slot in range(10):
            current_cid = call(0xE2998, player, slot)
            capacity = call(0xFFB80, current_cid)
            if capacity != 0 and call(0xE28A0, player, slot) < capacity:
                return 0
    elif cid == 0x14AC:
        if option_flag == 0 and call(0xE5988, 1-player) != 0:
            return 0
    elif cid == 0x118A:
        if call(0x6F74, player, 0x142A) != 0:
            return 0
    elif cid == 0x147A:
        if call(0xE5148, player, 0x146F) == 0:
            return 1
    elif cid == 0x14D7:
        if call(0xED950, player, 1) > 1:
            return 0
    elif cid == 0x13A7:
        if api.life_points(player) > 2000:
            return 0
    elif cid in FIELD_10F4_ATTACK:
        if context_mode == 0 and call(0xE76D0, 0x10F4) != 0:
            return 0
    elif cid in (0x1284, 0x1281):
        if call(0xE5988, 1-player) != 0:
            return 0
    elif cid == 0x1694:
        if context_mode == 0:
            left = call(0xE5988, 0)
            right = call(0xE5988, 1)
            total = (left + right) & 0xFFFFFFFF
            if total & 0x80000000: total -= 0x100000000
            return int(total > 0)
    elif cid == 0x12BB:
        if context_mode == 0 and call(0xE5C84, 1-player) != 0:
            return 0
    elif cid in MODE_ZERO_ATTACK:
        if context_mode == 0:
            return 0
    elif cid in BOTH_ZERO_ATTACK:
        if option_flag == 0 and context_mode == 0:
            return 0
    elif cid == 0x0FE0:
        if (context_mode != 0 and call(0xE6184, player) > 1 and
                call(0xE5684, player, 0x12C5) != 0):
            return 0
    if (call(0x32868, player) != 0 and call(0x6F74, player, 0x142A) != 0 and
            call(0x31E8, cid) != 0):
        return 0
    return 1 if context_mode == 0 and call(0x1C88, cid) != 0 else -1


def pre_action_priority(api):
    """1CE08: scan 61 early card rules, then stages 1, 2 and 3 with fallthrough."""
    player = api.load(ACTOR)
    stage = api.load(INNER)
    if stage == 0:
        for index in range(61):
            if api.call(0x1C618, player, api.base + EARLY_TABLE + 8 * index) != 0:
                return 0
        api.store(INNER, 1)
        stage = 1
    if stage == 1:
        if api.call(0x1CD44, player) != 0 and api.call(0x1D888, player) != 0:
            return 0
        api.store(INNER, 2)
        stage = 2
    if stage == 2:
        if api.call(0x1D124, player) != 0:
            return 0
        api.store(INNER, 3)
        stage = 3
    if stage == 3:
        result = api.call(0x1C948, player, 0)
        if result == 0:
            api.store(INNER, 4)
        return int(result == 0)
    return 1


def late_action_priority(api):
    """1DABC: scan 87 late rules, then consider back-row setting capacity."""
    player = api.load(ACTOR)
    stage = api.load(INNER)
    if stage == 0:
        for index in range(87):
            if api.call(0x1C618, player, api.base + LATE_TABLE + 8 * index) != 0:
                return 0
        api.store(INNER, 1)
        stage = 1
    if stage != 1:
        return 1
    if (api.call(0xE6E54, player) > 1 and api.call(0xDB664, player) != 0 and
            api.call(0x6FC4, player) != 0):
        hidden_count = sum(api.card_id(player, slot) != 0 and
                           api.record_word8(player, slot) == 0 and
                           bool(api.flags(player, slot) & (1 << 20))
                           for slot in range(5, 11))
        if (hidden_count < 2 or api.call(0x1CD44, player) != 0) and api.call(0x1D888, player) != 0:
            return 0
    return 1


def make_effect_record(cid, player, slot, full_record):
    """Original zeroed 24-byte descriptor, including all eight instance bits."""
    record = bytearray(24)
    record[:2] = (cid & 0xFFFF).to_bytes(2, 'little')
    record[2] = (player & 1) | ((slot & 31) << 1)
    handle = ((full_record >> 13) & 1) | (((full_record >> 22) & 255) << 1)
    record[4:6] = ((handle << 6) & 0xFFFF).to_bytes(2, 'little')
    return bytes(record)


@dataclass(frozen=True)
class CardRule:
    card_id: int
    predicate: object


def try_priority_rule(player, rule, api):
    """1C618: test field instances then a hand instance for a priority rule.

    Engine legality and the rule predicate stay explicit. Field flags 0x22 must
    be clear. A successful choice submits kind 3 and sets the refresh flag.
    """
    for slot in range(11):
        if api.current_card_id(player, slot) != rule.card_id or api.flags(player, slot) & 0x22:
            continue
        record = make_effect_record(rule.card_id, player, slot, api.field_record(player, slot))
        if (api.effect_legal(record, 0, api.record_word8(player, slot) != 0) and
                not api.activation_rejected(record) and rule.predicate(record, 0)):
            api.set_selection_player(player)
            api.submit(player, slot, 0, 3)
            api.set_refresh(1)
            return 1
    hand_index = api.find_in_hand(player, rule.card_id)
    if hand_index >= 0 and api.hand_activation_allowed(player):
        full_record = api.hand_records(player)[hand_index]
        record = make_effect_record(full_record & 0x1FFF, player, 11, full_record)
        if (api.effect_legal(record, 0, False) and not api.activation_rejected(record) and
                rule.predicate(record, 0)):
            api.set_selection_player(player)
            api.submit(player, 11, hand_index, 3)
            api.set_refresh(1)
            return 1
    return 0
