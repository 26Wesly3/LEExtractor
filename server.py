"""Backwards-compatible entry point.

The MCP server used to live at the repository root, but ``pyproject.toml``
declared the console script as ``litsearch.server:main`` — a module that did
not exist.  The real implementation now lives in ``litsearch/server.py``;
this shim keeps ``python server.py`` working.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from litsearch.server import main, mcp  # noqa: F401

if __name__ == "__main__":
    main()
