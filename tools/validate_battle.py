"""Differentially check readable battle code against original private-RAM MIPS.

The original routine bodies execute unchanged. Explicitly supplied engine
helper outputs isolate scheduling, card dispatch, attacker ordering and target
ranking. This validates recovered decisions, not the supplied damage/effect
helpers or all possible duel states. No live PPSSPP access is performed.
"""
import argparse
import csv
import json
import random
import struct
import time
from pathlib import Path
from mips_vm import VM
from battle_model import *

ROOT = Path(__file__).resolve().parent.parent
ARITIES = {
    0x3298C: 1, 0xC49BC: 4, 0x46CD8: 2, 0xD0C00: 4,
    0xDE694: 3, 0xC4AD0: 4, 0x4B30C: 3, 0xC4E88: 2,
    0x3F844: 0, 0x3F440: 1, 0x3F4D8: 1, 0xE8488: 3,
    0xE92E0: 5, 0xE43C8: 3, 0xE2998: 2, 0xC8178: 3,
    0x329D8: 1, 0xAA88: 2, 0x1CFD0: 2, 0xE83BC: 3,
    0xFB020: 4, 0x31944: 1, 0xDE2F8: 3, 0x46D18: 1,
    0x3F344: 1, 0x46F20: 1, 0x146C: 3, 0x815C: 3,
    0xE5988: 1, 0xEA248: 2, 0xEA28C: 3, 0x9288: 4,
    0xE79C8: 2, 0x1810: 2, 0x850C: 3,
    0xDB914: 1, 0xFFB48: 1, 0x32868: 1, 0x9C1C: 2,
    0xA414: 1, 0xA690: 1, 0x42584: 1, 0x442F8: 2,
    0xE5CE0: 2, 0xE4624: 1, 0xDD1AC: 3, 0xE2990: 2,
}
MUTATORS = frozenset((0xC49BC, 0x46CD8, 0xD0C00, 0xC4AD0,
    0x4B30C, 0xC4E88, 0xC8178, 0xFB020, 0x31944, 0x46D18, 0x46F20))
CARD_POOL = sorted(set(PRIORITY_ELEVEN | PRIORITY_NINE_CONDITIONAL
    | PRIORITY_EIGHT_CONDITIONAL | PRIORITY_SEVEN | SPECIAL_DIRECT)
    | {0, 0x147D, 0x16BF, 0x182D, 0x14D7, 0x13A7, 0x1115,
       0x148A, 0x1A79, 0x13F9, 0x10A6, 0x129A, 0x13CB,
       0x129C, 0x152E, 0x1657, 0x172C, 4095})


class Fixture:
    base = BASE
    def __init__(self, state, seed, values, evaluations, target_records):
        self.state = state.copy()
        self.seed = seed
        self.values = values
        self.evaluations = evaluations
        self.target_records = target_records
        self.actions = []
        self.evaluation_calls = []
        self.simulation_calls = 0
        self.pending_writes = []

    def load(self, relative):
        return self.state.get(relative, 0)

    def store(self, relative, value):
        self.state[relative] = value & 0xFFFFFFFF
        self.pending_writes.append((relative, value & 0xFFFFFFFF))

    def call(self, relative, *args):
        args = tuple(signed(a) for a in args)
        if relative in MUTATORS:
            self.actions.append((relative, args))
        if relative == 0xE2998:
            return card_id(self, *args[:2])
        if relative == 0x146C:
            owner, slot, pointer = args
            for index in range(6):
                self.store(pointer - self.base + index * 4,
                    100 * (owner + 1) + 10 * slot + index)
            return 0
        if relative == 0x9C1C:
            sequence_length = self.seed % 6
            if self.simulation_calls == sequence_length:
                return 0
            attacker = self.simulation_calls % 5
            self.simulation_calls += 1
            evaluation = self.evaluations[(attacker, 0)][1]
            for index, value in enumerate(evaluation):
                self.store(ATTACK_RESULT + index * 4, value)
            return 1
        if relative == 0xA414:
            for slot in range(5):
                self.store(field(args[0], slot) + 4, 0)
                self.store(field(args[0], slot) + 8, 1)
            return 0
        if relative == 0xA690:
            for slot in range(5):
                self.store(field(args[0], slot), 0)
                self.store(0x10FEDC + slot * 4, self.seed + slot)
            return signed(self.seed * 43)
        if relative in BATTLE_HANDLERS and self.seed % 11 == 0:
            self.store(BATTLE_OUTER, 12)
            self.store(BATTLE_INNER, 5)
        if relative in self.values:
            return self.values[relative]
        # Per-call legality and card presence inputs; stable across both copies.
        key = sum((index + 13) * arg for index, arg in enumerate(args)) + relative + self.seed
        return (key ^ (key >> 3)) & 1

    def score_attack(self, player, attacker, target, permissive):
        return self.target_records[(target, permissive)]

    def evaluate_target(self, player, attacker, flags, permissive):
        self.evaluation_calls.append((player, attacker, flags, permissive))
        accepted, data = self.evaluations[(attacker, permissive)]
        return accepted, data


def write_battle_map(ram, models, validation):
    """Save exact table/body boundaries and distinguish ports from listings."""
    module = next(m for m in json.loads((ROOT / 'reports/module_analysis.json').read_text())
        if m['name'] == 'modduel_eng')
    indexed = {item['relative_address']: item for item in module['functions']}
    relocated = ROOT / 'work/decompiled/modduel_eng_relocated'
    observed = struct.unpack_from('<15I', ram, BASE + 0x10E4AC - 0x08800000)
    expected_table = tuple(BASE + address if address is not None else 0
        for address in BATTLE_HANDLERS)
    if observed != expected_table:
        raise AssertionError('Battle function table differs from the recovered capture')
    roles = {
        0x333A4: 'Battle entry, interruption checks, entry queue payloads.',
        0x33500: 'Choose an attacker, process repeats and phase exits.',
        0x33B9C: 'Choose/reselect a target; direct-attack and replay paths.',
        0x345C8: 'Attacker legality, selected attack costs and sacrifice prompts.',
        0x34980: 'One-time attack-triggered spell/trap checks and fixed LP costs.',
        0x34A84: 'Pre-damage response windows and attack-triggered effects.',
        0x35360: 'Battle card effects and combat-stat setup around damage calculation.',
        0x362BC: 'Additional damage-calculation card/effect paths.',
        0x36EF8: 'Apply/show the two encoded damage records and related effects.',
        0x374A8: 'Post-damage effect checks and queued actions.',
        0x37B00: 'Extensive battle-destruction and card-effect dispatch.',
        0x39798: 'After-battle effects, repeat-attack routing and return to attacker selection.',
        0x3A63C: 'Battle end/reset cleanup, card effects and pending phase routing.',
        0x3B0DC: 'Final two-player back-row Fairy Box cleanup.',
    }
    entries = []
    for index, address in enumerate(BATTLE_HANDLERS):
        if address is None:
            entries.append(dict(table_index=index, address=0, role='Null sentinel: scheduler returns 1'))
            continue
        item = indexed[address]
        source = relocated / f'fn_{address:06X}.c'
        text = source.read_text() if source.exists() else ''
        ai_calls = sorted({call['target'] - BASE for call in item['calls']
            if BASE <= call['target'] < BASE + 0x1E000})
        entries.append(dict(table_index=index, relative_address=f'{address:06X}',
            absolute_address=f'{BASE + address:08X}', size_bytes=item['size'],
            exclusive_end_relative=f"{address + item['size']:06X}",
            role=roles[address], controller_mode_checks=text.count('controller_modes'),
            calls_into_initial_ai_region=[f'{target:06X}' for target in ai_calls],
            readable_port=address in models,
            listing_path=str(source.relative_to(ROOT)),
            validation_cases=validation['counts'].get(f'{address:06X}', 0)))
    result = dict(table_relative_address='10E4AC', table_absolute_address=f'{BASE + 0x10E4AC:08X}',
        table_matches_capture=True, handler_count=14,
        scheduler_relative_address='03B174', handlers=entries,
        readable_routines=[dict(relative_address=f'{address:06X}',
            name=model.__name__, size_bytes=indexed[address]['size'],
            tests=validation['counts'][f'{address:06X}'],
            instruction_coverage=validation['coverage'][f'{address:06X}'])
            for address, model in models.items()],
        interpretation='Roles describe inspected code and are not recovered original symbol names.',
        boundary_method='Function bodies seeded from PPSSPP analysis and cross-checked with relocated Ghidra listings; stripped-game heuristic boundaries, not original source symbols.',
        remaining_readable_handlers=[f'{address:06X}' for address in BATTLE_HANDLERS
            if address is not None and address not in models],
        omissions='The 11 other battle handlers are mapped but not rewritten in battle_model.py. Combat mechanics, effect dispatch and legality helpers remain engine inputs. AC5C hypothetical summon comparison is not ported in this file.')
    (ROOT / 'reports/battle_function_map.json').write_text(json.dumps(result, indent=2))
    cards = {int(row['card_id']): row['name'] for row in csv.DictReader(
        (ROOT / 'reports/card_database/cards.csv').open(encoding='utf-8-sig', newline=''))}
    tiers = [(7, 'Always, unless effect-suppressed flag bit 5 is set', PRIORITY_SEVEN),
        (8, 'When opponent monster-count helper E5988 is nonzero', PRIORITY_EIGHT_CONDITIONAL),
        (9, 'When an occupied opponent target passes helper 815C', PRIORITY_NINE_CONDITIONAL),
        (9, 'Always, unless effect-suppressed flag bit 5 is set', {0x147D}),
        (11, 'Always, unless effect-suppressed flag bit 5 is set', PRIORITY_ELEVEN)]
    with (ROOT / 'reports/battle_attacker_priorities.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(('card_id', 'card_id_hex', 'name', 'priority_tier', 'condition'))
        for tier, condition, ids in tiers:
            for cid in sorted(ids):
                writer.writerow((cid, f'0x{cid:04X}', cards.get(cid, 'Unmapped'), tier, condition))


def generate_fixture(rng, iteration, relative):
    player = rng.randrange(2)
    state = {}
    for owner in range(2):
        state[PLAYERS + owner * PLAYER_STRIDE] = rng.choice([299, 1000, 2000, 2001, 4000, 4001, 8000])
        state[CONTROLLERS + owner * 4] = rng.randrange(2)
        for slot in range(10):
            start = field(owner, slot)
            for offset in range(0, 20, 4):
                state[start + offset] = rng.randrange(10)
            cid = CARD_POOL[(iteration + owner * 5 + slot) % len(CARD_POOL)]
            if relative == 0x33500 and iteration % 14 == 1 and slot < 5:
                cid = 0x1A79
            if relative == 0x3B0DC:
                cid = rng.choice([0, 0x13F9, 0x148A])
            state[start] = cid
            state[start + 4] = rng.randrange(2) << 16  # position at +6
            state[start + 8] = rng.randrange(2)        # visibility/effect at +8
            state[start + 16] = rng.choice([0, 1 << 5, 1 << 22, (1 << 22) | (1 << 5), 2, 3])
        for slot in range(5):
            for offset in range(0x18, 0x44, 4):
                state[PERSPECTIVE + owner * 0xDC + slot * 0x2C + offset] = rng.choice([0, 299, 300, 1000, 1500, 1700, 1899, 1900, 2200, 4000])
            state[PERSPECTIVE + owner * 0xDC + slot * 0x2C + 0x28] = rng.choice([1, 4, 5, 6, 10])
    # Include every controlled global so unexpected writes are reported.
    globals_ = (PERSPECTIVE, BATTLE_OUTER, BATTLE_INNER, PENDING,
        RESPONSE_KIND, PHASE_CHOICE, INPUT_READY, INPUT_REPEAT, INPUT_COMMAND,
        INPUT_SUM_A, INPUT_SUM_B)
    for address in globals_:
        state[address] = rng.randrange(6)
    state[BATTLE_INNER] = iteration % 7
    state[BATTLE_OUTER] = iteration % len(BATTLE_HANDLERS)
    state[INPUT_COMMAND] = rng.choice([0, 1, 0x11, 0x12])
    state[INPUT_READY] = rng.randrange(2)
    state[INPUT_SUM_A] = rng.randrange(5)
    state[INPUT_SUM_B] = rng.randrange(5)
    for address in range(ATTACK_RESULT, 0x10FF40, 4):
        state[address] = rng.randrange(5)
    for address in range(0x10FEC4, ATTACK_RESULT, 4):
        state[address] = rng.randrange(5)
    state[CACHE_VALID] = iteration % 2
    for address in range(BATTLE_PLAYER, 0x110040, 4):
        state[address] = rng.choice([0, 1, 500, 1000, 2000])
    # The engine's damage receiver fields hold player indexes.
    for event in range(2):
        state[BATTLE_PLAYER + 0xA8 + event * 20] = rng.randrange(2)
        state[BATTLE_PLAYER + 0x9C + event * 20] = rng.choice([0, 0, 500, 1000, 0xFFFFFFFF])
    for owner in range(2):
        for offset in (0x58, 0x5C):
            state[BATTLE_PLAYER + offset + owner * 0x38] = rng.randrange(2)
    if relative == 0x860C:
        for owner in range(2):
            special = ((0x16BF, 0x13CB, 0x172C, 0x1657, 0x10A6, 4095)
                if owner == player else (0x129A, 0x172C, 0x1657, 0x10A6, 0x152E, 0x129C, 4095))
            for slot in range(5):
                state[field(owner, slot)] = special[(iteration + slot) % len(special)]
    values = {0x3F844: rng.randrange(2), 0x3F440: rng.choice([-1, 0, 2, 4]),
        0x3F4D8: rng.choice([-1, 0, 2, 4]), 0xDE694: rng.choice([-1, 0, 1]),
        0x1CFD0: rng.randrange(2), 0xAA88: rng.randrange(2),
        0x3F344: rng.randrange(2), 0x1810: rng.choice([-1, 0, 1]),
        0x850C: rng.choice([0, 2000, 3000, 5000]),
        0xEA248: rng.randrange(2), 0xEA28C: rng.randrange(3)}
    values.update({handler: rng.randrange(2) for handler in BATTLE_HANDLERS if handler is not None})
    evaluations = {}
    for attacker in range(5):
        for permissive in range(2):
            evaluations[(attacker, permissive)] = (rng.randrange(2), tuple(
                [rng.randrange(2), rng.randrange(2), attacker, rng.randrange(6),
                rng.randrange(2), rng.randrange(2), rng.randrange(3000), rng.randrange(3000)]))
    target_records = {}
    for target in range(6):
        for permissive in range(2):
            target_records[(target, permissive)] = tuple([int(target == 5), rng.randrange(2), 0, target,
                rng.randrange(2), rng.randrange(2), rng.randrange(3000), rng.choice([-1000, 0, 500, 1000])])
    return player, Fixture(state, rng.randrange(10000), values, evaluations, target_records)


def callbacks_for(vm, fixture, root, actual_actions, evaluation_calls):
    vm.callbacks = {}
    for relative, arity in ARITIES.items():
        def callback(v, relative=relative, arity=arity):
            args = tuple(signed(x) for x in v.r[4:4 + arity])
            if relative in MUTATORS:
                actual_actions.append((relative, args))
            if relative == 0x146C:
                owner, slot, pointer = args
                for index in range(6):
                    v.store(pointer + index * 4, 100 * (owner + 1) + 10 * slot + index)
                value = 0
            else:
                fixture.pending_writes = []
                value = fixture.call(relative, *args)
                for address, written in fixture.pending_writes:
                    v.store(BASE + address, written)
            v.set(2, value)
        vm.callbacks[BASE + relative] = callback
    for relative in BATTLE_HANDLERS:
        if relative is not None:
            def handler_callback(v, relative=relative):
                fixture.pending_writes = []
                value = fixture.call(relative, signed(v.r[4]))
                for address, written in fixture.pending_writes:
                    v.store(BASE + address, written)
                v.set(2, value)
            vm.callbacks[BASE + relative] = handler_callback

    def evaluation(v):
        player, attacker, flags, pointer, permissive = v.r[4:9]
        evaluation_calls.append((player, attacker, flags, permissive))
        accepted, data = fixture.evaluations[(attacker, permissive)]
        # Even unsuccessful helper calls may fill output; callers must ignore it.
        for index, value in enumerate(data):
            v.store(pointer + index * 4, value)
        v.set(2, accepted)
    vm.callbacks[BASE + 0x9924] = evaluation

    def score(v):
        target, permissive, pointer = v.r[6:9]
        for index, value in enumerate(fixture.target_records[(target, permissive)]):
            v.store(pointer + index * 4, value)
        v.set(2, 0)
    vm.callbacks[BASE + 0x860C] = score

    def copy(v):
        destination, source, count = v.r[4:7]
        data = v.read(source, count)
        for index, byte in enumerate(data):
            v.store(destination + index, byte, 1)
        v.set(2, destination)
    vm.callbacks[BASE + 0xFB0E8] = copy
    # The root under comparison always executes its original instructions.
    vm.callbacks.pop(BASE + root, None)
    if root == 0xAA88:
        # Original AA88 and original 9C1C execute together for this test.
        vm.callbacks.pop(BASE + 0x9C1C, None)
    if root == 0xAB64:
        # Original AB64 and 9924 execute together; only engine scoring is supplied.
        vm.callbacks.pop(BASE + 0x9924, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases-per-routine', type=int, default=600)
    args = parser.parse_args()
    begun = time.perf_counter()
    rng = random.Random(60933500)
    ram = (ROOT / 'work/trial4_selector_entry.bin').read_bytes()
    vm = VM(ram)
    models = {**RECREATED_BATTLE, 0x9924: choose_attack_target, 0x860C: evaluate_battle}
    failures = []
    counts = {}
    covered = {}
    for relative, model in models.items():
        counts[f'{relative:06X}'] = 0
        for iteration in range(args.cases_per_routine):
            player, fixture = generate_fixture(rng, iteration, relative)
            expected = Fixture(fixture.state, fixture.seed, fixture.values,
                fixture.evaluations, fixture.target_records)
            for address, value in fixture.state.items():
                vm.store(BASE + address, value)
            actual_actions, actual_evaluations = [], []
            callbacks_for(vm, fixture, relative, actual_actions, actual_evaluations)
            if relative in (0xAA88, 0x9C1C):
                mode = rng.choice([-1, 0, 1])
                actual = vm.call(BASE + relative, player, mode)
                wanted = model(expected, player, mode)
            elif relative == 0xAB64:
                attacker = rng.randrange(5)
                permissive = rng.randrange(2)
                actual = vm.call(BASE + relative, player, attacker, permissive)
                wanted = model(expected, player, attacker, permissive)
            elif relative == 0x9288:
                attacker = iteration % 5
                target = rng.randrange(6)
                flags = rng.randrange(2)
                actual = vm.call(BASE + relative, player, attacker, target, flags)
                wanted = model(expected, player, attacker, target, flags)
            elif relative in (0x9924, 0x860C):
                attacker = rng.randrange(5)
                flags = rng.randrange(2)
                permissive = rng.randrange(2)
                pointer = 0x09FFF000
                original_output = [rng.randrange(5) for _ in range(8)]
                for index, value in enumerate(original_output):
                    vm.store(pointer + index * 4, value)
                if relative == 0x9924:
                    actual = vm.call(BASE + relative, player, attacker, flags, pointer, permissive)
                    wanted, output = model(expected, player, attacker, flags, permissive, original_output)
                else:
                    target = rng.randrange(6)
                    actual = vm.call(BASE + relative, player, attacker, target, permissive, pointer)
                    output = model(expected, player, attacker, target, permissive)
                    wanted = actual  # fn_00860C is void; its result is the output record.
                actual_output = [vm.load(pointer + index * 4, 4) for index in range(8)]
                output = [value & 0xFFFFFFFF for value in output]
                if actual_output != output:
                    failures.append(dict(relative=f'{relative:06X}', iteration=iteration,
                        output_difference=[actual_output, output], inputs=[player, attacker, flags, permissive]))
            else:
                actual = vm.call(BASE + relative, player)
                wanted = model(expected, player)
                if relative == 0xA414:
                    wanted = actual  # The recovered fn_00A414 is void.
            actual_state = {address: vm.load(BASE + address, 4) for address in fixture.state}
            differences = {f'{address:06X}': [actual_state[address], expected.state[address]]
                for address in fixture.state if actual_state[address] != expected.state[address]}
            if (actual != wanted or differences or actual_actions != expected.actions
                    or actual_evaluations != expected.evaluation_calls):
                failures.append(dict(relative=f'{relative:06X}', iteration=iteration,
                    return_values=[actual, wanted], state_difference=differences,
                    actions=[actual_actions, expected.actions],
                    evaluation_order=[actual_evaluations, expected.evaluation_calls]))
            counts[f'{relative:06X}'] += 1
        index = next(m for m in json.loads((ROOT / 'reports/module_analysis.json').read_text())
            if m['name'] == 'modduel_eng')
        entry = next(f for f in index['functions'] if f['relative_address'] == relative)
        body = set(range(BASE + relative, BASE + relative + entry['size'], 4))
        covered[f'{relative:06X}'] = dict(visited_instructions=len(body & set(vm.visits)),
            instruction_count=len(body), function_size_bytes=entry['size'])
    result = dict(seed=60933500, case_count=sum(counts.values()), routine_count=len(models),
        counts=counts, coverage=covered, mismatch_count=len(failures), failures=failures[:20],
        seconds=round(time.perf_counter() - begun, 3),
        method='Original MIPS bodies against readable Python on identical synthetic engine-helper results, comparing returns, global/field state, queued actions, and target evaluation order.',
        limitations='Damage/effect simulation helpers and legality predicates are explicit inputs. These routine comparisons do not establish complete-AI or complete-duel equivalence.')
    (ROOT / 'reports/battle_validation.json').write_text(json.dumps(result, indent=2))
    write_battle_map(ram, models, result)
    print(json.dumps(result, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
