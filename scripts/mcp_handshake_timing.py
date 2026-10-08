"""Time each phase of the MCP stdio handshake, to find what a host waits on.

A host kills a server that does not answer ``initialize`` inside its startup
timeout, so a slow handshake is indistinguishable from a broken server. This
measures where the time actually goes.
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
    env.pop("LEEXTRACTOR_MAX_PDF_SIZE", None)

    t0 = time.perf_counter()
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(ROOT), env=env, text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    out: list[str] = []
    err: list[str] = []

    def drain(stream, sink):
        for line in stream:
            sink.append(line.rstrip())

    threading.Thread(target=drain, args=(proc.stdout, out), daemon=True).start()
    threading.Thread(target=drain, args=(proc.stderr, err), daemon=True).start()

    proc.stdin.write(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "timing-probe", "version": "1"}},
    }) + "\n")
    proc.stdin.flush()
    t1 = time.perf_counter()

    deadline = t1 + 90
    first_line = None
    while time.perf_counter() < deadline:
        if out:
            first_line = out[0]
            break
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    answered = time.perf_counter()

    print(f"spawn -> request written : {t1 - t0:.2f}s")
    print(f"request -> initialize    : {answered - t1:.2f}s")
    print(f"total to handshake       : {answered - t0:.2f}s")
    print(f"answered                 : {bool(first_line)}")
    if first_line:
        print(f"  {first_line[:160]}")
    if not first_line:
        print("--- stderr tail ---")
        for line in err[-15:]:
            print("  ", line[:180])

    # Now that it is warm, how fast is a second round trip?
    if first_line:
        t2 = time.perf_counter()
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n")
        proc.stdin.flush()
        deadline = time.perf_counter() + 60
        while time.perf_counter() < deadline:
            if any('"id":2' in line or '"id": 2' in line for line in out):
                break
            time.sleep(0.05)
        print(f"warm tools/list          : {time.perf_counter() - t2:.2f}s")

    proc.kill()
    return 0 if first_line else 1


if __name__ == "__main__":
    raise SystemExit(main())
