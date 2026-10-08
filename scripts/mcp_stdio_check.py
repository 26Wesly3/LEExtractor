"""Verify the LEExtractor MCP server over real stdio using the official MCP SDK.

Run from the repository root:

    python scripts/mcp_stdio_check.py

This is the transport that MCP hosts (Codex, Claude, Cursor, ...) actually use,
so a pass here is evidence that the server is host-compatible; the in-process
check in ``mcp_smoke.py`` only proves the tool surface exists.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {"search_literature", "build_research_landscape", "literature_review_search"}


async def check() -> int:
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError:
        print("SKIP: the 'mcp' package is not installed (pip install mcp)", file=sys.stderr)
        return 2

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "server.py")],
        env=env,
        cwd=str(ROOT),
    )

    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        init = await session.initialize()
        print(f"connected: {init.serverInfo.name} {init.serverInfo.version}")
        tools = await session.list_tools()
        names = sorted(tool.name for tool in tools.tools)
        print(f"tools: {len(names)}")
        for name in names:
            print(f"  - {name}")

        missing = REQUIRED - set(names)
        if missing:
            print(f"FAIL: missing required tools: {sorted(missing)}", file=sys.stderr)
            return 1

        # Exercise one call that must not touch the network.
        result = await session.call_tool(
            "plan_search_strategy",
            {"topic": "plant phenotyping", "research_direction": "deep learning"},
        )
        if result.isError:
            print(f"FAIL: tool call errored: {result.content}", file=sys.stderr)
            return 1
        payload = result.structuredContent
        if payload is None and result.content:
            text = getattr(result.content[0], "text", "")
            payload = json.loads(text) if text.strip().startswith("{") else {"raw": text[:200]}
        keys = sorted(payload) if isinstance(payload, dict) else []
        print(f"plan_search_strategy returned keys: {keys[:8]}")

    print("OK: MCP stdio transport verified")
    return 0


def main() -> int:
    try:
        return asyncio.run(check())
    except Exception as exc:  # noqa: BLE001 - report whatever the transport raised
        print(f"MCP stdio FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "note: a WinError 5 here means the environment refused the subprocess "
            "channel, not that the server is broken. Re-run outside the sandbox.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
