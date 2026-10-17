"""The AI model configuration path: choose a provider, save the key, get an adapter.

Before this, the settings page had a single unnamed model credential slot and the
rerank path only ever saw an injected adapter, so configuring a key could not
actually make AI reranking happen. These tests pin the whole chain without a
network: credential -> profile -> adapter, and the honest ``None`` when nothing is
configured.
"""

from __future__ import annotations

import pytest

from litsearch.v32 import api
from litsearch.v32 import ai_providers
from litsearch.v32.credentials import CredentialStore, PROVIDER_ALLOWLIST


class FakeState:
    def __init__(self, store):
        self.credentials = store


class FakeSession:
    def __init__(self, session_id="s1"):
        self.session_id = session_id


@pytest.fixture()
def store():
    return CredentialStore(allowlist=PROVIDER_ALLOWLIST)


def test_a_model_credential_keeps_its_provider_and_model_but_not_in_the_profile(store):
    store.put("s1", "ai_model", "model", "sk-test-secret",
              model="deepseek-chat", endpoint_profile_id="deepseek")
    profile = store.profile("s1", "ai_model")
    assert profile == {"configured": True, "model": "deepseek-chat", "endpoint_profile_id": "deepseek"}
    assert "sk-test-secret" not in str(profile)


def test_an_unconfigured_profile_reports_not_configured(store):
    assert store.profile("s1", "ai_model") == {
        "configured": False, "model": None, "endpoint_profile_id": None,
    }
    assert store.model_credential("s1") is None


def test_the_secret_is_still_only_reachable_through_get(store):
    store.put("s1", "ai_model", "model", "sk-test-secret", model="deepseek-chat",
              endpoint_profile_id="deepseek")
    assert store.get("s1", "ai_model") == "sk-test-secret"
    credential = store.model_credential("s1")
    assert credential["secret"] == "sk-test-secret"
    assert credential["provider_id"] == "ai_model"


def test_a_configured_model_yields_a_working_adapter(store):
    store.put("s1", "ai_model", "model", "sk-test-secret",
              model="deepseek-chat", endpoint_profile_id="deepseek")
    adapter = api._resolve_model_adapter(FakeState(store), FakeSession())
    assert adapter is not None
    assert adapter.provider_id == "deepseek"
    assert adapter.model == "deepseek-chat"
    # The key must not be visible in the adapter's representation.
    assert "sk-test-secret" not in repr(adapter)


@pytest.mark.parametrize("profile", ["openai", "deepseek", "anthropic"])
def test_all_three_providers_resolve(store, profile):
    store.put("s1", "ai_model", "model", "sk-test-secret", endpoint_profile_id=profile)
    adapter = api._resolve_model_adapter(FakeState(store), FakeSession())
    assert adapter is not None
    assert adapter.provider_id == profile
    # No model given -> the provider's documented default.
    assert adapter.model == ai_providers.PROVIDERS[profile].default_model


def test_no_credential_means_no_adapter(store):
    assert api._resolve_model_adapter(FakeState(store), FakeSession()) is None


def test_an_unknown_profile_degrades_to_none_instead_of_raising(store):
    store.put("s1", "ai_model", "model", "sk-test-secret", endpoint_profile_id="not-a-provider")
    # create_adapter raises unknown_provider; the resolver must swallow it so a bad
    # setting cannot break the request.
    assert api._resolve_model_adapter(FakeState(store), FakeSession()) is None


def test_a_purged_credential_stops_producing_an_adapter(store):
    store.put("s1", "ai_model", "model", "sk-test-secret", endpoint_profile_id="openai")
    assert api._resolve_model_adapter(FakeState(store), FakeSession()) is not None
    store.delete("s1", "ai_model")
    assert api._resolve_model_adapter(FakeState(store), FakeSession()) is None


def test_the_profile_never_leaks_into_the_status_dto(store):
    status = store.put("s1", "ai_model", "model", "sk-test-secret",
                       model="deepseek-chat", endpoint_profile_id="deepseek")
    dumped = str(status)
    assert "sk-test-secret" not in dumped
    # CredentialStatus is a closed schema: provider_id, kind, configured, updated_at.
    assert set(status.__dict__) == {"provider_id", "kind", "configured", "updated_at"}
