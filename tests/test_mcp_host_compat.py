"""MCP host compatibility: the server must be launchable by Codex/DSH/Claude.

Two properties decide whether an MCP host can actually use this server:

1. **It answers the protocol.** ``initialize`` → ``tools/list`` → ``tools/call``
   over real stdio, which is what every host does.
2. **It answers fast enough.** A host kills a server that misses its startup
   timeout, and a killed server looks exactly like a broken one. Importing the
   package used to cost ~6 s before a single byte of protocol was read, because
   ``litsearch/__init__.py`` eagerly imported the whole machine-learning stack;
   a version lookup alone paid for scikit-learn, scipy, pandas and networkx.
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run(code: str, timeout: int = 120) -> tuple[str, float]:
    """Run a snippet in a fresh interpreter and return (stdout, seconds)."""
    program = f"import time\nt=time.perf_counter()\n{code}\nprint(f'{{time.perf_counter()-t:.3f}}')\n"
    start = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-c", program],
        cwd=str(ROOT), capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    elapsed = time.perf_counter() - start
    assert proc.returncode == 0, proc.stderr[-800:]
    return proc.stdout.strip(), elapsed


# ---------------------------------------------------------------------------
# The package must not bill every caller for the whole engine
# ---------------------------------------------------------------------------


def test_importing_the_version_does_not_load_the_ml_stack():
    """`import litsearch.version` must not drag in scikit-learn/scipy/pandas."""
    program = (
        "import litsearch.version\n"
        "import sys\n"
        "heavy = sorted(m for m in ('sklearn','scipy','pandas','networkx','streamlit')\n"
        "               if m in sys.modules)\n"
        "print('HEAVY=' + ','.join(heavy))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", program],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    line = [row for row in proc.stdout.splitlines() if row.startswith("HEAVY=")][0]
    assert line == "HEAVY=", (
        f"reading the version still loaded the heavy stack: {line}. The package "
        f"initialiser must stay lazy so MCP hosts do not time out."
    )


def test_importing_models_does_not_load_the_ml_stack():
    program = (
        "import litsearch.models\n"
        "import sys\n"
        "heavy = sorted(m for m in ('sklearn','scipy','pandas','streamlit') if m in sys.modules)\n"
        "print('HEAVY=' + ','.join(heavy))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", program],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    line = [r for r in proc.stdout.splitlines() if r.startswith("HEAVY=")][0]
    assert line == "HEAVY=", f"litsearch.models pulled in {line}"


def test_the_public_api_survives_the_lazy_initialiser():
    """Lazy exports must still be importable and identical to the real classes."""
    program = (
        "import litsearch\n"
        "from litsearch import Cache, Paper, PRISMATracker, ReviewState, SourceManager\n"
        "from litsearch.models import Paper as DirectPaper\n"
        "assert Paper is DirectPaper\n"
        "assert litsearch.Paper is DirectPaper\n"
        "assert litsearch.__version__\n"
        "assert litsearch.version_tag().startswith('v')\n"
        "print('OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", program],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    assert proc.returncode == 0, proc.stderr[-1200:]
    assert "OK" in proc.stdout


def test_an_unknown_attribute_still_raises_attribute_error():
    import litsearch

    with pytest.raises(AttributeError):
        _ = litsearch.definitely_not_a_real_name


def test_every_export_points_at_a_module_that_defines_it():
    from litsearch import _EXPORTS

    for name, module_name in _EXPORTS.items():
        module = __import__(module_name, fromlist=[name])
        assert hasattr(module, name), f"{module_name} does not define {name}"


def test_dir_lists_the_public_names():
    import litsearch

    listed = dir(litsearch)
    for name in ("Paper", "ReviewState", "PRISMATracker", "__version__"):
        assert name in listed


# ---------------------------------------------------------------------------
# Cold start budget
# ---------------------------------------------------------------------------


def test_package_import_is_fast_enough_for_a_host_startup_timeout():
    """A host timeout is typically a few seconds; leave room inside it."""
    _, elapsed = _run("import litsearch; litsearch.__version__")
    assert elapsed < 8.0, f"importing litsearch took {elapsed:.1f}s"


def test_reading_the_version_is_near_instant():
    _, elapsed = _run("import litsearch.version")
    assert elapsed < 3.0, f"version lookup took {elapsed:.1f}s"


# ---------------------------------------------------------------------------
# Real stdio protocol (the transport every host uses)
# ---------------------------------------------------------------------------


def _spawn_server():
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8",
           "PYTHONUNBUFFERED": "1"}
    return subprocess.Popen(
        [sys.executable, str(ROOT / "server.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(ROOT), env=env, text=True, encoding="utf-8", errors="replace", bufsize=1,
    )


def _exchange(requests: list[dict], want_ids: set[int], timeout: float = 60):
    proc = _spawn_server()
    out: list[str] = []

    def drain(stream, sink):
        for line in stream:
            sink.append(line.rstrip())

    threading.Thread(target=drain, args=(proc.stdout, out), daemon=True).start()
    threading.Thread(target=lambda: list(proc.stderr), daemon=True).start()
    try:
        for payload in requests:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            seen = set()
            for line in out:
                try:
                    seen.add(json.loads(line).get("id"))
                except Exception:
                    continue
            if want_ids <= seen:
                break
            time.sleep(0.05)
        return {json.loads(line).get("id"): json.loads(line) for line in out if line.strip()}
    finally:
        proc.kill()


def test_the_server_completes_a_real_mcp_handshake_over_stdio():
    replies = _exchange(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "pytest", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ],
        want_ids={1, 2},
    )
    assert 1 in replies, "the server never answered initialize"
    server = replies[1]["result"]["serverInfo"]
    assert server["name"], "the server did not identify itself"
    assert "LEExtractor" in server["name"]

    tools = sorted(t["name"] for t in replies[2]["result"]["tools"])
    assert len(tools) == 12, f"expected the documented 12 tools, got {tools}"
    assert "literature_review_search" in tools
    assert "plan_search_strategy" in tools


def test_the_server_lists_tool_schemas_a_host_can_render():
    """Hosts build their tool UI from these schemas, so they must be well formed."""
    replies = _exchange(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "pytest", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ],
        want_ids={1, 2},
    )
    tools = replies[2]["result"]["tools"]
    for tool in tools:
        assert tool.get("name"), tool
        assert isinstance(tool.get("description"), str), tool["name"]
        assert tool["description"].strip(), f"{tool['name']} has no description"
        schema = tool.get("inputSchema")
        assert isinstance(schema, dict), f"{tool['name']} has no input schema"
        assert schema.get("type") == "object", f"{tool['name']} schema is not an object"


def test_a_network_free_tool_call_succeeds_over_stdio():
    """One real call, so the check covers dispatch and not just listing."""
    replies = _exchange(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "pytest", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "plan_search_strategy",
                        "arguments": {"topic": "plant phenotyping",
                                      "research_direction": "deep learning"}}},
        ],
        want_ids={1, 2},
    )
    assert 2 in replies, "the tool call was never answered"
    result = replies[2]["result"]
    assert "content" in result
    text = result["content"][0]["text"]
    assert "keywords" in text or "slots" in text
