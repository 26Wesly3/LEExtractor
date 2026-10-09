"""Real Session dispatch and replaced verbs must count the same transport unit."""

import threading
from types import SimpleNamespace

import pytest
import requests

from litsearch.sources import OpenAlexSource, RetrievalBudgetExceeded
from litsearch.stop_reasons import get_http_budget, reset_http_budget
from litsearch.web.jobs import RequestGuard


@pytest.mark.parametrize("status", [200, 429])
def test_normal_session_transport_is_counted_once(monkeypatch, status):
    response = requests.Response()
    response.status_code = status
    calls = []

    def transport(session, method, url, **kwargs):
        calls.append((method, url))
        return response

    monkeypatch.setattr(requests.Session, "request", transport)
    source = OpenAlexSource()
    reset_http_budget()
    assert source._transport_request("GET", "https://api.openalex.org/works") is response
    counters = get_http_budget().snapshot()
    assert len(calls) == counters["requests"] == 1
    assert counters["rate_limited"] == int(status == 429)
    assert counters["errors"] == 0


def test_normal_session_exception_consumes_one_request(monkeypatch):
    def transport(*args, **kwargs):
        raise requests.Timeout("test timeout")

    monkeypatch.setattr(requests.Session, "request", transport)
    reset_http_budget()
    with pytest.raises(requests.Timeout):
        OpenAlexSource()._transport_request("GET", "https://api.openalex.org/works")
    counters = get_http_budget().snapshot()
    assert counters["requests"] == counters["errors"] == 1


def test_replaced_verb_is_still_honored_and_counted_once(monkeypatch):
    source = OpenAlexSource()
    response = requests.Response()
    response.status_code = 200
    monkeypatch.setattr(source._session, "get", lambda *a, **k: response)
    reset_http_budget()
    assert source._transport_request("GET", "https://api.openalex.org/works") is response
    assert get_http_budget().snapshot()["requests"] == 1


def test_budget_denied_retry_does_not_count_as_a_transport_retry(monkeypatch):
    response = requests.Response()
    response.status_code = 500
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: response)
    monkeypatch.setattr("litsearch.sources.time.sleep", lambda *a: None)
    source = OpenAlexSource()
    RequestGuard(threading.Event(), 2).bind(SimpleNamespace(oa=source))
    reset_http_budget()
    with pytest.raises(RetrievalBudgetExceeded):
        source._request_with_retries("GET", "https://api.openalex.org/works", deadline=float("inf"))
    counters = get_http_budget().snapshot()
    assert counters["requests"] == 2
    assert counters["rate_limited"] == 0
    assert counters["retries"] == 1
