"""Default entry identity and browser failures cannot discard a running service."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import launch_web  # noqa: E402


def test_old_or_foreign_service_is_not_reused(monkeypatch):
    monkeypatch.setattr(launch_web, "health", lambda url: {"version": "0.9.7", "ui": "vue"})
    monkeypatch.setattr(launch_web.subprocess, "Popen", lambda *a, **k: pytest.fail("must not start over an occupied port"))
    assert launch_web.main(["--no-browser"]) == 1


def test_foreign_port_is_not_replaced(monkeypatch):
    monkeypatch.setattr(launch_web, "health", lambda url: None)
    monkeypatch.setattr(launch_web, "port_in_use", lambda port: True)
    monkeypatch.setattr(launch_web.subprocess, "Popen", lambda *a, **k: pytest.fail("must not replace another app"))
    assert launch_web.main(["--no-browser"]) == 1


@pytest.mark.parametrize("failure", [False, OSError("browser failed")])
def test_browser_failure_leaves_matching_service_available(monkeypatch, capsys, failure):
    monkeypatch.setattr(launch_web, "health", lambda url: {"version": launch_web.read_version(), "ui": "vue"})
    def open_browser(url):
        if isinstance(failure, Exception):
            raise failure
        return failure
    monkeypatch.setattr(launch_web.webbrowser, "open", open_browser)
    assert launch_web.main([]) == 0
    assert "http://127.0.0.1:8000" in capsys.readouterr().out


def test_packaged_default_delegates_to_web_and_forwards_arguments():
    root = Path(__file__).resolve().parents[1]
    default = (root / "启动LEExtractor_收到后改回bat.bat").read_text(encoding="utf-8")
    assert '启动Web版.bat" %*' in default
    assert "launch_gui" not in default
    assert "scripts\\launch_web.py %*" in (root / "启动Web版.bat").read_text(encoding="utf-8")
