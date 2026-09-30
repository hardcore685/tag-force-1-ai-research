"""Compare readable summon planning with unchanged original MIPS bodies.

Rules, modified stats, hypothetical attack simulation and material selection
are explicit synthetic inputs. Tests compare return values, all tracked field
and global state, engine-call arguments/order and material-history buffers.
The original code executes in copied RAM; no live PPSSPP connection is opened.
"""
import argparse
import csv
import json
import random
import time
from pathlib import Path
from mips_vm import VM
from summon_planning_model import *

ROOT = Path(__file__).resolve().parent.parent
ARITIES = {0xAA18: 1, 0x23D98: 2, 0x2730: 3, 0x1DA4: 4,
    0x38AC: 3, 0xE6660: 4, 0xA414: 1, 0xA690: 1, 0xE5988: 1,
    0xDB7EC: 1, 0xE6A1C: 2, 0xBBFD8: 3, 0xE5684: 2,
    0x6F74: 2, 0xE6C0C: 1, 0xE6784: 3, 0x26F5C: 3,
    0x8: 2, 0x22000: 2, 0x1810: 2, 0xE79C8: 2, 0xE7D4C: 2,
    0xE4624: 1, 0xF2380: 2, 0xC5878: 6, 0xF2320: 2, 0xE62A0: 1}
CARD_POOL = sorted(EARLY_SUMMON_IDS | {0, 4095, 4530, 0x1654, 0x1688})


class Fixture:
    base = BASE
    def __init__(self, state, seed, trial_data, baseline, overrides=None):
        self.state = state.copy()
        self.seed = seed
        self.trial_data = trial_data
        self.baseline = baseline
        self.overrides = overrides or {}
        self.current_trial = None
        self.calls = []
        self.pending_writes = []

    def load(self, relative):
        return self.state.get(relative, 0)

    def store(self, relative, value):
        self.state[relative] = value & 0xFFFFFFFF
        self.pending_writes.append((relative, value & 0xFFFFFFFF))

    def call(self, relative, *args):
        args = tuple(signed(value) for value in args)
        self.calls.append((f'{relative:06X}', args))
        if (relative, args) in self.overrides:
            return self.overrides[(relative, args)]
        if relative in self.overrides:
            return self.overrides[relative]
        key = self.seed + relative + sum((index + 17) * value for index, value in enumerate(args))
        value = (key ^ (key >> 4)) & 1
        if relative == 0xAA18:
            self.store(CACHE_VALID, 1)
            self.store(0x10FED8, self.baseline['score'])
            self.store(OPPONENT_LOSSES, self.baseline['losses'])
            return self.baseline['score']
        if relative == 0x23D98:
            return 0x10 if (value or self.seed % 5 == 0) else 0
        if relative == 0x2730:
            return 0 if self.seed % 5 == 0 else value
        if relative == 0x1DA4:
            return -1 if self.seed % 5 == 0 else (key % 3 - 1)
        if relative == 0x38AC:
            return key % 4
        if relative == 0xA414:
            for slot in range(5):
                set_half(self, field(args[0], slot) + 6, 0)
                set_half(self, field(args[0], slot) + 8, 1)
            return 0
        if relative == 0xA690:
            trial = self.trial_data[self.current_trial]
            self.store(OPPONENT_LOSSES, trial['losses'])
            if trial['slot'] >= 0:
                address = field(args[0], trial['slot']) + 16
                flags = self.load(address)
                self.store(address, (flags & ~(1 << 22)) | (trial['attacks'] << 22))
                if trial['own_remaining'] == 0:
                    self.store(field(args[0], trial['slot']), 0)
            return trial['score']
        if relative == 0xE5988 and self.current_trial is not None:
            trial = self.trial_data[self.current_trial]
            return trial['own_remaining'] if args[0] == trial['player'] else trial['opponent_remaining']
        if relative == 0xE6784:
            return key % 6
        if relative == 0x1810:
            return key % 3 - 1
        if relative == 0xF2380:
            return key % 9
        if relative == 0xF2320:
            # A faithful synthetic level helper observes previous removals.
            return (self.load(field(args[0], args[1])) & 0x1FFF) % 9
        if relative == 0xE62A0:
            return -1 if self.seed % 7 == 0 else self.seed % 5
        return value

    def select_material(self, player, cid, mode, used, materials):
        self.calls.append(('material', (player, cid, mode, used, tuple(materials[:used]))))
        key = self.seed + player + cid + used
        if key % 11 == 0:
            return -1
        return ((key % 5) << 8) | ((key // 3) & 1)

    def place_summon(self, player, cid, position, count, materials):
        self.calls.append(('trial_place', (player, cid, position, count, tuple(materials[:max(0, count)]))))
        self.current_trial = cid
        trial = self.trial_data[cid]
        if trial['slot'] >= 0:
            self.store(field(player, trial['slot']), cid)
            set_half(self, field(player, trial['slot']) + 6, position)
            set_half(self, field(player, trial['slot']) + 8, 1)
        return trial['slot']

    def modified_attack(self, player, slot):
        self.calls.append(('modified_attack', (player, slot)))
        return self.trial_data[self.current_trial]['attack']


def fixture_for(rng, iteration, relative):
    player = rng.randrange(2)
    state = {}
    for owner in range(2):
        state[PLAYERS + owner * PLAYER_STRIDE + 0x2C] = rng.getrandbits(32)
        state[PLAYERS + owner * PLAYER_STRIDE + 12] = 1 + iteration % 8
        for index in range(8):
            cid = CARD_POOL[(iteration + index * 3) % len(CARD_POOL)]
            packed = (rng.getrandbits(2) << 30) | (rng.randrange(256) << 22) | (rng.randrange(2) << 13) | cid
            state[PLAYERS + owner * PLAYER_STRIDE + 0x120 + index * 4] = packed
        for slot in range(5):
            for offset in range(0, 20, 4):
                state[field(owner, slot) + offset] = rng.getrandbits(32)
            state[field(owner, slot)] = rng.choice(CARD_POOL)
            state[field(owner, slot) + 4] = rng.randrange(2) << 16
            state[field(owner, slot) + 8] = rng.randrange(2)
    for address in (CACHE_VALID, OPPONENT_LOSSES, OWN_REMAINING, OPPONENT_REMAINING,
            SELECTED_INSTANCE, SELECTED_OWNER, SELECTED_CONTROLLER, 0x10FED8):
        state[address] = rng.randrange(5)
    for slot in range(5):
        state[SLOT_DAMAGE + slot * 4] = rng.randrange(2)
    overrides = {}
    if relative in (0xAC5C, 0x4C8) and iteration % 19 == 0:
        state[PLAYERS + player * PLAYER_STRIDE + 12] = 0
    if relative == 0x4C8 and iteration % 4 == 0:
        cid = CARD_POOL[(iteration // 4) % len(CARD_POOL)]
        state[PLAYERS + player * PLAYER_STRIDE + 12] = 1
        address = PLAYERS + player * PLAYER_STRIDE + 0x120
        state[address] = (state[address] & ~0x1FFF) | cid
        overrides.update({0xDB7EC: 1, 0x23D98: 0x10,
            (0xE5684, (player, 0x1870)): 0,
            (0xE5684, (player, 0x18F6)): iteration % 8 == 0,
            (0x6F74, (player, 0x18F6)): 1,
            (0x6F74, (player, 0x12E5)): 0,
            (0x6F74, (player, 0x18FE)): iteration % 8 == 0,
            0xE6A1C: 1, 0xBBFD8: iteration % 8 == 0,
            0xE6C0C: 1, 0xE5988: 1, 0xE6784: 3})
    if relative == 0xCC4 and iteration % 16 < 4:
        scenario = iteration % 16
        preference = 0 if scenario in (0, 1) else 1
        for owner in range(2):
            for slot in range(5):
                state[field(owner, slot) + 4] = (1 - preference) << 16
                state[field(owner, slot) + 8] = 1
        overrides.update({0x1810: preference, 0xE79C8: 1,
            (0xE4624, (0x17A6,)): 1,
            (0xE4624, (0x15FB,)): 0,
            (0xE4624, (0x197B,)): 1,
            0xF2380: 3 if scenario in (1, 2) else 4})
    baseline = dict(score=rng.choice([-1000, 0, 1000, 3000]), losses=rng.randrange(5))
    trials = {cid: dict(player=player, slot=rng.choice([-1, 0, 1, 2, 4]),
        attack=rng.choice([0, 899, 900, 1000, 3000]), attacks=rng.randrange(2),
        score=rng.choice([baseline['score'], baseline['score'] - 1000, baseline['score'] + 1000]),
        losses=rng.randrange(6), own_remaining=rng.randrange(3), opponent_remaining=rng.randrange(3))
        for cid in CARD_POOL}
    return player, Fixture(state, rng.randrange(10000), trials, baseline, overrides)


def set_callbacks(vm, fixture):
    vm.callbacks = {}
    def apply_pending(v):
        for address, value in fixture.pending_writes:
            v.store(BASE + address, value)
    for relative, arity in ARITIES.items():
        def callback(v, relative=relative, arity=arity):
            fixture.pending_writes = []
            result = fixture.call(relative, *[signed(value) for value in v.r[4:4 + arity]])
            apply_pending(v)
            v.set(2, result)
        vm.callbacks[BASE + relative] = callback
    def material(v):
        player, cid, mode, used, pointer = v.r[4:9]
        buffer = [v.load(pointer + index * 2, 2) for index in range(4)]
        result = fixture.select_material(player, cid, mode, used, buffer)
        for index in range(used):
            v.store(pointer + index * 2, buffer[index], 2)
        v.set(2, result)
    vm.callbacks[BASE + 0x350C] = material
    def trial_place(v):
        player, cid, position, count, pointer = v.r[4:9]
        fixture.pending_writes = []
        materials = [v.load(pointer + index * 2, 2) for index in range(count)]
        result = fixture.place_summon(player, cid, position, count, materials)
        apply_pending(v)
        v.set(2, result)
    vm.callbacks[BASE + 0xA4F0] = trial_place
    def modified(v):
        player, slot, pointer = v.r[4:7]
        attack = fixture.modified_attack(player, slot)
        for index in range(11):
            v.store(pointer + index * 4, attack if index == 5 else 0)
        v.set(2, 0)
    vm.callbacks[BASE + 0xEED70] = modified
    def copy(v):
        destination, source, count = v.r[4:7]
        data = v.read(source, count)
        for index, byte in enumerate(data):
            v.store(destination + index, byte, 1)
        v.set(2, destination)
    vm.callbacks[BASE + 0xFB0E8] = copy
    def zero(v):
        pointer, count = v.r[4:6]
        if count % 4 or pointer % 4:
            raise AssertionError('Unexpected zeroing alignment')
        for offset in range(0, count, 4):
            v.store(pointer + offset, 0)
            fixture.store(pointer + offset - BASE, 0)
        v.set(2, pointer)
    vm.callbacks[BASE + 0xFB0DC] = zero


def write_map(results):
    module = next(item for item in json.loads((ROOT / 'reports/module_analysis.json').read_text())
        if item['name'] == 'modduel_eng')
    indexed = {item['relative_address']: item for item in module['functions']}
    summary = dict(routines=[dict(relative_address=f'{address:06X}', name=function.__name__,
        size_bytes=indexed[address]['size'], exclusive_end_relative=f"{address + indexed[address]['size']:06X}",
        validation=results['coverage'][f'{address:06X}'])
        for address, function in RECREATED_SUMMON_PLANNING.items()],
        corrected_helper='The double-tribute helper is E6660, JAL target 09F4C260.',
        behavioral_findings=[
            'AC5C compares successive hypothetical summons with the existing cached full-battle prediction.',
            'A candidate must have ATK >= 900 and its simulated attack-used flag set.',
            'Greater predicted damage wins; an equal-damage trial may win on greater opposing casualties with an own monster remaining.',
            'Trial monster state is restored after every candidate. The best hand index is returned.',
            '4C8 takes the first qualifying special-card entry in hand order.',
            '2D4 can first flip an own tribute material before queuing the summon.',
            'CC4 honors the simulated attack plan before falling back to card/position advice.'
        ],
        rule_body_boundaries='Material legality/choice, card override 1DA4, modified stats EED70, cached battle score AA18 and simulated sequence A690 are supplied engine/helper results in this comparison. The battle_model.py file separately ports AA18/A690 decisions.',
        omissions='These routines are ports of selected AI decisions and do not establish all-card or entire-duel equivalence. Original stripped symbols were unavailable.')
    (ROOT / 'reports/summon_planning_map.json').write_text(json.dumps(summary, indent=2))
    with (ROOT / 'reports/card_database/cards.csv').open(encoding='utf-8-sig', newline='') as handle:
        cards = {int(row['card_id']): row['name'] for row in csv.DictReader(handle)}
    with (ROOT / 'reports/summon_planning_early_cards.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(('card_id', 'card_id_hex', 'name'))
        for cid in sorted(EARLY_SUMMON_IDS):
            writer.writerow((cid, f'0x{cid:04X}', cards.get(cid, 'Unmapped in extracted card database')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases-per-routine', type=int, default=800)
    args = parser.parse_args()
    started = time.perf_counter()
    rng = random.Random(0x609AC5C)
    vm = VM((ROOT / 'work/trial4_selector_entry.bin').read_bytes())
    counts, coverage, failures = {}, {}, []
    module = next(item for item in json.loads((ROOT / 'reports/module_analysis.json').read_text())
        if item['name'] == 'modduel_eng')
    indexed = {item['relative_address']: item for item in module['functions']}
    for relative, function in RECREATED_SUMMON_PLANNING.items():
        counts[f'{relative:06X}'] = 0
        for iteration in range(args.cases_per_routine):
            player, fixture = fixture_for(rng, iteration, relative)
            expected = Fixture(fixture.state, fixture.seed, fixture.trial_data, fixture.baseline, fixture.overrides)
            for address, value in fixture.state.items():
                vm.store(BASE + address, value)
            set_callbacks(vm, fixture)
            vm.callbacks.pop(BASE + relative, None)
            if relative == 0xAC5C:
                tribute_filter = rng.choice([-1, 0, 1, 2])
                actual = vm.call(BASE + relative, player, tribute_filter)
                wanted = function(expected, player, tribute_filter)
            elif relative == 0x2D4:
                index = rng.randrange(hand_count(fixture, player))
                position = rng.randrange(2)
                actual = vm.call(BASE + relative, player, index, position)
                wanted = function(expected, player, index, position)
            elif relative == 0xCC4:
                slot, after = rng.randrange(5), rng.randrange(2)
                if iteration % 16 < 4:
                    after = 1
                actual = vm.call(BASE + relative, player, slot, after)
                wanted = function(expected, player, slot, after)
            elif relative == 0xA4F0:
                cid, position, count = rng.choice(CARD_POOL), rng.randrange(2), rng.randrange(4)
                materials = [rng.randrange(2) | (rng.randrange(5) << 8) for _ in range(4)]
                pointer = 0x09FFF000
                for index, packed in enumerate(materials):
                    vm.store(pointer + index * 2, packed, 2)
                actual = vm.call(BASE + relative, player, cid, position, count, pointer)
                wanted = function(expected, player, cid, position, count, materials)
            else:
                actual = vm.call(BASE + relative, player)
                wanted = function(expected, player)
            actual_state = {address: vm.load(BASE + address, 4) for address in fixture.state}
            changes = {f'{address:06X}': [actual_state[address], expected.state[address]]
                for address in fixture.state if actual_state[address] != expected.state[address]}
            if actual != wanted or changes or fixture.calls != expected.calls:
                failures.append(dict(relative=f'{relative:06X}', iteration=iteration,
                    seed=fixture.seed, returns=[actual, wanted], state_difference=changes,
                    calls=[fixture.calls, expected.calls]))
            counts[f'{relative:06X}'] += 1
        body = set(range(BASE + relative, BASE + relative + indexed[relative]['size'], 4))
        coverage[f'{relative:06X}'] = dict(visited_instructions=len(body & set(vm.visits)),
            instruction_count=len(body), case_count=counts[f'{relative:06X}'])
    result = dict(seed=0x609AC5C, case_count=sum(counts.values()), routine_count=len(counts),
        counts=counts, coverage=coverage, mismatch_count=len(failures), failures=failures[:15],
        seconds=round(time.perf_counter() - started, 3),
        method='Original MIPS routine bodies compared with readable Python on identical explicit engine results; returns, globals, fields and helper call arguments/order are checked.',
        limitations='Rule/modified-stat helpers and what-if battle simulation are supplied inputs. This is component-level differential validation, not exhaustive full-AI equivalence.')
    (ROOT / 'reports/summon_planning_validation.json').write_text(json.dumps(result, indent=2))
    write_map(result)
    print(json.dumps(result, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
