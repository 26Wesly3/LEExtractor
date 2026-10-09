"""Launch the local Web workspace after checking the production bundle."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from launch_gui import read_version

ROOT = Path(__file__).resolve().parents[1]


def health(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
            data = json.load(response)
        return data if data.get("app_name") == "LEExtractor" else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Start the local LEExtractor Web workspace")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        print("LEExtractor " + read_version())
        return 0
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    if not (ROOT / "web" / "dist" / "index.html").exists():
        print("Missing Web bundle. In web/: npm ci, then npm run build.")
        return 1
    url = f"http://127.0.0.1:{args.port}"
    existing = health(url)
    if existing:
        if existing.get("version") != read_version():
            print("A different LEExtractor version uses this port. Close it or choose --port.")
            return 1
        print("Workspace already running: " + url)
        if not args.no_browser:
            webbrowser.open(url)
        return 0
    child = subprocess.Popen([sys.executable, "-m", "litsearch.web", "--port", str(args.port)], cwd=ROOT)
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if child.poll() is not None:
                return child.returncode or 1
            if health(url):
                print("LEExtractor " + read_version() + " ready: " + url, flush=True)
                if not args.no_browser:
                    webbrowser.open(url)
                return child.wait()
            time.sleep(0.25)
        print("Startup did not finish within 45 seconds. Check the server log.")
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()


if __name__ == "__main__":
    raise SystemExit(main())
