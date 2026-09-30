"""Decode PSP import/export tables and disassemble captured MIPS module code.

Function boundaries are PPSSPP's heuristic scan, not original source symbols.
Requires capstone (installed in the local Python environment on this computer).
"""
from __future__ import annotations
import bisect
import csv
import json
import struct
from pathlib import Path

from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN
from inspect_iso import elf_sections

ROOT = Path(__file__).resolve().parent.parent


class Elf:
    def __init__(self, path):
        self.data = path.read_bytes()
        self.sections = elf_sections(self.data)

    def section(self, name):
        return next(x for x in self.sections if x["name"] == name)

    def offset(self, address):
        for section in self.sections:
            if section["flags"] & 2 and section["type"] != 8 and section["address"] <= address < section["address"] + section["size"]:
                return address - section["address"] + section["offset"]
        raise ValueError(f"No backed section for {address:x}")

    def u32(self, address):
        return struct.unpack_from("<I", self.data, self.offset(address))[0]

    def string(self, address):
        if address == 0:
            return "module_system"
        offset = self.offset(address)
        return self.data[offset:self.data.index(b"\0", offset)].decode("ascii", "replace")

    def exports(self):
        section = self.section(".lib.ent")
        offset, end = section["offset"], section["offset"] + section["size"]
        result = []
        while offset < end:
            name, version, flags, words, variables, functions, table = struct.unpack_from("<IHHBBHI", self.data, offset)
            if words < 4:
                raise ValueError("Invalid export entry")
            library = self.string(name)
            count = functions + variables
            for index in range(count):
                result.append(dict(library=library, nid=f"0x{self.u32(table+4*index):08X}", address=self.u32(table+4*(count+index)), kind="function" if index < functions else "variable"))
            offset += words * 4
        return result

    def imports(self):
        section = self.section(".lib.stub")
        offset, end = section["offset"], section["offset"] + section["size"]
        result = []
        while offset < end:
            name, version, flags, words, variables, functions, nids, stubs = struct.unpack_from("<IHHBBHII", self.data, offset)
            if words < 5:
                raise ValueError("Invalid import entry")
            for index in range(functions):
                result.append(dict(library=self.string(name), nid=f"0x{self.u32(nids+4*index):08X}", stub_address=stubs + index * 8))
            offset += words * 4
        return result


def main():
    snapshot = json.loads((ROOT / "reports" / "runtime_snapshot.json").read_text())
    all_functions = json.loads((ROOT / "reports" / "runtime_functions.json").read_text())
    modules = []
    for name, file in [("modduel_eng", "libduel_eng.prx"), ("modduel_mgr", "libduel_mgr.prx"), ("modduel_draw", "libduel_draw.prx")]:
        elf = Elf(ROOT / "inputs" / "extracted" / "PSP_GAME" / "USRDIR" / "gmodule" / file)
        module = next(x for x in snapshot["hle.module.list"]["modules"] if x["name"] == name)
        base = module["address"]
        exports = elf.exports()
        imports = elf.imports()
        text = elf.section(".text")
        functions = sorted([dict(x, relative_address=x["address"]-base) for x in all_functions if base <= x["address"] < base + text["size"]], key=lambda x:x["address"])
        modules.append(dict(name=name, file=file, base_address=base, text_size=text["size"], exports=exports, imports=imports, functions=functions))
    exported = {(e["library"],e["nid"]): (m["name"],m["base_address"]+e["address"]) for m in modules for e in m["exports"] if e["kind"] == "function"}
    disassembler = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
    disassembler.skipdata = True
    for module in modules:
        name, base, size = module["name"],module["base_address"],module["text_size"]
        runtime = ROOT / "inputs" / f"{name}_runtime.bin"
        if not runtime.exists():
            continue
        data = runtime.read_bytes()[:size]
        functions = module["functions"]
        starts = [f["address"] for f in functions]
        for f in functions:
            f["calls"] = []
            f["callers"] = []
        by_start = {f["address"]:f for f in functions}
        for offset in range(0,len(data)-3,4):
            word = struct.unpack_from("<I", data, offset)[0]
            if word >> 26 != 3:
                continue
            address = base + offset
            target = ((address+4)&0xF0000000) | ((word & 0x3FFFFFF) << 2)
            index = bisect.bisect_right(starts,address)-1
            if index >= 0:
                functions[index]["calls"].append(dict(site=address,target=target))
                if target in by_start:
                    by_start[target]["callers"].append(functions[index]["address"])
        labels = {f["address"]:f"fn_{f['relative_address']:06X}" for f in functions}
        for e in module["exports"]:
            if e["kind"]=="function":
                labels[base+e["address"]] = f"export_{e['nid'][2:]}_{e['address']:06X}"
        for i in module["imports"]:
            address = base+i["stub_address"]
            labels[address] = f"import_{i['library']}_{i['nid'][2:]}"
            resolution = exported.get((i["library"],i["nid"]))
            if resolution:
                i["resolved_module"],i["resolved_address"] = resolution
        target = ROOT / "reports" / f"{name}_disassembly.asm"
        with target.open("w",encoding="utf-8") as output:
            output.write(f"; {name} runtime MIPS disassembly; base 0x{base:08X}\n; Names and function boundaries are inferred, not original symbols.\n")
            for address, length, mnemonic, operands in disassembler.disasm_lite(data,base):
                if address in labels:
                    output.write(f"\n{labels[address]}:\n")
                output.write(f"{address:08X} (+{address-base:06X})  {mnemonic:9s} {operands}\n")
        print(name,"functions",len(functions),"exports",sum(e["kind"]=="function" for e in module["exports"]),"imports",len(module["imports"]))
    (ROOT / "reports" / "module_analysis.json").write_text(json.dumps(modules,indent=2),encoding="utf-8")
    with (ROOT / "reports" / "engine_exports.csv").open("w",newline="",encoding="utf-8") as output:
        writer=csv.writer(output)
        writer.writerow(["library","nid","relative_address","runtime_address","kind"])
        for e in modules[0]["exports"]:
            writer.writerow([e["library"],e["nid"],f"0x{e['address']:06X}",f"0x{modules[0]['base_address']+e['address']:08X}",e["kind"]])


if __name__=="__main__":
    main()
