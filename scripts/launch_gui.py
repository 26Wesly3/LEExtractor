"""Start the Streamlit workspace and open it in the default browser.

Why this exists instead of relying on Streamlit's own browser launch: when the
app is started from a batch file, Streamlit's auto-open depends on how the
process was spawned and does nothing often enough to become a support problem.
Here the sequence is explicit — start the server, poll until it answers, then
open the URL exactly once. The URL is printed in every path, so a browser that
fails to open never means the app cannot be reached.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app.py"
DEFAULT_PORT = 8501


def read_version() -> str:
    """The version reported at startup, parsed without importing the package.

    ``litsearch.version`` has no third-party imports, but reading it with
    :mod:`ast` keeps this launcher runnable before the dependencies are
    installed — which is exactly when a user needs the version line.
    """
    import ast
    import re

    try:
        source = (ROOT / "litsearch" / "version.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return "unknown"
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                return "unknown"
            return value if isinstance(value, str) and re.fullmatch(r"\d+\.\d+\.\d+", value) \
                else "unknown"
    return "unknown"


def _alive(url: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def fresh_url(url: str) -> str:
    """Append a one-off marker so the browser cannot serve a cached old page.

    Without this, reopening the app after an update often shows the previous
    version's shell, which looks like the code did not change. Streamlit only
    reads the query parameters it knows about and ignores the rest.
    """
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}_={int(time.time())}"


def _open(url: str) -> None:
    try:
        webbrowser.open(fresh_url(url))
        print("If the page still looks like an older version, press Ctrl+F5 to reload it.")
    except webbrowser.Error as exc:  # pragma: no cover - depends on the desktop
        print(f"Could not open a browser automatically ({exc}). Open {url} manually.")


def _command(host: str, port: int) -> list[str]:
    return [
        sys.executable, "-m", "streamlit", "run", str(APP),
        "--server.address", host,
        "--server.port", str(port),
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch the LEExtractor workspace")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true", help="start the server but do not open a window")
    parser.add_argument("--wait", type=float, default=90.0, help="seconds to wait for the server to answer")
    parser.add_argument("--version", action="version",
                        version=f"LEExtractor {read_version()}",
                        help="print the version (read from litsearch/version.py) and exit")
    args = parser.parse_args(argv)

    if not APP.exists():
        print(f"app.py not found next to this script (expected {APP}).")
        return 1

    url = f"http://{args.host}:{args.port}"
    health = f"{url}/_stcore/health"

    if _alive(health):
        print(f"LEExtractor {read_version()} is already running at {url}.")
        if not args.no_browser:
            _open(url)
        return 0

    print(f"Starting LEExtractor {read_version()} at {url} — press Ctrl+C to stop.", flush=True)
    process = subprocess.Popen(_command(args.host, args.port), cwd=str(ROOT))
    deadline = time.time() + max(1.0, args.wait)
    try:
        while time.time() < deadline:
            if process.poll() is not None:
                print("Streamlit exited before the server answered. See the messages above.")
                return process.returncode or 1
            if _alive(health):
                print(f"Ready: {url}")
                if not args.no_browser:
                    _open(url)
                return process.wait()
            time.sleep(0.5)
        print(f"Timed out after {args.wait:.0f}s waiting for {url}.")
        return 1
    except KeyboardInterrupt:
        print("\nStopping…")
        process.terminate()
        try:
            return process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
