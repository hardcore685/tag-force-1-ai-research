"""Small local-only PPSSPP JSON debugger client for reproducible experiments."""
from __future__ import annotations
import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "vendor"))
import websocket

ROOT = Path(__file__).resolve().parent.parent


class Debugger:
    def __init__(self, port=65319, timeout=10):
        self.ws = websocket.create_connection(f"ws://127.0.0.1:{port}/debugger", subprotocols=["debugger.ppsspp.org"], timeout=timeout, http_proxy_host=None)
        self.ticket = 0
        self.events = []
        self.request("version", name="Tag Force AI experiments", version="0.1")

    def close(self):
        self.ws.close()

    def send(self, event, **params):
        self.ticket += 1
        self.ws.send(json.dumps(dict(event=event, ticket=self.ticket, **params)))
        return self.ticket

    def request(self, event, **params):
        ticket = self.send(event, **params)
        while True:
            result = json.loads(self.ws.recv())
            if result.get("ticket") == ticket:
                if result.get("event") == "error":
                    raise RuntimeError(result)
                return result
            self.events.append(result)

    def pause(self):
        self.send("cpu.stepping")
        for _ in range(50):
            status = self.request("cpu.status")
            if status.get("stepping"):
                return status
            time.sleep(0.02)
        raise RuntimeError("CPU did not enter stepping mode")

    def resume(self):
        self.send("cpu.resume")

    def read(self, address, size):
        return base64.b64decode(self.request("memory.read", address=address, size=size, replacements=False).get("base64", ""))

    def screenshot(self, target):
        result = self.request("gpu.buffer.screenshot", type="uri")
        uri = result["uri"]
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(uri.split(",", 1)[1]))
        return {k: v for k, v in result.items() if k != "uri"}


def main():
    client = Debugger()
    try:
        snapshot = {"version": client.request("version"), "cpu": client.pause()}
        for event in ("game.status", "hle.module.list", "hle.thread.list", "hle.func.list"):
            try:
                snapshot[event] = client.request(event)
            except Exception as error:
                snapshot[event] = {"error": str(error)}
        try:
            snapshot["screenshot"] = client.screenshot(ROOT / "reports" / "initial_state.png")
        except Exception as error:
            snapshot["screenshot"] = {"error": str(error)}
        (ROOT / "reports" / "runtime_snapshot.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        summary = {k: v for k, v in snapshot.items() if k != "hle.func.list"}
        summary["function_count"] = len(snapshot.get("hle.func.list", {}).get("functions", []))
        print(json.dumps(summary, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
