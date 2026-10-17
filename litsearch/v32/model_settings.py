"""模型服务的端点配置（S9：常见供应商 + 中转站 + 拉模型列表 + 离线持久化）。

设计约束（来自 docs/v3.2/10_SECURITY_PRIVACY.md「外部请求」）：

* 默认只允许官方预设；自定义端点（中转站）是**离线/私人部署**的显式能力，
  公共部署用 ``LEEXTRACTOR_PUBLIC_DEPLOYMENT=1`` 打开 host 白名单校验。
* 任何情况下都不允许把云元数据服务当模型端点。
* Key 只出现在请求头里；探测结果、日志与错误消息只有 scheme+host，没有 Key。

探测只读 ``GET {base}/models``：不产生 token 计费，又能一次拿到「能不能连、
是哪套协议、有哪些模型」三件事。OpenAI 兼容中转站和 Anthropic 协议都支持，
用户不给协议时按 openai → anthropic 顺序自动识别。
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

__all__ = [
    "DIALECTS",
    "PRESETS",
    "ModelEndpointError",
    "clear_persisted",
    "credential_file",
    "load_persisted",
    "normalize_base_url",
    "preset",
    "probe_endpoint",
    "save_persisted",
]


class ModelEndpointError(Exception):
    """A user-facing endpoint problem. Never carries the API key."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


DIALECTS = ("openai_compatible", "anthropic")

#: 常见供应商。``custom`` 是「中转站 / 自建网关」入口：地址由用户填。
PRESETS: tuple[dict[str, Any], ...] = (
    {
        "id": "openai",
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "dialect": "openai_compatible",
        "default_model": "gpt-5.5",
    },
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "dialect": "openai_compatible",
        "default_model": "deepseek-flash",
    },
    {
        "id": "anthropic",
        "label": "Anthropic Claude",
        "base_url": "https://api.anthropic.com/v1",
        "dialect": "anthropic",
        "default_model": "claude-opus-5-5",
    },
    {
        "id": "custom",
        "label": "自定义 / 中转站",
        "base_url": "",
        "dialect": "openai_compatible",
        "default_model": "",
    },
)

#: 云元数据地址：任何部署都不允许。
_BLOCKED_HOSTS = frozenset(
    {"169.254.169.254", "100.100.100.200", "metadata.google.internal", "metadata"}
)


def preset(preset_id: str) -> dict[str, Any] | None:
    wanted = str(preset_id or "").strip().lower()
    for row in PRESETS:
        if row["id"] == wanted:
            return dict(row)
    return None


def public_deployment() -> bool:
    return os.environ.get("LEEXTRACTOR_PUBLIC_DEPLOYMENT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _host_allowlist() -> set[str]:
    raw = os.environ.get("LEEXTRACTOR_MODEL_HOST_ALLOWLIST", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _is_private(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def normalize_base_url(value: str) -> str:
    """Validate and normalise a model endpoint. Raises ``ModelEndpointError``."""
    raw = str(value or "").strip()
    if not raw:
        raise ModelEndpointError("endpoint_required", "请填写模型服务地址。")
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ModelEndpointError("endpoint_invalid", "服务地址必须是 http(s) URL。")
    host = parts.hostname.lower()
    if host in _BLOCKED_HOSTS:
        raise ModelEndpointError("endpoint_blocked", "该地址属于云元数据服务，不能作为模型端点。")
    if public_deployment():
        allowed = _host_allowlist()
        if allowed and host not in allowed:
            raise ModelEndpointError(
                "endpoint_not_allowed", f"公共部署只允许白名单内的模型服务，{host} 不在其中。"
            )
        # 公共部署不得指向内网/环回（含 DNS 解析后的地址）。
        try:
            infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))
        except OSError:
            infos = []
        if any(_is_private(info[4][0]) for info in infos):
            raise ModelEndpointError("endpoint_private", "公共部署不允许指向内网地址。")
    return f"{parts.scheme}://{parts.netloc}{parts.path}".rstrip("/")


def _models_url(base_url: str, dialect: str) -> str:
    return f"{base_url}/models"


def endpoint_host(value: str) -> str:
    """Only the host of an endpoint is ever shown back to the user."""
    try:
        return urlsplit(str(value or "")).hostname or ""
    except ValueError:
        return ""


def _headers(dialect: str, api_key: str) -> dict[str, str]:
    if dialect == "anthropic":
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "accept": "application/json",
        }
    return {"authorization": f"Bearer {api_key}", "accept": "application/json"}


def _http_get_json(url: str, headers: dict[str, str], timeout: float) -> tuple[int, Any]:
    """Small transport. Tests inject their own; nothing here logs the headers."""
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - scheme validated
            payload = response.read().decode("utf-8", "replace")
            return int(response.status), json.loads(payload) if payload.strip() else {}
    except urllib.error.HTTPError as error:
        return int(error.code), {}
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ModelEndpointError("endpoint_unreachable", f"连接失败：{type(error).__name__}") from error


def _model_ids(body: Any) -> list[str]:
    if not isinstance(body, dict):
        return []
    rows = body.get("data") if isinstance(body.get("data"), list) else body.get("models")
    ids: list[str] = []
    for row in rows or []:
        if isinstance(row, dict):
            value = row.get("id") or row.get("name")
        else:
            value = str(row)
        if value:
            ids.append(str(value))
    return sorted(dict.fromkeys(ids))


def probe_endpoint(
    base_url: str,
    api_key: str,
    *,
    dialect: str | None = None,
    transport: Callable[[str, dict[str, str], float], tuple[int, Any]] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Detect the dialect and list models. Reads ``/models`` only; never bills tokens."""
    url = normalize_base_url(base_url)
    key = str(api_key or "").strip()
    if not key:
        raise ModelEndpointError("api_key_required", "请先粘贴 API Key。")
    call = transport or _http_get_json
    order = [dialect] if dialect in DIALECTS else list(DIALECTS)
    failures: list[str] = []
    for candidate in order:
        started = time.monotonic()
        try:
            status, body = call(_models_url(url, candidate), _headers(candidate, key), timeout)
        except ModelEndpointError as error:
            failures.append(f"{candidate}:{error.code}")
            continue
        except Exception as error:  # noqa: BLE001 - transport shape is injectable
            failures.append(f"{candidate}:{type(error).__name__}")
            continue
        if status in (401, 403):
            failures.append(f"{candidate}:unauthorized")
            continue
        if status >= 400:
            failures.append(f"{candidate}:http_{status}")
            continue
        models = _model_ids(body)
        if not models:
            failures.append(f"{candidate}:no_models")
            continue
        return {
            "ok": True,
            "dialect": candidate,
            "base_url": url,
            "host": urlsplit(url).hostname,
            "models": models,
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    if "unauthorized" in " ".join(failures):
        raise ModelEndpointError("api_key_rejected", "Key 被拒绝（401/403），请检查是否复制完整。")
    raise ModelEndpointError("probe_failed", "无法识别该服务：" + "；".join(failures))


# ---------------------------------------------------------------------------
# Offline persistence
# ---------------------------------------------------------------------------


def credential_file(root: Path | str) -> Path:
    return Path(root) / "secrets" / "model.json"


def save_persisted(root: Path | str, payload: dict[str, Any]) -> Path:
    """Write the model endpoint config with owner-only permissions."""
    path = credential_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "base_url": str(payload.get("base_url") or ""),
        "dialect": str(payload.get("dialect") or "openai_compatible"),
        "model": str(payload.get("model") or ""),
        "api_key": str(payload.get("api_key") or ""),
        "provider_id": str(payload.get("provider_id") or "ai_model"),
        # 记住用户选的服务商预设：重启后仍按同一身份建适配器，报错里的服务商
        # 名称才不会退化成一个笼统的 openai。
        "profile": str(payload.get("profile") or "custom"),
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:  # pragma: no cover - Windows may ignore POSIX bits
        pass
    temporary.replace(path)
    return path


def load_persisted(root: Path | str) -> dict[str, Any] | None:
    path = credential_file(root)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict) or not document.get("api_key"):
        return None
    return {
        "provider_id": document.get("provider_id") or "ai_model",
        "secret": document["api_key"],
        "base_url": document.get("base_url") or "",
        "dialect": document.get("dialect") or "openai_compatible",
        "model": document.get("model") or None,
        # 兼容旧文件：没有 profile 时按 custom 处理（地址仍决定真实去向）。
        "endpoint_profile_id": document.get("profile") or "custom",
    }


def clear_persisted(root: Path | str) -> bool:
    path = credential_file(root)
    try:
        path.unlink()
        return True
    except OSError:
        return False
