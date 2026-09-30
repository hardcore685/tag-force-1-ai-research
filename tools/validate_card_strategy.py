"""Original-MIPS differential checks for readable card-strategy routines.

Only a private copy of captured RAM is used. Legality and card-specific
predicates are explicit fixtures; the routines under test execute their
original instruction bodies. This does not alter the game or save files.
"""
import csv
import json
import random
import sqlite3
import time
from collections import Counter
from pathlib import Path

from card_strategy_model import (
    BASE, ACTOR, INNER, EARLY_TABLE, LATE_TABLE, POSITION_SPECIAL_IDS,
    ALWAYS_ATTACK, ALWAYS_DEFENSE, RACE_COUNT_ATTACK, FIELD_10F4_ATTACK,
    MODE_ZERO_ATTACK, BOTH_ZERO_ATTACK, CardRule, card_position_override,
    pre_action_priority, late_action_priority, try_priority_rule,
    SPELL_COUNTER_LIMITS, spell_counter_capacity,
    SET_DEFENSE_EXTRA_IDS, CREATURE_SWAP_ATTACK_IDS, prefer_set_defense, creature_swap_attack_class,
)
from mips_vm import VM, signed
from validate_responses import structurally_reachable

ROOT = Path(__file__).resolve().parent.parent
PLAYER = BASE + 0x111E5C
SELECTION_PLAYER = BASE + 0x115648
REFRESH = BASE + 0x11563C
RULE_POINTER = 0x09FFF200
RULE_CALLBACK = 0x09FFF120

ARITIES = {
    0xDE2F8:3, 0xE4624:1, 0xE5198:2, 0xE55AC:2, 0xE43C8:3,
    0x6F74:2, 0xE7360:2, 0xE5C1C:3, 0xE5C84:1, 0xE5988:1, 0xED9FC:2,
    0x6400:5, 0xFFA78:1, 0x707C:1, 0xE2998:2, 0xFFB80:1,
    0xE28A0:2, 0xE5148:2, 0xED950:2, 0xE76D0:1, 0xE6184:1,
    0xE5684:2, 0x32868:1, 0x31E8:1, 0x1C88:1, 0xFFB48:1,
    0x1C618:2, 0x1CD44:1, 0x1D888:1, 0x1D124:1, 0x1C948:2,
    0xE6E54:1, 0xDB664:1, 0x6FC4:1, 0xEDAAC:2,
}


class Fixture:
    base = BASE
    def __init__(self, data, state):
        self.data = data
        self.state = {key:value & 0xFFFFFFFF for key,value in state.items()}
        self.calls = []
        self.records = []
        self.selected = None
        self.selection_player = 82
        self.refresh = 83
    def load(self, relative): return self.state.get(relative, 0)
    def store(self, relative, value): self.state[relative] = value & 0xFFFFFFFF
    def phase(self): return self.data['phase']
    def life_points(self, player): return self.data['life'][player]
    def card_id(self, player, slot): return self.data['cards'][player][slot]
    def flags(self, player, slot): return self.data['flags'][player][slot]
    def record_word8(self, player, slot): return self.data['word8'][player][slot]
    def current_card_id(self, player, slot): return self.data['current'][player][slot]
    def field_record(self, player, slot): return self.data['full_records'][player][slot]
    def hand_records(self, player): return self.data['hands'][player]
    def find_in_hand(self, player, cid): return self.data['hand_index']
    def hand_activation_allowed(self, player): return self.data['defaults'][0x6FC4]
    def effect_legal(self, record, context, face_up):
        self.records.append(('legal', record.hex(), context, face_up))
        return self.data['legality'][int(face_up)][(record[2] >> 1) & 31]
    def activation_rejected(self, record):
        self.records.append(('reject', record.hex()))
        return self.data['rejected'][(record[2] >> 1) & 31]
    def predicate(self, record, context):
        self.records.append(('predicate', record.hex(), context))
        return self.data['predicate'][(record[2] >> 1) & 31]
    def set_selection_player(self, player): self.selection_player = player
    def set_refresh(self, value): self.refresh = value
    def submit(self, *args): self.selected = list(args); return 42
    def call(self, relative, *args):
        args = tuple(signed(value) for value in args[:ARITIES[relative]])
        self.calls.append((relative, args))
        if (relative, args) in self.data['specific']:
            return self.data['specific'][(relative, args)]
        if relative == 0xE2998: return self.current_card_id(*args)
        if relative == 0xFFA78: return self.data['races'].get(args[0], 0)
        if relative == 0xFFB80: return self.data['capacities'].get(args[0], 0)
        if relative == 0xE28A0: return self.data['counters'][args[0]][args[1]]
        if relative == 0xE55AC: return self.data['race_counts'][args[0]][args[1] & 31]
        if relative == 0xE5988: return self.data['monster_counts'][args[0]]
        if relative == 0x1C618: return int(args[1] in self.data['table_successes'])
        if relative == 0xEDAAC: return self.find_in_hand(*args)
        return self.data['defaults'][relative]


def make_data(rng):
    cards = [[4000 + player*50 + slot for slot in range(11)] for player in range(2)]
    current = [row[:] for row in cards]
    data = {
        'phase':rng.choice([0,2,3,5]), 'life':[rng.choice([-1,0,2000,2001,3999,4000,8000]) for _ in range(2)],
        'cards':cards, 'current':current,
        'flags':[[rng.getrandbits(24) for _ in range(11)] for _ in range(2)],
        'word8':[[rng.choice([0,0,1,-1,255]) for _ in range(11)] for _ in range(2)],
        'full_records':[[cid | (rng.getrandbits(19) << 13) for cid in row] for row in cards],
        'hands':[[rng.randrange(4000,7000) | (rng.getrandbits(19) << 13) for _ in range(rng.randrange(9))] for _ in range(2)],
        'defaults':{r:rng.choice([-1,0,0,0,1,2]) for r in ARITIES},
        'specific':{}, 'races':{cid:rng.randrange(32) for cid in POSITION_SPECIAL_IDS},
        'capacities':{cid:rng.choice([0,0,1,2,3]) for row in cards for cid in row},
        'counters':[[rng.randrange(4) for _ in range(11)] for _ in range(2)],
        'race_counts':[[rng.randrange(6) for _ in range(32)] for _ in range(2)],
        'monster_counts':[rng.randrange(6) for _ in range(2)],
        'table_successes':set(), 'hand_index':-1,
        'legality':[[int(rng.random()<.75) for _ in range(12)] for _ in range(2)],
        'rejected':[int(rng.random()<.25) for _ in range(12)],
        'predicate':[int(rng.random()<.5) for _ in range(12)],
    }
    data['defaults'][0x6400] = rng.choice([-1,0,1100,1101,1899,1900,3000])
    data['defaults'][0xE6E54] = rng.choice([0,1,2,3])
    data['defaults'][0xED950] = rng.choice([0,1,2,3])
    data['defaults'][0xE6184] = rng.choice([0,1,2,3])
    data['specific'][(0xE5198,(0,0x1784))] = rng.randrange(2)
    data['specific'][(0xE5198,(1,0x1784))] = rng.randrange(2)
    for cid in (0x135D,0x140E,0x17A6):
        data['specific'][(0xE4624,(cid,))] = rng.choice([-1,0,0,1])
    return data


def seed_memory(vm, data, state):
    for relative, value in state.items(): vm.store(BASE+relative,value)
    vm.store(BASE+0x1155DC,data['phase'])
    vm.store(SELECTION_PLAYER,82)
    vm.store(REFRESH,83)
    for player in range(2):
        address = PLAYER + player*0xFAC
        vm.store(address,data['life'][player])
        vm.store(address+0xC,len(data['hands'][player]))
        for slot in range(11):
            vm.store(address+0x30+20*slot,data['full_records'][player][slot])
            vm.store(address+0x38+20*slot,data['word8'][player][slot],2)
            vm.store(address+0x40+20*slot,data['flags'][player][slot])
        for index, value in enumerate(data['hands'][player]):
            vm.store(address+0x120+4*index,value)


def install_callbacks(vm, api, test_relative):
    vm.callbacks = {}
    for relative, arity in ARITIES.items():
        if relative == test_relative: continue
        def callback(v, relative=relative, count=arity):
            v.set(2,api.call(relative,*v.r[4:4+count]))
        vm.callbacks[BASE+relative] = callback
    vm.callbacks[BASE+0x65ACC] = lambda v:v.set(2,api.effect_legal(v.read(v.r[4],24),v.r[5],False))
    vm.callbacks[BASE+0x6581C] = lambda v:v.set(2,api.effect_legal(v.read(v.r[4],24),v.r[5],True))
    vm.callbacks[BASE+0x1C54C] = lambda v:v.set(2,api.activation_rejected(v.read(v.r[4],24)))
    vm.callbacks[RULE_CALLBACK] = lambda v:v.set(2,api.predicate(v.read(v.r[4],24),v.r[5]))
    vm.callbacks[BASE+0x31F34] = lambda v:v.set(2,api.submit(*[signed(x) for x in v.r[4:8]]))


def export_routes(ram):
    vm = VM(ram)
    connection = sqlite3.connect(ROOT/'reports/card_database/cards.sqlite')
    names = dict(connection.execute('SELECT card_id,name FROM cards'))
    connection.close()
    source = ROOT/'work/decompiled/modduel_eng_relocated'
    function_names = {int(row['address'],16):row['name'] for row in csv.DictReader((source/'function_index.csv').open())}
    tables = []
    for name, relative, count in [('early',EARLY_TABLE,61),('late',LATE_TABLE,87)]:
        entries = []
        for index in range(count):
            cid = vm.load(BASE+relative+8*index,4)
            callback = vm.load(BASE+relative+8*index+4,4)
            entries.append(dict(index=index,card_id=cid,name=names.get(cid),record_relative=f'{relative+8*index:06X}',
                                predicate_address=f'{callback:08X}',predicate_relative=f'{callback-BASE:06X}',
                                predicate_function=function_names.get(callback)))
        tables.append(dict(name=name,table_relative=f'{relative:06X}',entries=entries))
    rows = []
    for cid in sorted(POSITION_SPECIAL_IDS):
        if cid in ALWAYS_ATTACK: route='attack_after_global_checks'
        elif cid in ALWAYS_DEFENSE: route='defense_after_global_checks'
        elif cid in RACE_COUNT_ATTACK: route='race_count_comparison_in_mode_zero'
        elif cid in FIELD_10F4_ATTACK: route='field_card_10F4_in_mode_zero'
        elif cid in MODE_ZERO_ATTACK: route='attack_when_context_mode_zero'
        elif cid in BOTH_ZERO_ATTACK: route='attack_when_both_parameters_zero'
        else: route='individual_condition'
        rows.append(dict(card_id=cid,name=names.get(cid),route=route))
    result = dict(position_routine_relative='001DA4',position_special_card_count=len(rows),position_cards=rows,
                  global_rules_precede_card_rows=True,
                  global_rules=[dict(condition='phase == 3 and eng+DE2F8(player,11,0x1669) != 0',card_name=names.get(0x1669),result='defense'),
                                dict(condition='eng+E4624(0x135D) > 0',card_name=names.get(0x135D),result='force context_mode to 1')],
                  priority_tables=tables,table_record_count=148,
                  unique_predicate_addresses=len({entry['predicate_address'] for table in tables for entry in table['entries']}),
                  spell_counter_getter_address='088504A8',
                  spell_counter_capacities=[dict(card_id=cid,name=names.get(cid),capacity=value) for cid,value in SPELL_COUNTER_LIMITS.items()],
                  extra_set_defense_cards=[dict(card_id=cid,name=names.get(cid)) for cid in sorted(SET_DEFENSE_EXTRA_IDS)],
                  creature_swap_attack_cards=[dict(card_id=cid,name=names.get(cid)) for cid in sorted(CREATURE_SWAP_ATTACK_IDS)],
                  limitations='Mappings preserve original IDs and ordering. Individual priority predicates remain explicit engine services in the readable component.')
    (ROOT/'reports/card_strategy_routes.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    with (ROOT/'reports/card_strategy_position_routes.csv').open('w',newline='',encoding='utf-8') as output:
        writer = csv.DictWriter(output,fieldnames=['card_id','name','route'])
        writer.writeheader();writer.writerows(rows)
    return result


def main():
    started = time.perf_counter()
    rng = random.Random(72408)
    ram = (ROOT/'work/ram_turn25.bin').read_bytes()
    vm = VM(ram)
    counts, failures = Counter(), []
    metadata = {int(row['address'],16)-BASE:int(row['bytes']) for row in csv.DictReader((ROOT/'work/decompiled/modduel_eng_relocated/function_index.csv').open())}

    def compare(relative, iteration, data, state, args, model, compare_calls=True, compare_selection=False):
        actual_api, expected_api = Fixture(data,state), Fixture(data,state)
        seed_memory(vm,data,state)
        install_callbacks(vm,actual_api,relative)
        if relative == 0x1C618:
            vm.store(RULE_POINTER,args[1])
            vm.store(RULE_POINTER+4,RULE_CALLBACK)
            actual = vm.call(BASE+relative,args[0],RULE_POINTER)
        else:
            actual = vm.call(BASE+relative,*args)
        expected = model(expected_api)
        actual_api.state = {key:vm.load(BASE+key,4) for key in state}
        actual_api.selection_player = vm.load(SELECTION_PLAYER,4)
        actual_api.refresh = vm.load(REFRESH,4)
        counts[f'{relative:06X}'] += 1
        mismatch = actual != expected or actual_api.state != expected_api.state
        if compare_calls: mismatch |= actual_api.calls != expected_api.calls
        if compare_selection:
            mismatch |= (actual_api.records != expected_api.records or actual_api.selected != expected_api.selected or
                         actual_api.selection_player != expected_api.selection_player or actual_api.refresh != expected_api.refresh)
        if mismatch:
            failures.append(dict(relative=f'{relative:06X}',iteration=iteration,args=args,actual=actual,expected=expected,
                                 actual_calls=actual_api.calls,expected_calls=expected_api.calls,
                                 actual_state=actual_api.state,expected_state=expected_api.state,
                                 actual_records=actual_api.records,expected_records=expected_api.records,
                                 actual_selection=actual_api.selected,expected_selection=expected_api.selected,
                                 actual_refresh=actual_api.refresh,expected_refresh=expected_api.refresh))

    for cid in sorted(POSITION_SPECIAL_IDS | {4007,0x1FFF}):
        for iteration in range(100):
            data = make_data(rng)
            player = rng.randrange(2)
            context, option = rng.choice([-1,0,0,1,2]), rng.choice([-1,0,0,1,2])
            state = {ACTOR:player,INNER:rng.randrange(5)}
            compare(0x1DA4,iteration,data,state,(player,cid,context,option),
                    lambda a:card_position_override(player,cid,context,option,a))

    # Force both exact strength boundaries and LP boundaries while clearing
    # global overrides, so an unrelated effect cannot mask the tested path.
    for cid, boundary, field in [(0x1523,1100,'score'),(0x1523,1101,'score'),
                                 (0x1523,1899,'score'),(0x1523,1900,'score'),
                                 (0x1836,3999,'life'),(0x1836,4000,'life'),
                                 (0x13A7,2000,'life'),(0x13A7,2001,'life')]:
        data = make_data(rng)
        data['phase'] = 2
        data['specific'][(0xE4624,(0x135D,))] = 0
        if field == 'score':data['defaults'][0x6400] = boundary
        else:data['life'][0] = boundary
        compare(0x1DA4,100,data,{ACTOR:0,INNER:0},(0,cid,0,0),lambda a:card_position_override(0,cid,0,0,a))

    # Force a counter-capacity match in the tenth slot, and a full ten-slot
    # scan with no match, to exercise both terminal forms of the special loop.
    for match in (False,True):
        data = make_data(rng)
        data['phase'] = 2
        data['specific'][(0xE4624,(0x135D,))] = 0
        data['capacities'] = {cid:0 for row in data['cards'] for cid in row}
        if match:
            data['capacities'][data['current'][0][9]] = 2
            data['counters'][0][9] = 1
        compare(0x1DA4,101,data,{ACTOR:0,INNER:0},(0,0x1612,0,0),lambda a:card_position_override(0,0x1612,0,0,a))

    for relative, model in [(0x1CE08,pre_action_priority),(0x1DABC,late_action_priority)]:
        for iteration in range(1000):
            data = make_data(rng)
            player = rng.randrange(2)
            stage = rng.choice([-1,0,0,0,1,1,2,3,4,7])
            state = {ACTOR:player,INNER:stage}
            for table,count in [(EARLY_TABLE,61),(LATE_TABLE,87)]:
                index = rng.choice([-1,-1,-1,0,count-1,rng.randrange(count)])
                if index >= 0:data['table_successes'].add(BASE+table+8*index)
            compare(relative,iteration,data,state,(),model)

    # Cover empty back-row slots and the exact hidden-card threshold, together
    # with both branches of the subsequent CD44/D888 checks.
    iteration = 1000
    for hidden in (0,1,2,6):
        for first_gate in (0,1):
            for action in (0,1):
                data = make_data(rng)
                data['defaults'].update({0xE6E54:2,0xDB664:1,0x6FC4:1,0x1CD44:first_gate,0x1D888:action})
                for slot in range(5,11):
                    cid = 4000+slot if slot-5 < hidden else 0
                    data['cards'][0][slot] = cid
                    data['full_records'][0][slot] = (data['full_records'][0][slot]&~0x1FFF)|cid
                    data['word8'][0][slot] = 0
                    data['flags'][0][slot] = 1<<20
                compare(0x1DABC,iteration,data,{ACTOR:0,INNER:1},(),late_action_priority)
                iteration += 1

    for iteration in range(1400):
        data = make_data(rng)
        player,cid = rng.randrange(2),rng.choice([4007,5010,6002])
        state = {ACTOR:player,INNER:rng.randrange(5)}
        data['current'] = [[cid if rng.random()<.3 else 4000+slot for slot in range(11)] for _ in range(2)]
        if data['hands'][player] and rng.random()<.5:
            data['hand_index'] = rng.randrange(len(data['hands'][player]))
        compare(0x1C618,iteration,data,state,(player,cid),
                lambda a:try_priority_rule(player,CardRule(cid,a.predicate),a),
                compare_calls=False,compare_selection=True)

    connection = sqlite3.connect(ROOT/'reports/card_database/cards.sqlite')
    card_ids = [row[0] for row in connection.execute('SELECT card_id FROM cards')]
    connection.close()
    for cid in card_ids:
        for flip in (0,1):
            data = make_data(rng)
            data['defaults'][0xFFB48] = flip
            compare(0x1C88,flip,data,{ACTOR:0,INNER:0},(cid,),lambda a:prefer_set_defense(cid,a))
        data = make_data(rng)
        compare(0x31E8,0,data,{ACTOR:0,INNER:0},(cid,),lambda a:creature_swap_attack_class(cid))

    coverage = []
    for text,count in sorted(counts.items()):
        relative,length = int(text,16),metadata[int(text,16)]
        reachable = structurally_reachable(vm,BASE+relative,length)
        visited = sum(BASE+relative<=pc<BASE+relative+length for pc in vm.visits)
        missing = [f'{pc:08X}' for pc in sorted(reachable) if pc not in vm.visits]
        coverage.append(dict(relative=text,cases=count,instructions_visited=visited,total_instructions=length//4,
                             structurally_reachable_instructions=len(reachable),reachable_instructions_not_visited=missing))
    counter_failures = []
    vm.callbacks = {}
    for cid in card_ids:
        actual,expected = vm.call(0x088504A8,cid),spell_counter_capacity(cid)
        if actual != expected:counter_failures.append(dict(card_id=cid,actual=actual,expected=expected))
    counter_report = dict(address='088504A8',cards_checked=len(card_ids),mismatch_count=len(counter_failures),failures=counter_failures,
                          instructions_visited=sum(0x088504A8<=pc<0x08850544 for pc in vm.visits),total_instructions=(0x08850544-0x088504A8)//4)
    result = dict(seed=72408,case_count=sum(counts.values()),mismatch_count=len(failures),failures=failures[:10],
                  routines=coverage,spell_counter_getter=counter_report,seconds=round(time.perf_counter()-started,3),
                  limitations='Original instruction bodies run with explicit synthetic legality, field records and predicate returns. Position tests also compare complete helper call traces. Priority scheduler tests verify complete table traversal order and fallthrough state changes. Individual card predicates and complete-duel/live-game equivalence are outside this readable component validation.')
    (ROOT/'reports/card_strategy_validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    routes = export_routes(ram)
    print(json.dumps(result,indent=2))
    print(json.dumps(dict(position_special_ids=routes['position_special_card_count'],priority_records=routes['table_record_count'],
                          priority_unique_predicates=routes['unique_predicate_addresses'])))
    if failures or counter_failures:raise SystemExit(1)


if __name__=='__main__':main()
