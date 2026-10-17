"""S9：模型端点（预设 / 中转站 / 探测 / 离线持久化）。

网络一律用注入的 transport，测试不联网。
"""
from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from litsearch.v32 import model_settings as ms
from tests.test_v32_api import _csrf, app, client  # noqa: F401  (fixtures)


def _transport(status: int, body: dict, seen: list | None = None):
    def call(url: str, headers: dict, timeout: float):
        if seen is not None:
            seen.append((url, dict(headers)))
        return status, body
    return call


# -- pure module -----------------------------------------------------------


def test_presets_cover_the_official_three_and_a_custom_entry():
    ids = [row["id"] for row in ms.PRESETS]
    assert ids == ["openai", "deepseek", "anthropic", "custom"]
    assert ms.preset("custom")["base_url"] == ""
    assert ms.preset("nope") is None


@pytest.mark.parametrize("value", ["ftp://x.example.com", "not-a-url", ""])
def test_endpoint_must_be_http_or_https(value):
    with pytest.raises(ms.ModelEndpointError) as error:
        ms.normalize_base_url(value)
    assert error.value.code in {"endpoint_invalid", "endpoint_required"}


def test_cloud_metadata_addresses_are_never_allowed():
    for value in ("http://169.254.169.254/latest/meta-data", "http://metadata.google.internal/v1"):
        with pytest.raises(ms.ModelEndpointError) as error:
            ms.normalize_base_url(value)
        assert error.value.code == "endpoint_blocked"


def test_trailing_slash_is_normalised():
    assert ms.normalize_base_url("https://relay.example.com/v1/") == "https://relay.example.com/v1"


def test_probe_detects_openai_compatible_and_lists_models():
    seen: list = []
    result = ms.probe_endpoint(
        "https://relay.example.com/v1",
        "sk-test-123",
        transport=_transport(200, {"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]}, seen),
    )
    assert result["dialect"] == "openai_compatible"
    assert result["models"] == ["gpt-4o", "gpt-4o-mini"]
    assert result["host"] == "relay.example.com"
    # 只读 /models，且 Key 只在请求头里
    assert seen[0][0] == "https://relay.example.com/v1/models"
    assert seen[0][1]["authorization"] == "Bearer sk-test-123"
    assert "sk-test-123" not in json.dumps(result)


def test_probe_falls_back_to_anthropic_when_the_first_protocol_404s():
    calls = []

    def call(url, headers, timeout):
        calls.append(headers)
        if "authorization" in headers:
            return 404, {}
        return 200, {"data": [{"id": "claude-opus-5-5"}]}

    result = ms.probe_endpoint("https://relay.example.com", "sk-ant-1", transport=call)
    assert result["dialect"] == "anthropic"
    assert result["models"] == ["claude-opus-5-5"]
    assert calls[-1]["x-api-key"] == "sk-ant-1"
    assert calls[-1]["anthropic-version"]


def test_probe_reports_a_rejected_key_without_echoing_it():
    with pytest.raises(ms.ModelEndpointError) as error:
        ms.probe_endpoint(
            "https://relay.example.com",
            "sk-secret-value",
            transport=_transport(401, {}),
        )
    assert error.value.code == "api_key_rejected"
    assert "sk-secret-value" not in error.value.message


def test_persisted_credential_is_owner_only_and_round_trips(tmp_path):
    path = ms.save_persisted(
        tmp_path,
        {"base_url": "https://relay.example.com/v1", "dialect": "openai_compatible", "model": "gpt-4o", "api_key": "sk-1"},
    )
    assert path.exists()
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = ms.load_persisted(tmp_path)
    assert loaded == {
        "provider_id": "ai_model",
        "secret": "sk-1",
        "base_url": "https://relay.example.com/v1",
        "dialect": "openai_compatible",
        "model": "gpt-4o",
        "endpoint_profile_id": "custom",
    }
    assert ms.clear_persisted(tmp_path) is True
    assert ms.load_persisted(tmp_path) is None


def test_persisted_file_without_a_key_is_ignored(tmp_path):
    path = ms.credential_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"base_url": "https://x", "api_key": ""}), encoding="utf-8")
    assert ms.load_persisted(tmp_path) is None


# -- HTTP surface ----------------------------------------------------------


def test_presets_endpoint_lists_the_four_entries(client):
    _csrf(client)
    payload = client.get("/api/v3/settings/model/presets").json()
    assert [row["id"] for row in payload["items"]] == ["openai", "deepseek", "anthropic", "custom"]
    assert payload["deployment"] in {"offline", "public"}


def test_probe_endpoint_maps_a_rejected_key_to_a_400(client, monkeypatch):
    csrf = _csrf(client)

    def broken(base_url, api_key, dialect=None):
        raise ms.ModelEndpointError("api_key_rejected", "Key 被拒绝（401/403），请检查是否复制完整。")

    monkeypatch.setattr(ms, "probe_endpoint", broken)
    response = client.post(
        "/api/v3/settings/model/probe",
        json={"preset_id": "deepseek", "api_key": "sk-x"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "api_key_rejected"
    assert "sk-x" not in response.text


def test_writing_a_custom_endpoint_persists_it_and_reports_the_host(client, monkeypatch, tmp_path):
    csrf = _csrf(client)

    def fake_probe(base_url, api_key, dialect=None):
        return {"ok": True, "dialect": "openai_compatible", "base_url": base_url,
                "host": "relay.example.com", "models": ["gpt-4o"], "latency_ms": 5}

    monkeypatch.setattr(ms, "probe_endpoint", fake_probe)
    response = client.put(
        "/api/v3/settings/model",
        json={
            "preset_id": "custom",
            "base_url": "https://relay.example.com/v1",
            "api_key": "sk-relay",
            "model": "gpt-4o",
            "persist": True,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["configured"] is True
    assert payload["persisted"] is True
    assert payload["host"] == "relay.example.com"
    assert "sk-relay" not in response.text

    status = client.get("/api/v3/settings/credentials").json()
    row = next(item for item in status["items"] if item["provider_id"] == "ai_model")
    assert row["configured"] is True
