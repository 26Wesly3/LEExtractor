"""The v3.2 error envelope is a closed contract, not a convenient dict.

``contracts/domain.schema.json#/$defs/Error`` closes ``details`` to exactly four
keys.  That closure is the mechanism that stops a careless call site from
putting another user's data — or a credential — on the wire, so it is worth
testing rather than trusting.
"""

from __future__ import annotations

import json

import pytest

from litsearch.v32 import contracts
from litsearch.v32.errors import (
    BAD_REQUEST,
    CAPABILITY_UNAVAILABLE,
    DETAIL_KEYS,
    REVISION_CONFLICT,
    SECRET_IN_PROJECT_DOCUMENT,
    V32Error,
    error_envelope,
    new_request_id,
    sanitize_details,
)

try:  # jsonschema is a declared dev dependency
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None


def test_the_envelope_matches_the_contract_schema():
    envelope = V32Error(409, REVISION_CONFLICT, "项目已更新", {"current_revision": 7}).envelope()
    assert set(envelope) == {"error"}
    assert set(envelope["error"]) == {"code", "message", "retryable", "details", "request_id"}
    assert envelope["error"]["details"] == {"current_revision": 7}
    assert envelope["error"]["request_id"]

    if jsonschema is None:  # pragma: no cover
        pytest.skip("jsonschema is not installed")
    schema = contracts.domain_schema()["$defs"]["Error"]
    # The contract file uses a relative $ref; inline it so validation is local.
    schema = json.loads(json.dumps(schema).replace('"#/$defs/Error"', '"#/$defs/Error"'))
    jsonschema.Draft202012Validator(schema).validate(envelope)


def test_details_is_closed_to_the_four_safe_keys():
    assert DETAIL_KEYS == {"current_revision", "item_ids", "retry_after_seconds", "capability"}
    hostile = {
        "current_revision": 3,
        "item_ids": ["a", "b"],
        "retry_after_seconds": 10,
        "capability": "semantic_scholar",
        # Everything below must be dropped, not forwarded.
        "owner_id": "someone-else",
        "api_key": "s2k-should-never-surface",
        "email": "someone@example.invalid",
        "pdf_path": "C:/private/paper.pdf",
    }
    safe = sanitize_details(hostile)
    assert set(safe) == DETAIL_KEYS
    assert "someone-else" not in json.dumps(safe)
    assert "should-never-surface" not in json.dumps(safe)


def test_detail_values_are_type_checked():
    assert sanitize_details({"current_revision": -1}) == {}
    assert sanitize_details({"current_revision": True}) == {}
    assert sanitize_details({"retry_after_seconds": "soon"}) == {}
    assert sanitize_details({"capability": ""}) == {}
    assert sanitize_details(None) == {}


def test_a_code_constant_passed_as_the_http_status_is_refused_loudly():
    """Guards the mistake that put a string where an HTTP status belongs."""
    with pytest.raises(TypeError) as excinfo:
        V32Error(BAD_REQUEST, BAD_REQUEST, "请求格式错误")
    assert "must be an int" in str(excinfo.value)
    with pytest.raises(TypeError):
        V32Error(CAPABILITY_UNAVAILABLE, CAPABILITY_UNAVAILABLE, "能力未配置")
    with pytest.raises(TypeError):
        V32Error(True, BAD_REQUEST, "布尔不是状态码")


def test_retryable_defaults_follow_the_status_and_can_be_overridden():
    assert V32Error(409, REVISION_CONFLICT, "冲突").retryable is False
    assert V32Error(429, "rate_limited", "稍后重试").retryable is True
    assert V32Error(503, CAPABILITY_UNAVAILABLE, "未配置").retryable is True
    assert V32Error(400, BAD_REQUEST, "格式错误").retryable is False
    forced = V32Error(429, "rate_limited", "稍后重试", retryable=False)
    assert forced.retryable is False


def test_the_message_is_the_exception_text_and_never_takes_details_with_it():
    error = V32Error(500, SECRET_IN_PROJECT_DOCUMENT, "项目文档中禁止出现凭据值",
                     {"current_revision": 2})
    assert str(error) == "项目文档中禁止出现凭据值"
    assert error.details == {"current_revision": 2}


def test_request_ids_are_unique_and_opaque():
    ids = {new_request_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(len(value) == 32 for value in ids)


def test_error_envelope_accepts_a_caller_supplied_request_id():
    envelope = error_envelope(V32Error(404, "not_found", "项目不存在"), "trace-123")
    assert envelope["error"]["request_id"] == "trace-123"
