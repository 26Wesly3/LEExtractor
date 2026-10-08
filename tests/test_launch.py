"""Offline checks for the workspace launcher (scripts/launch_gui.py).

The behaviour worth pinning down is the contract with the user: the launcher
must not report success unless the server answered, and it must never leave a
half-started server behind silently.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import launch_gui  # noqa: E402


def test_alive_is_false_on_a_closed_port():
    assert launch_gui._alive("http://127.0.0.1:1/_stcore/health") is False


def test_command_runs_streamlit_headless():
    """Headless so Streamlit does not open a second, possibly duplicate tab."""
    command = launch_gui._command("127.0.0.1", 8511)
    assert command[1:3] == ["-m", "streamlit"]
    assert str(launch_gui.APP) in command
    assert command[command.index("--server.headless") + 1] == "true"


def test_fresh_url_breaks_the_browser_cache():
    """Reopening must not show the previous version's cached page."""
    assert launch_gui.fresh_url("http://127.0.0.1:8501").startswith("http://127.0.0.1:8501?_=")
    assert launch_gui.fresh_url("http://127.0.0.1:8501?embed=true").startswith(
        "http://127.0.0.1:8501?embed=true&_="
    )


def test_missing_app_reports_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(launch_gui, "APP", tmp_path / "absent.py")
    assert launch_gui.main(["--no-browser", "--wait", "1"]) == 1
