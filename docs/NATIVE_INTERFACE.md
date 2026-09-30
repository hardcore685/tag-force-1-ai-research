# Native interface

The context layout is `tools/native_port/generated/tf_native.h`, ABI version 2.
The exports are `tf_run(TFContext*)` and `tf_abi_version()`.

`NativePort` copies 24 MiB of initialized guest memory, mapped at 0x08800000.
The captured engine base is 0x09E65C00. Guest addresses are not host pointers.
A compatible state needs valid card tables, field/hand records, instance mapping,
heap context, RNG, pending actions and event/chain descriptors.

`call(address, *args, sp=..., registers=..., special_registers=...)` retains memory
while initializing a fresh CPU-call context. The first eight integer arguments
use registers 4..11; additional arguments start at the supplied stack address.
Captured gp, k0, stack, return address and raw special registers must match the
call. Setting a player or phase in a blank byte array is insufficient.

Callbacks are function-entry hooks. The Python wrapper rejects addresses in
compiled branch delay slots using `generated/callback_contract.json`. C callers
must obey this same contract. The wrapper's interrupt bridges have stateful
single-thread semantics; other reached PSP services raise errors.

`TFSTATE1` means an 8-byte ASCII magic followed by zlib-compressed state bytes.
Public examples omit these game-derived data fixtures. Original PSP executable
bytes must not be mistaken for a required runtime implementation: the native
port follows compiled C operations, and erased-instruction repeats were tested.

Full-update entry 0x09EA4998 (engine+0x3ED98) replays one AI/engine update. Hand
selector entry 0x09E70FB4 (engine+0xB3B4) takes player and an output-word pointer.
The host must process appropriate action/update/resolution stages for a match.
Repeating one saved caller context is not a validated full-duel loop.

Source-generation inputs and original-reference fixtures are local research
inputs. Inspect the analysis/capture tools and produce compatible metadata with
your own copy; the two public analysis JSONs contain only runtime import metadata
and are not substitutes for full local function/disassembly analysis.
