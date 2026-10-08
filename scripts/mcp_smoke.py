"""Verify the shipped root MCP entry using a real stdio child process."""

import asyncio
import sys
from pathlib import Path

from fastmcp import Client


async def main():
    root = Path(__file__).resolve().parents[1]
    config = {"mcpServers": {"leextractor": {"command": sys.executable, "args": [str(root / "server.py")]}}}
    async with Client(config) as client:
        tools = await client.list_tools()
        names = sorted(tool.name for tool in tools)
        assert "search_literature" in names and "build_research_landscape" in names
        print(f"MCP stdio: {len(names)} tools registered")
        print(", ".join(names))


if __name__ == "__main__":
    asyncio.run(main())
