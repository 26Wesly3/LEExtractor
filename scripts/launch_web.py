"""Launch the local Web workspace after checking the production bundle."""

from __future__ import annotations

import argparse
import json
import socket
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
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with direct.open(url + "/api/health", timeout=1) as response:
            data = json.load(response)
        return data if isinstance(data, dict) and data.get("app_name") == "LEExtractor" else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def open_browser(url: str) -> None:
    try:
        opened = webbrowser.open(url)
    except (webbrowser.Error, OSError):
        opened = False
    if not opened:
        print("浏览器未能自动打开，服务继续运行。请手动访问：" + url, flush=True)


def port_in_use(port: int) -> bool:
    with socket.socket() as connection:
        connection.settimeout(1)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def matches(data: dict | None, version: str) -> bool:
    return bool(data and data.get("version") == version and data.get("ui") == "vue")


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
    version = read_version()
    existing = health(url)
    if matches(existing, version):
        print("Workspace already running: " + url)
        if not args.no_browser:
            open_browser(url)
        return 0
    if existing or port_in_use(args.port):
        print(f"端口 {args.port} 已被旧版本或其他程序占用。请关闭旧服务窗口，或运行：启动Web版.bat --port {args.port + 1 if args.port < 65535 else 8000}")
        return 1
    child = subprocess.Popen([sys.executable, "-m", "litsearch.web", "--port", str(args.port)], cwd=ROOT)
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if child.poll() is not None:
                return child.returncode or 1
            if matches(health(url), version):
                print("LEExtractor " + read_version() + " ready: " + url, flush=True)
                if not args.no_browser:
                    open_browser(url)
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
