# What we found in the Tag Force 1 AI

This research targets **Yu-Gi-Oh! GX Tag Force (USA), ULUS10136, disc version
1.03**. The recovered routines combine ordered scans, card-specific conditions,
temporary battle simulations and seeded randomness. Function names below are
our descriptions of stripped routines, not recovered Konami source symbols.

## Hand order can change the chosen monster

`choose_hand_monster` scans the hand from first to last. Each accepted candidate
overwrites the previous choice. Its strength comparisons use references from
the board; they never become the strength of the previously selected hand card.
Consequently, this routine can choose a later, weaker monster that passes its
conditions. Eligibility still matters: legality, tribute availability, position
restrictions and card-specific preferences affect which candidates pass.

In replays of one initialized board, all six permutations of its three-card hand
selected the last slot (index 2). A separate live test that reordered complete
hand records changed the choice from Star Boy to Nightmare Penguin. This is a
finding about that selector and context, not every summon decision.
The separate `choose_fallback` behaves differently: after the normal scan
fails, it returns the **first** eligible low-level monster.

Read [`choose_hand_monster`, `choose_fallback` and the small `fixture_demo`](../tools/ai_model.py).
The demo uses explicit current statistics and costs; it does not simulate every
card effect or imply those costs are the cards' printed requirements.

## Position choice and action timing are separate decisions

`choose_position` applies concrete thresholds. For example, after earlier
special cases, an opponent threat at least 1,000 points above the candidate's
ATK selects defense. The threat has a minimum reference of 1,500. Card-specific
position overrides run before this general heuristic, so printed ATK versus DEF
alone cannot explain every position choice.

The phase controller is a staged checklist. Its outer handler table visits early
card priorities, summon/effect passes, position changes, battle preparation and
later priorities. Inner counters track progress and can restart after an action.
In one early pass, a defense selection can finish that pass without queuing the
summon; a later summon pass can act on it. A returned selection and a submitted
action therefore need to be inspected separately.

Read [`choose_position`](../tools/ai_model.py),
[`card_position_override`](../tools/card_strategy_model.py), and
[`HANDLERS`, `outer_tick`, `main_phase_tick` and `summon_tick`](../tools/phase_model.py).

## Card effects have ordered priorities and individual conditions

Four recovered tables contain **491 records referring to 142 distinct card
predicate functions**: 61 early priorities, 87 late priorities, 257 general
activation records and 86 response records. These are table entries, not 491
independent strategies. The controllers try entries in stored order and can stop
on the first successful action.

A predicate's approval is only one step. The controllers also check legality,
locate a card instance, construct an action record and submit it. Position
overrides likewise include fixed card groups and contextual tests involving
life points, field cards or counters.

Read [`pre_action_priority`, `late_action_priority` and `try_priority_rule`](../tools/card_strategy_model.py),
and [`optional_activation`, `chain_response` and `try_activation_rule`](../tools/response_model.py).

## Battle and tribute planning try temporary board states

Attack ordering includes card-specific priority tiers as well as strength.
Target choice compares simulated damage and monster losses with additional
acceptance and tie rules. The recovered battle evaluator also rejects lethal
self-damage in its tested conditions.

`plan_battle_summon` temporarily removes tribute material, places a candidate and
evaluates the resulting attack sequence. A qualifying trial improves predicted
damage, or ties damage while removing more opposing monsters and leaving at
least one own monster. Each trial restores the ten monster records it saved;
simulation result counters remain available. This is concrete look-ahead, but
does not establish globally optimal play or exhaustive search.

Read [`attacker_order`, `choose_attack_target`, `evaluate_battle` and `simulated_attack_score`](../tools/battle_model.py),
and [`plan_battle_summon` and `place_hypothetical_summon`](../tools/summon_planning_model.py).

## Target ties can depend on randomness

Effect targeting dispatches through card-specific routes and generic selectors.
Some scored selectors shuffle eligible candidates before comparing them. Equal
scores retain the earlier shuffled candidate, so a tie can change with the RNG
state. `GameRandom` reconstructs the game's 32-bit generator and bounded draw.
Reproducing a decision requires the same random state **and call order**.

Read [`select_effect_target`](../tools/target_model.py), and
[`GameRandom`, `shuffle_candidates`, `scored_target` and `stat_target`](../tools/response_model.py).

## What the evidence establishes

The [recorded validation summary](../reports/VALIDATION_SUMMARY.json) reports
49,929 readable-component differential cases with no mismatches. Many of these
provide controlled legality, card-effect or simulation helper answers; they
validate the surrounding decisions rather than independently recreating all
rules. Separate native tests executed all 142 table predicate functions in 1,964
constructed cases using original rule helpers, with no mismatches or execution
errors. Measured predicate-body instruction coverage was **63.24%**, using
heuristic function boundaries; that is not complete branch or state coverage.

Four captured calls, including selection and full AI update ticks, matched the
complete duel-engine allocation. Two also matched all captured RAM. The tests
retain external asynchronous differences and a documented emulator `memcpy`
temporary-register exception. Live comparisons came from one initialized duel
with an attack restriction; unrestricted live battle sequences remain unproven.

All recovered duel-engine instruction positions are represented in the generated
C source. That source coverage does not prove every card interaction or whole
duel equivalent. A fresh-duel constructor, complete match-host loop and broader
platform integration remain work; unsupported platform services fail explicitly.
The public package contains reconstructed source and tooling. It excludes the
game ISO, extracted game files, saves, initialized memory captures, decoded card
data/text and artwork. Repeating original-input differential checks requires
separately obtained inputs.
See [current status](STATUS.md) and [native interface](NATIVE_INTERFACE.md).
