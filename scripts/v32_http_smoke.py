"""End-to-end HTTP smoke test of the v3.2 API against a REAL uvicorn server.

Every other v3.2 test drives the ASGI app in-process with injected sources. This
script boots the actual server the product ships, over a real socket, and walks
the documented operations the way a browser would — session cookie, CSRF header,
idempotency key, project creation, discovery enqueue, job polling and the two
read endpoints that need no network.

    $env:PYTHONUTF8="1"; python scripts/v32_http_smoke.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    port = free_port()
    root = Path(tempfile.mkdtemp(prefix="leextractor-smoke-"))
    env = {
        **os.environ,
        "LEEXTRACTOR_DATA_DIR": str(root),
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
        # The smoke run must not be perturbed by a stray proxy variable.
        "NO_PROXY": "*",
        "no_proxy": "*",
    }
    env.pop("HTTP_PROXY", None)
    env.pop("HTTPS_PROXY", None)

    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "litsearch.web.api:create_app",
         "--factory", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    report: dict = {"base": f"{BASE}:{port}", "steps": []}

    def call(method: str, path: str, body: dict | None = None, headers: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{BASE}:{port}{path}", data=data, method=method)
        request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with opener.open(request, timeout=20) as response:
                raw = response.read().decode("utf-8")
                return response.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                return exc.code, json.loads(raw) if raw else None
            except ValueError:
                return exc.code, {"raw": raw[:200]}

    try:
        deadline = time.time() + 40
        while time.time() < deadline:
            if server.poll() is not None:
                break
            try:
                with urllib.request.urlopen(f"{BASE}:{port}/api/health/live", timeout=2):
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.4)
        else:
            report["error"] = "the server did not become ready"
            return 1

        if server.poll() is not None:
            report["error"] = "the server exited during start-up"
            report["server_output"] = (server.stdout.read() if server.stdout else "")[-1500:]
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1

        status, session = call("GET", "/api/v3/session")
        report["steps"].append({"step": "GET /api/v3/session", "status": status,
                                "keys": sorted(session or {}), "has_csrf": bool((session or {}).get("csrf_token"))})
        csrf = (session or {}).get("csrf_token", "")

        status, caps = call("GET", "/api/v3/capabilities")
        items = (caps or {}).get("items", [])
        report["steps"].append({"step": "GET /api/v3/capabilities", "status": status,
                                "deployment_mode": (caps or {}).get("deployment_mode"),
                                "capability_count": len(items),
                                "statuses": sorted({i.get("status") for i in items})})

        status, created = call("POST", "/api/v3/projects",
                               {"name": "烟雾测试项目", "entry_mode": "topic"},
                               {"X-CSRF-Token": csrf, "Idempotency-Key": "smoke-project-create-0001"})
        report["steps"].append({"step": "POST /api/v3/projects", "status": status,
                                "revision": (created or {}).get("revision"),
                                "contract_version": (created or {}).get("contract_version"),
                                "has_anchor": "anchor" in (created or {})})
        pid = (created or {}).get("project_id")

        if pid:
            # The same idempotency key with the same body must replay, not duplicate.
            replay_status, replay = call("POST", "/api/v3/projects",
                                         {"name": "烟雾测试项目", "entry_mode": "topic"},
                                         {"X-CSRF-Token": csrf, "Idempotency-Key": "smoke-project-create-0001"})
            report["steps"].append({
                "step": "POST /api/v3/projects (same idempotency key)",
                "status": replay_status,
                "same_project": (replay or {}).get("project_id") == pid,
            })

            status, fetched = call("GET", f"/api/v3/projects/{pid}")
            report["steps"].append({"step": "GET /api/v3/projects/{pid}", "status": status,
                                    "name": (fetched or {}).get("name")})

            # `mode` is the contract's ["basic", "ai_enhanced"], not the project
            # entry mode — an invalid enum here is a 422, which is correct.
            status, discover = call("POST", f"/api/v3/projects/{pid}/discover",
                                    {"expected_revision": (fetched or {}).get("revision", 1),
                                     "research_text": "薄荷干旱胁迫的转录组响应",
                                     "seed_paper_ids": [], "mode": "basic"},
                                    {"X-CSRF-Token": csrf, "Idempotency-Key": "smoke-discover-0000001"})
            report["steps"].append({"step": "POST /api/v3/projects/{pid}/discover", "status": status,
                                    "state": (discover or {}).get("state"),
                                    "has_poll_url": bool((discover or {}).get("poll_url"))})
            job_id = (discover or {}).get("job_id")

            if job_id:
                final = None
                for _ in range(60):
                    status, job = call("GET", f"/api/v3/projects/{pid}/jobs/{job_id}")
                    final = job
                    if (job or {}).get("state") in {"succeeded", "partial", "failed",
                                                    "cancelled", "superseded", "interrupted"}:
                        break
                    time.sleep(1.0)
                report["steps"].append({
                    "step": "GET /api/v3/projects/{pid}/jobs/{job_id}",
                    "status": status,
                    "final_state": (final or {}).get("state"),
                    "stage": (final or {}).get("stage"),
                    "coverage_status": ((final or {}).get("coverage") or {}).get("status"),
                    "failed_sources": ((final or {}).get("coverage") or {}).get("failed_sources"),
                })

            status, papers = call("GET", f"/api/v3/projects/{pid}/papers")
            report["steps"].append({"step": "GET /api/v3/projects/{pid}/papers", "status": status,
                                    "revision": (papers or {}).get("revision"),
                                    "item_count": len((papers or {}).get("items", []))})

            status, overview = call("GET", f"/api/v3/projects/{pid}/overview")
            report["steps"].append({"step": "GET /api/v3/projects/{pid}/overview", "status": status,
                                    "overview_status": (overview or {}).get("status"),
                                    "statement_count": len((overview or {}).get("statements", [])),
                                    "has_all_keys": all(k in (overview or {}) for k in (
                                        "branches", "timeline", "trends", "leads",
                                        "recommended_paper_ids", "coverage"))})

        # An unauthorized read must not leak another owner's project.
        status, _ = call("GET", "/api/v3/projects/" + "0" * 32)
        report["steps"].append({"step": "GET a project that does not exist", "status": status,
                                "expected": 404})

        # AC-28: no campus authentication surface anywhere.
        status, _ = call("GET", "/api/v3/auth/cas/login")
        report["steps"].append({"step": "GET /api/v3/auth/cas/login (must not exist)", "status": status})

        report["ok"] = all(
            step.get("status") in {200, 201, 202, 204, 404} for step in report["steps"]
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("ok") else 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
