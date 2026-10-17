"""OpenAI / DeepSeek / Claude adapters: BYOK, bounded, and never leaking the key.

``ai_rerank`` only knows "hand a request to an adapter, validate what comes back".
These tests pin the other side: the wire shape for each dialect, strict JSON
parsing, that a key cannot reach a log or an exception message, and that a real
``ai_rerank.rerank`` call accepts an adapter built here.
"""

from __future__ import annotations

import json

import pytest

from litsearch.v32 import ai_providers as P


REQUEST = {
    "schema_version": "3.2",
    "prompt_version": "rerank-3.2.0",
    "context_hash": "hash-abc",
    "parent_snapshot_id": "a" * 32,
    "intent": "mint drought stress transcriptome",
    "judgements_summary": "",
    "papers": [{"paper_id": "p1", "title": "A"}, {"paper_id": "p2", "title": "B"}],
    "allowed_paper_ids": ["p1", "p2"],
    "response_schema": {"type": "object"},
    "timeout_seconds": 5,
}

OPENAI_REPLY = {
    "model": "gpt-4o-mini-2024-07-18",
    "choices": [{"message": {"content": json.dumps({"ordered_paper_ids": ["p1", "p2"]})}}],
}
ANTHROPIC_REPLY = {
    "model": "claude-3-5-sonnet-20241022",
    "content": [{"type": "text", "text": json.dumps({"ordered_paper_ids": ["p2", "p1"]})}],
}


def recorder(reply):
    calls = []

    def transport(url, headers, body, timeout):
        calls.append({"url": url, "headers": headers, "body": body, "timeout": timeout})
        return reply

    return transport, calls


def test_all_three_providers_are_supported():
    assert P.provider_ids() == ["anthropic", "deepseek", "openai"]
    for provider_id in P.provider_ids():
        adapter = P.create_adapter(provider_id, "test-key-not-real", transport=lambda *a: {})
        assert adapter.provider_id == provider_id
        assert adapter.model_revision


def test_an_unknown_provider_is_rejected():
    with pytest.raises(P.ProviderError) as excinfo:
        P.create_adapter("does-not-exist", "k")
    assert excinfo.value.code == "unknown_provider"
    assert "openai" in str(excinfo.value)


def test_an_empty_key_is_rejected():
    with pytest.raises(P.ProviderError) as excinfo:
        P.create_adapter("openai", "   ")
    assert excinfo.value.code == "no_key"


@pytest.mark.parametrize("provider_id", ["openai", "deepseek"])
def test_openai_compatible_wire_shape(provider_id):
    transport, calls = recorder(OPENAI_REPLY)
    adapter = P.create_adapter(provider_id, "sk-test-secret-value", transport=transport)
    adapter.rerank(REQUEST)

    call = calls[0]
    assert call["url"].endswith("/chat/completions")
    assert call["headers"]["authorization"] == "Bearer sk-test-secret-value"
    body = call["body"]
    assert body["model"] == P.PROVIDERS[provider_id].default_model
    if P.PROVIDERS[provider_id].temperature is None:
        # OpenAI's GPT-5 family is a reasoning family: a non-default temperature is
        # rejected, so the parameter must be absent rather than set to 0.
        assert "temperature" not in body
    else:
        assert body["temperature"] == 0.0
    assert body["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    # The user turn carries exactly the facts the model may use.
    user = json.loads(body["messages"][1]["content"])
    assert user["allowed_paper_ids"] == ["p1", "p2"]
    assert user["context_hash"] == "hash-abc"
    assert "do not invent" not in json.dumps(user).lower()
    assert "never invent" in body["messages"][0]["content"].lower()
    # The caller's timeout budget is honoured.
    assert call["timeout"] == 5.0


def test_anthropic_wire_shape():
    transport, calls = recorder(ANTHROPIC_REPLY)
    adapter = P.create_adapter("anthropic", "sk-ant-test", transport=transport)
    adapter.rerank(REQUEST)

    call = calls[0]
    assert call["url"].endswith("/messages")
    assert call["headers"]["x-api-key"] == "sk-ant-test"
    assert call["headers"]["anthropic-version"]
    body = call["body"]
    assert body["system"], "Claude takes the instructions out of band"
    assert body["messages"][0]["role"] == "user"
    assert body["temperature"] == 0.0
    assert "response_format" not in body, "Claude has no such flag"


def test_a_fenced_json_reply_is_parsed():
    reply = {"choices": [{"message": {"content": '```json\n{"ordered_paper_ids": ["p1"]}\n```'}}]}
    adapter = P.create_adapter("openai", "k", transport=recorder(reply)[0])
    assert adapter.rerank(REQUEST) == {"ordered_paper_ids": ["p1"]}


def test_json_with_leading_prose_is_parsed():
    reply = {"choices": [{"message": {"content": 'Here you go: {"a": 1}'}}]}
    adapter = P.create_adapter("openai", "k", transport=recorder(reply)[0])
    assert adapter.rerank(REQUEST) == {"a": 1}


def test_a_non_json_reply_raises_instead_of_guessing():
    reply = {"choices": [{"message": {"content": "I cannot help with that."}}]}
    adapter = P.create_adapter("openai", "k", transport=recorder(reply)[0])
    with pytest.raises(P.ProviderError) as excinfo:
        adapter.rerank(REQUEST)
    assert excinfo.value.code == "invalid_json"


def test_an_empty_reply_raises():
    adapter = P.create_adapter("openai", "k", transport=recorder({"choices": []})[0])
    with pytest.raises(P.ProviderError) as excinfo:
        adapter.rerank(REQUEST)
    assert excinfo.value.code == "invalid_response"


def test_a_transport_failure_names_the_provider_without_the_key():
    secret = "sk-super-secret-value-123"

    def boom(*_args, **_kwargs):
        raise TimeoutError("connection timed out")

    adapter = P.create_adapter("openai", secret, transport=boom)
    with pytest.raises(P.ProviderError) as excinfo:
        adapter.rerank(REQUEST)
    message = str(excinfo.value)
    assert excinfo.value.code == "transport_error"
    assert "OpenAI" in message
    assert secret not in message
    assert "authorization" not in message.lower()


def test_no_representation_of_the_adapter_contains_the_key():
    secret = "sk-leak-me-not"
    adapter = P.create_adapter("deepseek", secret)
    assert secret not in repr(adapter)
    assert secret not in str(adapter.__dict__.get("model", ""))
    # The key lives in a private slot and is only rendered into headers.
    assert secret in adapter._headers()["authorization"]


def test_temperature_is_only_sent_where_the_provider_documents_it():
    for provider_id, spec in P.PROVIDERS.items():
        if spec.temperature is None:
            # OpenAI's reasoning family rejects a non-default temperature.
            assert provider_id == "openai"
            continue
        assert spec.temperature == 0.0, provider_id
        assert 0.0 <= spec.temperature <= 2.0


def test_the_default_models_are_the_current_published_names():
    """Guards against re-introducing a retired model id.

    Verified against the providers' own docs on 2026-10-17; `deepseek-chat` and
    `claude-3-5-sonnet-latest` are gone, and gpt-5.5 is the current flagship.
    """
    assert P.PROVIDERS["openai"].default_model == "gpt-5.5"
    assert P.PROVIDERS["deepseek"].default_model == "deepseek-flash"
    assert P.PROVIDERS["anthropic"].default_model == "claude-opus-5-5"
    for spec in P.PROVIDERS.values():
        assert spec.default_model in spec.known_models, spec.id
    # Retired ids must not come back as a default.
    assert P.PROVIDERS["deepseek"].default_model != "deepseek-chat"
    assert "3-5-sonnet" not in P.PROVIDERS["anthropic"].default_model
    assert P.PROVIDERS["deepseek"].base_url == "https://api.deepseek.com"


def test_capability_rows_never_read_a_secret():
    rows = P.provider_status(configured={"openai": True})
    by_id = {row["id"]: row for row in rows}
    assert by_id["model:openai"]["status"] == "available"
    assert by_id["model:deepseek"]["status"] == "not_configured"
    assert by_id["model:deepseek"]["reason_code"] == "api_key_required"
    assert "sk-" not in json.dumps(rows)


# ---------------------------------------------------------------------------
# it must actually plug into the real validation path
# ---------------------------------------------------------------------------


def test_an_adapter_built_here_passes_ai_rerank_validation():
    from litsearch.v32 import ai_rerank

    items = [
        ai_rerank.RerankInput(paper_id="p1", title="A", abstract="a", year=2020, doc_type="article"),
        ai_rerank.RerankInput(paper_id="p2", title="B", abstract="b", year=2021, doc_type="article"),
    ]
    window = ai_rerank.build_window(
        items,
        context_hash="hash-abc",
        intent="mint drought stress",
        judgements_summary="",
        parent_snapshot_id="a" * 32,
    )
    payload = {
        "schema_version": "3.2",
        "context_hash": window.context_hash,
        "ordered_paper_ids": ["p2", "p1"],
        "judgments": [
            {"paper_id": pid, "priority_score": 2, "direct_relevance": 2,
             "method_relevance": 1, "role": "unknown", "reason": "test", "evidence_ids": []}
            for pid in ("p2", "p1")
        ],
    }
    reply = {"choices": [{"message": {"content": json.dumps(payload)}}]}
    adapter = P.create_adapter("openai", "k", transport=recorder(reply)[0])

    outcome = ai_rerank.rerank(window, adapter, parent_snapshot_id="a" * 32)
    assert outcome.status == "applied", outcome
    assert outcome.ordered_paper_ids == ["p2", "p1"]
    assert outcome.rank_stage == "ai_refined"


def test_an_adapter_with_no_key_is_not_called_at_all():
    """ai_rerank must skip rather than call when nothing is configured."""
    from litsearch.v32 import ai_rerank

    items = [
        ai_rerank.RerankInput(paper_id=pid, title=pid, abstract="", year=None, doc_type="")
        for pid in ("p1", "p2")
    ]
    window = ai_rerank.build_window(
        items, context_hash="h", intent="i", judgements_summary="", parent_snapshot_id="a" * 32
    )
    outcome = ai_rerank.rerank(window, None, parent_snapshot_id="a" * 32)
    assert outcome.status == "skipped"
    assert outcome.ordered_paper_ids == ["p1", "p2"]
