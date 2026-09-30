"""Capture the PSP framebuffer through the emulator's documented memory API.

Works with the hidden experiment window. Stops at sceDisplaySetFrameBuf,
reads its documented ABI arguments, and saves a 480x272 image. Leaves paused.
"""
import json
import struct
import time
from pathlib import Path

from PIL import Image
from debugger import Debugger, ROOT


def capture(client, target):
    functions = json.loads((ROOT / "reports" / "runtime_functions.json").read_text())
    address = next(f["address"] for f in functions if f["name"] == "zz_sceDisplaySetFrameBuf")
    client.pause()
    client.request("cpu.breakpoint.add", address=address)
    try:
        client.resume()
        for _ in range(200):
            status = client.request("cpu.status")
            if status.get("stepping") and status.get("pc") == address:
                break
            if status.get("stepping"):
                raise RuntimeError("A different breakpoint stopped framebuffer capture")
            time.sleep(0.01)
        else:
            raise RuntimeError("No display framebuffer call within the capture limit")
        registers = client.request("cpu.getAllRegs")["categories"][0]
        regs = dict(zip(registers["registerNames"], registers["uintValues"]))
        width, pixel_format = regs["a1"], regs["a2"]
        if not 480 <= width <= 2048:
            raise ValueError(f"Unexpected framebuffer stride {width}")
        if pixel_format == 3:
            raw = client.read(regs["a0"], width * 272 * 4)
            image = Image.frombytes("RGBA", (width, 272), raw).convert("RGB")
        elif pixel_format in (0, 1, 2):
            raw = client.read(regs["a0"], width * 272 * 2)
            rgb = bytearray()
            for (word,) in struct.iter_unpack("<H", raw):
                if pixel_format == 0:
                    rgb.extend(((word & 31) * 255 // 31, ((word >> 5) & 63) * 255 // 63, ((word >> 11) & 31) * 255 // 31))
                elif pixel_format == 1:
                    rgb.extend(((word & 31) * 255 // 31, ((word >> 5) & 31) * 255 // 31, ((word >> 10) & 31) * 255 // 31))
                else:
                    rgb.extend(((word & 15) * 17, ((word >> 4) & 15) * 17, ((word >> 8) & 15) * 17))
            image = Image.frombytes("RGB", (width, 272), bytes(rgb))
        else:
            raise ValueError(f"Unsupported PSP pixel format {pixel_format}")
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.crop((0, 0, 480, 272)).save(target)
        return dict(path=str(target), framebuffer_address=regs["a0"], stride=width, pixel_format=pixel_format)
    finally:
        client.request("cpu.breakpoint.remove", address=address)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    client = Debugger()
    try:
        print(json.dumps(capture(client, args.output), indent=2))
    finally:
        client.close()
