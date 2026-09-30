# Yu-Gi-Oh! GX Tag Force 1 AI Research

Reverse-engineering notes, readable decision models and a native source
translation for **Tag Force 1 USA, ULUS10136, disc version 1.03**.

This project investigates how the original AI selects summons, positions,
attacks, targets, optional activations and chain responses. It preserves the
recovered behavior, including order-dependent decisions.

The generated C covers the complete recovered duel-engine instruction region
and its import stubs, plus shared helper source. It compiles into a native
Windows library. Runtime decisions execute C operations and branches; they do
not decode an original PSP instruction stream.

This is **a source translation and AI research project**, not a byte-matching
PSP decompilation, the original Konami source, or a finished standalone duel game.

## Interesting findings

**Hand order can change which monster this AI summons.** On the tested board,
the recovered selector replaces its earlier choice when a later eligible card
passes its checks. All six tested orders chose slot 2; swapping complete hand
records in the running game changed the selected monster.

The recovered routines also expose fixed phase plans, card-specific activation
tables, battle what-if calculations and a random fallback for some targets.
Read [the findings and their evidence](docs/FINDINGS.md) for the exact scope,
source functions and limits of each result.

## Try the readable model

Python 3.10 or newer is sufficient for this demo:

```text
python tools/recreate_ai.py demo
python tools/recreate_ai.py choose examples/turn26.json
python tools/recreate_ai.py position examples/position.json
```

The demo compares two hand orders. On the recovered test board, both select
the last eligible slot; moving a different card into that slot changes the
selected monster. This finding applies to that routine and context, not every
AI decision.

Readable models take explicit current stats, legality and engine-helper answers.
They do not infer every duel rule from printed card statistics.

## Build the native source

The tested environment is 64-bit Windows, 64-bit Python, and Zig 0.16.0.

```text
python tools/native_port/build_native_port.py --zig C:/path/to/zig.exe --jobs 3 --opt 1
```

The generated C can be built without an ISO. **Running the full native AI also
requires privately obtained, correctly initialized game state.** Those inputs
are deliberately absent from this public package. `full_ai.py demo` needs the
private instruction-erased example states and matching contexts; it does not
run from this source-only checkout by itself.

Keep game extracts and memory captures in ignored local directories. The native
interface is described in `docs/NATIVE_INTERFACE.md`. Fixed addresses and guest
pointers must match this captured release and module layout.

## What was checked

The original experiment used private copies of the user's game and save data.
Original ISO/savedata fingerprints were checked unchanged. Recorded results:

| Check | Extent | Result |
| --- | --- | --- |
| Database values/text versus recovered getters | 24,790 comparisons over 2,479 records | No mismatches |
| Readable AI components | 49,929 differential cases | No mismatches |
| Independent instruction/branch/interface checks | 34,630 checks | All passed |
| Native port with actual rule helpers | 51 state/helper cases | No mismatches |
| Card predicates with actual helper graphs | 1,964 fixtures; 491 records; 142 unique predicates | No mismatches or execution errors |
| Captured live calls | One selector and three full AI updates | Entire 1,139,456-byte engine allocation matched |

The readable cases were also checked with compiled C as their reference. Native
cases were repeated with original executable instruction bytes erased.

Callback fixtures exercised 4,570 of 7,227 predicate-body instruction positions
(63.24%); 40 of 142 bodies were fully visited. They are four constructed contexts,
not exhaustive legal activations or complete duels. Three legacy table IDs had
no corresponding decoded database record and were retained in the tests.

One live call differed in two temporary CPU registers because PPSSPP substitutes
a recognized `memcpy` implementation. A separately verified replacement oracle
explains those differences. Unrelated asynchronous clock/audio RAM differences
are distinct from the matched engine state.

See `reports/VALIDATION_SUMMARY.json` and `docs/STATUS.md` for coverage and limits.
The source contains all engine instruction positions; that is a different claim
from testing every possible execution path or full-duel outcome.

## Repository layout

- `tools/*_model.py`: readable hand, position, phase, battle, tribute, target,
  response and card-priority logic.
- `tools/native_port/generated/`: complete generated C, context header and
  instruction/branch metadata.
- `tools/native_port/native_ai.py`: copied-state and native-call interface.
- `tools/native_port/generate_native_port.py`: regenerate from a matching private
  relocated capture and analysis metadata.
- `tools/native_port/build_native_port.py`: compile the supplied C.
- `tools/validate_*.py`: component differential validators; private reference
  inputs and local analysis metadata are required.
- `tools/card_database.py`: archive/property/text decoder, for locally provided
  game data. Decoded game data is not shipped here.
- `examples/`: small explicit decision inputs, with no memory snapshots.
- `docs/`: method, limitations, provenance and native interface.

## Useful next contributions

1. Build a valid fresh-duel initializer and complete host/update loop.
2. Increase card-predicate and legal-state test coverage beyond the captured duel.
3. Give more functions and state fields meaningful names.
4. Add new host services only with verified contracts and meaningful tests.
5. Create an EDOPro adapter that maps IDs, state, events and action requests.
   This code is not a drop-in WindBot executor.

## Source and data separation

This checkout contains source, tooling, small explicit examples and research
documentation. It excludes game ISOs, executable/module extracts, save data,
RAM captures, `.tfstate` fixtures, decoded card archives/text exports, artwork,
compiler downloads, and generated binary libraries. Anyone reproducing the
experiments must supply their own game and private reference data.

See `RIGHTS_AND_PROVENANCE.md`. A blanket open-source license has not been
selected; reconstructed game code and newly authored tooling must not be assumed
to have the same rights or provenance.

## Related work

- [Tag Force 5 matching decompilation](https://github.com/angelof-exe/ygo-tf-5-decomp)
  is a separate project for another release, with a byte-matching PSP objective.
- [PPSSPP v1.20.4](https://github.com/hrydgard/ppsspp/tree/v1.20.4)
  supplied debugger and instruction/service behavior references.
- [Ghidra Allegrex](https://github.com/kotcrab/ghidra-allegrex)
  supplied processor/calling-convention background.
- [EHP tooling](https://github.com/xan1242/ehppack) and
  [TFCardEdit](https://github.com/xan1242/TFCardEdit)
  supplied archive/property-format background.
