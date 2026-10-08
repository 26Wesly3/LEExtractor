"""Launch the server exactly as the registered MCP host config does.

Reads the ``[mcp_servers.leextractor]`` block from the Codex config and drives a
real ``initialize`` -> ``tools/list`` exchange with that command, so a green
result means the *registered entry* works, not merely that the server does.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import tomllib

CONFIG = Path.home() / ".codex" / "config.toml"


def main() -> int:
    if not CONFIG.exists():
        print(f"SKIP: {CONFIG} not found", file=sys.stderr)
        return 2
    data = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    block = (data.get("mcp_servers") or {}).get("leextractor")
    if not block:
        print("SKIP: no [mcp_servers.leextractor] block registered", file=sys.stderr)
        return 2

    command = block["command"]
    args = list(block.get("args") or [])
    print("registered command:", command)
    print("registered args   :", args)

    start = time.perf_counter()
    proc = subprocess.Popen(
        [command, *args],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    out: list[str] = []
    err: list[str] = []

    def drain(stream, sink):
        for line in stream:
            sink.append(line.rstrip())

    threading.Thread(target=drain, args=(proc.stdout, out), daemon=True).start()
    threading.Thread(target=drain, args=(proc.stderr, err), daemon=True).start()

    def send(payload):
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()

    send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                     "clientInfo": {"name": "config-check", "version": "1"}}})
    deadline = time.time() + 60
    while time.time() < deadline and not any('"id":1' in row or '"id": 1' in row for row in out):
        time.sleep(0.05)
    handshake = time.perf_counter() - start
    answered = any('"id":1' in row or '"id": 1' in row for row in out)
    print(f"initialize answered: {answered} in {handshake:.2f}s")
    if not answered:
        for line in err[-12:]:
            print("  stderr:", line[:180])
        proc.kill()
        return 1

    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    deadline = time.time() + 30
    while time.time() < deadline and not any('"id":2' in row or '"id": 2' in row for row in out):
        time.sleep(0.05)
    listing = [row for row in out if '"id":2' in row or '"id": 2' in row]
    if not listing:
        print("tools/list did NOT answer", file=sys.stderr)
        proc.kill()
        return 1
    tools = sorted(t["name"] for t in json.loads(listing[-1])["result"]["tools"])
    print(f"tools: {len(tools)}")
    proc.kill()

    ok = answered and len(tools) == 12
    print("REGISTERED ENTRY:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
