"""Readable response and generic target routines recovered from Tag Force 1.

Addresses in docstrings are relative to modduel_eng's 0x09E65C00 base.
This module preserves engine decisions rather than introducing a new strategy.
It is a component of the reconstruction, not a complete standalone duel engine.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = 0x09E65C00


@dataclass
class GameRandom:
    """48D24/48D58: original 32-bit LCG and bounded draw, including overflow."""
    state: int

    def next15(self):
        self.state = (self.state * 0x343FD + 0x269EC3) & 0xFFFFFFFF
        return (self.state & 0x7FFFFFFF) >> 16

    def below(self, count):
        return ((count * self.next15()) & 0xFFFFFFFF) >> 15


def eligible_slots(player, mask, first=0, end=11):
    return [slot for slot in range(first, end)
            if mask & (1 << ((player * 16 + slot) & 31))]


def shuffle_candidates(slots, api):
    """The source uses descending Fisher-Yates; the last index is inclusive."""
    result = list(slots)
    for index in range(len(result) - 1, 0, -1):
        other = api.random_below(index + 1)
        result[index], result[other] = result[other], result[index]
    return result


def random_target(player, mask, api):
    """11E20: use the original random draw to choose a slot; empty mask is -1."""
    slots = eligible_slots(player, mask)
    return slots[api.random_below(len(slots))] if slots else -1


def scored_target(player, mask, score, api):
    """119C8: shuffle then retain the first strictly positive greatest score."""
    chosen = -1
    greatest = 0
    for slot in shuffle_candidates(eligible_slots(player, mask), api):
        value = score(player, slot)
        if value > greatest:
            chosen, greatest = slot, value
    return chosen


def stat_target(player, mask, attack_mode, defense_mode, api):
    """11548: choose a monster using the original signed ATK/DEF modes.

    Modes are normally 1/0 (highest ATK), 1/1 (highest of ATK/DEF),
    or -1/-1 (lowest of ATK/DEF). Mixed mode signs are preserved literally.
    Equal scores retain the earlier candidate after shuffling.
    """
    chosen, best = -1, -1
    for slot in shuffle_candidates(eligible_slots(player, mask, end=5), api):
        attack, defense = api.stats(player, slot)
        if attack_mode != 0 and defense_mode != 0:
            value = max(attack, defense)
        elif attack_mode != 0:
            value = attack
        elif defense_mode != 0:
            value = defense
        else:
            value = 0
        if best < 0:
            chosen, best = slot, value
            continue
        better = ((attack_mode > 0 and best < value) or
                  (attack_mode < 0 and value < best) or
                  (defense_mode > 0 and best < value) or
                  (defense_mode < 0 and value < best))
        if better:
            if attack_mode > 0 and best < value:
                best = value
            if attack_mode < 0 and value < best:
                best = value
            if defense_mode > 0 and best < value:
                best = value
            if defense_mode < 0 and value < best:
                best = value
            chosen = slot
    return chosen


def back_row_target(player, mask, api):
    """11E90: shuffled back row priority passes, then a random fallback.

    Flags and record_word8 retain source field names because their general
    meanings have not all been established. The comparison is exact.
    """
    slots = shuffle_candidates(eligible_slots(player, mask, first=5), api)
    for slot in slots:
        if api.flags(player, slot) & 2 and api.icon(api.card_id(player, slot)) in (2, 3, 4):
            return slot
    for slot in slots:
        if api.record_word8(player, slot) != 0 and not api.flags(player, slot) & 2:
            return slot
    for slot in slots:
        if api.record_word8(player, slot) == 0 and api.flags(player, slot) & (1 << 20):
            return slot
    for slot in slots:
        if api.record_word8(player, slot) == 0:
            return slot
    return slots[api.random_below(len(slots))] if slots else -1


def opponent_removal_target(player, mask, mode, api):
    """1216C: special threats, lethal attacker, strongest monster, back row."""
    slots = shuffle_candidates(eligible_slots(player, mask), api)
    for slot in slots:
        if api.flags(player, slot) & 0x80 and api.special_priority(api.card_id(player, slot), mode):
            return slot
    if api.phase() == 3 and api.actor() == player:
        lethal_mask = 0
        for slot in eligible_slots(player, mask):
            if api.can_attack(player, slot):
                lethal_mask |= 1 << ((player * 16 + slot) & 31)
        candidate = stat_target(player, lethal_mask, 1, 0, api)
        if candidate >= 0 and api.stats(player, candidate)[0] >= api.life_points(1 - player):
            return candidate
    candidate = stat_target(player, mask, 1, 1, api)
    return candidate if candidate >= 0 else back_row_target(player, mask, api)


def own_removal_target(player, mask, api):
    """123E4: exclude priority cards, prefer weakest monster, random fallback."""
    slots = shuffle_candidates(eligible_slots(player, mask), api)
    protected = 0
    for slot in slots:
        if api.special_priority(api.card_id(player, slot), 0):
            protected |= 1 << ((player * 16 + slot) & 31)
    candidate = stat_target(player, mask ^ protected, -1, -1, api)
    return candidate if candidate >= 0 else random_target(player, mask, api)


def own_sacrifice_target(player, mask, strict, api):
    """1257C: stolen monsters, useful material, fixed IDs, equips, weakest.

    Original card IDs and ordering are retained. The engine interface supplies
    current ownership, transformed IDs, card attributes and equip relationships.
    """
    slots = shuffle_candidates(eligible_slots(player, mask), api)
    for slot in slots:
        if api.original_owner(player, slot) != player:
            return slot
    for slot in slots:
        if api.flags(player, slot) & 0x20:
            continue
        current_id = api.current_card_id(player, slot)
        if current_id not in (0x1874, 0x1873, 0x17E9, 0x17E8, 0x1798, 0x1521):
            continue
        for card_id in api.hand(player):
            if (api.level(card_id) >= 7 and api.property_ffb60(card_id) == 0 and
                    api.attribute(card_id) == api.attribute(current_id)):
                return slot
    for slot in slots:
        if api.card_id(player, slot) in (0x1A8E, 0x163F, 0x14F3, 0x11E4, 0x0FD6):
            return slot
    for slot in slots:
        for equip_id in (0x149D, 0x1286, 0x13F3, 0x14B2):
            if api.has_equip(player, slot, equip_id):
                return slot
    protected = 0
    for slot in slots:
        if api.special_priority(api.card_id(player, slot), 0):
            protected |= 1 << ((player * 16 + slot) & 31)
    candidate = stat_target(player, mask ^ protected, -1, -1, api)
    if not strict:
        return candidate if candidate >= 0 else random_target(player, mask, api)
    if candidate >= 0 and max(api.stats(player, candidate)) <= 1400:
        return candidate
    return -1


def scored_hand_target(player, eligible, score, api):
    """11AF8: highest strictly positive score among eligible hand entries."""
    chosen, greatest = -1, 0
    for index in range(len(api.hand(player))):
        if eligible(player, 11, index):
            value = score(player, index)
            if greatest < value:
                chosen, greatest = index, value
    return chosen


def stat_hand_target(player, eligible, attack_mode, defense_mode, api):
    """11BD0: same stat policy as 11548 using printed hand-card statistics."""
    chosen, best = -1, -1
    for index, card_id in enumerate(api.hand(player)):
        if not eligible(player, 11, index):
            continue
        # This preserves the getter call order used in the executable.
        if attack_mode and defense_mode:
            attack, defense = api.printed_attack(card_id), api.printed_defense(card_id)
            value = api.printed_attack(card_id) if defense < attack else api.printed_defense(card_id)
        elif attack_mode:
            value = api.printed_attack(card_id)
        elif defense_mode:
            value = api.printed_defense(card_id)
        else:
            value = 0
        if best < 0:
            chosen, best = index, value
            continue
        better = ((attack_mode > 0 and best < value) or
                  (attack_mode < 0 and value < best) or
                  (defense_mode > 0 and best < value) or
                  (defense_mode < 0 and value < best))
        if better:
            if attack_mode > 0 and best < value:
                best = value
            if attack_mode < 0 and value < best:
                best = value
            if defense_mode > 0 and best < value:
                best = value
            if defense_mode < 0 and value < best:
                best = value
            chosen = index
    return chosen


def field_mask(predicate):
    """154D0: query all 22 field slots and combine their eligibility bits."""
    mask = 0
    for player in (0, 1):
        for slot in range(11):
            if predicate(player, slot, 0):
                mask |= 1 << ((player * 16 + slot) & 31)
    return mask


def discard_hand_target(player, eligible, mode, strict, api):
    """12A74: original ordered discard policy with reservoir-like tie draws.

    The engine's FFB60 property retains its address name until the complete
    classifier is documented. ban_limit is the game's 0..3 restriction status;
    value 3 means the normal three-copy limit.
    """
    high_level = special_179a = special_1181 = category_three = generic = -1
    hand = api.hand(player)
    for index, cid in enumerate(hand):
        if not eligible(player, 11, index):
            continue
        if api.level(cid) > 4 and api.property_ffb60(cid) == 0:
            if high_level < 0 or api.level(cid) > api.level(hand[high_level]):
                high_level = index
        if cid in (0x1B2A, 0x1AAA, 0x19FE, 0x197F):
            return index
        if cid == 0x179A:
            special_179a = index
        elif cid == 0x1181:
            special_1181 = index
        elif api.discard_priority(cid, mode, 1):
            return index
        if api.hand_card_gate(player, 1, cid) == 0 and api.ban_limit(cid) == 3:
            if category_three < 0 or api.random_below(2) != 0:
                category_three = index
        elif generic < 0 or api.random_below(2) != 0:
            generic = index
    has_enabler = (api.find_in_hand(player, 0x12EA) >= 0 or
                   api.find_in_hand(player, 0x1366) >= 0 or api.field_card_gate(player, 0x137D) != 0)
    if has_enabler and high_level >= 0:
        return high_level
    if api.field_contains(player, 0x179A) and api.phase() != 5 and special_179a >= 0:
        return special_179a
    if special_1181 >= 0:
        return special_1181
    if category_three >= 0:
        return category_three
    return generic if not strict else -1


RESPONSE_SPECIAL_IDS = frozenset({
    0x198D, 0x1889, 0x1678, 0x18D9, 0x15E8, 0x1764, 0x16DF, 0x1578,
    0x12DC, 0x142A, 0x15F3, 0x1544, 0x139C, 0x15DC, 0x1888, 0x16D3,
    0x132B, 1, 2, 3,
})


def select_response_target(player, cid, predicate, api):
    """1556C: route a response's field or hand selection to the original helpers.

    This native routine has no specified return value. The observable result is
    a submitted selection/cancellation; Python returns that action's status or
    None when no action is submitted. API.call uses relative engine addresses.
    """
    api.set_perspective(player)
    api.set_selection_player(player)
    mask = field_mask(predicate)
    callback = api.predicate_address(predicate)
    other = 1 - player

    def choose_field(owner, helper, *args):
        slot = api.call(helper, owner, mask, *args)
        if slot >= 0:
            return True, api.submit(owner, slot, 0, 11)
        return False, None

    def choose_hand(helper, *args):
        index = api.call(helper, player, callback, *args)
        if index >= 0:
            return True, api.submit(player, 11, index, 11)
        return False, None

    if cid == 0x198D:
        for index, hand_cid in enumerate(api.hand(player)):
            if (api.call(0x2730, player, 1, hand_cid) == 0 and
                    api.call(0xFFBE0, hand_cid) > 2 and api.call(0x6A04, hand_cid, 0) == 0):
                return api.submit(player, 11, index, 11)
        return api.cancel()
    if cid in (0x1889, 0x1678):
        found, result = choose_hand(0x11BD0, 1, 1)
        if found: return result
    elif cid in (0x18D9, 0x15E8):
        found, result = choose_hand(0x11AF8, api.base + 0x13550)
        if found: return result
    elif cid in (0x1764, 0x16DF, 0x1578):
        found, result = choose_field(other, 0x1216C, 0)
        if found: return result
        found, result = choose_field(player, 0x123E4)
        if found: return result
    elif cid == 0x12DC:
        found, result = choose_field(other, 0x1216C, 0)
        if found: return result
        found, result = choose_field(player, 0x119C8, api.base + 0x1339C)
        if found: return result
    elif cid == 0x142A:
        found, result = choose_field(player, 0x119C8, api.base + 0x12EA4)
        if found: return result
        found, result = choose_field(player, 0x123E4)
        if found: return result
    elif cid in (0x15F3, 0x1544):
        found, result = choose_field(other, 0x1216C, 1)
        if found: return result
        found, result = choose_field(player, 0x123E4)
        if found: return result
    elif cid == 0x139C:
        found, result = choose_field(player, 0x119C8, api.base + 0x130EC)
        if found: return result
    elif cid == 3:
        found, result = choose_hand(0x12A74, 0, 0)
        if found: return result
    elif cid in (0x15DC, 0x1888, 0x16D3, 0x132B, 2):
        found, result = choose_hand(0x12A74, 1, 0)
        if found: return result
    elif cid == 1:
        found, result = choose_field(other, 0x1216C, 0)
        if found: return result
        found, result = choose_field(player, 0x1257C, 0)
        if found: return result
    # The executable uses the acting player's hand count for both passes.
    # Preserve that behavior even when the other player has a shorter hand.
    for owner in (other, player):
        for index in range(len(api.hand(player))):
            if predicate(owner, 11, index):
                return api.submit(owner, 11, index, 11)
    found, result = choose_field(other, 0x11E20)
    if found: return result
    found, result = choose_field(player, 0x11E20)
    return result if found else None


def activation_record(card_id, player, slot, full_card_record):
    """Build the original zeroed 24-byte effect descriptor used by 10EC8."""
    record = bytearray(24)
    record[:2] = (card_id & 0xFFFF).to_bytes(2, 'little')
    record[2] = (player & 1) | ((slot & 31) << 1)
    handle = ((full_card_record >> 13) & 1) | (((full_card_record >> 22) & 255) << 1)
    record[4:6] = ((handle << 6) & 0xFFFF).to_bytes(2, 'little')
    return bytes(record)


@dataclass(frozen=True)
class ActivationRule:
    card_id: int
    predicate: object


def try_activation_rule(player, rule, context, api):
    """10EC8: find the first legal field/hand instance whose card callback agrees.

    Legality and the rule's predicate remain engine services. This routine
    reproduces the selection order, duplicate-chain checks, gates, 24-byte
    descriptors and submitted action. It does not guess a card's predicate.
    """
    card_id = rule.card_id
    if api.race(card_id) == 23:
        for owner, chained_id in api.chain_entries():
            if owner == player and chained_id == card_id:
                return 0
    if not api.is_monster(card_id) and api.activation_blocked(player, card_id):
        return 0
    if api.race(card_id) == 23 and api.card_present(0x184F):
        return 0
    if api.race(card_id) == 22 and api.card_present(0x171D):
        return 0
    for slot in range(11):
        if api.current_card_id(player, slot) != card_id:
            continue
        record = activation_record(card_id, player, slot, api.field_record(player, slot))
        legal = api.effect_legal(record, context, api.record_word8(player, slot) != 0)
        if legal and not api.activation_rejected(record) and rule.predicate(record, context):
            api.set_selection_player(player)
            api.submit(player, slot, 0, 3)
            return 1
    hand_index = api.find_in_hand(player, card_id)
    if hand_index >= 0 and api.hand_activation_allowed(player):
        record = activation_record(card_id, player, 11, api.hand_records(player)[hand_index])
        if (api.effect_legal(record, context, False) and
                not api.activation_rejected(record) and rule.predicate(record, context)):
            api.set_selection_player(player)
            api.submit(player, 11, hand_index, 3)
            return 1
    return 0


def optional_activation(player, api):
    """11288: test all 257 activation-priority records, then the 1D094 fallback."""
    api.set_perspective(player)
    for index in range(257):
        if api.evaluate_table_rule(player, 0x101B30 + 8 * index, 0):
            return 1
    return int(api.fallback_1d094(player) != 0)


def chain_response(player, context, context_record, api):
    """1130C: 86 response records first, then the conditional 257-record list."""
    api.set_perspective(player)
    for index in range(86):
        if api.evaluate_table_rule(player, 0x102338 + 8 * index, context):
            return 1
    owner = (context_record[2] & 1) ^ ((context_record[3] & 0x7F) >> 6)
    card_id = int.from_bytes(context_record[:2], 'little', signed=True)
    if player != owner or api.is_monster(card_id) or card_id == 0x13F9:
        for index in range(257):
            if api.evaluate_table_rule(player, 0x101B30 + 8 * index, context):
                return 1
    return 0


def export_controller_route_map():
    """Inventory every decompiled controller-mode check without claiming coverage.

    Branch excerpts are source evidence, not an invented classification. Ghidra
    declarations and prototypes remain unverified; original-code tests validate
    the readable routines separately.
    """
    directory = ROOT / 'work/decompiled/modduel_eng_relocated'
    metadata = list(csv.DictReader((directory / 'function_index.csv').open(newline='')))
    records = []
    for row in metadata:
        path = directory / (row['name'] + '.c')
        if not path.exists():
            continue
        source = path.read_text(encoding='utf-8')
        if 'controller_modes' not in source and 'DAT_09f755fc' not in source:
            continue
        lines = source.splitlines()
        checks = []
        for index, line in enumerate(lines):
            if 'controller_modes' in line or 'DAT_09f755fc' in line:
                checks.append({'line': index + 1,
                               'context': '\n'.join(lines[max(0, index - 1):index + 9])})
        calls = sorted(set(re.findall(r'\b(?:fn_[0-9A-F]{6}|ai_[a-z_]+|FUN_[0-9a-f]{8})(?=\s*\()', source)) - {row['name']})
        # Heuristic seed names: AI helpers mostly occupy 000000..01DD68. Some
        # routines there are rule helpers; the full calls list is retained.
        ai_calls = [name for name in calls if name.startswith('ai_') or
                    (name.startswith('fn_') and int(name[3:], 16) < 0x1DD68)]
        records.append({'address': row['address'],
                        'relative': f'{int(row["address"], 16) - BASE:06X}',
                        'function': row['name'], 'bytes': int(row['bytes']),
                        'check_count': len(checks), 'checks': checks,
                        'calls': calls, 'early_block_calls': ai_calls})
    report = {'module': 'modduel_eng', 'runtime_base': f'{BASE:08X}',
              'routines_with_controller_references': len(records),
              'reference_count': sum(record['check_count'] for record in records),
              'limitations': 'Decompiled source reference inventory. Checks include human, AI, replay and network modes. Early-block calls are an address-range heuristic, not a completeness proof.',
              'routes': records}
    (ROOT / 'reports/response_controller_routes.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    with (ROOT / 'reports/response_controller_routes.csv').open('w', newline='', encoding='utf-8') as output:
        writer = csv.writer(output)
        writer.writerow(['relative', 'function', 'bytes', 'check_count', 'early_block_calls', 'all_calls'])
        for record in records:
            writer.writerow([record['relative'], record['function'], record['bytes'], record['check_count'],
                             '; '.join(record['early_block_calls']), '; '.join(record['calls'])])
    return report


if __name__ == '__main__':
    report = export_controller_route_map()
    print(json.dumps({key: value for key, value in report.items() if key != 'routes'}, indent=2))
