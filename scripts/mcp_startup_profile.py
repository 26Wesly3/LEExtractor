"""Break down what the MCP server's cold start is actually spent on.

An MCP host kills a server that does not answer ``initialize`` inside its
startup timeout, so cold-start cost is a compatibility property, not a nicety.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SNIPPETS = [
    ("fastmcp only", "from fastmcp import FastMCP"),
    ("+ litsearch.version", "from fastmcp import FastMCP\nimport litsearch.version"),
    ("+ litsearch.models", "from fastmcp import FastMCP\nimport litsearch.models"),
    ("+ litsearch.filters", "from fastmcp import FastMCP\nimport litsearch.filters"),
    ("+ litsearch.prisma", "from fastmcp import FastMCP\nimport litsearch.prisma"),
    ("+ litsearch.sources", "from fastmcp import FastMCP\nimport litsearch.sources"),
    ("+ litsearch.search", "from fastmcp import FastMCP\nimport litsearch.search"),
    ("+ litsearch.landscape", "from fastmcp import FastMCP\nimport litsearch.landscape"),
    ("litsearch.server (real)", "import litsearch.server"),
]


def time_snippet(code: str) -> float:
    program = (
        "import time\n"
        "t = time.perf_counter()\n"
        f"{code}\n"
        "print(f'{time.perf_counter() - t:.3f}')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", program],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
        env={"PYTHONPATH": str(ROOT), "PATH": "", "SYSTEMROOT": "C:\\Windows"},
    )
    line = (proc.stdout or "").strip().splitlines()
    return float(line[-1]) if line else float("nan")


def main() -> int:
    print(f"{'phase':28s} {'cold start':>10s}")
    print("-" * 40)
    for name, code in SNIPPETS:
        print(f"{name:28s} {time_snippet(code):9.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
