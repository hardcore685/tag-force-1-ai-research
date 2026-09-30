"""Decode this local TF1 ISO's complete card database without modifying it.

EHP layout checked against https://github.com/xan1242/ehppack.
Card fields and strings validated against the original getters for all records.
"""
import argparse
import csv
import json
import hashlib
import sqlite3
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

KINDS={0:'Normal Monster',1:'Effect Monster',2:'Fusion Monster',3:'Fusion Effect Monster',4:'Ritual Monster',5:'Ritual Effect Monster',6:'Toon Monster',7:'Spirit Monster',8:'Union Monster',9:'Token',13:'Spell',14:'Trap'}
ATTRIBUTES={0:'Unspecified',1:'LIGHT',2:'DARK',3:'WATER',4:'FIRE',5:'EARTH',6:'WIND',7:'DIVINE',8:'SPELL',9:'TRAP'}
RACES={0:'Unspecified',1:'Dragon',2:'Zombie',3:'Fiend',4:'Pyro',5:'Sea Serpent',6:'Rock',7:'Machine',8:'Fish',9:'Dinosaur',10:'Insect',11:'Beast',12:'Beast-Warrior',13:'Plant',14:'Aqua',15:'Warrior',16:'Winged Beast',17:'Fairy',18:'Spellcaster',19:'Thunder',20:'Reptile',21:'Divine-Beast',22:'Spell',23:'Trap'}
ICONS={0:'Normal',1:'Counter',2:'Field',3:'Equip',4:'Continuous',5:'Quick-Play',6:'Ritual'}

def decode_properties(raw):
    """Bit layout from modehsys property getters at 0884DA84..0884DC50.

    Rarity/genre codes are retained numerically; their labels are not assumed.
    Unknown stat 0x1FF returns 0 in battle getters, 0xFFFF in display getters.
    """
    first,second=struct.unpack('<II',raw)
    atk=(first>>13)&0x1FF; deff=(first>>22)&0x1FF
    return dict(embedded_card_id=first&0x1FFF,attack_raw=atk,defense_raw=deff,
        attack=None if atk==0x1FF else atk*10,defense=None if deff==0x1FF else deff*10,
        record_flag31=first>>31,kind_code=second&15,attribute_code=(second>>4)&15,
        level=(second>>8)&15,icon_code=(second>>12)&7,race_code=(second>>15)&31,
        rarity_code=(second>>20)&7,reserved_bits23_31=second>>23,property_hex=raw.hex())

def encode_properties(fields):
    """Round-trip every decoded bit, including reserved fields."""
    a=fields['embedded_card_id'] | fields['attack_raw']<<13 | fields['defense_raw']<<22 | fields['record_flag31']<<31
    b=fields['kind_code'] | fields['attribute_code']<<4 | fields['level']<<8 | fields['icon_code']<<12 | fields['race_code']<<15 | fields['rarity_code']<<20 | fields['reserved_bits23_31']<<23
    return struct.pack('<II',a,b)

def decode_cards(files):
    ids,indices=(files[n] for n in ('CARD_IntID.bin','CARD_Indx_E.bin'))
    names=files['CARD_Name_E.bin'].decode('utf-16-le')
    descriptions=files['CARD_Desc_E.bin'].decode('utf-16-le')
    props=files['CARD_Prop.bin']; count=len(props)//8
    assert len(props)%8==0 and len(files['CARD_Pass.bin'])==count*4
    assert len(files['CARD_Genre.bin'])==count*6 and len(files['CARD_Sort_E.bin'])==count*2
    def string(blob,offset):
        if offset%2 or not 0<=offset//2<len(blob):raise ValueError('Bad UTF-16 offset')
        end=blob.find('\0',offset//2)
        if end<0:raise ValueError('Unterminated UTF-16 string')
        return blob[offset//2:end]
    rows=[]
    for relative_id in range(len(ids)//2):
        ix=struct.unpack_from('<H',ids,relative_id*2)[0]
        if not ix:continue
        if not 0<ix<count:raise ValueError('Bad resource index')
        noff,doff=struct.unpack_from('<II',indices,ix*8)
        raw=props[ix*8:ix*8+8]; fields=decode_properties(raw)
        cid=relative_id+4000
        if cid!=fields['embedded_card_id']:raise ValueError('ID mapping disagrees with property record')
        if encode_properties(fields)!=raw:raise ValueError('Property round trip failed')
        password=struct.unpack_from('<I',files['CARD_Pass.bin'],ix*4)[0]
        genre=files['CARD_Genre.bin'][ix*6:ix*6+6]
        genre_bits=int.from_bytes(genre,'little')
        row=dict(card_id=cid,card_id_hex=f'0x{cid:04X}',resource_index=ix,
            name=string(names,noff),description=string(descriptions,doff),
            **fields,card_type=KINDS.get(fields['kind_code'],f"Unknown {fields['kind_code']}"),
            attribute=ATTRIBUTES.get(fields['attribute_code'],'Unknown'),
            monster_type=RACES.get(fields['race_code'],'Unknown'),
            spell_trap_icon=ICONS.get(fields['icon_code'],'Unknown') if fields['kind_code'] in (13,14) else None,
            password=password,password_text=f'{password:08d}' if password else None,
            genre_hex=genre.hex(),genre_flags=[bit for bit in range(48) if genre_bits&(1<<bit)],
            sort_code=struct.unpack_from('<H',files['CARD_Sort_E.bin'],ix*2)[0],
            name_offset=noff,description_offset=doff)
        rows.append(row)
    if len(rows)!=count-1 or len({r['resource_index'] for r in rows})!=count-1:
        raise ValueError('Resource mapping is not complete and one-to-one')
    return rows

def write_database(rows,folder,manifest):
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'cards.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    with (folder/'cards.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=rows[0]);writer.writeheader()
        for row in rows:writer.writerow({k:json.dumps(v) if isinstance(v,list) else v for k,v in row.items()})
    database=folder/'cards.sqlite'
    # Rebuild only this generated database, in a transaction; preserve originals.
    with sqlite3.connect(database) as con:
        con.execute('DROP TABLE IF EXISTS cards')
        con.execute('DROP TABLE IF EXISTS metadata')
        types={k:('INTEGER' if isinstance(v,int) or k in ('attack','defense') else 'TEXT') for k,v in rows[0].items()}
        con.execute('CREATE TABLE cards ('+','.join(f'"{k}" {t}'+(' PRIMARY KEY' if k=='card_id' else '') for k,t in types.items())+')')
        sql='INSERT INTO cards VALUES ('+','.join('?' for _ in types)+')'
        con.executemany(sql,[[json.dumps(v) if isinstance(v,list) else v for v in r.values()] for r in rows])
        con.execute('CREATE INDEX cards_name ON cards(name COLLATE NOCASE)')
        con.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        con.executemany('INSERT INTO metadata VALUES (?,?)',[(k,json.dumps(v)) for k,v in manifest.items()])
        assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert con.execute('SELECT COUNT(*) FROM cards').fetchone()[0]==len(rows)
    (folder/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')


def unpack_ehp(raw):
    if raw[:4] != b"EHP\x03":
        raise ValueError("Not a version-3 EHP archive")
    total, marker, count = struct.unpack_from("<3I", raw, 4)
    if total != len(raw) or count > (len(raw) - 16) // 8:
        raise ValueError("Invalid EHP size or entry count")
    files = {}
    for index in range(count):
        info, offset = struct.unpack_from("<2I", raw, 16 + index * 8)
        end = raw.index(0, info)
        name = raw[info:end].decode("ascii")
        size = struct.unpack_from("<I", raw, end + 1)[0]
        if Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("Unsafe EHP filename")
        if offset + size > len(raw):
            raise ValueError("EHP entry exceeds archive")
        files[name] = raw[offset:offset + size]
    return files


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'reports/card_database')
    args=parser.parse_args()
    inventory = json.loads((ROOT / "reports/iso_inventory.json").read_text())
    entry = next(item for item in inventory["files"] if item["path"] == "PSP_GAME/USRDIR/duelsys/cardinfo_eng.ehp")
    with open(inventory["source_iso"], "rb") as source:
        source.seek(entry["sector"] * 2048)
        raw = source.read(entry["size"])
    files = unpack_ehp(raw)
    directory = ROOT / "inputs/card_database"
    directory.mkdir(exist_ok=True)
    for name, content in files.items():
        (directory / name).write_bytes(content)
    rows=decode_cards(files)
    mapping = {row["card_id"]: row["name"] for row in rows}
    assert mapping[0x134A] == "Messenger of Peace"
    assert mapping[0x11B2] == "Star Boy"
    assert mapping[0x16AB] == "Nightmare Penguin"
    with (ROOT / "reports/card_id_names.csv").open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=['card_id','card_id_hex','resource_index','name'],extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    manifest=dict(game='Yu-Gi-Oh! GX Tag Force (USA)',disc_id='ULUS10136',disc_version='1.03',
        iso_sha256=inventory.get('sha256',inventory.get('source_sha256')),
        archive_sha256=hashlib.sha256(raw).hexdigest(),archive_path=entry['path'],
        record_count=len(rows),token_count=sum(r['kind_code']==9 for r in rows),
        resource_count=len(files['CARD_Prop.bin'])//8,
        files={n:dict(size=len(v),sha256=hashlib.sha256(v).hexdigest()) for n,v in files.items()},
        unresolved=['Rarity code labels','Genre bit labels','Sort code semantics','Record flag31 meaning'],
        evidence='Modehsys getters: 0884D794 ID map; 0884D850 property pointer; 0884DA84 race; 0884DAAC attribute; 0884DAD4 level; 0884DAF8 icon; 0884DB20 kind; 0884DB44/DB88 stats; 0884D8E4 rarity')
    write_database(rows,args.output,manifest)
    print(f"Decoded {len(rows)} complete records to {args.output}; property bits round-trip exactly")


if __name__ == "__main__":
    main()
