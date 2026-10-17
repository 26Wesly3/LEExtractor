"""Model-provider adapters: OpenAI, DeepSeek and Claude, all BYOK.

``litsearch.v32.ai_rerank`` deliberately knows nothing about providers — it hands
an adapter a ``request`` mapping and validates whatever comes back against the
contract schema.  This module is the other side of that boundary: it turns the
same request into a real HTTP call for each provider and returns parsed JSON.

Three rules hold for every provider here:

* **BYOK.** The key is passed in per call and never stored, logged or echoed. Error
  messages name the provider and the status, never the request headers.
* **Determinism where the provider allows it.** Temperature is the provider's
  lowest supported setting and the real value is recorded, because a remote model
  cannot be claimed to be fully deterministic.
* **Bounded.** One attempt, the caller's timeout, no retries — ``ai.max_calls_per_rerank``
  is one call, and a model failure must never block the local ranking.

A ``transport`` can be injected so tests never touch the network.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

__all__ = [
    "ProviderSpec",
    "PROVIDERS",
    "ProviderError",
    "ModelAdapter",
    "create_adapter",
    "provider_ids",
    "provider_status",
    "extract_json_object",
]

#: A transport takes (url, headers, payload, timeout) and returns the decoded JSON
#: response.  Injecting one is how the tests stay offline.
Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


@dataclass(frozen=True)
class ProviderSpec:
    """How to talk to one provider."""

    id: str
    label: str
    dialect: str          # "openai_compatible" | "anthropic"
    base_url: str
    default_model: str
    key_env: str
    #: Lowest temperature the provider documents. ``None`` means "do not send the
    #: parameter at all": OpenAI's GPT-5 family is a reasoning family and rejects a
    #: non-default temperature, so omitting it is the only safe choice there. The
    #: real value sent (or its absence) is recorded with every call.
    temperature: float | None = 0.0
    #: Whether the provider documents a JSON object response mode.
    json_mode: bool = True
    #: Current model ids, as published by the provider. The UI offers these and
    #: still accepts a free-text override, so a new model never needs a release.
    known_models: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: Model ids verified against the providers' own documentation on 2026-10-17:
#:  * OpenAI   https://developers.openai.com/api/docs/changelog  (GPT-5.5 flagship, Apr 2026)
#:  * DeepSeek https://api-docs.deepseek.com/quick_start/pricing  (`deepseek-chat` is
#:             no longer listed; the current names are deepseek-flash / deepseek-v4-pro)
#:  * Anthropic https://platform.claude.com/docs/en/models/overview  (5.5 generation;
#:             the docs recommend Opus 5.5 as the default for most workloads)
PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        id="openai",
        label="OpenAI",
        dialect="openai_compatible",
        base_url="https://api.openai.com/v1",
        default_model="gpt-5.5",
        key_env="OPENAI_API_KEY",
        temperature=None,
        known_models=("gpt-5.5", "gpt-5.5-pro", "gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-4.1", "gpt-4o"),
    ),
    "deepseek": ProviderSpec(
        id="deepseek",
        label="DeepSeek",
        dialect="openai_compatible",
        # Documented OpenAI-format base URL. The old `deepseek-chat` name is gone.
        base_url="https://api.deepseek.com",
        default_model="deepseek-flash",
        key_env="DEEPSEEK_API_KEY",
        known_models=("deepseek-flash", "deepseek-v4-pro"),
    ),
    "anthropic": ProviderSpec(
        id="anthropic",
        label="Anthropic Claude",
        dialect="anthropic",
        base_url="https://api.anthropic.com/v1",
        default_model="claude-opus-5-5",
        key_env="ANTHROPIC_API_KEY",
        json_mode=False,   # Claude has no response_format flag; prompt for JSON only.
        known_models=("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"),
    ),
}

#: Never let a key-bearing header reach a log line or an exception message.
_SENSITIVE_HEADERS = frozenset({"authorization", "x-api-key", "api-key"})

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class ProviderError(RuntimeError):
    """A provider call failed.  The message never contains the key."""

    def __init__(self, code: str, message: str, *, status: int | None = None):
        self.code = code
        self.status = status
        super().__init__(message)


def provider_ids() -> list[str]:
    return sorted(PROVIDERS)


def provider_status(*, configured: Mapping[str, bool] | None = None) -> list[dict[str, Any]]:
    """A status row per provider, for capability negotiation.  No secret is read."""
    configured = configured or {}
    rows = []
    for spec in PROVIDERS.values():
        ready = bool(configured.get(spec.id))
        rows.append({
            "id": f"model:{spec.id}",
            "label": spec.label,
            "status": "available" if ready else "not_configured",
            "reason_code": None if ready else "api_key_required",
            "default_model": spec.default_model,
        })
    return rows


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse the model's JSON, tolerating markdown fences and leading prose.

    Providers occasionally wrap JSON in a fence or prefix a sentence. Anything that
    is not a JSON object raises, so the caller rejects the whole batch rather than
    guessing at a partial answer.
    """
    if not isinstance(text, str) or not text.strip():
        raise ProviderError("empty_response", "模型返回为空")
    cleaned = _FENCE.sub("", text).strip()
    try:
        payload = json.loads(cleaned)
    except ValueError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ProviderError("invalid_json", "模型返回不是 JSON 对象") from None
        try:
            payload = json.loads(cleaned[start:end + 1])
        except ValueError:
            raise ProviderError("invalid_json", "模型返回不是合法 JSON") from None
    if not isinstance(payload, dict):
        raise ProviderError("invalid_json", "模型返回不是 JSON 对象")
    return payload


def _redact_url(url: str) -> str:
    """Only the scheme+host of a provider endpoint is ever quoted back."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}" if parts.scheme else "<endpoint>"


class ModelAdapter:
    """One provider, one key, one model.  Implements the ``ai_rerank`` protocol."""

    def __init__(
        self,
        spec: ProviderSpec,
        api_key: str,
        *,
        model: str | None = None,
        transport: Transport | None = None,
        timeout: float = 20.0,
    ):
        if not api_key or not str(api_key).strip():
            raise ProviderError("no_key", f"{spec.label} 未配置密钥")
        self.spec = spec
        self._key = str(api_key).strip()
        self.model = (model or spec.default_model).strip()
        self.transport = transport or _urllib_transport
        self.timeout = float(timeout)

    # -- identity ---------------------------------------------------------

    @property
    def provider_id(self) -> str:
        return self.spec.id

    @property
    def model_revision(self) -> str:
        return self.model

    def __repr__(self) -> str:  # never include the key
        return f"ModelAdapter(provider={self.spec.id!r}, model={self.model!r})"

    # -- request building -------------------------------------------------

    def _headers(self) -> dict[str, str]:
        if self.spec.dialect == "anthropic":
            return {
                "x-api-key": self._key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        return {
            "authorization": f"Bearer {self._key}",
            "content-type": "application/json",
        }

    def _endpoint(self) -> str:
        base = self.spec.base_url.rstrip("/")
        return f"{base}/messages" if self.spec.dialect == "anthropic" else f"{base}/chat/completions"

    @staticmethod
    def _system_prompt(request: Mapping[str, Any]) -> str:
        schema = request.get("response_schema") or {}
        return (
            "You re-rank an already-retrieved list of papers for one research intent.\n"
            "Rules:\n"
            "1. Every id you return MUST come from allowed_paper_ids. Never invent a paper, "
            "author, year, citation count or graph edge.\n"
            "2. Return every allowed id exactly once in ordered_paper_ids.\n"
            "3. Echo context_hash unchanged.\n"
            "4. Output ONE JSON object and nothing else. No prose, no markdown fence.\n"
            f"JSON Schema: {json.dumps(schema, ensure_ascii=False)}"
        )

    @staticmethod
    def _user_prompt(request: Mapping[str, Any]) -> str:
        compact = {
            "schema_version": request.get("schema_version"),
            "context_hash": request.get("context_hash"),
            "parent_snapshot_id": request.get("parent_snapshot_id"),
            "intent": request.get("intent"),
            "judgements_summary": request.get("judgements_summary"),
            "allowed_paper_ids": request.get("allowed_paper_ids"),
            "papers": request.get("papers"),
        }
        return json.dumps(compact, ensure_ascii=False)

    def build_payload(self, request: Mapping[str, Any], *, max_tokens: int = 2000) -> tuple[str, dict[str, Any]]:
        """The endpoint and body for this provider.  Pure; no I/O."""
        system = self._system_prompt(request)
        user = self._user_prompt(request)
        if self.spec.dialect == "anthropic":
            body = {
                "model": self.model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }
        else:
            body = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
            if self.spec.json_mode:
                body["response_format"] = {"type": "json_object"}
        # Only send temperature where the provider documents it. A reasoning model
        # that rejects a non-default value would turn one bad parameter into a failed
        # rerank, so "provider default" is expressed by leaving the key out.
        if self.spec.temperature is not None:
            body["temperature"] = self.spec.temperature
        return self._endpoint(), body

    # -- calls ------------------------------------------------------------

    def _timeout_for(self, request: Mapping[str, Any]) -> float:
        raw = request.get("timeout_seconds")
        try:
            wanted = float(raw)
        except (TypeError, ValueError):
            return self.timeout
        return min(self.timeout, wanted) if wanted > 0 else self.timeout

    def _call(self, request: Mapping[str, Any], *, max_tokens: int = 2000) -> tuple[str, str]:
        url, body = self.build_payload(request, max_tokens=max_tokens)
        try:
            response = self.transport(url, self._headers(), body, self._timeout_for(request))
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - the boundary converts everything
            # str(exc) from an HTTP layer can contain a URL but never our headers;
            # still, quote only the endpoint host to be safe.
            raise ProviderError(
                "transport_error",
                f"{self.spec.label} 请求失败（{_redact_url(url)}）：{type(exc).__name__}",
            ) from None
        return self._extract_text(response), self._revision(response) or self.model

    def _extract_text(self, response: Mapping[str, Any]) -> str:
        if not isinstance(response, Mapping):
            raise ProviderError("invalid_response", f"{self.spec.label} 返回结构无效")
        if self.spec.dialect == "anthropic":
            blocks = response.get("content")
            if not isinstance(blocks, list):
                raise ProviderError("invalid_response", f"{self.spec.label} 返回缺少 content")
            for block in blocks:
                if isinstance(block, Mapping) and block.get("type") in (None, "text"):
                    text = block.get("text")
                    if isinstance(text, str) and text.strip():
                        return text
            raise ProviderError("empty_response", f"{self.spec.label} 返回内容为空")
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError("invalid_response", f"{self.spec.label} 返回缺少 choices")
        message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
        content = message.get("content") if isinstance(message, Mapping) else None
        if isinstance(content, list):  # newer OpenAI content-part form
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, Mapping)
            )
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("empty_response", f"{self.spec.label} 返回内容为空")
        return content

    @staticmethod
    def _revision(response: Mapping[str, Any]) -> str | None:
        if not isinstance(response, Mapping):
            return None
        if isinstance(response.get("model"), str):
            return response["model"]
        return None

    # -- ai_rerank protocol ----------------------------------------------

    def rerank(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Return the parsed structured payload for one rerank window."""
        text, _revision = self._call(request, max_tokens=2400)
        return extract_json_object(text)

    def complete(self, request: Mapping[str, Any]) -> str:
        """Raw text, for callers that want the model's own wording."""
        text, _revision = self._call(request)
        return text

    def overview(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Structured statements for the research overview (same strict boundary)."""
        text, _revision = self._call(request, max_tokens=1600)
        return extract_json_object(text)


def create_adapter(
    provider_id: str,
    api_key: str,
    *,
    model: str | None = None,
    transport: Transport | None = None,
    timeout: float = 20.0,
) -> ModelAdapter:
    """Build an adapter for ``provider_id``, or raise if the provider is unknown."""
    spec = PROVIDERS.get(str(provider_id or "").strip().lower())
    if spec is None:
        raise ProviderError(
            "unknown_provider",
            f"不支持的模型服务：{provider_id!r}（可用：{', '.join(provider_ids())}）",
        )
    return ModelAdapter(spec, api_key, model=model, transport=transport, timeout=timeout)


def _urllib_transport(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """The real transport.  Kept deliberately small and only used when not injected."""
    import urllib.error
    import urllib.request

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    for name, value in headers.items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed provider host
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # Do NOT surface the body: a provider error body can echo request fields.
        raise ProviderError("http_error", f"模型服务返回 HTTP {exc.code}", status=exc.code) from None
    try:
        return json.loads(raw)
    except ValueError:
        raise ProviderError("invalid_response", "模型服务返回的不是 JSON") from None
