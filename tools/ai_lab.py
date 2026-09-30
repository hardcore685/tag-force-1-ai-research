"""Read-only observations and opt-in debugger hooks for the isolated TF1 lab.

Run from anywhere: python tools/ai_lab.py status
See README.txt for launching the portable emulator and replaying a trial.
Only the explicit swap-hand command writes game RAM. No command edits the ISO.
"""
import argparse
import json
import struct
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from debugger import Debugger, ROOT

HOOKS = {
    "main_phase_stages": 0x000828,
    "summon_stage": 0x000BAC,
    "choose_hand_monster": 0x00B3B4,
    "battle_position_heuristic": 0x0015A0,
    "effect_target_dispatch": 0x014C34,
    "submit_action": 0x0D0C00,
}
OFFSETS = {"controllers": 0x10F9F8, "turn_state": 0x1155D0, "players": 0x111E5C}
PLAYER_STRIDE = 0xFAC


def portable_owner():
    # Verify the debugger port belongs to this lab's portable executable.
    # This is process inspection, not Windows UI automation.
    command = "$p = (Get-NetTCPConnection -LocalPort 65319 -State Listen -ErrorAction Stop | Select-Object -First 1).OwningProcess; (Get-Process -Id $p -ErrorAction Stop).Path"
    result = subprocess.run(["powershell", "-NoProfile", "-Command", command], check=True, capture_output=True, text=True)
    actual = Path(result.stdout.strip()).resolve()
    expected = (ROOT / "work/ppsspp/PPSSPPWindows64.exe").resolve()
    if str(actual).casefold() != str(expected).casefold():
        raise RuntimeError("Debugger port is not owned by the lab's portable PPSSPP")


def validated_client():
    portable_owner()
    client = Debugger()
    status = client.request("game.status")
    if status.get("game", {}).get("id") != "ULUS10136" or status.get("game", {}).get("version") != "1.03":
        client.close()
        raise RuntimeError("This map is only validated for ULUS10136 version 1.03")
    modules = client.request("hle.module.list")["modules"]
    engine = next(module for module in modules if module["name"] == "modduel_eng")
    base = engine["address"] if "address" in engine else engine["baseAddress"]
    if client.read(base + HOOKS["choose_hand_monster"], 4) != struct.pack("<I", 0x27BDFF70):
        client.close()
        raise RuntimeError("Summon-selector instruction signature does not match the map")
    return client, base


def u32s(client, address, count):
    return list(struct.unpack("<" + "I" * count, client.read(address, count * 4)))


def snapshot(client, base):
    state = u32s(client, base + OFFSETS["turn_state"], 4)
    players = []
    for player in range(2):
        address = base + OFFSETS["players"] + player * PLAYER_STRIDE
        head = u32s(client, address, 8)
        if head[3] > 80:
            raise RuntimeError("Player layout did not match the validated map")
        hand = u32s(client, address + 0x120, head[3])
        players.append(dict(player=player, life_points=head[0], hand_count=head[3], deck_count=head[4], grave_count=head[5], hand_entries=hand, card_ids=[item & 0x1FFF for item in hand]))
    return dict(time_utc=datetime.now(timezone.utc).isoformat(), engine_base=f"0x{base:08X}", actor=state[0], displayed_turn=state[1] + 1, phase_code=state[3], controllers=u32s(client, base + OFFSETS["controllers"], 2), players=players)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("arm")
    sub.add_parser("disarm")
    watch = sub.add_parser("watch")
    watch.add_argument("hook", choices=HOOKS)
    watch.add_argument("--condition", default="")
    press = sub.add_parser("press")
    press.add_argument("button", choices=["cross", "circle", "triangle", "square", "up", "down", "left", "right", "start", "select", "ltrigger", "rtrigger"])
    press.add_argument("--frames", type=int, default=8)
    swap = sub.add_parser("swap-hand")
    swap.add_argument("--player", type=int, choices=[0, 1], default=1)
    swap.add_argument("--slots", type=int, nargs=2, required=True)
    args = parser.parse_args()
    client, base = validated_client()
    try:
        if args.command == "status":
            was_stepping = client.request("cpu.status")["stepping"]
            client.pause()
            result = snapshot(client, base)
            (ROOT / "traces/latest_snapshot.json").write_text(json.dumps(result, indent=2))
            print(json.dumps(result, indent=2))
            if not was_stepping:
                client.resume()
        elif args.command in ("arm", "watch"):
            client.pause()
            selected = HOOKS if args.command == "arm" else {args.hook: HOOKS[args.hook]}
            current = {bp["address"] for bp in client.request("cpu.breakpoint.list")["breakpoints"]}
            if args.command == "watch":
                # Replace stopping hooks while the CPU is paused, avoiding a
                # resume/disarm/re-arm race in the controlled intervention.
                for name, relative in HOOKS.items():
                    if name != args.hook and base + relative in current:
                        client.request("cpu.breakpoint.remove", address=base + relative)
            for name, relative in selected.items():
                params = dict(address=base + relative, enabled=args.command == "watch", log=True, condition=getattr(args, "condition", ""), logFormat=f"TF1_LAB {name} rel={relative:06X} ra={{ra:x}} a0={{a0:x}} a1={{a1:x}} a2={{a2:x}} a3={{a3:x}}")
                client.request("cpu.breakpoint.update" if params["address"] in current else "cpu.breakpoint.add", **params)
            client.resume()
            print("Armed: " + ", ".join(selected))
        elif args.command == "disarm":
            client.pause()
            current = {bp["address"] for bp in client.request("cpu.breakpoint.list")["breakpoints"]}
            for relative in HOOKS.values():
                if base + relative in current:
                    client.request("cpu.breakpoint.remove", address=base + relative)
            client.resume()
            print("Removed lab hooks")
        elif args.command == "press":
            if client.request("cpu.status")["stepping"] or client.request("game.status")["paused"]:
                raise RuntimeError("Resume emulation and close the PPSSPP pause menu before pressing game buttons")
            if not 1 <= args.frames <= 24:
                raise ValueError("Use between 1 and 24 frames to avoid unintended menu repeats")
            client.request("input.buttons.press", button=args.button, duration=args.frames)
            print(f"Pressed {args.button}")
        elif args.command == "swap-hand":
            cpu = client.request("cpu.status")
            if not cpu["stepping"] or cpu["pc"] != base + HOOKS["choose_hand_monster"]:
                raise RuntimeError("Swap only at the choose_hand_monster entry breakpoint")
            before = snapshot(client, base)
            if before["actor"] != args.player or before["phase_code"] not in (2, 4):
                raise RuntimeError("Wrong actor or phase for the controlled intervention")
            hand = before["players"][args.player]["hand_entries"]
            first, second = args.slots
            if first == second or min(first, second) < 0 or max(first, second) >= len(hand):
                raise ValueError("Choose two different occupied hand slots")
            address = base + OFFSETS["players"] + args.player * PLAYER_STRIDE + 0x120
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            record_path = ROOT / "traces" / f"hand_swap_{stamp}.json"
            record = dict(before=before, slots=[first, second], address=f"0x{address:08X}")
            record_path.write_text(json.dumps(record, indent=2))
            client.request("memory.write_u32", address=address + first * 4, value=hand[second])
            client.request("memory.write_u32", address=address + second * 4, value=hand[first])
            record["after"] = snapshot(client, base)
            record_path.write_text(json.dumps(record, indent=2))
            print("Swapped RAM entries; emulator remains paused. Record: " + str(record_path))
    finally:
        client.close()


if __name__ == "__main__":
    main()
