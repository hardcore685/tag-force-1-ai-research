"""Independent read-only coverage audit of the captured Tag Force executable.

Writes only reports/coverage_* and work/audit_*; original game and saves are
never opened for writes. Instruction-level closure includes direct tail jumps,
branch-and-link calls, and targets missed by PPSSPP's heuristic boundaries.
An unresolved indirect call is explicitly reported, not silently replaced.
"""
from __future__ import annotations
import bisect
import collections
import hashlib
import json
import re
import struct
from pathlib import Path
from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN

ROOT = Path(__file__).resolve().parent.parent
RAM_BASE = 0x08800000
# These eight switch tables are established by the bounded-index tests and
# LUI/ADDIU/ADDU/LW/JR sequences immediately before each listed jump site.
HSYS_SWITCH_TABLES = {
    0x08841AFC: (0x088755E8, 6),
    0x0886A15C: (0x088EB050, 6),
    0x0884F23C: (0x088AEB84, 6),
    0x08850A2C: (0x088AEB9C, 6),
    0x0884DFA8: (0x088AEB10, 16),
    0x0884E4A0: (0x088AEB50, 13),
    0x088154AC: (0x08874238, 5),
    0x088153D4: (0x08874054, 121),
    0x08811718: (0x08873D90, 46),
    0x0881369C: (0x08873EC4, 89),
    0x08815DA0: (0x08874384, 59),
    0x08815AC4: (0x0887424C, 78),
    0x0880AD98: (0x08873B58, 6),
}
HSYS_CALLBACK_TARGETS = {
    0x0880CDB4: ([0x0880C040,0x08810904], "Constant a1 values at all three direct callers of file-walk helper 0x0880CCCC."),
    0x0880CDC4: ([0x0880C040,0x08810904], "Constant a1 values at all three direct callers of file-walk helper 0x0880CCCC."),
    0x0880C0B8: ([0x08810E9C], "Captured FILE+0x24 write callback; other/custom FILE registrations need validation."),
    0x0880CBF0: ([0x08810E9C], "Captured FILE+0x24 write callback; other/custom FILE registrations need validation."),
    0x0880CB20: ([0x08810E9C], "Captured FILE+0x24 write callback; other/custom FILE registrations need validation."),
    0x0880C998: ([0x08810E9C], "Captured FILE+0x24 write callback; other/custom FILE registrations need validation."),
    0x088109CC: ([0x08810E24], "Captured FILE+0x20 read callback; other/custom FILE registrations need validation."),
    0x08815B80: ([0x08812784,0x08812C68], "Every nonzero assignment to local stack+0x280 is one of these two literal numeric-parser functions; initial value zero."),
}


def readjson(path):
    return json.loads(path.read_text(encoding="utf-8"))


def hx(value):
    return f"0x{value:08X}"


class Audit:
    def __init__(self):
        self.ram = (ROOT / "work/ram_turn25.bin").read_bytes()
        self.modules = readjson(ROOT / "reports/module_analysis.json")
        self.hsys = readjson(ROOT / "reports/modehsys_analysis.json")
        self.modules.append(self.hsys)
        self.dep = readjson(ROOT / "reports/hsys_engine_dependencies.json")
        self.map = readjson(ROOT / "reports/complete_ai_map.json")
        self.functions = {f["address"]: (m, f) for m in self.modules for f in m["functions"]}
        self.starts = sorted(self.functions)
        self.imports = {m["base_address"]+i["stub_address"]: dict(i, module=m["name"])
                        for m in self.modules for i in m["imports"]}
        self.dis = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
        self.dis.skipdata = True

    def word(self, addr):
        return struct.unpack_from("<I", self.ram, addr-RAM_BASE)[0]

    def owner(self, addr):
        pos = bisect.bisect_right(self.starts, addr)-1
        if pos >= 0:
            mod, f = self.functions[self.starts[pos]]
            if f["address"] <= addr < f["address"]+f["size"]:
                return mod, f
        return None, None

    def span(self, addr, count=20):
        raw = self.ram[addr-RAM_BASE:addr-RAM_BASE+4*count]
        return [dict(address=hx(a), word=hx(self.word(a)), instruction=f"{mn} {op}".strip())
                for a, size, mn, op in self.dis.disasm_lite(raw, addr)]

    def text_module(self, addr):
        return next((m for m in self.modules if m["base_address"] <= addr < m["base_address"]+m["text_size"]), None)

    def direct(self, pc, word):
        op = word >> 26
        if op in (2, 3):
            return (((pc+4) & 0xF0000000) | ((word & 0x03FFFFFF) << 2), "jump" if op == 2 else "call")
        if op == 1 and ((word >> 16) & 31) in (16, 17, 18, 19):
            imm = word & 0xFFFF
            if imm & 0x8000:
                imm -= 0x10000
            return pc+4+imm*4, "branch_link"
        return None

    def instruction_closure(self, seeds, modules, switches=None):
        allowed = {m["name"] for m in modules}
        todo = list(seeds)
        processed = set()
        code = set()
        external = collections.defaultdict(list)
        direct_edges = []
        indirect = []
        while todo:
            pc = todo.pop()
            if pc in processed:
                continue
            processed.add(pc)
            m = self.text_module(pc)
            if m is None or m["name"] not in allowed:
                external[pc]  # Keep seed-only unknown destinations, too.
                continue
            if pc & 3:
                raise ValueError(f"Unaligned control-flow destination {hx(pc)}")
            code.add(pc)
            word = self.word(pc)
            op = word >> 26
            fn = word & 63
            rt = (word >> 16) & 31
            target = self.direct(pc, word)
            if target:
                dest, kind = target
                code.add(pc+4)
                direct_edges.append(dict(site=pc, target=dest, kind=kind))
                dest_module = self.text_module(dest)
                if dest_module is None or dest_module["name"] not in allowed:
                    external[dest].append(pc)
                else:
                    todo.append(dest)
                if kind in ("call", "branch_link"):
                    todo.append(pc+8)
                continue
            if op == 0 and fn in (8, 9):
                code.add(pc+4)
                rs = (word >> 21) & 31
                if fn == 9:
                    info = dict(site=pc, register=rs, kind="indirect_call")
                    if pc in HSYS_CALLBACK_TARGETS:
                        targets, evidence = HSYS_CALLBACK_TARGETS[pc]
                        info.update(known_targets=targets, evidence=evidence)
                        todo.extend(targets)
                    indirect.append(info)
                    todo.append(pc+8)
                elif rs != 31:
                    if switches and pc in switches:
                        table, count = switches[pc]
                        for index in range(count):
                            dest = self.word(table+4*index)
                            direct_edges.append(dict(site=pc, target=dest, kind="switch", table=table, index=index))
                            todo.append(dest)
                    else:
                        indirect.append(dict(site=pc, register=rs, kind="indirect_jump"))
                continue
            if op in (4, 5, 6, 7, 20, 21, 22, 23) or (op == 1 and rt in (0, 1, 2, 3)) or (op == 17 and ((word >> 21) & 31) == 8):
                imm = word & 0xFFFF
                if imm & 0x8000:
                    imm -= 0x10000
                code.add(pc+4)
                todo.extend((pc+8, pc+4+imm*4))
                continue
            # syscalls terminate the local path unless this is an import stub.
            if op == 0 and fn in (12, 13):
                indirect.append(dict(site=pc, kind="syscall" if fn == 12 else "break", number=(word >> 6) & 0xFFFFF))
                continue
            todo.append(pc+4)
        return dict(code=code, processed=processed, external=external,
                    direct_edges=direct_edges, indirect=indirect)

    def main(self):
        unknown = []
        for addr in self.dep["unknown"]:
            m, f = self.owner(addr)
            owners = []
            for module in self.modules:
                for caller in module["functions"]:
                    for edge in caller.get("calls", []):
                        if edge["target"] == addr:
                            owners.append(dict(module=module["name"], function=hx(caller["address"]), site=hx(edge["site"])))
            unknown.append(dict(address=hx(addr), import_record=self.imports.get(addr),
                                heuristic_owner=None if f is None else dict(module=m["name"], start=hx(f["address"]), size=f["size"], offset=addr-f["address"]),
                                callsites=owners, disassembly=self.span(addr, 24)))
        seed_targets = [x["runtime_target"] for x in self.dep["used_engine_imports"]]
        # Proven callbacks held in both captured newlib FILE contexts. The
        # cleanup callback is stored at _reent+0x3C in both contexts.
        callback_roots = [0x08810E24,0x08810E9C,0x08810F28,0x08810FA0,0x0880C2EC]
        seed_targets.extend(callback_roots)
        h = self.instruction_closure(seed_targets, [self.hsys], HSYS_SWITCH_TABLES)
        known = set(self.dep["closure"])
        old_code = set()
        for addr in known:
            item = self.functions.get(addr)
            if item:
                f = item[1]
                old_code.update(range(addr, addr+f["size"], 4))
        added_code = sorted(h["code"] - old_code)
        removed_code = sorted(old_code - h["code"])
        nonstart = []
        for edge in h["direct_edges"]:
            if self.text_module(edge["target"]) is self.hsys and edge["target"] not in self.functions:
                m, f = self.owner(edge["target"])
                nonstart.append(dict(site=hx(edge["site"]), target=hx(edge["target"]), kind=edge["kind"],
                                     heuristic_owner=None if f is None else hx(f["address"])))
        externals = [dict(address=hx(a), callsites=[hx(x) for x in sites], import_record=self.imports.get(a),
                          disassembly=self.span(a, 2) if RAM_BASE <= a < RAM_BASE+len(self.ram)-8 else [])
                     for a, sites in sorted(h["external"].items())]
        opcounts = collections.Counter(self.word(a) >> 26 for a in h["code"])
        cop1 = [dict(address=hx(a), disassembly=self.span(a, 1)) for a in sorted(h["code"]) if self.word(a) >> 26 == 17]
        closure = dict(seed_count=len(seed_targets), captured_newlib_callback_roots=[hx(x) for x in callback_roots], reachable_instruction_count=len(h["code"]),
                       earlier_instruction_count=len(old_code), instruction_addresses=[hx(x) for x in sorted(h["code"])],
                       added_instructions=[hx(x) for x in added_code], discarded_unreachable_instructions_count=len(removed_code),
                       nonstart_direct_targets=nonstart, external_targets=externals,
                       indirect_transfers=[dict(x, site=hx(x["site"]), **({"known_targets":[hx(t) for t in x["known_targets"]]} if "known_targets" in x else {})) for x in h["indirect"]],
                       switch_tables=[dict(site=hx(s), table=hx(t), count=n, targets=[hx(self.word(t+4*i)) for i in range(n)]) for s, (t,n) in HSYS_SWITCH_TABLES.items()],
                       opcode_counts=dict(sorted(opcounts.items())), cop1=cop1,
                       caveat="Static direct control-flow closure excludes unidentified indirect targets and PSP service behavior. Code presence is not behavioral equivalence.")
        (ROOT / "reports/coverage_unknown_targets.json").write_text(json.dumps(unknown, indent=2), encoding="utf-8")
        (ROOT / "reports/coverage_hsys_instruction_closure.json").write_text(json.dumps(closure, indent=2), encoding="utf-8")
        self.module_pointer_audit()
        self.service_audit(closure)
        print(json.dumps({k:v for k,v in closure.items() if k not in ("instruction_addresses", "added_instructions", "nonstart_direct_targets", "indirect_transfers", "switch_tables")}, indent=2))
        print("Added instructions", len(added_code), "nonstart transfers", len(nonstart), "indirect transfers", len(h["indirect"]))

    def module_pointer_audit(self):
        from analyze_modules import Elf
        snapshot = readjson(ROOT / "reports/runtime_snapshot.json")
        loaded = {m["name"]: m for m in snapshot["hle.module.list"]["modules"]}
        eng = next(m for m in self.modules if m["name"] == "modduel_eng")
        elf = Elf(ROOT / "inputs/extracted/PSP_GAME/USRDIR/gmodule/libduel_eng.prx")
        refs = []
        for section in elf.sections:
            if not section["flags"] & 2 or section["flags"] & 4:
                continue
            lo = eng["base_address"] + section["address"]
            hi = lo + section["size"]
            for addr in range((lo+3)&~3, hi-3, 4):
                value = self.word(addr)
                m = self.text_module(value)
                if m and m["name"] != "modduel_eng" and value % 4 == 0:
                    _, f = self.owner(value)
                    refs.append(dict(storage=hx(addr), section=section["name"], target=hx(value), module=m["name"],
                                     heuristic_function_start=value in self.functions,
                                     containing_heuristic_function=None if f is None else hx(f["address"])))
        direct = []
        for pc in range(eng["base_address"], eng["base_address"]+eng["text_size"], 4):
            edge = self.direct(pc, self.word(pc))
            if not edge:
                continue
            target, kind = edge
            if eng["base_address"] <= target < eng["base_address"]+eng["text_size"]:
                continue
            imp = self.imports.get(target)
            direct.append(dict(site=hx(pc), target=hx(target), kind=kind, import_record=imp,
                               module=None if self.text_module(target) is None else self.text_module(target)["name"]))
        outside = [r for r in direct if r["import_record"] is None]
        ctx_fallback = self.word(0x088EB598)
        trial = readjson(ROOT / "traces/trial4_selector.json")
        k0 = trial["entry"]["registers"]["k0"]
        ctx_thread = self.word(k0+4)
        contexts = []
        for name, addr in (("global_fallback", ctx_fallback), ("captured_thread",ctx_thread)):
            record = dict(name=name, pointer=hx(addr))
            if RAM_BASE <= addr < RAM_BASE+len(self.ram)-0x200:
                record["initial_words"] = [hx(self.word(addr+i*4)) for i in range(24)]
                # Common newlib _reent initial slots are pointers to FILEs.
                record["file_candidates"] = []
                for index in (1,2,3):
                    stream = self.word(addr+4*index)
                    if RAM_BASE <= stream < RAM_BASE+len(self.ram)-0x100:
                        record["file_candidates"].append(dict(reent_word=index, pointer=hx(stream),
                            words=[hx(self.word(stream+i*4)) for i in range(16)],
                            callbacks=[dict(offset=hx(off),target=hx(self.word(stream+off)),
                                module=None if self.text_module(self.word(stream+off)) is None else self.text_module(self.word(stream+off))["name"])
                                for off in (0x20,0x24,0x28,0x2C)]))
            contexts.append(record)
        result = dict(engine_nontext_cross_module_code_pointer_candidates=refs,
                      engine_direct_transfer_count=len(direct), engine_direct_unique_targets=len(set(x["target"] for x in direct)),
                      engine_direct_non_import_transfers=outside,
                      engine_direct_import_libraries=dict(collections.Counter(x["import_record"]["library"] for x in direct if x["import_record"])),
                      engine_heap_gate=dict(address="0x088F0CC4",captured_value=hx(self.word(0x088F0CC4)),
                          interpretation="The alloc/get-block PSP calls at 0x08804684/0x0880469C are gated by this pointer being zero."),
                      network_close_gates=[dict(address=hx(a),value=hx(self.word(a))) for a in (0x088F0B7C,0x088F0B78)],
                      captured_k0=hx(k0), newlib_contexts=contexts,
                      caveat="Runtime pointers cover this captured duel. Other registrations or configurations require separate validation; candidates are not proven AI callbacks.")
        (ROOT / "reports/coverage_module_dependencies.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    def service_audit(self, closure):
        mapping = {
            "0x092968F4": ("sceKernelCpuSuspendIntr", "sceKernelInterrupt.cpp", "int(void)", "Return previous interrupt-enable state and disable interrupts. Preserve this state in the host bridge."),
            "0x5F10D406": ("sceKernelCpuResumeIntr", "sceKernelInterrupt.cpp", "void(u32 enable)", "Restore enable state according to the saved flag. The local newlib lock counter controls nested calls."),
            "0x237DBD4F": ("sceKernelAllocPartitionMemory", "sceKernelMemory.cpp", "int(int partition,const char *name,int type,u32 size,u32 addr)", "Allocate guest partition memory and return a UID. Captured initialized heap skips this startup call."),
            "0x9D9A5BA1": ("sceKernelGetBlockHeadAddr", "sceKernelMemory.cpp", "u32(int uid)", "Resolve the base address of the UID's allocated guest block. Captured initialized heap skips this startup call."),
            "0x157E6225": ("sceNetAdhocPtpClose", "sceNetAdhoc.cpp", "int(int id,int unknown)", "Close an ad-hoc PTP connection. Captured handle globals are -1, so both calls skip in this local duel state."),
            "0x2AD8E239": ("sceUtilityMsgDialogInitStart", "sceUtility.cpp", "int(u32 paramAddr)", "Begin a PSP utility message dialog. Not an AI decision primitive; host should report unsupported interaction explicitly if reached."),
            "0x42EC03AC": ("sceIoWrite", "sceIo.cpp", "int(int fd,u32 data,int size)", "Write the guest buffer. For stdout/stderr, retain bytes in a host log and return the number written; general files need an explicit file bridge."),
            "0x6A638D83": ("sceIoRead", "sceIo.cpp", "int(int fd,u32 data,int size)", "Read into guest memory through the configured input/file bridge. Do not silently provide invented data."),
            "0x810C4BC3": ("sceIoClose", "sceIo.cpp", "int(int fd)", "Close a mapped file descriptor; propagate the result. Standard descriptors have permission restrictions in PPSSPP."),
            "0x27EB27B8": ("sceIoLseek", "sceIo.cpp", "s64(int fd,s64 offset,int whence)", "Guest arguments are a0=fd,a2/a3=offset,t0=whence; return v0/v1. Evidence: 0x088044B0 through 0x088044C0."),
            "0x172D316E": ("sceKernelStdin", "sceIo.cpp", "u32(void)", "Return standard-input descriptor 0."),
            "0xA6BAB2E9": ("sceKernelStdout", "sceIo.cpp", "u32(void)", "Return standard-output descriptor 1."),
            "0xF78BA90A": ("sceKernelStderr", "sceIo.cpp", "u32(void)", "Return standard-error descriptor 2."),
        }
        services = []
        for target in closure["external_targets"]:
            imp = target["import_record"]
            if not imp or imp["nid"] not in mapping:
                services.append(dict(target, identification="unresolved"))
                continue
            name, filename, signature, bridge = mapping[imp["nid"]]
            source = ROOT / "work" / ("audit_"+filename)
            data = source.read_bytes()
            source_lines = data.decode("utf-8").splitlines()
            matches = [dict(line=i+1,text=line.strip()) for i,line in enumerate(source_lines) if imp["nid"][2:].lower() in line.lower()]
            if not any('"'+name+'"' in x["text"] for x in matches):
                raise AssertionError(f"NID/name table did not validate {imp['nid']} {name}")
            services.append(dict(address=target["address"], library=imp["library"], nid=imp["nid"], name=name,
                                 signature=signature, callsites=target["callsites"], host_bridge_guidance=bridge,
                                 source_url=f"https://github.com/hrydgard/ppsspp/blob/v1.20.4/Core/HLE/{filename}",
                                 source_sha256=hashlib.sha256(data).hexdigest(), source_table_lines=[x["line"] for x in matches]))
        gen_path = ROOT / "tools/native_port/generated/generation_manifest.json"
        unsupported_check = None
        if gen_path.exists():
            gen = readjson(gen_path)
            pcset = {int(x,16) for x in closure["instruction_addresses"]}
            hits = [x for x in gen["unsupported_instructions"] if x["address"] in pcset]
            unsupported_check = dict(total_generated_unsupported_locations=len(gen["unsupported_instructions"]),
                                     reachable_unsupported_locations=hits,
                                     limitation="Comparison applies to the audited static roots, switch tables, and captured callbacks only. It excludes unidentified indirect targets and other configurations.")
        result = dict(service_count=len(services), services=services,
                      native_instruction_coverage_check=unsupported_check,
                      remaining_indirect_calls=closure["indirect_transfers"],
                      warnings=["A headless source port may map console IO and single-thread interrupt state, but this is a host boundary with explicit semantics, not an empty stub.",
                                "Network/dialog behavior must either be implemented or stop explicitly. One captured guard value does not prove all later states skip the service.",
                                "No additional game PRX dependency was found in direct engine transfers or captured allocated engine data pointers. Future callback registrations remain a validation obligation."])
        (ROOT / "reports/coverage_services.json").write_text(json.dumps(result,indent=2),encoding="utf-8")


if __name__ == "__main__":
    Audit().main()
