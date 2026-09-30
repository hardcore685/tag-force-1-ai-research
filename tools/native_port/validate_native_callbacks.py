"""Real-helper native-versus-MIPS checks of TF1's card AI callback tables.

All 491 early/late/general/response table records are exercised with their
actual card IDs. No card predicate, rules, stats, RNG, target or effect helper
is stubbed. Only initialized-context PSP interrupt services use host bridges.
Four configurations start from the user's captured RAM, transplant a card
into a matching hand/field record and provide a well-formed 24-byte activation
descriptor. Chain-response records always receive a nonnull chain descriptor.

These are constructed decision fixtures, not a claim that every transplanted
card was legally activatable in a complete live duel. Unmapped legacy card IDs,
fixture errors and behavioral mismatches are recorded separately.
"""
from __future__ import annotations
import argparse
import collections
import csv
import ctypes
import hashlib
import json
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from mips_vm import VM
from native_ai import NativePort, BASE, START
from validate_native_port import cleared_code, differences, prepare_reference

ACTIVATION = 0x09FFF100
CHAIN = 0x09FFF180
STACK = 0x09FFE000
REGISTERS = {26: 0x09FFFB00, 28: 0x088F8CB0}
PLAYERS = BASE + 0x111E5C
INSTANCES = BASE + 0x113DC8
CONTEXTS = (
    dict(name='owner_main_field', player=1, actor=1, phase=2,
         zone='field', event=0, chain=False, battle=False, direct=False),
    dict(name='opponent_attack_field', player=0, actor=1, phase=3,
         zone='field', event=18, chain=False, battle=True, direct=False),
    dict(name='opponent_chain_hand', player=0, actor=1, phase=2,
         zone='hand', event=8, chain=True, battle=False, direct=False),
    dict(name='opponent_direct_attack_hand', player=1, actor=0, phase=3,
         zone='hand', event=19, chain=True, battle=True, direct=True),
)


def read_tables():
    with (ROOT / 'reports/card_database/cards.csv').open(encoding='utf-8-sig', newline='') as handle:
        cards = {int(row['card_id']): row for row in csv.DictReader(handle)}
    mapping = json.loads((ROOT / 'reports/complete_ai_map.json').read_text(encoding='utf-8'))
    activation = json.loads((ROOT / 'reports/response_activation_tables.json').read_text(encoding='utf-8'))
    records = []
    for group in ('early_card_priorities', 'late_card_priorities'):
        table = mapping['tables'][group]
        for item in table['records']:
            cid = item['words'][0] & 0xFFFF
            records.append(dict(group=group, table_index=item['index'],
                card_id=cid, predicate_relative=item['function'],
                table_address=table['address'] + item['index'] * table['stride']))
    for group in activation['groups']:
        for item in group['entries']:
            records.append(dict(group=group['name'], table_index=item['index'],
                card_id=item['card_id'], predicate_relative=item['predicate_relative'],
                table_address=BASE + int(item['rule_relative'], 16)))
    for item in records:
        card = cards.get(item['card_id'])
        item['card_name'] = card['name'] if card else None
        item['card_type'] = card['card_type'] if card else None
        item['card_database_mapped'] = card is not None
    return records, cards


def get32(ram, address):
    return struct.unpack_from('<I', ram, address - START)[0]


def put(ram, address, value, size=4):
    offset = address - START
    ram[offset:offset + size] = (value & ((1 << (size * 8)) - 1)).to_bytes(size, 'little')


def field(player, slot):
    return PLAYERS + (player & 1) * 0xFAC + 0x30 + slot * 20


def instance(record):
    return ((record >> 13) & 1) | (((record >> 22) & 0xFF) << 1)


def descriptor(cid, player, slot, full_record, event=0, argument=0):
    data = bytearray(24)
    struct.pack_into('<H', data, 0, cid & 0xFFFF)
    struct.pack_into('<H', data, 2, (player & 1) | ((slot & 31) << 1) | ((event & 63) << 6))
    struct.pack_into('<H', data, 4, (instance(full_record) << 6) & 0xFFFF)
    struct.pack_into('<I', data, 20, argument & 0xFFFFFFFF)
    return bytes(data)


def fixture(source, row, context):
    ram = bytearray(source)
    player = context['player']
    opponent = 1 - player
    put(ram, BASE + 0x1155D0, context['actor'])
    put(ram, BASE + 0x1155DC, context['phase'])
    put(ram, BASE + 0x10FCF4, player)
    put(ram, BASE + 0x10F9F4, player)
    put(ram, BASE + 0x10F9F8, 1)
    put(ram, BASE + 0x10F9FC, 1)
    put(ram, BASE + 0x10FEC4, 0)
    if context['zone'] == 'hand':
        source_address = PLAYERS + player * 0xFAC + 0x120
        slot = 11
        original = get32(ram, source_address)
        if not original:
            # Reuse a live instance from this owner's occupied monster slot.
            original = get32(ram, field(player, 2 if player == 0 else 1))
        put(ram, PLAYERS + player * 0xFAC + 12,
            max(1, get32(ram, PLAYERS + player * 0xFAC + 12)))
    else:
        monster = row['card_type'] is not None and 'Monster' in row['card_type']
        slot = (2 if player == 0 else 1) if monster else (7 if player == 0 else 6)
        source_address = field(player, slot)
        original = get32(ram, source_address)
        put(ram, source_address + 8, 1, 2)
        flags = get32(ram, source_address + 16)
        put(ram, source_address + 16, (flags & ~0x23) | 0x80)
    selected = (original & ~0x1FFF) | row['card_id']
    put(ram, source_address, selected)
    identifier = instance(selected)
    backing_address = INSTANCES + identifier * 4
    backing = get32(ram, backing_address)
    put(ram, backing_address, (backing & ~0x1FFF) | row['card_id'])
    # A direct attack fixture has no defending monsters. The candidate is in hand.
    if context['direct']:
        for monster_slot in range(5):
            offset = field(player, monster_slot) - START
            ram[offset:offset + 20] = bytes(20)
    attacker_slot = 2 if opponent == 0 else 1
    defender_slot = 5 if context['direct'] else (2 if player == 0 else 1)
    battle_argument = (opponent & 0xFF) | ((attacker_slot & 0xFF) << 8)
    battle_argument |= ((player & 0xFF) | ((defender_slot & 0xFF) << 8)) << 16
    if context['battle']:
        for offset in (8, 12, 16, 20, 24):
            put(ram, BASE + 0x10FF10 + offset, 0)
        put(ram, BASE + 0x10FF10, opponent)
        put(ram, BASE + 0x10FF14, player)
        put(ram, BASE + 0x10FF18, int(context['direct']))
        put(ram, BASE + 0x10FF2C, attacker_slot)
        put(ram, BASE + 0x10FF30, defender_slot)
    # A source card and record for the active opposing chain always exist.
    chain_slot = attacker_slot if context['battle'] else (7 if opponent == 0 else 6)
    chain_record = get32(ram, field(opponent, chain_slot))
    chain_cid = chain_record & 0x1FFF
    chain_descriptor = descriptor(chain_cid, opponent, chain_slot, chain_record,
        context['event'], battle_argument)
    pointer = CHAIN if context['chain'] or row['group'] == 'chain_response' else 0
    count = 1 if pointer else 0
    put(ram, BASE + 0x1107D0, count)
    put(ram, BASE + 0x1107DC, BASE + 0x110650)
    offset = BASE + 0x110650 - START
    ram[offset:offset + 24] = chain_descriptor if count else bytes(24)
    # Supply a real event queue entry, not arbitrary descriptor flag bytes.
    event_count = 1 if context['event'] else 0
    put(ram, BASE + 0x110920, event_count)
    put(ram, BASE + 0x110928, context['event'], 1)
    put(ram, BASE + 0x110948, battle_argument)
    activation_descriptor = descriptor(row['card_id'], player, slot, selected,
        context['event'], battle_argument)
    offset = ACTIVATION - START
    ram[offset:offset + 24] = activation_descriptor
    offset = CHAIN - START
    ram[offset:offset + 24] = chain_descriptor
    return bytes(ram), pointer, dict(player=player, actor=context['actor'],
        phase=context['phase'], activation_slot=slot, activation_instance=identifier,
        activation_descriptor_hex=activation_descriptor.hex(),
        chain_descriptor_hex=chain_descriptor.hex() if pointer else None,
        chain_card_id=chain_cid if pointer else None, event=context['event'],
        event_argument=f'{battle_argument:08X}', direct=context['direct'])


def reset_native(port, ram):
    ctypes.memmove(ctypes.addressof(port.mem), ram, len(ram))
    port.ctx.steps = 0
    port.services.clear()
    port.irq_enabled = 1
    port.callbacks = {}


def register_differences(vm, port):
    """Compare every architected register exposed by the original VM.

    The reference VM has no FCR31 state. These integer card predicates do not
    use scalar FP arithmetic, so FCR31 is explicitly outside this comparison.
    """
    differences = []
    for index, (reference, native) in enumerate(zip(vm.r, port.r)):
        if reference != native:
            differences.append(dict(register=f'r{index}', reference=f'{reference:08X}',
                native=f'{native:08X}'))
    for index, (reference, native) in enumerate(zip(vm.f, port.ctx.f)):
        if reference != native:
            differences.append(dict(register=f'f{index}', reference=f'{reference:08X}',
                native=f'{native:08X}'))
    for name in ('hi', 'lo'):
        reference, native = getattr(vm, name), getattr(port.ctx, name)
        if reference != native:
            differences.append(dict(register=name, reference=f'{reference:08X}',
                native=f'{native:08X}'))
    if vm.pc != port.pc:
        differences.append(dict(register='pc', reference=f'{vm.pc:08X}', native=f'{port.pc:08X}'))
    return differences


def summarize(cases, rows, metadata, covered, begun, dll_hash, args):
    completed = [case for case in cases if case.get('completed')]
    mismatches = [case for case in completed if not case.get('matches')]
    errors = [case for case in cases if not case.get('completed')]
    tested = {case['predicate_relative'] for case in completed}
    all_functions = {row['predicate_relative'] for row in rows}
    coverage = []
    for relative in sorted(all_functions):
        address = BASE + int(relative, 16)
        size = metadata[int(relative, 16)]['size']
        body = set(range(address, address + size, 4))
        coverage.append(dict(relative=relative, size_bytes=size,
            executed_instructions=len(body & covered), total_instructions=size // 4,
            completed_cases=sum(case['predicate_relative'] == relative for case in completed)))
    return dict(case_count=len(cases), completed_case_count=len(completed),
        differential_mismatch_count=len(mismatches), execution_error_count=len(errors),
        table_record_count=len(rows), unique_predicate_count=len(all_functions),
        predicates_with_completed_tests=len(tested),
        predicates_without_completed_tests=sorted(all_functions - tested),
        mapped_card_case_count=sum(case['card_database_mapped'] for case in completed),
        legacy_unmapped_card_records=[row for row in rows if not row['card_database_mapped']],
        cases=cases, mismatches=mismatches[:20], execution_errors=errors,
        predicate_body_coverage=coverage,
        predicate_body_coverage_totals=dict(
            executed_instructions=sum(item['executed_instructions'] for item in coverage),
            total_instructions=sum(item['total_instructions'] for item in coverage),
            fully_covered_predicate_bodies=sum(item['executed_instructions'] == item['total_instructions'] for item in coverage),
            boundary_source='Heuristic function boundaries from reports/module_analysis.json; coverage is instruction coverage, not complete branch or state coverage.'),
        group_case_counts=dict(collections.Counter(case['group'] for case in cases)),
        callback_return_histogram=dict(collections.Counter(str(case['returns'][-1]['reference']) for case in completed)),
        source_snapshot='work/trial4_selector_entry.bin',
        source_snapshot_sha256=hashlib.sha256((ROOT / 'work/trial4_selector_entry.bin').read_bytes()).hexdigest(),
        ram_address_range=['08800000', '09FFFFFF'],
        original_instruction_vm='tools/mips_vm.py', native_abi_version=2,
        distinct_original_instructions_executed=len(covered),
        dynamic_original_instructions_executed=sum(case['reference_instructions'] for case in completed),
        dynamic_native_instructions_executed=sum(case['native_instructions'] for case in completed),
        actual_psp_host_service_counts=dict(sum((collections.Counter(case.get('services', {}))
            for case in completed), collections.Counter())),
        native_dll_sha256=dll_hash, seconds=round(time.perf_counter() - begun, 3),
        instruction_bytes_erased_test_enabled=not args.skip_erased,
        original_instruction_bytes_used_by_native=False,
        contexts=list(CONTEXTS[:args.context_count]),
        special_register_inputs={str(key): f'{value:08X}' for key, value in REGISTERS.items()},
        comparison_criteria=dict(memory='All 24 MiB of private RAM, including stack and globals; for erased-code repeats the manifest instruction ranges are erased in both compared states.',
            registers='All 32 GPRs, HI, LO, all 32 raw FPU storage registers, and final PC after each preparation helper and callback.',
            returns='Signed v0 return after each preparation helper and callback.',
            excluded='FCR31 (not exposed by reference VM), instruction totals (host service bridge changes totals), instruction text deliberately erased in the extra repeat.'),
        method='Each callback executes its full original helper graph in a private original-instruction VM, compiled C and an additional native state with source instruction bytes erased. Complete copied RAM states, exposed register state and returns are compared. Only PSP interrupt suspend/resume services are bridged.',
        limits='Constructed decision fixtures based on one initialized captured duel. Activation legality is not asserted; not exhaustive legal game states, complete duels or every conditional card branch. Three legacy table IDs have no card row in the extracted database and are identified separately. Card rule helpers are never synthetic stubs.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit-records', type=int)
    parser.add_argument('--context-count', type=int, choices=range(1, 5), default=4)
    parser.add_argument('--skip-erased', action='store_true', help='Diagnostic only; full validation should include erased code.')
    parser.add_argument('--maximum-instructions', type=int, default=12000000)
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/native_callback_validation.json')
    args = parser.parse_args()
    begun = time.perf_counter()
    rows, cards = read_tables()
    if args.limit_records:
        rows = rows[:args.limit_records]
    source = (ROOT / 'work/trial4_selector_entry.bin').read_bytes()
    module = next(item for item in json.loads((ROOT / 'reports/module_analysis.json').read_text())
        if item['name'] == 'modduel_eng')
    metadata = {item['relative_address']: item for item in module['functions']}
    native = NativePort(source, coverage=True)
    erased = NativePort(source) if not args.skip_erased else None
    dll_hash = hashlib.sha256((Path(__file__).parent / 'build/tagforce_ai.dll').read_bytes()).hexdigest()
    cases, covered = [], set()
    for row in rows:
        for context in CONTEXTS[:args.context_count]:
            state, chain_pointer, fixture_info = fixture(source, row, context)
            # The original early/late dispatchers pass NULL by definition.
            if row['group'] in ('early_card_priorities', 'late_card_priorities'):
                chain_pointer = 0
            vm = VM(state)
            prepare_reference(vm)
            reset_native(native, state)
            if erased:
                reset_native(erased, cleared_code(state))
            case = dict(**row, context=context['name'], fixture=fixture_info,
                context_argument=f'{chain_pointer:08X}', completed=False)
            relative = int(row['predicate_relative'], 16)
            prefix = [(0x42584, (0,)), (0x442F8, (0, 1))] if context['battle'] else []
            prefix.append((relative, (ACTIVATION, chain_pointer)))
            try:
                returns = []
                for routine, parameters in prefix:
                    reference_return = vm.call(BASE + routine, *parameters, sp=STACK,
                        max_steps=args.maximum_instructions, registers=REGISTERS)
                    native_return = native.call(BASE + routine, *parameters, sp=STACK,
                        max_steps=args.maximum_instructions, registers=REGISTERS)
                    erased_return = erased.call(BASE + routine, *parameters, sp=STACK,
                        max_steps=args.maximum_instructions, registers=REGISTERS) if erased else native_return
                    returns.append(dict(relative=f'{routine:06X}',
                        reference=reference_return, native=native_return, erased=erased_return,
                        register_differences=register_differences(vm, native),
                        erased_register_differences=register_differences(vm, erased) if erased else []))
                native_state = native.bytes()
                reference_state = bytes(vm.mem)
                diff = differences(reference_state, native_state)
                erased_diff = differences(cleared_code(native_state), erased.bytes()) if erased else []
                matched_returns = all(item['reference'] == item['native'] == item['erased'] for item in returns)
                matched_registers = all(not item['register_differences'] and not item['erased_register_differences'] for item in returns)
                case.update(completed=True, matches=matched_returns and matched_registers and not diff and not erased_diff,
                    returns=returns, memory_matches=not diff, memory_difference=diff,
                    registers_match=matched_registers,
                    erased_instruction_state_matches=not erased_diff,
                    erased_instruction_state_difference=erased_diff,
                    reference_instructions=vm.steps, native_instructions=native.steps,
                    services=dict(native.services),
                    activation_descriptor_after=vm.read(ACTIVATION, 24).hex())
                covered.update(vm.visits)
            except Exception as error:
                case.update(error=str(error), reference_pc=f'{vm.pc:08X}',
                    reference_recent=[f'{address:08X}' for address in vm.recent],
                    native_pc=f'{native.pc:08X}', services=dict(native.services),
                    classification='execution_or_fixture_error_requires_investigation')
            cases.append(case)
            if not case.get('matches'):
                print(f"ERROR {row['group']}[{row['table_index']}] {row['predicate_relative']} {context['name']}: {case.get('error', 'behavior/state mismatch')}", flush=True)
            elif len(cases) % 50 == 0:
                print(f'Completed {len(cases)}/{len(rows) * args.context_count} callback fixtures', flush=True)
            if len(cases) % 100 == 0:
                args.output.write_text(json.dumps(summarize(cases, rows, metadata, covered, begun, dll_hash, args), indent=2), encoding='utf-8')
    summary = summarize(cases, rows, metadata, covered, begun, dll_hash, args)
    native_coverage = {START + index * 4 for index, count in enumerate(native.visits) if count}
    summary['distinct_native_source_instructions_executed'] = len(native_coverage)
    summary['native_coverage_excludes_host_services_in_reference'] = True
    args.output.write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in summary.items()
        if key not in ('cases', 'predicate_body_coverage', 'execution_errors', 'mismatches', 'contexts')}, indent=2))
    if summary['differential_mismatch_count'] or summary['execution_error_count']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
