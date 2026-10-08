"""Offline tests must fail immediately if they accidentally call a provider."""

import pytest
import requests


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Unexpected network request in an offline test")
    monkeypatch.setattr(requests.Session, "request", blocked)
