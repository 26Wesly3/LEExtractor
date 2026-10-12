"""HTTP timeout policy (v0.9.12 P0#3).

v0.9.11 用统一的 `timeout=30`（9 处）与 `timeout=20`（1 处）：一次主机无响应就能把整个检索
拖住 30 秒。现在统一走 `http_timeouts()`（connect/read 分离、可配置），并保留 provider 级别的
总 deadline 作为外层上限。
"""

from __future__ import annotations

from pathlib import Path

from litsearch import sources
from litsearch.sources import DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT, http_timeouts


def test_defaults_are_connect_read_split(monkeypatch):
    monkeypatch.delenv("LEEXTRACTOR_HTTP_CONNECT_TIMEOUT", raising=False)
    monkeypatch.delenv("LEEXTRACTOR_HTTP_READ_TIMEOUT", raising=False)
    assert http_timeouts() == (DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT)
    assert DEFAULT_CONNECT_TIMEOUT < DEFAULT_READ_TIMEOUT <= 12


def test_timeouts_are_configurable(monkeypatch):
    monkeypatch.setenv("LEEXTRACTOR_HTTP_CONNECT_TIMEOUT", "2.5")
    monkeypatch.setenv("LEEXTRACTOR_HTTP_READ_TIMEOUT", "9")
    assert http_timeouts() == (2.5, 9.0)


def test_invalid_or_nonpositive_values_fall_back(monkeypatch):
    monkeypatch.setenv("LEEXTRACTOR_HTTP_CONNECT_TIMEOUT", "abc")
    monkeypatch.setenv("LEEXTRACTOR_HTTP_READ_TIMEOUT", "0")
    assert http_timeouts() == (DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT)


def test_no_hard_coded_thirty_second_request_timeout_remains():
    """回归护栏：单请求超时不得再散落写死为 30/20 秒。"""
    text = Path(sources.__file__).read_text(encoding="utf-8")
    assert "timeout=30" not in text
    assert "timeout=20" not in text


def test_read_timeout_maps_to_timeout_status():
    import requests

    from litsearch.retrieval import RetrievalStatus
    from litsearch.sources import retrieval_status_for_exception

    assert retrieval_status_for_exception(requests.exceptions.ReadTimeout()) is RetrievalStatus.TIMEOUT
    assert retrieval_status_for_exception(TimeoutError()) is RetrievalStatus.TIMEOUT
