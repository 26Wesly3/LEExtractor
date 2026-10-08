"""Raw JSON-RPC check against server.py over stdio, with hard timeouts.

Used to decide whether a hanging MCP handshake is the *server's* fault or the
client environment's: this speaks the protocol by hand and prints exactly how
far it gets.
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    lines: list[str] = []
    errors: list[str] = []

    def pump(stream, sink):
        for line in stream:
            sink.append(line.rstrip())

    threading.Thread(target=pump, args=(proc.stdout, lines), daemon=True).start()
    threading.Thread(target=pump, args=(proc.stderr, errors), daemon=True).start()

    def send(payload: dict) -> None:
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()

    def wait_for_id(request_id: int, timeout: float):
        """Wait for a reply carrying this JSON-RPC id, tolerating spacing.

        Matched on the parsed message rather than on a string literal: servers
        differ on whether they emit ``"id":1`` or ``"id": 1``, and a literal
        match on one form silently waits out the whole timeout — which is how
        this probe used to report a 30 s handshake for a 3.4 s one.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            for line in lines:
                try:
                    if json.loads(line).get("id") == request_id:
                        return line
                except Exception:
                    continue
            time.sleep(0.05)
        return None

    print("server pid:", proc.pid)
    started = time.time()
    send({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "raw-probe", "version": "1"},
        },
    })
    got = wait_for_id(1, 60)
    print(f"initialize answered in {time.time() - started:.2f}s: {bool(got)}")
    if not got:
        print("--- stdout so far ---")
        for line in lines[:15]:
            print("  ", line[:200])
        print("--- stderr so far ---")
        for line in errors[:25]:
            print("  ", line[:200])
        proc.kill()
        return 1
    print("  ", got[:300])

    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    listing = wait_for_id(2, 30)
    if not listing:
        print("tools/list did NOT answer")
        print("--- stderr ---")
        for line in errors[:25]:
            print("  ", line[:200])
        proc.kill()
        return 1
    try:
        payload = json.loads(listing)
        names = sorted(t["name"] for t in payload["result"]["tools"])
    except Exception as exc:
        print("could not parse tools/list:", exc, listing[:300])
        proc.kill()
        return 1
    print(f"tools/list answered: {len(names)} tools")
    for name in names:
        print("  -", name)

    send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
          "params": {"name": "plan_search_strategy",
                     "arguments": {"topic": "plant phenotyping",
                                   "research_direction": "deep learning"}}})
    call = wait_for_id(3, 30)
    print("tools/call answered:", bool(call))
    if call:
        print("  ", call[:300])

    proc.kill()
    ok = bool(listing) and len(names) >= 12
    print("RAW STDIO:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
