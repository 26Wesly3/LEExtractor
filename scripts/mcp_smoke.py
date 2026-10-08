"""Verify the shipped root MCP entry.

Two modes:

``--in-process`` (default)
    Connects to the server object that ``server.py`` actually exports, and
    checks the tool surface. This works anywhere Python runs, including
    environments that forbid the named pipes a stdio child process needs.

``--stdio``
    Spawns ``server.py`` as a real child process and talks JSON-RPC over its
    stdio — the closest thing to how an MCP host launches it. Requires the
    ability to create subprocess pipes; under a restrictive sandbox it fails
    with ``WinError 5`` at ``Client(...)``/``__aenter__``, which is an
    environment limitation and not a defect in the server. Run it outside such
    a sandbox (or in CI) for that coverage; do not report stdio as verified
    when only the in-process check ran.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from fastmcp import Client

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {"search_literature", "build_research_landscape", "literature_review_search"}


def _check(names: list[str], mode: str) -> int:
    missing = REQUIRED - set(names)
    print(f"MCP {mode}: {len(names)} tools registered")
    print(", ".join(sorted(names)))
    if missing:
        print(f"FAIL: missing required tools: {sorted(missing)}", file=sys.stderr)
        return 1
    print("OK")
    return 0


async def run_in_process() -> int:
    """Check the entry point ``server.py`` re-exports, without a child process."""
    sys.path.insert(0, str(ROOT))
    from server import mcp  # the shipped root entry point

    async with Client(mcp) as client:
        names = [tool.name for tool in await client.list_tools()]
    return _check(names, "in-process")


async def run_stdio() -> int:
    config = {"mcpServers": {"leextractor": {
        "command": sys.executable, "args": [str(ROOT / "server.py")],
    }}}
    async with Client(config) as client:
        names = [tool.name for tool in await client.list_tools()]
    return _check(names, "stdio")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stdio", action="store_true",
                        help="use a real child process over stdio (needs subprocess pipes)")
    args = parser.parse_args()
    try:
        return asyncio.run(run_stdio() if args.stdio else run_in_process())
    except Exception as exc:
        mode = "stdio" if args.stdio else "in-process"
        print(f"MCP {mode} FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        if args.stdio:
            print(
                "note: a WinError 5 here is the environment refusing subprocess pipes, "
                "not a server defect. Re-run outside the sandbox and record the result "
                "separately.",
                file=sys.stderr,
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
