"""Differential checks of recovered generic targeting and response control flow.

Original MIPS functions run in copied RAM. Legality/stat/property services are
explicit synthetic fixtures; RNG and nested generic selectors execute their
original instructions. Nothing here changes a PPSSPP process or save file.
"""
import csv
import json
import random
import sqlite3
import time
from collections import Counter

from mips_vm import VM, signed
from response_model import (
    ROOT, BASE, ActivationRule, GameRandom, activation_record,
    back_row_target, chain_response, export_controller_route_map, field_mask,
    optional_activation, opponent_removal_target, own_removal_target,
    own_sacrifice_target, random_target, scored_hand_target, scored_target,
    stat_hand_target, stat_target, try_activation_rule, discard_hand_target,
    RESPONSE_SPECIAL_IDS, select_response_target,
)

RNG_STATE = BASE + 0x1155C8
PERSPECTIVE = BASE + 0x10FCF4
SELECTION_PLAYER = BASE + 0x115648
PLAYER = BASE + 0x111E5C
SCORE_CALLBACK = 0x09FFF100
ELIGIBLE_CALLBACK = 0x09FFF110
RULE_CALLBACK = 0x09FFF120
RULE_POINTER = 0x09FFF200
CONTEXT_POINTER = 0x09FFF300


class Fixture:
    base = BASE
    def __init__(self, values, seed):
        self.data = values
        self.rng = GameRandom(seed)
        self.selected = None
        self.selection_player = 82
        self.perspective = 83
        self.table_calls = []
        self.records = []
        self.calls = []

    def random_below(self, count): return self.rng.below(count)
    def stats(self, player, slot): return self.data['stats'][player][slot]
    def flags(self, player, slot): return self.data['flags'][player][slot]
    def card_id(self, player, slot): return self.data['cards'][player][slot]
    def icon(self, cid): return self.data['icons'][cid]
    def record_word8(self, player, slot): return self.data['word8'][player][slot]
    def special_priority(self, cid, mode): return self.data['special'][cid][mode & 1]
    def phase(self): return self.data['phase']
    def actor(self): return self.data['actor']
    def can_attack(self, player, slot): return self.data['attackable'][player][slot]
    def life_points(self, player): return self.data['life'][player]
    def original_owner(self, player, slot): return self.data['owners'][player][slot]
    def current_card_id(self, player, slot): return self.data['current_ids'][player][slot]
    def hand(self, player): return self.data['hands'][player]
    def level(self, cid): return self.data['levels'][cid]
    def property_ffb60(self, cid): return self.data['prop_ffb60'][cid]
    def attribute(self, cid): return self.data['attributes'][cid]
    def has_equip(self, player, slot, cid): return (player, slot, cid) in self.data['equips']
    def printed_attack(self, cid): return self.data['printed_stats'][cid][0]
    def printed_defense(self, cid): return self.data['printed_stats'][cid][1]
    def score(self, player, slot): return self.data['scores'][player][slot]
    def hand_score(self, player, index): return self.data['hand_scores'][player][index]
    def eligible(self, player, slot, index):
        if slot == 11:
            hand = self.data['hand_eligible'][player]
            return hand[index] if index < len(hand) else 0
        return self.data['eligible'][player][slot]
    def set_perspective(self, player): self.perspective = player
    def set_selection_player(self, player): self.selection_player = player
    def is_monster(self, cid): return self.data['prop_ffad8'].get(cid, 0)
    def fallback_1d094(self, player): return self.data['fallback']
    def evaluate_table_rule(self, player, relative, context):
        self.table_calls.append((player, relative, context))
        return int(relative in self.data['accepted_table_entries'])
    def race(self, cid): return self.data['kinds'].get(cid, 0)
    def chain_entries(self): return self.data['chain']
    def activation_blocked(self, player, cid): return self.data['blocked']
    def card_present(self, cid): return cid in self.data['present']
    def field_record(self, player, slot): return self.data['full_records'][player][slot]
    def effect_legal(self, record, context, on_field):
        slot = (record[2] >> 1) & 31
        self.records.append(('legal', record.hex(), context, on_field))
        return self.data['legality'][int(on_field)][slot]
    def activation_rejected(self, record):
        slot = (record[2] >> 1) & 31
        self.records.append(('reject', record.hex()))
        return self.data['rejected'][slot]
    def rule_predicate(self, record, context):
        slot = (record[2] >> 1) & 31
        self.records.append(('predicate', record.hex(), context))
        return self.data['predicate'][slot]
    def find_in_hand(self, player, cid): return self.data['hand_lookup'].get(cid, self.data['hand_index'])
    def hand_activation_allowed(self, player): return self.data['hand_allowed']
    def hand_records(self, player): return self.data['full_hands'][player]
    def submit(self, *args): self.selected = list(args); return 42
    def cancel(self): self.selected = ['cancel']; return 43
    def predicate_address(self, predicate): return ELIGIBLE_CALLBACK
    def hand_card_gate(self, player, mode, cid): return self.data['hand_gate'][cid]
    def ban_limit(self, cid): return self.data['prop_ffbe0'][cid]
    def discard_priority(self, cid, mode, last): return self.data['discard_priority'][cid][mode & 1]
    def field_card_gate(self, player, cid): return self.data['field_gate']
    def field_contains(self, player, cid): return self.data['field_contains']
    def call(self, relative, *args):
        arities = {0x2730:3, 0xFFBE0:1, 0x6A04:2, 0x11BD0:4, 0x11AF8:3,
                   0x1216C:3, 0x123E4:2, 0x119C8:3, 0x12A74:4, 0x1257C:3, 0x11E20:2}
        args = tuple(signed(value) for value in args[:arities[relative]])
        self.calls.append((relative, args))
        if relative == 0x2730: return self.hand_card_gate(*args)
        if relative == 0xFFBE0: return self.ban_limit(*args)
        if relative == 0x6A04: return self.special_priority(*args)
        return self.data['helper_results'][(relative, args[0])]


def fixture_data(rng, iteration):
    important = [0x1874, 0x1873, 0x17E9, 0x17E8, 0x1798, 0x1521,
                 0x1A8E, 0x163F, 0x14F3, 0x11E4, 0x0FD6]
    cards = [[rng.choice(important + [4000 + p * 50 + s]) for s in range(11)] for p in range(2)]
    current = [[rng.choice(important + [cards[p][s]]) for s in range(11)] for p in range(2)]
    discard_ids = [0x1B2A, 0x1AAA, 0x19FE, 0x197F, 0x179A, 0x1181]
    hands = [[rng.choice([4000 + p * 30 + index] * 4 + discard_ids)
              for index in range(rng.randrange(9))] for p in range(2)]
    all_ids = set(sum(cards, []) + sum(current, []) + sum(hands, []))
    values = {
        'cards': cards, 'current_ids': current, 'hands': hands,
        'stats': [[(rng.choice([0, 0, 1000, 1400, 1401, 2500, rng.randrange(5000)]),
                    rng.choice([0, 0, 1000, 1400, 1401, 2500, rng.randrange(5000)])) for s in range(11)] for p in range(2)],
        'flags': [[rng.getrandbits(24) for s in range(11)] for p in range(2)],
        'icons': {c: rng.randrange(8) for c in all_ids},
        'word8': [[rng.choice([0, 0, 1, -1, 255]) for s in range(11)] for p in range(2)],
        'special': {c: [int(rng.random() < .2), int(rng.random() < .2)] for c in all_ids},
        'phase': rng.choice([0, 2, 3, 3, 5]), 'actor': rng.randrange(2),
        'attackable': [[int(rng.random() < .4) for s in range(11)] for p in range(2)],
        'life': [rng.choice([0, 1400, 1401, 2500, 8000]) for p in range(2)],
        'owners': [[p if rng.random() < .95 else 1-p for s in range(11)] for p in range(2)],
        'levels': {c: rng.randrange(1, 10) for c in all_ids},
        'prop_ffb60': {c: int(rng.random() < .25) for c in all_ids},
        'attributes': {c: rng.randrange(1, 7) for c in all_ids},
        'equips': {(p, s, c) for p in range(2) for s in range(11)
                   for c in (0x149D, 0x1286, 0x13F3, 0x14B2) if rng.random() < .015},
        'printed_stats': {c: (rng.choice([0, 0, 1000, 1400, 2500, rng.randrange(5000)]),
                              rng.choice([0, 0, 1000, 1400, 2500, rng.randrange(5000)])) for c in all_ids},
        'scores': [[rng.choice([-100, 0, 0, 1, 300, 300, 700]) for s in range(11)] for p in range(2)],
        'hand_scores': [[rng.choice([-100, 0, 0, 1, 300, 300, 700]) for _ in hand] for hand in hands],
        'hand_eligible': [[int(rng.random() < .5) for _ in hand] for hand in hands],
        'eligible': [[int(rng.random() < .5) for s in range(11)] for p in range(2)],
        'prop_ffad8': {}, 'accepted_table_entries': set(), 'fallback': rng.choice([0, 1, -1]),
        'kinds': {}, 'chain': [], 'blocked': int(rng.random() < .2),
        'present': set(rng.sample([0x184F, 0x171D], rng.choice([0, 0, 0, 1, 2]))),
        'full_records': [[cards[p][s] | (rng.getrandbits(19) << 13) for s in range(11)] for p in range(2)],
        'full_hands': [[cid | (rng.getrandbits(19) << 13) for cid in hand] for hand in hands],
        'legality': [[int(rng.random() < .75) for s in range(12)] for layer in range(2)],
        'rejected': [int(rng.random() < .25) for s in range(12)],
        'predicate': [int(rng.random() < .5) for s in range(12)],
        'hand_index': -1, 'hand_allowed': int(rng.random() < .7),
        'hand_lookup': {0x12EA: rng.choice([-1, -1, 0]), 0x1366: rng.choice([-1, -1, 0])},
        'hand_gate': {cid: int(rng.random() < .4) for cid in all_ids},
        'prop_ffbe0': {cid: rng.choice([0, 1, 2, 3, 3, 4, 5]) for cid in all_ids},
        'discard_priority': {cid: [int(rng.random() < .15), int(rng.random() < .15)] for cid in all_ids},
        'field_gate': int(rng.random() < .3), 'field_contains': int(rng.random() < .3),
        'helper_results': {(a, p):rng.choice([-1, -1, -1, 0, 4, 5, 9]) for p in range(2)
                           for a in (0x11BD0, 0x11AF8, 0x1216C, 0x123E4, 0x119C8, 0x12A74, 0x1257C, 0x11E20)},
    }
    return values


def seed_memory(vm, data, seed):
    vm.store(RNG_STATE, seed)
    vm.store(PERSPECTIVE, 83)
    vm.store(SELECTION_PLAYER, 82)
    vm.store(BASE + 0x1155D0, data['actor'])
    vm.store(BASE + 0x1155DC, data['phase'])
    for p in range(2):
        address = PLAYER + p * 0xFAC
        vm.store(address, data['life'][p])
        vm.store(address + 0xC, len(data['hands'][p]))
        for slot in range(11):
            vm.store(address + 0x30 + 20 * slot, data['full_records'][p][slot])
            vm.store(address + 0x38 + 20 * slot, data['word8'][p][slot], 2)
            vm.store(address + 0x40 + 20 * slot, data['flags'][p][slot])
        for index, record in enumerate(data['full_hands'][p]):
            vm.store(address + 0x120 + 4 * index, record)
    vm.store(BASE + 0x1107D0, len(data['chain']))
    for index, (player, cid) in enumerate(data['chain']):
        record = bytearray(24)
        record[:2] = cid.to_bytes(2, 'little')
        record[2] = player
        for j, byte in enumerate(record):
            vm.store(BASE + 0x110650 + 24 * index + j, byte, 1)


def install_callbacks(vm, api):
    vm.callbacks = {}
    def returning(relative, method, arity):
        vm.callbacks[BASE + relative] = lambda v: v.set(2, method(*[signed(x) for x in v.r[4:4+arity]]))
    returning(0x6A04, api.special_priority, 2)
    returning(0xE8488, api.can_attack, 2)
    returning(0xFFA90, api.icon, 1)
    returning(0xEAA30, api.original_owner, 2)
    returning(0xE2998, api.current_card_id, 2)
    returning(0xFFA88, api.level, 1)
    returning(0xFFB60, api.property_ffb60, 1)
    returning(0xFFA80, api.attribute, 1)
    returning(0xDD1AC, api.has_equip, 3)
    returning(0xFFAA8, api.printed_attack, 1)
    returning(0xFFAB0, api.printed_defense, 1)
    returning(0xFFAD8, api.is_monster, 1)
    returning(0x1D094, api.fallback_1d094, 1)
    returning(0xFFA78, api.race, 1)
    returning(0x682E0, api.activation_blocked, 2)
    returning(0xE4624, api.card_present, 1)
    returning(0xEDAAC, api.find_in_hand, 2)
    returning(0x6FC4, api.hand_activation_allowed, 1)
    returning(0x2730, api.hand_card_gate, 3)
    returning(0xFFBE0, api.ban_limit, 1)
    returning(0x3B38, api.discard_priority, 3)
    returning(0x1141C, api.field_card_gate, 2)
    returning(0xECB3C, api.field_contains, 2)
    def stat_callback(v):
        attack, defense = api.stats(v.r[4], v.r[5])
        v.store(v.r[6] + 20, attack)
        v.store(v.r[6] + 24, defense)
        v.set(2, 0)
    vm.callbacks[BASE + 0x146C] = stat_callback
    vm.callbacks[SCORE_CALLBACK] = lambda v: v.set(2, api.score(v.r[4], v.r[5]))
    vm.callbacks[ELIGIBLE_CALLBACK] = lambda v: v.set(2, api.eligible(v.r[4], v.r[5], v.r[6]))
    vm.callbacks[RULE_CALLBACK] = lambda v: v.set(2, api.rule_predicate(v.read(v.r[4], 24), v.r[5]))
    vm.callbacks[BASE + 0x65ACC] = lambda v: v.set(2, api.effect_legal(v.read(v.r[4], 24), v.r[5], False))
    vm.callbacks[BASE + 0x6581C] = lambda v: v.set(2, api.effect_legal(v.read(v.r[4], 24), v.r[5], True))
    vm.callbacks[BASE + 0x1C54C] = lambda v: v.set(2, api.activation_rejected(v.read(v.r[4], 24)))
    def submit(v):
        api.submit(*[signed(x) for x in v.r[4:8]])
        v.set(2, 42)
    vm.callbacks[BASE + 0x31F34] = submit
    vm.callbacks[BASE + 0x32028] = lambda v: v.set(2, api.cancel())


def export_activation_tables(ram):
    vm = VM(ram)
    db = sqlite3.connect(ROOT / 'reports/card_database/cards.sqlite')
    names = dict(db.execute('SELECT card_id, name FROM cards'))
    db.close()
    groups = []
    for name, relative, count in [('general_activation', 0x101B30, 257), ('chain_response', 0x102338, 86)]:
        entries = []
        for index in range(count):
            cid = vm.load(BASE + relative + 8 * index, 4)
            callback = vm.load(BASE + relative + 8 * index + 4, 4)
            entries.append(dict(index=index, card_id=cid, name=names.get(cid),
                                rule_relative=f'{relative + 8 * index:06X}',
                                predicate_address=f'{callback:08X}', predicate_relative=f'{callback - BASE:06X}'))
        groups.append(dict(name=name, table_relative=f'{relative:06X}', entries=entries,
                           unique_card_ids=len({entry['card_id'] for entry in entries})))
    result = dict(groups=groups, record_count=343,
                  unique_predicate_addresses=len({e['predicate_address'] for group in groups for e in group['entries']}),
                  limitation='Table inventory only. A callback name does not establish its strategy; the full native reconstruction must preserve its original instruction logic.')
    (ROOT / 'reports/response_activation_tables.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def export_ban_limits(ram):
    """Read and verify the game's restriction getter in the captured state."""
    vm = VM(ram)
    connection = sqlite3.connect(ROOT / 'reports/card_database/cards.sqlite')
    names = dict(connection.execute('SELECT card_id, name FROM cards'))
    connection.close()
    static = {}
    entries = []
    for index in range(83):
        cid = vm.load(0x0887544C + 4 * index, 2)
        limit = vm.load(0x0887544E + 4 * index, 2)
        static[cid] = limit
        entries.append(dict(card_id=cid, name=names.get(cid), limit=limit))
    rows, mismatches = [], []
    for cid, name in sorted(names.items()):
        current = vm.call(0x0884DC54, cid)
        rows.append(dict(card_id=cid, name=name, limit=current))
        expected = static.get(cid, 3)
        if current != expected:
            mismatches.append(dict(card_id=cid, captured=current, default=expected))
    report = dict(import_stub='eng+0xFFBE0', getter='modehsys 0x0884DC54',
                  default_table='0x0887544C', default_table_entries=83,
                  status_meaning={'0':'forbidden', '1':'one copy', '2':'two copies', '3':'three copies'},
                  card_count=len(rows), default_mismatches=len(mismatches),
                  mismatches=mismatches, default_entries=entries, captured_cards=rows,
                  limitations='Restrictions belong to this game version and captured game state. The getter may use a current save/settings override; no modern tournament list is implied.')
    (ROOT / 'reports/response_ban_limits.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def structurally_reachable(vm, start, length):
    """Conservative intraprocedural CFG with MIPS delay-slot/likely semantics.

    Calls may return; external tail jumps and jr end a path. Unknown conditional
    branches explore both paths. Only register-identity or zero-register cases
    are folded. This identifies compiler duplicates following unconditional
    transfers without treating missed instructions as padding.
    """
    end = start + length
    todo, states, addresses = [(start, None)], set(), set()
    while todo:
        pc, after_delay = todo.pop()
        if not start <= pc < end or (pc, after_delay) in states:
            continue
        states.add((pc, after_delay))
        addresses.add(pc)
        if after_delay is not None:
            if after_delay != -1: todo.append((after_delay, None))
            continue
        word = vm.load(pc, 4)
        op, rs, rt, fn = word >> 26, (word >> 21) & 31, (word >> 16) & 31, word & 63
        immediate = word & 0xFFFF
        if immediate & 0x8000: immediate -= 0x10000
        target = pc + 4 + immediate * 4
        if op == 0 and fn in (8, 9):
            todo.append((pc + 4, pc + 8 if fn == 9 else -1))
        elif op in (2, 3):
            todo.append((pc + 4, pc + 8 if op == 3 else ((pc + 4) & 0xF0000000) | ((word & 0x3FFFFFF) << 2)))
        elif op in (1, 4, 5, 6, 7, 20, 21, 22, 23):
            likely = op in (20, 21, 22, 23) or op == 1 and rt in (2, 3, 18, 19)
            can_take = can_skip = True
            if op in (4, 20) and rs == rt: can_skip = False
            elif op in (5, 21) and rs == rt: can_take = False
            elif op in (6, 22) and rs == 0: can_skip = False
            elif op in (7, 23) and rs == 0: can_take = False
            elif op == 1 and rs == 0:
                if rt in (0, 2, 16, 18): can_take = False
                elif rt in (1, 3, 17, 19): can_skip = False
            if can_take: todo.append((pc + 4, pc + 8 if op == 1 and rt in (16, 17, 18, 19) else target))
            if can_skip: todo.append((pc + 8, None) if likely else (pc + 4, pc + 8))
        else:
            todo.append((pc + 4, None))
    return addresses


def main():
    started = time.perf_counter()
    rng = random.Random(15746)
    ram = (ROOT / 'work/ram_turn25.bin').read_bytes()
    vm = VM(ram)
    cases, failures, counts = 0, [], Counter()
    metadata = {int(row['address'], 16) - BASE: int(row['bytes'])
                for row in csv.DictReader((ROOT / 'work/decompiled/modduel_eng_relocated/function_index.csv').open())}

    def check(relative, iteration, actual, expected, actual_api, expected_api, compare_rng=True, extra=None):
        nonlocal cases
        cases += 1
        counts[f'{relative:06X}'] += 1
        native_state = vm.load(RNG_STATE, 4)
        mismatch = actual != expected or (compare_rng and native_state != expected_api.rng.state)
        if extra: mismatch |= extra(actual_api, expected_api)
        if mismatch:
            failures.append(dict(relative=f'{relative:06X}', iteration=iteration, actual=actual, expected=expected,
                                 native_rng=f'{native_state:08X}', model_rng=f'{expected_api.rng.state:08X}',
                                 actual_selection=actual_api.selected, expected_selection=expected_api.selected,
                                 actual_records=actual_api.records, expected_records=expected_api.records,
                                 actual_table_calls=actual_api.table_calls, expected_table_calls=expected_api.table_calls))

    # RNG bounds include a large count to check the machine's 32-bit overflow.
    for iteration in range(200):
        seed = rng.getrandbits(32)
        api = Fixture({}, seed)
        vm.callbacks = {}
        vm.store(RNG_STATE, seed)
        bound = rng.choice([0, 1, 2, 11, 257, 32768, 10000000])
        actual = vm.call(BASE + 0x48D58, bound)
        expected = api.random_below(bound)
        check(0x48D58, iteration, actual, expected, api, api)

    for iteration in range(600):
        data = fixture_data(rng, iteration)
        seed = rng.getrandbits(32)
        player = rng.randrange(2)
        mask = rng.choice([0, 0xFFFFFFFF, 1 << (16 * player + rng.randrange(11)), rng.getrandbits(32)])
        mode, strict = rng.randrange(2), rng.randrange(2)
        atk_mode, def_mode = rng.choice([-1, 0, 1]), rng.choice([-1, 0, 1])
        tests = [
            (0x11E20, (player, mask), lambda a: random_target(player, mask, a)),
            (0x119C8, (player, mask, SCORE_CALLBACK), lambda a: scored_target(player, mask, a.score, a)),
            (0x11548, (player, mask, atk_mode, def_mode), lambda a: stat_target(player, mask, atk_mode, def_mode, a)),
            (0x11E90, (player, mask), lambda a: back_row_target(player, mask, a)),
            (0x1216C, (player, mask, mode), lambda a: opponent_removal_target(player, mask, mode, a)),
            (0x123E4, (player, mask), lambda a: own_removal_target(player, mask, a)),
            (0x1257C, (player, mask, strict), lambda a: own_sacrifice_target(player, mask, strict, a)),
            (0x11BD0, (player, ELIGIBLE_CALLBACK, atk_mode, def_mode), lambda a: stat_hand_target(player, a.eligible, atk_mode, def_mode, a)),
            (0x154D0, (ELIGIBLE_CALLBACK,), lambda a: signed(field_mask(a.eligible))),
            (0x12A74, (player, ELIGIBLE_CALLBACK, mode, strict), lambda a: discard_hand_target(player, a.eligible, mode, strict, a)),
        ]
        for relative, args, model in tests:
            actual_api, expected_api = Fixture(data, seed), Fixture(data, seed)
            seed_memory(vm, data, seed)
            install_callbacks(vm, actual_api)
            actual = vm.call(BASE + relative, *args)
            expected = model(expected_api)
            check(relative, iteration, actual, expected, actual_api, expected_api)
        # The two callbacks have different signatures in 11AF8.
        actual_api, expected_api = Fixture(data, seed), Fixture(data, seed)
        seed_memory(vm, data, seed)
        install_callbacks(vm, actual_api)
        vm.callbacks[SCORE_CALLBACK] = lambda v: v.set(2, actual_api.hand_score(v.r[4], v.r[5]))
        actual = vm.call(BASE + 0x11AF8, player, ELIGIBLE_CALLBACK, SCORE_CALLBACK)
        expected = scored_hand_target(player, expected_api.eligible, expected_api.hand_score, expected_api)
        check(0x11AF8, iteration, actual, expected, actual_api, expected_api)

    # Force the uncommon material-matching path rather than relying on its
    # small probability in generated fields. Earlier hand entries independently
    # fail level, property and attribute gates before the fourth entry matches.
    for iteration, material in enumerate((0x1874, 0x1873, 0x17E9, 0x17E8, 0x1798, 0x1521)):
        data = fixture_data(rng, iteration)
        seed, player, mask = rng.getrandbits(32), 0, 1
        data['owners'][0][0] = 0
        data['flags'][0][0] = 0
        data['current_ids'][0][0] = material
        data['hands'][0] = [7000, 7001, 7002, 7003]
        data['full_hands'][0] = list(data['hands'][0])
        data['levels'].update({7000:6, 7001:7, 7002:7, 7003:7})
        data['prop_ffb60'].update({7000:0, 7001:1, 7002:0, 7003:0})
        data['attributes'].update({material:1, 7000:1, 7001:1, 7002:2, 7003:1})
        actual_api, expected_api = Fixture(data, seed), Fixture(data, seed)
        seed_memory(vm, data, seed)
        install_callbacks(vm, actual_api)
        actual = vm.call(BASE + 0x1257C, player, mask, 0)
        expected = own_sacrifice_target(player, mask, 0, expected_api)
        check(0x1257C, 600+iteration, actual, expected, actual_api, expected_api)

    for cid in sorted(RESPONSE_SPECIAL_IDS | {4007, 0x1FFF}):
        for iteration in range(50):
            data = fixture_data(rng, iteration)
            seed, player = rng.getrandbits(32), rng.randrange(2)
            actual_api, expected_api = Fixture(data, seed), Fixture(data, seed)
            seed_memory(vm, data, seed)
            install_callbacks(vm, actual_api)
            for rel, arity in {0x2730:3, 0xFFBE0:1, 0x6A04:2, 0x11BD0:4, 0x11AF8:3,
                               0x1216C:3, 0x123E4:2, 0x119C8:3, 0x12A74:4, 0x1257C:3, 0x11E20:2}.items():
                def callback(v, relative=rel, n=arity):
                    v.set(2, actual_api.call(relative, *v.r[4:4+n]))
                vm.callbacks[BASE + rel] = callback
            vm.call(BASE + 0x1556C, player, cid, ELIGIBLE_CALLBACK)
            select_response_target(player, cid, expected_api.eligible, expected_api)
            actual_api.perspective = vm.load(PERSPECTIVE, 4)
            actual_api.selection_player = vm.load(SELECTION_PLAYER, 4)
            check(0x1556C, iteration, 0, 0, actual_api, expected_api,
                  extra=lambda a,e: (a.selected != e.selected or a.calls != e.calls or
                                     a.perspective != e.perspective or a.selection_player != e.selection_player))

    for iteration in range(800):
        data = fixture_data(rng, iteration)
        seed, player = rng.getrandbits(32), rng.randrange(2)
        successes = rng.choice([[], [], [0], [85], [256], [rng.randrange(257)]])
        data['accepted_table_entries'] = {0x101B30 + 8*i for i in successes}
        if rng.random() < .5:
            data['accepted_table_entries'].add(0x102338 + 8*rng.randrange(86))
        cid = rng.choice([4007, 0x13F9, 5500])
        data['prop_ffad8'][cid] = rng.randrange(2)
        record = bytearray(24)
        record[:2] = cid.to_bytes(2, 'little')
        record[2], record[3] = rng.getrandbits(8), rng.getrandbits(8)
        for relative in (0x11288, 0x1130C):
            actual_api, expected_api = Fixture(data, seed), Fixture(data, seed)
            seed_memory(vm, data, seed)
            install_callbacks(vm, actual_api)
            vm.callbacks[BASE + 0x10EC8] = lambda v: v.set(2, actual_api.evaluate_table_rule(v.r[4], v.r[5]-BASE, v.r[6]))
            for j, byte in enumerate(record): vm.store(CONTEXT_POINTER + j, byte, 1)
            if relative == 0x11288:
                actual = vm.call(BASE + relative, player)
                expected = optional_activation(player, expected_api)
            else:
                actual = vm.call(BASE + relative, player, CONTEXT_POINTER)
                expected = chain_response(player, CONTEXT_POINTER, bytes(record), expected_api)
            actual_api.perspective = vm.load(PERSPECTIVE, 4)
            check(relative, iteration, actual, expected, actual_api, expected_api,
                  extra=lambda a,e: a.table_calls != e.table_calls or a.perspective != e.perspective)

    for iteration in range(1000):
        data = fixture_data(rng, iteration)
        seed, player = rng.getrandbits(32), rng.randrange(2)
        cid = rng.choice([4007, 5010, 6002])
        data['kinds'][cid] = rng.choice([0, 0, 21, 22, 23])
        data['prop_ffad8'][cid] = rng.randrange(2)
        data['current_ids'] = [[cid if rng.random() < .25 else 4000+s for s in range(11)] for p in range(2)]
        if rng.random() < .15: data['chain'].append((player, cid))
        if rng.random() < .15: data['chain'].append((1-player, cid))
        if rng.random() < .25: data['chain'].append((player, cid+1))
        if data['hands'][player] and rng.random() < .5:
            data['hand_index'] = rng.randrange(len(data['hands'][player]))
        actual_api, expected_api = Fixture(data, seed), Fixture(data, seed)
        seed_memory(vm, data, seed)
        install_callbacks(vm, actual_api)
        vm.store(RULE_POINTER, cid)
        vm.store(RULE_POINTER + 4, RULE_CALLBACK)
        actual = vm.call(BASE + 0x10EC8, player, RULE_POINTER, CONTEXT_POINTER)
        expected = try_activation_rule(player, ActivationRule(cid, expected_api.rule_predicate), CONTEXT_POINTER, expected_api)
        actual_api.selection_player = vm.load(SELECTION_PLAYER, 4)
        check(0x10EC8, iteration, actual, expected, actual_api, expected_api,
              extra=lambda a,e: a.selected != e.selected or a.records != e.records or a.selection_player != e.selection_player)

    coverage = []
    for text, count in sorted(counts.items()):
        relative = int(text, 16)
        length = metadata[relative]
        visited = sum(BASE + relative <= pc < BASE + relative + length for pc in vm.visits)
        unvisited = [f'{pc:08X}' for pc in range(BASE + relative, BASE + relative + length, 4) if pc not in vm.visits]
        reachable = structurally_reachable(vm, BASE + relative, length)
        reachable_unvisited = [f'{pc:08X}' for pc in sorted(reachable) if pc not in vm.visits]
        coverage.append(dict(relative=text, cases=count, instructions_visited=visited, total_instructions=length//4,
                             unvisited_instruction_addresses=unvisited,
                             structurally_reachable_instructions=len(reachable),
                             reachable_instructions_not_visited=reachable_unvisited))
    result = dict(seed=15746, case_count=cases, mismatch_count=len(failures), failures=failures[:10],
                  routines=coverage, seconds=round(time.perf_counter()-started, 3),
                  limitations='Original recovered routines executed with explicit synthetic legality, stats, ownership, printed-property and card-callback fixtures. Original LCG and nested generic target selectors execute their own instructions. Table schedulers verify ordering and descriptors, not all 343 card predicates. No complete-duel/live-chain parity is claimed.')
    (ROOT / 'reports/response_validation.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    tables = export_activation_tables(ram)
    bans = export_ban_limits(ram)
    routes = export_controller_route_map()
    print(json.dumps(result, indent=2))
    print(json.dumps(dict(activation_records=tables['record_count'], unique_predicates=tables['unique_predicate_addresses'],
                          controller_routines=routes['routines_with_controller_references'], controller_references=routes['reference_count'],
                          ban_limit_cards=bans['card_count'], default_ban_limit_mismatches=bans['default_mismatches'])))
    if failures: raise SystemExit(1)


if __name__ == '__main__': main()
