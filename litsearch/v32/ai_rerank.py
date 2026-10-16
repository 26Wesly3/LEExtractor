"""AI local rerank for LEExtractor v3.2 (doc 17, M3).

The AI is a *local refinement* of an already computed ranking, never a second
ranking system:

* only the locally recommended window is sent (``ai.top_n`` = 30),
* the model may only **permute the existing positions** of that window,
* its scores never enter the global RRF (``core_score_raw`` is untouched),
* the structured answer is validated against the real contract schema
  (``litsearch/contracts/ai_rerank.schema.json``) and then semantically checked,
* any invalid answer rejects the **whole batch** and the local order stands
  (AC-15); there is no repair loop (``ai.max_calls_per_rerank`` = 1).

No model provider key exists in this environment, so every code path that talks
to a model is driven by an **injected adapter**.  The module never performs a
network call itself and the default path (``adapter is None``) is an honest
``skipped`` outcome: the local ranking stands (AC-14).

Adapter contract (deliberately tiny, so the whole provider is replaceable —
doc 12 "adapter 要求"):

    adapter.rerank(request: dict) -> str | dict          # preferred
    adapter.complete(request: dict) -> str | dict        # fallback
    callable(request: dict) -> str | dict                # last resort

``request`` carries only fields that are allowed to leave the process:
``schema_version``, ``prompt_version``, ``context_hash``, ``parent_snapshot_id``,
``intent``, ``judgements_summary``, ``papers`` (the window payload) and the
contract ``response_schema``.  The answer may be raw JSON text, the payload
object itself, or ``{"output": <payload>, "model_revision": <str>}``.  The call
is bounded by ``ai.call_budget_seconds``; a timeout is an outcome, not an
exception that escapes.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

from . import contracts as C

__all__ = [
    "AIWindow",
    "AIOutcome",
    "RerankInput",
    "ModelAdapterError",
    "ModelTimeoutError",
    "ERR_INVALID_JSON",
    "ERR_SCHEMA",
    "ERR_CONTEXT_HASH",
    "ERR_UNKNOWN_ID",
    "ERR_MISSING_ID",
    "ERR_DUPLICATE_ID",
    "ERR_DUPLICATE_JUDGMENT",
    "ERR_MISSING_JUDGMENT",
    "ERR_EVIDENCE",
    "ERR_ADAPTER",
    "ERR_TIMEOUT",
    "ERR_WINDOW",
    "ERR_NOT_CONFIGURED",
    "CONSERVATIVE_BYTES_PER_TOKEN",
    "PER_ITEM_OVERHEAD_TOKENS",
    "ai_config",
    "estimate_tokens",
    "build_window",
    "validate_output",
    "apply_window",
    "rerank",
    "call_model_adapter",
    "judgment_roles",
]


# --------------------------------------------------------------------------
# error codes
# --------------------------------------------------------------------------

ERR_INVALID_JSON = "invalid_output_json"
ERR_SCHEMA = "schema_invalid"
ERR_CONTEXT_HASH = "context_hash_mismatch"
ERR_UNKNOWN_ID = "unknown_paper_id"
ERR_MISSING_ID = "missing_paper_id"
ERR_DUPLICATE_ID = "duplicate_paper_id"
ERR_DUPLICATE_JUDGMENT = "duplicate_judgment"
ERR_MISSING_JUDGMENT = "missing_judgment"
ERR_EVIDENCE = "evidence_not_in_input"
ERR_ADAPTER = "adapter_error"
ERR_TIMEOUT = "adapter_timeout"
ERR_WINDOW = "window_too_small"
ERR_NOT_CONFIGURED = "ai_not_configured"

#: Conservative byte-based token estimate: doc 17 requires a margin when the
#: model exposes no tokenizer.  3 UTF-8 bytes per token over-counts Latin text
#: and CJK alike, so the window is never larger than the real budget.
CONSERVATIVE_BYTES_PER_TOKEN = 3

#: Structural tokens charged per paper regardless of field lengths.
PER_ITEM_OVERHEAD_TOKENS = 16


class ModelAdapterError(RuntimeError):
    """The injected adapter could not produce an answer."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ModelTimeoutError(ModelAdapterError):
    """The injected adapter exceeded ``ai.call_budget_seconds``."""


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------


def ai_config(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The ``ai`` contract section with optional caller overrides.

    ``config`` may be the section itself, or a larger mapping that nests it
    under ``"ai"``.  Unknown keys are ignored; every default comes from
    ``contracts/defaults.json`` so no budget, cap or version is hard-coded here.
    """
    base: dict[str, Any] = C.ai_defaults()
    if isinstance(config, Mapping):
        nested = config.get("ai")
        if isinstance(nested, Mapping):
            base.update(nested)
        for key in list(base):
            if key in config and not isinstance(config[key], Mapping):
                base[key] = config[key]
    return base


def _cfg_int(cfg: Mapping[str, Any], key: str, *, minimum: int = 0) -> int:
    value = cfg.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelAdapterError(ERR_NOT_CONFIGURED, f"missing integer config: {key}")
    return max(minimum, int(value))


# --------------------------------------------------------------------------
# input model
# --------------------------------------------------------------------------


@dataclass
class RerankInput:
    """One candidate that may be sent to the model."""

    paper_id: str
    title: str = ""
    abstract: str | None = None
    year: int | None = None
    doc_type: str | None = None


def _as_input(item: Any) -> RerankInput | None:
    if isinstance(item, RerankInput):
        candidate = item
    elif isinstance(item, Mapping):
        candidate = RerankInput(
            paper_id=str(item.get("paper_id") or ""),
            title=str(item.get("title") or ""),
            abstract=item.get("abstract"),
            year=item.get("year"),
            doc_type=item.get("doc_type"),
        )
    else:
        paper_id = getattr(item, "paper_id", None)
        if paper_id is None:
            return None
        candidate = RerankInput(
            paper_id=str(paper_id),
            title=str(getattr(item, "title", "") or ""),
            abstract=getattr(item, "abstract", None),
            year=getattr(item, "year", None),
            doc_type=getattr(item, "doc_type", None),
        )
    if not candidate.paper_id:
        return None
    return RerankInput(
        paper_id=candidate.paper_id,
        title=str(candidate.title or ""),
        abstract=None if candidate.abstract is None else str(candidate.abstract),
        year=_clean_year(candidate.year),
        doc_type=None if candidate.doc_type is None else str(candidate.doc_type),
    )


def _clean_year(value: Any) -> int | None:
    """A real publication year, or ``None``.  Never invents a year."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 1000 <= number <= 3000 else None


# --------------------------------------------------------------------------
# token estimate (deterministic, no tokenizer needed)
# --------------------------------------------------------------------------


def estimate_tokens(
    text: str | None, *, bytes_per_token: int = CONSERVATIVE_BYTES_PER_TOKEN
) -> int:
    """Conservative, deterministic token estimate for one text field."""
    if not text:
        return 0
    raw = str(text).encode("utf-8")
    divisor = max(1, int(bytes_per_token))
    return -(-len(raw) // divisor)


def _window_tokens(
    payload: Sequence[Mapping[str, Any]],
    *,
    intent: str,
    judgements_summary: str,
    prompt_version: str,
    cfg: Mapping[str, Any],
) -> int:
    per_token = cfg.get("token_bytes_per_token", CONSERVATIVE_BYTES_PER_TOKEN)
    per_item = cfg.get("per_item_overhead_tokens", PER_ITEM_OVERHEAD_TOKENS)
    per_token = (
        int(per_token) if isinstance(per_token, (int, float)) else CONSERVATIVE_BYTES_PER_TOKEN
    )
    per_item = int(per_item) if isinstance(per_item, (int, float)) else PER_ITEM_OVERHEAD_TOKENS
    total = (
        estimate_tokens(intent, bytes_per_token=per_token)
        + estimate_tokens(judgements_summary, bytes_per_token=per_token)
        + estimate_tokens(prompt_version, bytes_per_token=per_token)
    )
    for paper in payload:
        total += max(0, per_item)
        total += estimate_tokens(str(paper.get("paper_id") or ""), bytes_per_token=per_token)
        total += estimate_tokens(str(paper.get("title") or ""), bytes_per_token=per_token)
        total += estimate_tokens(paper.get("abstract"), bytes_per_token=per_token)
        total += estimate_tokens(str(paper.get("doc_type") or ""), bytes_per_token=per_token)
        year = paper.get("year")
        total += estimate_tokens("" if year is None else str(year), bytes_per_token=per_token)
    return max(0, total)


# --------------------------------------------------------------------------
# window
# --------------------------------------------------------------------------


@dataclass
class AIWindow:
    """The frozen input window of one rerank attempt."""

    ordered_paper_ids: list[str]
    payload: list[dict]
    truncated: bool
    dropped_ids: list[str]
    estimated_tokens: int
    prompt_version: str
    #: The parent snapshot's context hash; the model must echo it back.
    context_hash: str = ""
    parent_snapshot_id: str | None = None
    #: The original intent / user-judgement summary that framed the window.
    intent: str = ""
    judgements_summary: str = ""


def _payload_item(item: RerankInput, abstract_chars: int) -> dict:
    abstract = item.abstract
    if abstract is not None and abstract_chars >= 0:
        abstract = abstract[:abstract_chars]
    return {
        "paper_id": item.paper_id,
        "title": item.title,
        "abstract": abstract,
        "year": item.year,
        "doc_type": item.doc_type,
    }


def _shrink(
    payload: list[dict], allowed_abstract_tokens: int, cfg: Mapping[str, Any]
) -> list[dict]:
    """First stage of the budget reduction: shrink abstracts proportionally."""
    min_chars = _cfg_int(cfg, "abstract_min_chars", minimum=0)
    per_token = cfg.get("token_bytes_per_token", CONSERVATIVE_BYTES_PER_TOKEN)
    lengths = [len(p.get("abstract") or "") for p in payload]
    current = sum(estimate_tokens(p.get("abstract"), bytes_per_token=per_token) for p in payload)
    if current <= allowed_abstract_tokens or current == 0:
        return [dict(p) for p in payload]
    if allowed_abstract_tokens <= 0:
        scale = 0.0
    else:
        scale = allowed_abstract_tokens / float(current)
    shrunk: list[dict] = []
    for paper, length in zip(payload, lengths, strict=True):
        item = dict(paper)
        abstract = paper.get("abstract")
        if abstract:
            target = max(min_chars, min(length, int(math.floor(length * scale))))
            item["abstract"] = abstract[:target] if target < length else abstract
        shrunk.append(item)
    return shrunk


def build_window(
    items: list[RerankInput],
    *,
    context_hash: str,
    intent: str,
    judgements_summary: str,
    parent_snapshot_id: str,
    config: Mapping[str, Any] | None = None,
) -> AIWindow:
    """Freeze the locally recommended top-N into a bounded AI window.

    The input order is the parent snapshot order and is never re-sorted here.
    Over budget: abstracts shrink proportionally first (``abstract_min_chars``
    floor), then N shrinks from the end; if N would fall below ``minimum_n`` the
    call is cancelled and the window comes back empty.
    """
    cfg = ai_config(config)
    top_n = max(0, _cfg_int(cfg, "top_n", minimum=0))
    min_n = max(1, _cfg_int(cfg, "minimum_n", minimum=1))
    max_chars = _cfg_int(cfg, "abstract_max_chars", minimum=0)
    budget = _cfg_int(cfg, "input_token_budget", minimum=0)
    prompt_version = str(cfg.get("prompt_version") or "")

    ordered: list[RerankInput] = []
    seen: set[str] = set()
    for raw in items or []:
        candidate = _as_input(raw)
        if candidate is None or candidate.paper_id in seen:
            continue
        seen.add(candidate.paper_id)
        ordered.append(candidate)

    selected = ordered[:top_n] if top_n else []
    dropped = [item.paper_id for item in ordered[len(selected) :]]

    if len(selected) < min_n:
        return AIWindow(
            ordered_paper_ids=[item.paper_id for item in selected],
            payload=[],
            truncated=bool(dropped),
            dropped_ids=dropped,
            estimated_tokens=0,
            prompt_version=prompt_version,
            context_hash=str(context_hash or ""),
            parent_snapshot_id=parent_snapshot_id,
            intent=str(intent or ""),
            judgements_summary=str(judgements_summary or ""),
        )

    payload = [_payload_item(item, max_chars) for item in selected]
    estimated = _window_tokens(
        payload,
        intent=intent,
        judgements_summary=judgements_summary,
        prompt_version=prompt_version,
        cfg=cfg,
    )
    truncated = bool(dropped)

    if estimated > budget:
        fixed = _window_tokens(
            [dict(p, abstract=None) for p in payload],
            intent=intent,
            judgements_summary=judgements_summary,
            prompt_version=prompt_version,
            cfg=cfg,
        )
        payload = _shrink(payload, budget - fixed, cfg)
        estimated = _window_tokens(
            payload,
            intent=intent,
            judgements_summary=judgements_summary,
            prompt_version=prompt_version,
            cfg=cfg,
        )
        truncated = True

    # Second stage: shrink N from the end, never a mid-window hole.
    while estimated > budget and len(payload) > min_n:
        dropped.append(payload.pop()["paper_id"])
        estimated = _window_tokens(
            payload,
            intent=intent,
            judgements_summary=judgements_summary,
            prompt_version=prompt_version,
            cfg=cfg,
        )

    if estimated > budget:
        # Even the floor does not fit: cancel rather than send a broken window.
        dropped = [item.paper_id for item in ordered]
        return AIWindow(
            ordered_paper_ids=[],
            payload=[],
            truncated=True,
            dropped_ids=dropped,
            estimated_tokens=0,
            prompt_version=prompt_version,
            context_hash=str(context_hash or ""),
            parent_snapshot_id=parent_snapshot_id,
            intent=str(intent or ""),
            judgements_summary=str(judgements_summary or ""),
        )

    return AIWindow(
        ordered_paper_ids=[paper["paper_id"] for paper in payload],
        payload=payload,
        truncated=truncated,
        dropped_ids=dropped,
        estimated_tokens=estimated,
        prompt_version=prompt_version,
        context_hash=str(context_hash or ""),
        parent_snapshot_id=parent_snapshot_id,
    )


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def _schema_errors(parsed: Any, schema: Mapping[str, Any]) -> list[Any]:
    validator = Draft202012Validator(dict(schema))
    return sorted(validator.iter_errors(parsed), key=lambda error: list(error.path))


def validate_output(
    raw: Any,
    window: AIWindow,
    *,
    schema: Mapping[str, Any] | None = None,
) -> tuple[bool, str, dict | None]:
    """Validate one model answer: contract schema first, then semantics.

    Returns ``(ok, error_code, parsed)``.  ``error_code`` is ``""`` on success.
    Any failure rejects the whole batch — the caller keeps the local order.
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            parsed: Any = json.loads(raw)
        except ValueError:
            return False, ERR_INVALID_JSON, None
    elif isinstance(raw, Mapping):
        parsed = dict(raw)
    else:
        return False, ERR_INVALID_JSON, None

    contract = dict(schema) if schema is not None else C.ai_rerank_schema()
    if _schema_errors(parsed, contract):
        return False, ERR_SCHEMA, None

    expected = [str(pid) for pid in window.ordered_paper_ids]
    expected_set = set(expected)

    if str(parsed.get("context_hash")) != str(window.context_hash):
        return False, ERR_CONTEXT_HASH, None

    order = [str(pid) for pid in parsed.get("ordered_paper_ids", [])]
    if len(set(order)) != len(order):
        return False, ERR_DUPLICATE_ID, None
    if any(pid not in expected_set for pid in order):
        return False, ERR_UNKNOWN_ID, None
    if set(order) != expected_set:
        return False, ERR_MISSING_ID, None

    judgments = list(parsed.get("judgments", []))
    judgment_ids = [str(judgment.get("paper_id")) for judgment in judgments]
    if len(set(judgment_ids)) != len(judgment_ids):
        return False, ERR_DUPLICATE_JUDGMENT, None
    if any(pid not in expected_set for pid in judgment_ids):
        return False, ERR_UNKNOWN_ID, None
    if set(judgment_ids) != expected_set:
        return False, ERR_MISSING_JUDGMENT, None

    # A reason may only cite evidence that was actually in the input.
    for judgment in judgments:
        for evidence_id in judgment.get("evidence_ids", []) or []:
            if str(evidence_id) not in expected_set:
                return False, ERR_EVIDENCE, None

    return True, "", parsed


# --------------------------------------------------------------------------
# merging (AI only permutes the window)
# --------------------------------------------------------------------------


def apply_window(
    local_order: list[str],
    ai_order: list[str],
    *,
    parent_snapshot_id: str,
) -> list[str]:
    """Permute the window's existing positions; everything else is untouched.

    Papers outside the window keep their exact relative order and their
    ``core_score_raw`` (this function never touches a score — AC-17).  An
    ``ai_order`` that is not a permutation of the window's ids is ignored and
    the local order is returned unchanged.
    """
    outer = [str(pid) for pid in local_order]
    window_ids = [str(pid) for pid in ai_order]
    window_set = set(window_ids)
    if not window_set:
        return outer

    positions = [index for index, pid in enumerate(outer) if pid in window_set]
    expected = [outer[index] for index in positions]

    seen: set[str] = set()
    permutation: list[str] = []
    for pid in window_ids:
        if pid in window_set and pid not in seen:
            seen.add(pid)
            permutation.append(pid)
    if sorted(permutation) != sorted(expected):
        return outer

    merged = list(outer)
    for position, pid in zip(positions, permutation, strict=True):
        merged[position] = pid
    return merged


# --------------------------------------------------------------------------
# adapter plumbing
# --------------------------------------------------------------------------

_DEFAULT_ADAPTER_METHODS = ("rerank", "complete", "generate")


def call_model_adapter(
    adapter: Any,
    request: Mapping[str, Any],
    *,
    timeout_seconds: float | None = None,
    methods: Sequence[str] = _DEFAULT_ADAPTER_METHODS,
) -> tuple[Any, str | None]:
    """Invoke an injected model adapter with a bounded timeout.

    Returns ``(payload, model_revision)``.  Raises :class:`ModelTimeoutError`
    or :class:`ModelAdapterError`; the caller turns those into an outcome.  No
    retry lives here — ``ai.max_calls_per_rerank`` is one call per rerank.
    """
    entry: Callable[[Mapping[str, Any]], Any] | None = None
    for name in methods:
        candidate = getattr(adapter, name, None)
        if callable(candidate):
            entry = candidate
            break
    if entry is None:
        if callable(adapter):
            entry = adapter
        else:
            raise ModelAdapterError(ERR_NOT_CONFIGURED, "adapter exposes no callable entry point")

    result: Any
    if timeout_seconds is not None and float(timeout_seconds) > 0:
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(entry, request)
            try:
                result = future.result(timeout=float(timeout_seconds))
            except FutureTimeoutError as exc:  # pragma: no cover - timing dependent
                raise ModelTimeoutError(ERR_TIMEOUT, "模型调用超出配置预算") from exc
        finally:
            pool.shutdown(wait=False)
    else:
        result = entry(request)

    model_revision: str | None = None
    if isinstance(result, Mapping) and "ordered_paper_ids" not in result and "output" in result:
        revision = result.get("model_revision")
        model_revision = None if revision is None else str(revision)
        result = result.get("output")
    if model_revision is None:
        revision = getattr(adapter, "model_revision", None)
        model_revision = None if revision is None else str(revision)
    return result, model_revision


# --------------------------------------------------------------------------
# rerank
# --------------------------------------------------------------------------


@dataclass
class AIOutcome:
    """The result of one rerank attempt.

    ``status`` is ``applied`` | ``rejected`` | ``skipped`` | ``failed``.
    ``ordered_paper_ids`` is always a safe order to keep: the AI order only when
    the whole batch validated, the local (frozen) order otherwise.
    """

    status: str
    ordered_paper_ids: list[str]
    judgments: list[dict] = field(default_factory=list)
    error_code: str | None = None
    model_revision: str | None = None
    detail: str = ""
    #: ``ai_refined`` only when the AI order was accepted, else the local stage.
    rank_stage: str | None = None


def rerank(
    window: AIWindow,
    adapter: Any,
    *,
    parent_snapshot_id: str,
    config: Mapping[str, Any] | None = None,
) -> AIOutcome:
    """Run the single bounded AI call and validate it as one batch."""
    cfg = ai_config(config)
    local = [str(pid) for pid in window.ordered_paper_ids]
    min_n = max(1, _cfg_int(cfg, "minimum_n", minimum=1))

    if len(local) < min_n:
        return AIOutcome(
            status="skipped",
            ordered_paper_ids=local,
            error_code=ERR_WINDOW,
            detail=f"窗口可发送文献 {len(local)} 篇 < 最小 {min_n} 篇，不调用模型",
            rank_stage="local_enhanced",
        )
    if adapter is None:
        return AIOutcome(
            status="skipped",
            ordered_paper_ids=local,
            error_code=ERR_NOT_CONFIGURED,
            detail="未配置 AI 适配器（无模型 Key），保留本地排序",
            rank_stage="local_enhanced",
        )

    request = {
        "schema_version": str(cfg.get("schema_version") or ""),
        "prompt_version": window.prompt_version,
        "context_hash": window.context_hash,
        "parent_snapshot_id": parent_snapshot_id,
        "intent": window.intent,
        "judgements_summary": window.judgements_summary,
        "papers": [dict(paper) for paper in window.payload],
        "allowed_paper_ids": list(local),
        "response_schema": C.ai_rerank_schema(),
        "timeout_seconds": float(cfg.get("call_budget_seconds") or 0),
    }

    try:
        result, model_revision = call_model_adapter(
            adapter,
            request,
            timeout_seconds=float(cfg.get("call_budget_seconds") or 0),
        )
    except ModelTimeoutError as exc:
        return AIOutcome(
            status="failed",
            ordered_paper_ids=local,
            error_code=ERR_TIMEOUT,
            detail=str(exc),
            rank_stage="local_enhanced",
        )
    except Exception as exc:  # noqa: BLE001 - adapter boundary, never escapes
        return AIOutcome(
            status="failed",
            ordered_paper_ids=local,
            error_code=getattr(exc, "code", ERR_ADAPTER),
            detail=f"{type(exc).__name__}: {exc}"[:400],
            rank_stage="local_enhanced",
        )

    ok, error_code, parsed = validate_output(result, window)
    if not ok or parsed is None:
        return AIOutcome(
            status="rejected",
            ordered_paper_ids=local,
            error_code=error_code,
            model_revision=model_revision,
            detail=f"结构化输出未通过校验（{error_code}），整批拒绝，保留本地排序",
            rank_stage="local_enhanced",
        )

    return AIOutcome(
        status="applied",
        ordered_paper_ids=[str(pid) for pid in parsed["ordered_paper_ids"]],
        judgments=[dict(judgment) for judgment in parsed["judgments"]],
        error_code=None,
        model_revision=model_revision,
        detail="模型输出通过 schema 与语义校验，仅重排窗口内已有位置",
        rank_stage="ai_refined",
    )


def judgment_roles(outcome: AIOutcome) -> dict[str, dict]:
    """Map ``paper_id -> {role, role_origin, priority_score}`` for display.

    An adopted model role is an inference, never an observation: ``role_origin``
    is always ``model_inference`` (doc 17).  Only the 0–4 ordinal scores the
    contract defines are exposed — an uncalibrated confidence is never presented
    as a scientific probability, and none of these values enters the global RRF.
    """
    roles: dict[str, dict] = {}
    if outcome.status != "applied":
        return roles
    for judgment in outcome.judgments:
        paper_id = str(judgment.get("paper_id") or "")
        if not paper_id:
            continue
        roles[paper_id] = {
            "role": str(judgment.get("role") or "unknown"),
            "role_origin": "model_inference",
            "priority_score": judgment.get("priority_score"),
        }
    return roles
