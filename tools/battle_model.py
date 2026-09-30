"""Readable reconstruction of TF1 attack planning and battle controllers.

Recovered routines use module-relative addresses to identify unrenamed game
helpers. The adapter API is load(relative), store(relative, u32),
call(relative, *args), score_attack(player, attacker, defender, permissive),
and evaluate_target(player, attacker, flags, permissive). score_attack returns
the original eight-word evaluation record; evaluate_target returns
(accepted, record). Those helpers may execute reconstructed game code or a
private RAM interpreter. No live process, ISO, or save is opened by this module.

The simulator and target-ranking logic below are AI decisions. The damage,
effect, visibility, legality and special-card mechanics are supplied by the
duel engine and are not approximated with ATK subtraction.
"""

BASE = 0x09E65C00
PLAYERS = 0x111E5C
PLAYER_STRIDE = 0xFAC
PERSPECTIVE = 0x10FCF4
CONTROLLERS = 0x10F9F8
BATTLE_OUTER = 0x11560C
BATTLE_INNER = 0x115610
PENDING = 0x115614
ATTACK_RESULT = 0x10FEF0
BATTLE_PLAYER = 0x10FF10
RESPONSE_KIND = 0x115618
PHASE_CHOICE = 0x115624
INPUT_READY = 0x115638
INPUT_REPEAT = 0x11563C
INPUT_COMMAND = 0x115640
INPUT_SUM_A = 0x115650
INPUT_SUM_B = 0x115654
CACHE_VALID = 0x10FEC4
CACHE_SCORE = 0x10FED8
BATTLE_HANDLERS = (
    0x333A4, 0x33500, 0x33B9C, 0x345C8, 0x34980, 0x34A84,
    0x35360, 0x362BC, 0x36EF8, 0x374A8, 0x37B00, 0x39798,
    0x3A63C, 0x3B0DC, None,
)

# Exact stripped-game dispatch IDs, not a proposed general-purpose heuristic.
PRIORITY_ELEVEN = frozenset((0x18A4, 0x12A0, 0x128F, 0x11CD))
PRIORITY_NINE_CONDITIONAL = frozenset((
    0x19C2, 0x18F3, 0x1752, 0x1963, 0x17DF, 0x1829, 0x1823,
    0x15E0, 0x1524,
))
PRIORITY_EIGHT_CONDITIONAL = frozenset((
    0x1A95, 0x1A23, 0x19D3, 0x19A4, 0x1987, 0x194F, 0x1943,
    0x193B, 0x18C8, 0x18AE, 0x17C8, 0x17C7, 0x1792, 0x1791,
    0x174B, 0x174A, 0x172B, 0x172A, 0x170B, 0x1704, 0x16C5,
    0x1641, 0x164D, 0x15DC, 0x1592, 0x158F, 0x1520, 0x147A,
    0x17DA, 0x17D8, 0x17D7, 0x17D3, 0x17D2, 0x1529, 0x1A93,
    0x19F8, 0x14D5, 0x19D4, 0x1861, 0x1826, 0x17E3, 0x16CB,
    0x13B1,
))
PRIORITY_SEVEN = frozenset((0x1A4E, 0x19C1, 0x186C, 0x1958, 0x1644, 0x1505))
SPECIAL_DIRECT = frozenset((
    0x1A78, 0x1A52, 0x1A61, 0x1915, 0x1912, 0x15D2, 0x1566,
    0x1419, 0x1993, 0x14D6,
))


def signed(value):
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def field(player, slot):
    return PLAYERS + (player & 1) * PLAYER_STRIDE + 0x30 + slot * 20


def half(api, relative):
    """Read a halfword through the phase-model-compatible word API."""
    return (api.load(relative & ~3) >> ((relative & 3) * 8)) & 0xFFFF


def set_half(api, relative, value):
    aligned = relative & ~3
    shift = (relative & 3) * 8
    word = api.load(aligned)
    api.store(aligned, (word & ~(0xFFFF << shift)) | ((value & 0xFFFF) << shift))


def card_id(api, player, slot):
    return half(api, field(player, slot)) & 0x1FFF


def record(api, player, slot, offset):
    return api.load(PERSPECTIVE + player * 0xDC + slot * 0x2C + offset)


def effect_is_active(api, player, slot):
    flags = api.load(field(player, slot) + 16)
    return (~((flags & 3) >> 1) & half(api, field(player, slot) + 8)
            & ~((flags & 0x3F) >> 5)) != 0


def refresh_field_records(api, player, set_perspective=True):
    """fn_00AA88's preparation: only occupied slots receive new estimates."""
    if set_perspective:
        api.store(PERSPECTIVE, player)
    for owner in range(2):
        for slot in range(5):
            if card_id(api, owner, slot):
                output = PERSPECTIVE + owner * 0xDC + slot * 0x2C + 0x18
                api.call(0x146C, owner, slot, getattr(api, 'base', BASE) + output)


def battle_outer_tick(api, player):
    """fn_03B174: the 14-handler battle scheduler, one tick at a time."""
    step = api.load(BATTLE_OUTER)
    if not 0 <= step < len(BATTLE_HANDLERS):
        raise ValueError('Battle scheduler index is outside its function table')
    handler = BATTLE_HANDLERS[step]
    if handler is None:
        return 1
    if api.call(handler, player) != 0:
        api.store(BATTLE_INNER, 0)
        # Reload after the handler: a handler is permitted to change the step.
        api.store(BATTLE_OUTER, api.load(BATTLE_OUTER) + 1)
    return 0


def battle_start_tick(api, player):
    """fn_0333A4: entry actions and special-card battle interruption."""
    step = api.load(BATTLE_INNER)
    if step not in (0, 1):
        return 1
    if step == 0:
        if api.call(0x3298C, player) != 0:
            api.call(0xC49BC, player, 11, 0x16A1, 1)
            api.call(0x46CD8, player, 0x136)
            api.store(BATTLE_OUTER, 13)
            api.store(BATTLE_INNER, 0)
            api.store(PENDING, 1)
            return 0
        owner = 0x8000 if player else 0
        api.call(0xD0C00, owner | 0x18, 1, 0, 0)
        api.call(0xD0C00, owner | 0x0F, 0, 0, 0)
        api.store(BATTLE_INNER, step + 1)
    index = api.call(0xDE694, player, 11, 0x1A7A)
    if index >= 0:
        api.call(0xC4AD0, player, 11, 0x1A7A, index)
        api.call(0x4B30C, (player << 31) | 0x0A501A7A, 0, index)
    return int(index < 0)


def battle_cleanup_tick(api, player=0):
    """fn_03B0DC: clear the five back-row Fairy Box slots for both owners."""
    for owner in range(2):
        for slot in range(5, 10):
            if card_id(api, owner, slot) == 0x13F9:
                api.call(0xC4E88, owner, slot)
    return 1


def _finish_battle(api, pending):
    api.store(BATTLE_OUTER, 12)
    api.store(BATTLE_INNER, 0)
    api.store(PENDING, pending)
    return 0


def attack_controller_tick(api, player):
    """fn_033500: AI/human attack selection, repeats, and phase exits.

    The AI branch calls the attacker planner and a pre-attack effect pass.
    Human-response paths are preserved because the same battle controller
    handles both types of participant and can enter through saved states.
    """
    api.store(BATTLE_PLAYER, player)
    step = api.load(BATTLE_INNER)
    if step == 0:
        api.store(0x10FF18, 0)
        api.store(0x10FF24, 0)
        api.call(0xD0C00, (0x8000 if player else 0) | 0x15, 0, 0, 0)
        api.store(BATTLE_INNER, step + 1)
        return 0
    if step == 1:
        if api.call(0x3F844) != 0:
            attacker = api.call(0x3F440, player)
            target = api.call(0x3F4D8, 1 - player)
            if (attacker < 0 or target < 0
                    or api.call(0xE8488, player, attacker, 1) == 0
                    or api.call(0xE92E0, player, attacker, 1 - player, target, 1) == 0):
                return _finish_battle(api, 0)
            api.store(0x10FF2C, api.call(0x3F440, player))
            api.store(0x10FF20, 0)
            api.store(0x10FF1C, 0)
            return 1
        if api.call(0xE43C8, player, 0x1A79, -1) != 0:
            for attacker in range(5):
                if (api.call(0xE2998, player, attacker) == 0x1A79
                        and api.load(field(player, attacker) + 12) != 0):
                    can_attack = False
                    if api.call(0xE8488, player, attacker, 1) != 0:
                        for target in range(5):
                            if api.call(0xE92E0, player, attacker, 1 - player, target, 1) != 0:
                                can_attack = True
                                break
                    if not can_attack:
                        api.call(0xC8178, player, attacker, 0)
                        return 0
        if api.load(CONTROLLERS + player * 4) == 1:
            mode = int(api.call(0x329D8, player) == 0)
            planned = api.call(0xAA88, player, mode)
            if api.call(0x1CFD0, player, planned) != 0:
                api.store(INPUT_REPEAT, 1)
                return 0
            if planned == 0:
                return _finish_battle(api, 0)
            api.store(0x10FF20, 0)
            api.store(0x10FF1C, 0)
            api.store(0x10FF28, 0)
            api.store(0x10FF2C, api.load(ATTACK_RESULT + 8))
            return 1
        for attacker in range(5):
            if api.call(0xE83BC, player, attacker, 1) != 0:
                api.call(0xFB020, 7, player, attacker, 0)
                break
        api.call(0x31944, 3)
        api.store(BATTLE_INNER, api.load(BATTLE_INNER) + 1)
        return 0
    if step == 2:
        if api.load(INPUT_READY) == 0:
            if api.call(0x329D8, player) != 0:
                if api.call(0xDE2F8, player, 11, 0x11ED) != 0:
                    api.call(0x46D18, 0xDD)
                    api.store(BATTLE_INNER, api.load(BATTLE_INNER) + 1)
                    return 0
                command = api.load(INPUT_COMMAND)
                if command == 0x12:
                    if api.call(0x3F344, player) != 0:
                        return _finish_battle(api, 1)
                elif command == 0x11:
                    return _finish_battle(api, 0)
            api.store(PHASE_CHOICE, 0)
            api.store(BATTLE_INNER, api.load(BATTLE_INNER) + 1)
            return 0
        if api.load(INPUT_COMMAND) == 0:
            api.store(0x10FF20, 0)
            api.store(0x10FF1C, 0)
            api.store(0x10FF28, 0)
            total = (api.load(INPUT_SUM_A) + api.load(INPUT_SUM_B)) & 0xFFFFFFFF
            api.store(0x10FF2C, total)
            api.call(0xFB020, 11, total, 1 - player, -1)
            return 1
        api.store(INPUT_REPEAT, 1)
        api.store(BATTLE_INNER, 1)
        return 0
    if step == 3:
        if api.load(PHASE_CHOICE) != 0:
            return _finish_battle(api, 0)
        mask = 8
        if api.call(0x329D8, player) != 0:
            mask = 0x18
            if api.call(0x3F344, player) != 0:
                mask = 0x38
        api.call(0x46F20, mask)
        api.store(BATTLE_INNER, api.load(BATTLE_INNER) + 1)
        return 0
    if step == 4:
        phase = api.load(PHASE_CHOICE)
        if phase == 5:
            return _finish_battle(api, 1)
        if phase == 4:
            _finish_battle(api, 0)
            api.call(0xC49BC, player, 11, 0x11ED, 1)
            return 0
        api.store(BATTLE_INNER, 1)
    return 0


def attacker_order(api, player, mode):
    """fn_009C1C's filter and selection sort; equal keys retain strict '<'."""
    attackers = []
    for slot in range(5):
        if api.call(0xE83BC, player, slot, 1) == 0:
            continue
        flags = api.load(field(player, slot) + 16)
        if mode < 0 and flags & (1 << 22):
            skip = (api.call(0xDE2F8, player, 11, 0x15FF) != 0
                    or api.call(0xDE2F8, player, 11, 0x195B) != 0)
            if not skip and effect_is_active(api, player, slot):
                skip = api.call(0xE2998, player, slot) in PRIORITY_SEVEN
            if skip:
                continue
        attackers.append(slot)
    # Preserve the original selection-sort tie behavior, including swaps.
    for position in range(len(attackers) - 1):
        chosen, best = position, 0xFFFFFFFF
        for index in range(position, len(attackers)):
            slot = attackers[index]
            tier = 10
            if not api.load(field(player, slot) + 16) & (1 << 5):
                cid = api.call(0xE2998, player, slot)
                if cid in PRIORITY_ELEVEN:
                    tier = 11
                elif cid == 0x147D:
                    tier = 9
                elif cid in PRIORITY_NINE_CONDITIONAL:
                    for target in range(5):
                        if (card_id(api, 1 - player, target)
                                and api.call(0x815C, player, slot, target) != 0):
                            tier = 9
                            break
                elif cid in PRIORITY_EIGHT_CONDITIONAL:
                    if api.call(0xE5988, 1 - player) != 0:
                        tier = 8
                elif cid in PRIORITY_SEVEN:
                    tier = 7
            key = (record(api, player, slot, 0x2C) & 0xFFFF) | (tier << 16)
            if key < best:
                chosen, best = index, key
        attackers[position], attackers[chosen] = attackers[chosen], attackers[position]
    return attackers


def scan_attackers(api, player, mode=0):
    """fn_009C1C: ranked first-success scan; fallback runs in reverse order."""
    for offset in (0, 16, 20, 24, 28):
        api.store(ATTACK_RESULT + offset, 0)
    ordered = attacker_order(api, player, mode)
    for permissive, order in ((0, ordered), (1, list(reversed(ordered)) if mode > 0 else [])):
        for slot in order:
            accepted, evaluation = api.evaluate_target(player, slot, 1, permissive)
            if accepted:
                for index, value in enumerate(evaluation):
                    api.store(ATTACK_RESULT + index * 4, value)
                return 1
    return 0


def choose_attack(api, player, mode=0):
    """fn_00AA88, including its returned result rather than Ghidra's void hint."""
    refresh_field_records(api, player)
    return scan_attackers(api, player, mode)


def choose_attack_target(api, player, attacker, flags=1, permissive=0, output=None):
    """fn_009924: direct attack or five targets, compared by an eight-word record.

    The record contains raw decision fields, not a newly invented scoring
    formula. Word 1 is acceptability, 6/7 are predicted owner/opponent damage,
    4/5 are predicted losses. Equal evaluations preserve the game's hidden-
    position and strength tie breakers. Unwritten output words remain intact.
    """
    result = list(output) if output is not None else [0] * 8
    direct = api.call(0xEA248, player, attacker) != 0
    if not direct:
        direct = (api.call(0xEA28C, player, attacker, 1) != 0
                  and api.call(0xE43C8, 1 - player, 0x17FE, -1) == 0)
    if direct:
        if permissive == 0 and api.call(0x9288, player, attacker, 5, flags) == 0:
            return 0, result
        result = list(api.score_attack(player, attacker, 5, permissive))
        return int(permissive != 0 or signed(result[7]) > 0), result
    for index, value in ((0, 0), (1, 0), (6, 0), (7, 0xFFFF0000)):
        result[index] = value
    for target in range(5):
        if api.call(0xE92E0, player, attacker, 1 - player, target, 1) == 0:
            continue
        if permissive == 0 and api.call(0x9288, player, attacker, target, flags) == 0:
            continue
        candidate = list(api.score_attack(player, attacker, target, permissive))
        better = permissive != 0 and result[1] == 0
        if signed(result[1]) < signed(candidate[1]):
            better = True
        if candidate[1] == result[1]:
            if signed(result[7]) < signed(candidate[7]):
                better = True
            elif candidate[7] == result[7]:
                if signed(result[5]) < signed(candidate[5]):
                    better = True
                elif half(api, field(1 - player, candidate[3]) + 8) == 0:
                    better = True
                elif signed(record(api, 1 - player, result[3], 0x30)) < signed(record(api, 1 - player, candidate[3], 0x30)):
                    better = True
        if better:
            result = candidate
    return int(permissive != 0 or result[1] != 0), result


def choose_target_for_attacker(api, player, attacker, permissive=0):
    """fn_00AB64: recompute estimates and choose a target for one attacker."""
    refresh_field_records(api, player)
    previous = [api.load(ATTACK_RESULT + index * 4) for index in range(8)]
    accepted, result = choose_attack_target(api, player, attacker, 1, permissive, previous)
    for index, value in enumerate(result):
        api.store(ATTACK_RESULT + index * 4, value)
    return accepted


def attack_is_worth_trying(api, player, attacker, target, flags):
    """fn_009288: card-specific vetoes before the full battle simulation."""
    opponent = 1 - player
    against_monster = target < 5
    if not api.load(field(player, attacker) + 16) & (1 << 5):
        cid = api.call(0xE2998, player, attacker)
        if cid == 0x16BF:
            return 1
        if cid == 0x182D:
            if not against_monster:
                return 1
        elif cid in (0x14D7, 0x13A7):
            if half(api, field(opponent, target) + 6) != 0:
                return 0
            if cid == 0x13A7 and signed(api.load(PLAYERS + (player & 1) * PLAYER_STRIDE)) < 2001:
                return 0
        elif cid in (0x1915, 0x1912, 0x15D2, 0x1566, 0x1419):
            if against_monster:
                if half(api, field(opponent, target) + 6) == 0:
                    power = signed(record(api, opponent, target, 0x2C))
                    if 1000 < power < 1900:
                        return 0
                elif signed(record(api, opponent, target, 0x30)) < 1900:
                    return 0
        elif cid in (0x12A0, 0x128F):
            if flags != 0 and not any(
                    card_id(api, player, slot)
                    and signed(record(api, player, slot, 0x2C)) < signed(record(api, player, attacker, 0x2C))
                    for slot in range(5)):
                return 0
        elif cid == 0x1115:
            if ((api.call(0xEA248, player, attacker) == 0
                    or signed(record(api, player, attacker, 0x2C)) < signed(api.load(PLAYERS + (opponent & 1) * PLAYER_STRIDE)))
                    and flags != 0
                    and signed(api.load(PLAYERS + (player & 1) * PLAYER_STRIDE)) > 4000):
                return 0
    if against_monster:
        cid = api.call(0xE2998, opponent, target)
        if cid == 0x148A and half(api, field(opponent, target) + 8) != 0:
            for slot in range(5):
                cid = api.call(0xE2998, opponent, slot)
                if cid and (half(api, field(opponent, slot) + 8) == 0 or cid != 0x148A):
                    return 0
        if api.call(0xE79C8, player, attacker) == 0:
            return 1
        if api.call(0x1810, player, attacker) == 1:
            if signed(record(api, player, attacker, 0x2C)) + 1000 <= api.call(0x850C, opponent, target, 1):
                return 0
    else:
        power = signed(record(api, player, attacker, 0x2C))
        if signed(api.load(PLAYERS + (opponent & 1) * PLAYER_STRIDE)) <= power:
            return 1
        cid = api.call(0xE2998, player, attacker)
        if cid not in SPECIAL_DIRECT and api.call(0xE79C8, player, attacker) != 0:
            if power < 300 or power + 1000 <= api.call(0x850C, opponent, -1, 1):
                return 0
    return 1


def prepare_simulated_positions(api, player):
    """fn_00A414: make eligible hypothetical monsters face-up in attack."""
    for slot in range(5):
        cid = card_id(api, player, slot)
        if cid == 0:
            continue
        if half(api, field(player, slot) + 8) == 0:
            if api.call(0xDB914, player) == 0:
                continue
            if (half(api, field(player, slot) + 8) == 0
                    and api.call(0xFFB48, cid) != 0):
                continue
        if (half(api, field(player, slot) + 6) != 0
                and api.call(0xE79C8, player, slot) != 0):
            set_half(api, field(player, slot) + 6, 0)
            set_half(api, field(player, slot) + 8, 1)


def simulated_attack_score(api, player):
    """fn_00A690: repeated hypothetical battles, with casualties removed.

    This routine deliberately changes the hypothetical monster records. Its
    caller saves and restores the ten real records around it. No arbitrary
    iteration cap is introduced: engine attack eligibility consumes the
    attack-used bits, just as the original routine does.
    """
    api.store(0x10FEC8, 0)
    api.store(0x10FECC, 0)
    for slot in range(5):
        api.store(field(player, slot) + 16,
            api.load(field(player, slot) + 16) & 0xFF3FFFFF)
        api.store(0x10FEDC + slot * 4, 0)
    total = 0
    if api.call(0x32868, player) == 0:
        return 0
    refresh_field_records(api, player, False)
    while api.call(0x9C1C, player, -1) != 0:
        attacker = api.load(ATTACK_RESULT + 8)
        target = api.load(ATTACK_RESULT + 12)
        slot_flags = field(player, attacker) + 16
        flags = api.load(slot_flags)
        api.store(slot_flags, flags | (1 << (23 if flags & (1 << 22) else 22)))
        if api.load(ATTACK_RESULT + 16) != 0:
            set_half(api, field(player, attacker), half(api, field(player, attacker)) & 0xE000)
            api.store(0x10FEC8, api.load(0x10FEC8) + 1)
        if api.load(ATTACK_RESULT + 20) != 0:
            set_half(api, field(1 - player, target), half(api, field(1 - player, target)) & 0xE000)
            api.store(0x10FECC, api.load(0x10FECC) + 1)
        damage = api.load(ATTACK_RESULT + 28)
        api.store(0x10FEDC + attacker * 4, damage + 1)
        total = signed(total + damage)
        refresh_field_records(api, player, False)
    return total


def cached_attack_score(api, player):
    """fn_00AA18: save 200 monster bytes, simulate once, then restore."""
    if api.load(CACHE_VALID) == 0:
        saved = [(field(owner, slot) + offset, api.load(field(owner, slot) + offset))
            for owner in range(2) for slot in range(5) for offset in range(0, 20, 4)]
        api.call(0xA414, player)
        api.store(CACHE_SCORE, api.call(0xA690, player))
        api.store(CACHE_VALID, 1)
        for address, value in saved:
            api.store(address, value)
    return signed(api.load(CACHE_SCORE))


def evaluate_battle(api, player, attacker, target, permissive=0):
    """fn_00860C: preserve the engine context, simulate, then score an attack.

    The two engine simulation calls preserve all card effects. This recovered
    AI layer adds the original card-specific preferences, casualty tradeoffs,
    lethal self-damage veto and permissive fallback. The 304-byte simulation
    context is always restored; only the eight-word result escapes.
    """
    opponent = 1 - player
    result = [int(target >= 5), 0, attacker, target, 0, 0, 0, 0]
    if (target < 5 and effect_is_active(api, player, attacker)
            and api.call(0x815C, player, attacker, target) != 0):
        result[1], result[5] = 1, 1
        return result
    saved = [(address, api.load(address)) for address in range(BATTLE_PLAYER, 0x110040, 4)]
    api.store(BATTLE_PLAYER, player)
    api.store(BATTLE_PLAYER + 4, opponent)
    api.store(BATTLE_PLAYER + 8, result[0])
    api.store(BATTLE_PLAYER + 28, attacker)
    api.store(BATTLE_PLAYER + 32, target)
    api.call(0x42584, 0)
    count = api.call(0xE43C8, opponent, 0x1381, -1)
    # The original assumes the nonnegative count returned by this rule helper.
    for _ in range(max(0, count)):
        address = BATTLE_PLAYER + 0x48 + player * 0x38
        value = signed(api.load(address))
        api.store(address, (value + int(value < 0)) >> 1)
    api.call(0x442F8, 0, 1)
    for event in range(2):
        offset = BATTLE_PLAYER + event * 20
        recipient = api.load(offset + 0xA8)
        damage = api.load(offset + 0x9C)
        index = 6 if recipient == player else 7
        result[index] = (result[index] + damage) & 0xFFFFFFFF
    if result[0] != 0:
        result[1] = 1
    else:
        preserve_trade = False
        if not api.load(field(player, attacker) + 16) & (1 << 5):
            cid = api.call(0xE2998, player, attacker)
            if cid == 0x16BF:
                preserve_trade = True
            elif cid == 0x13CB:
                if (half(api, field(opponent, target) + 6) == 0
                        and api.call(0xE5CE0, player, 0) > 1):
                    result[7] = (result[7] + 500) & 0xFFFFFFFF
            elif cid in (0x172C, 0x1657):
                if result[4] != 0:
                    result[5] = 1
            elif cid == 0x10A6:
                result[4], result[5] = 1, 1
        if effect_is_active(api, opponent, target):
            cid = api.call(0xE2998, opponent, target)
            if cid == 0x129A:
                if half(api, field(opponent, target) + 6) == 0:
                    result[6] = (result[6] + record(api, player, attacker, 0x2C)) & 0xFFFFFFFF
                    result[5] = 1
            elif cid == 0x172C:
                if result[5] != 0:
                    result[4] = 1
            elif cid in (0x1657, 0x10A6):
                result[4], result[5] = 1, 1
        if api.call(0xDE2F8, player, 11, 0x1669) != 0:
            preserve_trade = True
        if api.call(0xDE2F8, player, 11, 0x14A6) != 0:
            preserve_trade = True
        result[4] |= api.load(BATTLE_PLAYER + 0x58 + player * 0x38) | api.load(BATTLE_PLAYER + 0x5C + player * 0x38)
        result[5] |= api.load(BATTLE_PLAYER + 0x58 + opponent * 0x38) | api.load(BATTLE_PLAYER + 0x5C + opponent * 0x38)
        result[1] = int(signed(result[7]) > 0 or signed(result[4]) < signed(result[5]))
        if result[5] != 0 and result[4] != 0:
            power = signed(record(api, player, attacker, 0x2C))
            level = signed(record(api, player, attacker, 0x28))
            if (signed(record(api, player, attacker, 0x30)) < power
                    and (level < 5 or level <= signed(record(api, opponent, target, 0x28)))):
                reference = api.call(0x850C, player, attacker, 1)
                if (reference <= power and reference > 1599
                        and api.call(0x850C, opponent, target, 1) < reference):
                    result[1] = 1
        if result[4] == 0 and result[5] == 0 and api.call(0xE4624, 0x166C) == 0:
            if (api.call(0xDD1AC, player, attacker, 0x143A) != 0
                    and api.call(0xE2990, opponent, target) != 0):
                result[1] = 1
            cid = api.call(0xE2998, opponent, target)
            own_life = signed(api.load(PLAYERS + (player & 1) * PLAYER_STRIDE))
            if cid == 0x152E:
                if (half(api, field(opponent, target) + 8) == 0
                        and own_life > 1000 and signed(result[6]) < 1001):
                    result[1] = 1
            elif (cid == 0x129C and api.call(0x850C, player, attacker, 1) > 999
                    and own_life > 1000 and signed(result[6]) < 1001):
                result[1] = 1
        if signed(api.load(PLAYERS + (player & 1) * PLAYER_STRIDE)) <= signed(result[6]):
            result[1] = 0
        if permissive != 0 and preserve_trade:
            result[1] = 1
    for address, value in saved:
        api.store(address, value)
    return result


RECREATED_BATTLE = {
    0x333A4: battle_start_tick,
    0x33500: attack_controller_tick,
    0x3B0DC: battle_cleanup_tick,
    0x3B174: battle_outer_tick,
    0xAA88: choose_attack,
    0xAB64: choose_target_for_attacker,
    0x9C1C: scan_attackers,
    0x9288: attack_is_worth_trying,
    0xA414: prepare_simulated_positions,
    0xA690: simulated_attack_score,
    0xAA18: cached_attack_score,
}
