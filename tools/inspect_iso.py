"""Read-only ISO9660 inventory and selective PSP module extraction.

Uses only the Python standard library. Never writes to the source ISO.
Run: python tools/inspect_iso.py --iso PATH --root EXPERIMENT_FOLDER
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import struct
from pathlib import Path

SECTOR = 2048


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_sfo(data):
    if data[:4] != b"\x00PSF":
        return {}
    _, key_offset, data_offset, count = struct.unpack_from("<4I", data, 4)
    result = {}
    for index in range(count):
        key, kind, length, _, value = struct.unpack_from("<HHIII", data, 20 + index * 16)
        start = key_offset + key
        name = data[start:data.index(b"\0", start)].decode("utf-8", "replace")
        raw = data[data_offset + value:data_offset + value + length]
        result[name] = struct.unpack_from("<I", raw)[0] if kind == 0x404 else raw.rstrip(b"\0").decode("utf-8", "replace")
    return result


def elf_sections(data):
    if data[:4] != b"\x7fELF" or data[4:6] != b"\x01\x01":
        return []
    header = struct.unpack_from("<16sHHIIIIIHHHHHH", data)
    section_offset, entry_size, count, names_index = header[6], header[11], header[12], header[13]
    sections = [struct.unpack_from("<10I", data, section_offset + i * entry_size) for i in range(count)]
    if not sections:
        return []
    names = data[sections[names_index][4]:sections[names_index][4] + sections[names_index][5]]
    result = []
    for index, section in enumerate(sections):
        offset = section[0]
        end = names.find(b"\0", offset)
        name = names[offset:end].decode("ascii", "replace") if offset < len(names) else ""
        result.append(dict(index=index, name=name, type=section[1], flags=section[2], address=section[3], offset=section[4], size=section[5], link=section[6], info=section[7], entry_size=section[9]))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iso", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    reports = root / "reports"
    extracted = root / "inputs" / "extracted"
    reports.mkdir(parents=True, exist_ok=True)
    extracted.mkdir(parents=True, exist_ok=True)
    files = []
    with args.iso.open("rb") as stream:
        stream.seek(16 * SECTOR)
        pvd = stream.read(SECTOR)
        if pvd[:7] != b"\x01CD001\x01":
            raise ValueError("Expected an ISO9660 primary volume descriptor")
        root_record = pvd[156:156 + pvd[156]]
        seen = set()

        def walk(record, prefix):
            sector, size = struct.unpack_from("<I", record, 2)[0], struct.unpack_from("<I", record, 10)[0]
            if sector in seen:
                return
            seen.add(sector)
            stream.seek(sector * SECTOR)
            listing = stream.read(size)
            cursor = 0
            while cursor < len(listing):
                length = listing[cursor]
                if length == 0:
                    cursor = ((cursor // SECTOR) + 1) * SECTOR
                    continue
                item = listing[cursor:cursor + length]
                cursor += length
                if len(item) < 34:
                    raise ValueError("Truncated ISO directory record")
                name = item[33:33 + item[32]]
                if name in (b"\0", b"\1"):
                    continue
                name = name.decode("ascii", "replace").split(";")[0].rstrip(".")
                if name in (".", "..") or "/" in name or "\\" in name:
                    raise ValueError("Unsafe ISO entry name")
                path = f"{prefix}/{name}".lstrip("/")
                if item[25] & 2:
                    walk(item, path)
                else:
                    files.append(dict(path=path, sector=struct.unpack_from("<I", item, 2)[0], size=struct.unpack_from("<I", item, 10)[0]))

        walk(root_record, "")
        extracted_files = []
        interesting_strings = []
        elf_inventory = []
        sfo = {}
        for record in files:
            stream.seek(record["sector"] * SECTOR)
            prefix = stream.read(min(16, record["size"]))
            record["magic_hex"] = prefix[:8].hex()
            path = Path(record["path"])
            if path.suffix.lower() not in (".prx", ".sfo") and path.name.upper() not in ("EBOOT.BIN", "BOOT.BIN", "UMD_DATA.BIN"):
                continue
            stream.seek(record["sector"] * SECTOR)
            data = stream.read(record["size"])
            if len(data) != record["size"]:
                raise ValueError("Truncated ISO file")
            target = (extracted / path).resolve()
            if not target.is_relative_to(extracted.resolve()):
                raise ValueError("Extraction path escapes output directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            extracted_files.append(dict(**record, sha256=hashlib.sha256(data).hexdigest(), local_path=str(target)))
            if record["path"].upper() == "PSP_GAME/PARAM.SFO":
                sfo = parse_sfo(data)
            sections = elf_sections(data)
            if sections:
                elf_inventory.append(dict(path=record["path"], sections=sections))
            for match in re.finditer(rb"[\x20-\x7e]{5,}", data):
                value = match.group().decode("ascii")
                if re.search(r"duel|\bai\b|think|cpu|com_|comthink|card|attack|defen|summon|select|rand|\.c$", value, re.I):
                    interesting_strings.append(dict(path=record["path"], offset=f"0x{match.start():X}", string=value[:1000]))

    manifest = dict(source_iso=str(args.iso.resolve()), source_size=args.iso.stat().st_size, source_sha256=sha256_file(args.iso), volume_id=pvd[40:72].decode("ascii", "replace").strip(), game=sfo, files=files, extracted_files=extracted_files)
    (reports / "iso_inventory.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (reports / "elf_sections.json").write_text(json.dumps(elf_inventory, indent=2), encoding="utf-8")
    (reports / "interesting_strings.json").write_text(json.dumps(interesting_strings, indent=2), encoding="utf-8")
    with (reports / "iso_files.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=["path", "sector", "size", "magic_hex"])
        writer.writeheader()
        writer.writerows(files)
    print(json.dumps(dict(game=sfo, volume_id=manifest["volume_id"], source_sha256=manifest["source_sha256"], file_count=len(files), modules=[dict(path=x["path"], size=x["size"], magic=x["magic_hex"]) for x in extracted_files if x["path"].lower().endswith(".prx")], elf_modules=len(elf_inventory), interesting_string_count=len(interesting_strings)), indent=2))


if __name__ == "__main__":
    main()
