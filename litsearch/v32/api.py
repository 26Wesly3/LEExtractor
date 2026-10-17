"""The v3.2 HTTP surface: 25 operations from ``contracts/openapi.json``.

Mounting (doc 03: "若新增路径与旧路由冲突，保留旧行为并使用内部版本化路由及兼容映射"):

``/api/v3``
    the authoritative surface — the whole router is included here;
``/api``
    the same router, so the documented paths also work; it is registered
    **after** the legacy routes, so the four paths that exist in both worlds
    (``POST /api/projects``, ``GET /api/projects/{pid}``,
    ``POST /api/projects/{pid}/expand``, ``GET /api/projects/{pid}/papers``)
    keep answering exactly as they did before.

Nothing in this module composes a URL, validates a ticket or asks for a campus
password: ``access.application_sso_enabled`` and ``federation_sp_enabled`` are
both ``false`` this round and ``docs/20_ZJU_CAS_DEFERRED.md`` keeps that work
deferred (AC-28).  Institutional entries come only from
``contracts/access_links.json``.

Every response body is projected through the matching ``$defs`` object of
``litsearch/contracts/domain.schema.json``, field by field, instead of being
hand-assembled: a field the contract does not declare cannot reach the wire,
and a field it requires cannot go missing silently.
"""

from __future__ import annotations

import hashlib
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal, Mapping

from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from litsearch.v32 import external_match
from litsearch.v32 import contracts
from litsearch.v32 import export as v32_export
from litsearch.v32.errors import (
    CAPABILITY_UNAVAILABLE,
    CURSOR_STALE,
    NOT_FOUND,
    UNPROCESSABLE,
    V32Error,
    error_envelope,
)
from litsearch.v32.jobs import JobManager
from litsearch.v32.pipeline import (
    V32State,
    citation_edges as pipeline_citation_edges,
    config_hash,
    import_batch_dto,
    overview_payload,
    query_variants,
    register_handlers,
)
from litsearch.v32.sessions import SESSION_COOKIE, SessionStore
from litsearch.v32.store import V32Project, V32Store, content_hash, decode_cursor, encode_cursor
from litsearch.v32.types import JobStage, iso_now, utc_now

__all__ = ["V32State", "build_router", "install_v32", "v32_from_app"]

logger = logging.getLogger("leextractor.v32.api")

#: Idempotency keys are bounded by the contract, not by taste.
_IDEMPOTENCY_MIN = 16
_IDEMPOTENCY_MAX = 128

#: Providers that need a user credential before they can be used at all.  This
#: is a fact about the providers, not a contract value: ``defaults.json`` names
#: ``discovery.provider_priority`` but says nothing about authentication.
_KEYED_PROVIDERS = frozenset({"google_scholar"})

#: Provider id -> the environment variable that carries its optional key.
_PROVIDER_KEY_ENV = {
    "semantic_scholar": "S2_API_KEY",
    "openalex": "OPENALEX_API_KEY",
    "google_scholar": "SERPAPI_API_KEY",
}


# ---------------------------------------------------------------------------
# domain-schema projections
# ---------------------------------------------------------------------------


@lru_cache(maxsize=None)
def _def_meta(name: str) -> tuple[tuple[str, ...], frozenset[str]]:
    node = contracts.domain_schema()["$defs"][name]
    return tuple(node.get("properties", {})), frozenset(node.get("required", ()))


def _project(name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Keep exactly the ``$defs/<name>`` properties, in contract order."""
    properties, required = _def_meta(name)
    out = {key: payload[key] for key in properties if key in payload}
    missing = sorted(required - set(out))
    if missing:
        # A missing required field is our bug, not the caller's: say so with a
        # 500 envelope rather than emitting a body the contract rejects.
        raise V32Error(500, "internal_contract_violation", "响应缺少契约必需字段，已阻止返回")
    return out


@lru_cache(maxsize=None)
def _enum_values(name: str, field: str) -> tuple[str, ...]:
    return tuple(contracts.domain_schema()["$defs"][name]["properties"][field]["enum"])


def _literal(name: str, field: str) -> Any:
    return Literal[_enum_values(name, field)]


@lru_cache(maxsize=None)
def _query_enum(path: str, method: str, parameter: str) -> tuple[str, ...]:
    """A query-parameter enum, read from ``contracts/openapi.json``.

    ``view`` is declared by the operation, not by ``domain.schema.json``, so
    this reads the operation itself instead of re-typing the four values.
    """
    operation = contracts.openapi_schema()["paths"][path][method]
    for entry in operation.get("parameters", []):
        if entry.get("name") == parameter:
            return tuple(entry.get("schema", {}).get("enum", ()))
    raise KeyError(f"{method.upper()} {path} has no parameter {parameter!r}")


#: An opaque 1..128 character identifier, with the contract's own bounds.
_Id = Annotated[str, Field(min_length=1, max_length=128)]


def _datetime_text(value: Any, fallback: str) -> str:
    text = str(value) if value not in (None, "") else ""
    if len(text) >= 19 and text[4] == "-" and text[7] == "-":
        return text
    return fallback


def _int_or_none(value: Any, *, minimum: int | None = None, maximum: int | None = None) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if minimum is not None and number < minimum:
        return None
    if maximum is not None and number > maximum:
        return None
    return number


def paper_dto(record: Mapping[str, Any], *, fallback_time: str) -> dict[str, Any]:
    """``$defs/Paper``.  Nothing is invented: an unknown number is ``null``."""
    aliases = [str(value) for value in (record.get("aliases") or []) if str(value)]
    linked = [str(value) for value in (record.get("linked_version_ids") or []) if str(value)]
    provenance: list[dict[str, Any]] = []
    for row in record.get("source_records") or []:
        if not isinstance(row, Mapping):
            continue
        provider = str(row.get("provider") or "").strip()[:128] or "unknown"
        external = row.get("external_id")
        url = row.get("source_url")
        provenance.append(
            {
                "provider": provider,
                "external_id": str(external) if external not in (None, "") else None,
                "source_url": url if isinstance(url, str) and url.startswith("http") else None,
                "retrieved_at": _datetime_text(row.get("retrieved_at"), fallback_time),
            }
        )
    abstract = record.get("abstract")
    doi = record.get("doi")
    return {
        "paper_id": str(record.get("paper_id") or "")[:128],
        "title": str(record.get("title") or ""),
        "authors": [str(author) for author in (record.get("authors") or []) if isinstance(author, str)],
        "year": _int_or_none(record.get("year"), minimum=1000, maximum=3000),
        "abstract": str(abstract) if isinstance(abstract, str) else None,
        "doi": str(doi) if doi not in (None, "") else None,
        "aliases": aliases,
        "citation_count": _int_or_none(record.get("citation_count"), minimum=0),
        "source_records": provenance,
        "linked_version_ids": linked,
    }


def judgement_dto(record: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    return _project(
        "Judgement",
        {
            "paper_id": str(record.get("paper_id") or ""),
            "value": str(record.get("value") or "unjudged"),
            "exclusion_reason": str(record.get("exclusion_reason") or "unspecified"),
            "reason": str(record.get("reason") or ""),
            "updated_at": _datetime_text(record.get("updated_at"), iso_now()),
            "event_sequence": int(record.get("event_sequence") or 0),
        },
    )


def ranked_dto(item: Mapping[str, Any]) -> dict[str, Any]:
    """``$defs/RankedPaper`` straight out of the committed snapshot."""
    return _project("RankedPaper", dict(item))


# ---------------------------------------------------------------------------
# request bodies
# ---------------------------------------------------------------------------


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCreateBody(_Body):
    name: str = Field(min_length=1, max_length=200)
    entry_mode: _literal("ProjectCreate", "entry_mode")


class RevisionBody(_Body):
    expected_revision: int = Field(ge=0)


class DiscoverBody(RevisionBody):
    research_text: str = Field(default="", max_length=3000)
    seed_paper_ids: list[_Id] = Field(default_factory=list, max_length=128)
    mode: _literal("DiscoverRequest", "mode") = "basic"


class JudgementBody(RevisionBody):
    paper_id: _Id
    judgement: _literal("JudgementRequest", "judgement")
    exclusion_reason: _literal("JudgementRequest", "exclusion_reason") | None = None
    reason: str = Field(default="", max_length=1000)


class ResolveBody(RevisionBody):
    item_id: _Id
    action: _literal("ResolveRequest", "action")
    candidate_id: str | None = Field(default=None, max_length=128)


class RetryBody(RevisionBody):
    item_ids: list[_Id] = Field(min_length=1, max_length=200)


class CredentialWriteBody(_Body):
    kind: _literal("CredentialWrite", "kind")
    secret: str = Field(min_length=1, max_length=8192)
    model: str | None = None
    endpoint_profile_id: _Id | None = None


class ModelProbeBody(_Body):
    """S9：探测模型端点（只读 /models，不产生 token 计费）。"""

    preset_id: str | None = None
    base_url: str | None = None
    dialect: str | None = None
    api_key: str = Field(min_length=1, max_length=8192)


class ModelConfigBody(_Body):
    """S9：保存模型端点配置（常见预设或中转站），可选记住在本机。"""

    preset_id: str | None = None
    base_url: str | None = None
    dialect: str | None = None
    model: str | None = None
    api_key: str = Field(min_length=1, max_length=8192)
    persist: bool = False


class ZoteroLocalImportBody(RevisionBody):
    """S11：把本机 Zotero 里选中的条目导入当前项目。"""

    item_keys: list[str] = Field(min_length=1, max_length=500)


class ZoteroImportBody(RevisionBody):
    library_type: _literal("ZoteroImportRequest", "library_type")
    library_id: _Id
    item_keys: list[_Id] = Field(min_length=1, max_length=500)
    include_selected_attachments: bool = False


class ExportPreviewBody(RevisionBody):
    paper_ids: list[_Id] = Field(min_length=1, max_length=500)
    library_type: _literal("ExportPreviewRequest", "library_type")
    library_id: _Id
    collection_key: _Id | None = None


class ExportBody(RevisionBody):
    preview_id: _Id
    confirm: Literal[True]


# ---------------------------------------------------------------------------
# session / idempotency plumbing
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def session_cookie_names() -> tuple[str, ...]:
    """The documented cookie name first, then the frozen store's own name.

    ``contracts/openapi.json`` names the cookie in
    ``components.securitySchemes``; ``litsearch.v32.sessions`` declares its own
    constant.  Both are accepted so neither layer has to be edited to agree.
    """
    scheme = contracts.openapi_schema()["components"]["securitySchemes"]["sessionCookie"]
    documented = str(scheme.get("name") or "").strip()
    return tuple(name for name in (documented, SESSION_COOKIE) if name)


def _state(request: Request) -> V32State:
    state = getattr(request.app.state, "v32", None)
    if state is None:  # pragma: no cover - install_v32 always runs first
        raise V32Error(503, CAPABILITY_UNAVAILABLE, "服务尚未初始化完成，请稍后重试")
    return state


def _session(state: V32State, request: Request) -> Any:
    """The caller's anonymous session, or 401 with the contract envelope."""
    for name in session_cookie_names():
        value = request.cookies.get(name)
        if value:
            return state.sessions.get(value)
    return state.sessions.get(None)


def _optional_session(state: V32State, request: Request) -> Any:
    for name in session_cookie_names():
        value = request.cookies.get(name)
        if value:
            try:
                return state.sessions.get(value)
            except V32Error:
                return None
    return None


def _mutate(state: V32State, request: Request) -> Any:
    """Session plus the CSRF check every mutating request must pass (403)."""
    session = _session(state, request)
    state.sessions.require_csrf(session, request.headers.get("X-CSRF-Token"))
    return session


def _owned_project(state: V32State, session: Any, pid: str) -> V32Project:
    """Owner-scoped lookup: another owner's project is a plain 404 (AC-25)."""
    return state.store.get(pid, owner_id=session.owner_id)


def _replay(
    state: V32State,
    session: Any,
    *,
    project_id: str,
    endpoint: str,
    key: str,
    body: Any,
) -> dict[str, Any] | None:
    return state.store.idempotency_lookup(
        owner_id=session.owner_id,
        project_id=project_id,
        endpoint=endpoint,
        key=key,
        body_hash=state.store.body_hash(body),
    )


def _remember(
    state: V32State,
    session: Any,
    *,
    project_id: str,
    endpoint: str,
    key: str,
    body: Any,
    response: Mapping[str, Any],
) -> None:
    state.store.idempotency_store(
        owner_id=session.owner_id,
        project_id=project_id,
        endpoint=endpoint,
        key=key,
        body_hash=state.store.body_hash(body),
        response=dict(response),
    )


def _accepted(state: V32State, project: V32Project, job: Any) -> dict[str, Any]:
    return _project("JobAccepted", state.jobs.accepted(job))


def _job_dto(state: V32State, project: V32Project, job_id: str) -> dict[str, Any]:
    job = state.jobs.get(project, job_id)
    return _project("Job", state.jobs.dto(job))


def _submit_config(state: V32State, project: V32Project) -> dict[str, Any]:
    """Pin the ranking configuration before a job captures its own versions."""
    year = utc_now().year
    model_revision = state.ai_model_revision
    digest = config_hash(year, model_revision)
    state.store.set_config_hash(project, digest, model_revision)
    return {"config_hash": digest, "model_revision": model_revision}


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------


@lru_cache(maxsize=8)
def _module_available(name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):  # pragma: no cover - a broken install
        return False


def _resolve_model_adapter(state: Any, session: Any) -> Any:
    """Build a real model adapter from this session's stored model credential.

    Returns ``None`` when nothing is configured, the profile is unknown, or the
    key is unusable — the caller then keeps the local ranking and the UI can say
    「已使用基础排序」 honestly instead of pretending a model ran.

    The secret lives only in this function's locals and inside the adapter; it is
    never logged, never returned, and never put in an error message.
    """
    try:
        credential = _model_credential(state, session)
        if not credential:
            return None
        from litsearch.v32 import contracts
        from litsearch.v32.ai_providers import create_adapter

        timeout = float(contracts.ai_defaults().get("call_budget_seconds") or 20)
        return create_adapter(
            credential.get("endpoint_profile_id") or "openai",
            credential["secret"],
            model=credential.get("model"),
            base_url=credential.get("base_url"),
            dialect=credential.get("dialect"),
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001 - a bad profile must not break the request
        return None


def _model_credential(state: Any, session: Any) -> dict[str, Any] | None:
    """This session's model credential, else the offline-persisted one (S9).

    The persisted record is what makes 「记住在本机」 work; it is only read by
    the model adapter path and never returned through a DTO.
    """
    if session is not None:
        try:
            credential = state.credentials.model_credential(session.session_id)
        except Exception:  # noqa: BLE001
            credential = None
        if credential:
            return credential
    from litsearch.v32 import model_settings

    return model_settings.load_persisted(getattr(state, "data_dir", None) or ".")


def _capability(capability_id: str, status: str, reason_code: str | None) -> dict[str, Any]:
    return {
        "id": str(capability_id),
        "status": str(status),
        "reason_code": reason_code,
        "checked_at": iso_now(),
    }


def _provider_status(provider_id: str, session: Any, state: V32State) -> dict[str, Any]:
    configured = False
    if session is not None:
        configured = bool(state.credentials.get(session.session_id, provider_id))
    env_name = _PROVIDER_KEY_ENV.get(provider_id)
    if not configured and env_name:
        configured = bool(os.environ.get(env_name, "").strip())
    if provider_id in _KEYED_PROVIDERS and not configured:
        return _capability(provider_id, "not_configured", "api_key_required")
    return _capability(
        provider_id,
        "available",
        "api_key_configured" if configured else "public_endpoint",
    )


def capabilities_payload(state: V32State, session: Any) -> dict[str, Any]:
    """``$defs/Capabilities`` with an honest status for every dependency.

    A capability that is unavailable never blocks a flow that does not need it;
    this report exists so the user can see *why* something is not offered, not
    to gate the rest of the application.  No credential value is read here —
    only whether one exists.
    """
    discovery = contracts.discovery()
    pdf = contracts.pdf_defaults()
    runtime = contracts.runtime_defaults()

    items: list[dict[str, Any]] = [
        _provider_status(str(provider), session, state) for provider in discovery["provider_priority"]
    ]
    parser_status = "available" if _module_available("fitz") else "unavailable"
    items.append(
        _capability(
            "parser:pdf", parser_status, None if parser_status == "available" else "parser_missing"
        )
    )
    # Models are DISCOVERED, never assumed: a pinned model that is already on disk
    # (often outside FastEmbed's default caches) must be reported as available
    # instead of "not configured", and nothing is downloaded from here.
    try:
        from litsearch.v32.models import discover_local_models

        models = discover_local_models()
    except Exception:  # noqa: BLE001 - discovery must never break capabilities
        models = None

    # NOTE: reason_code is a short stable code, NOT a filesystem path. A full
    # absolute path blows past the schema's 128-char limit (the contract test
    # caught exactly that) and doc 10 forbids leaking absolute paths through the
    # API. The concrete location is reported by scripts/v32_models.py instead.
    if models is not None and models.translation_ready:
        items.append(_capability("translation_model:zh_en", "available", "local_model_found"))
    else:
        items.append(_capability("translation_model:zh_en", "not_configured", "no_local_translation_model"))

    if models is not None and models.semantic_ready:
        items.append(_capability("semantic_model:embedding", "available", "local_model_found"))
    else:
        items.append(
            _capability(
                "semantic_model:embedding",
                "not_configured",
                f"embedding_status:{runtime.get('embedding_status') or 'unknown'}",
            )
        )
    # A configured model credential makes AI rerank usable even before an adapter
    # has been constructed for a request; without one it stays not_configured.
    try:
        model_configured = bool(_model_credential(state, session))
    except Exception:  # noqa: BLE001
        model_configured = False
    ai_ready = state.ai() is not None or model_configured
    items.append(
        _capability(
            "ai:rerank",
            "available" if ai_ready else "not_configured",
            None if ai_ready else "ai_disabled_by_default",
        )
    )
    zotero_ready = state.zotero_ready()
    items.append(
        _capability(
            "zotero:web_api",
            "available" if zotero_ready else "external_blocked",
            None if zotero_ready else "api_key_required",
        )
    )
    try:
        from litsearch.v32.access_gateway import capabilities_for_access

        items.extend(capabilities_for_access()["items"])
    except ImportError:  # pragma: no cover - the module ships with the package
        items.append(_capability("institution_link_registry", "unavailable", "module_unavailable"))

    return _project(
        "Capabilities",
        {
            "deployment_mode": str(runtime["default_mode"]),
            "items": items,
            "limits": {
                "max_pdf_files": int(pdf["batch_max_files"]),
                "max_pdf_file_bytes": int(pdf["file_max_mib"]) * 1024 * 1024,
                "max_pdf_batch_bytes": int(pdf["batch_max_mib"]) * 1024 * 1024,
                "candidate_cap": int(discovery["candidate_cap"]),
            },
        },
    )


# ---------------------------------------------------------------------------
# papers
# ---------------------------------------------------------------------------


def _snapshot_for(
    state: V32State, project: V32Project, snapshot_id: str | None, cursor_snapshot: str | None
) -> dict[str, Any] | None:
    """Resolve the snapshot a page is read from, or refuse a stale cursor.

    A cursor is bound to one snapshot: replaying it against another one, or
    against a project whose current snapshot has moved on, is a 409 and never a
    silently different page (doc 08 ``CURSOR_STALE``).
    """
    current = project.doc.get("current_snapshot_id")
    if snapshot_id is not None:
        if cursor_snapshot is not None and cursor_snapshot != snapshot_id:
            raise V32Error(409, CURSOR_STALE, "分页游标属于另一个快照，请重新加载列表")
        return state.store.load_snapshot(project, snapshot_id)
    if cursor_snapshot is not None:
        if current is None or cursor_snapshot != current:
            raise V32Error(409, CURSOR_STALE, "分页游标已过期，请重新加载列表")
        return state.store.load_snapshot(project, cursor_snapshot)
    if not current:
        return None
    return state.store.load_snapshot(project, current)


def papers_payload(
    state: V32State,
    project: V32Project,
    *,
    cursor: str | None,
    snapshot_id: str | None,
    view: str,
    limit: int,
) -> dict[str, Any]:
    """``$defs/PaperList``: a real page of a committed ranking, or an empty one."""
    cursor_snapshot, offset = decode_cursor(cursor)
    snapshot = _snapshot_for(state, project, snapshot_id, cursor_snapshot)
    effective_snapshot_id = (snapshot or {}).get("snapshot_id")

    judgements = project.doc.get("judgements") or {}
    fallback_time = str(project.doc.get("updated_at") or iso_now())
    rows: list[dict[str, Any]] = []
    for item in (snapshot or {}).get("items") or []:
        paper_id = str(item.get("paper_id") or "")
        record = (project.doc.get("papers") or {}).get(paper_id)
        if record is None:
            continue
        judgement = judgements.get(paper_id)
        eligibility = str(item.get("eligibility") or "exploration")
        excluded = str((judgement or {}).get("value") or "") == "excluded"
        if view == "recommended" and (eligibility != "recommended" or excluded):
            continue
        if view == "exploration" and (eligibility != "exploration" or excluded):
            continue
        if view == "excluded" and not excluded:
            continue
        rows.append(
            {
                "paper": paper_dto(record, fallback_time=fallback_time),
                "ranking": ranked_dto(item),
                "judgement": judgement_dto(judgement),
            }
        )

    page = rows[offset : offset + limit]
    next_offset = offset + len(page)
    next_cursor = None
    if next_offset < len(rows):
        next_cursor = encode_cursor(effective_snapshot_id, next_offset)
    return _project(
        "PaperList",
        {
            "snapshot_id": effective_snapshot_id,
            "revision": int(project.revision),
            "items": page,
            "next_cursor": next_cursor,
        },
    )


def _import_batch(state: V32State, project: V32Project, import_id: str) -> dict[str, Any]:
    """``$defs/ImportBatch`` with every item projected to ``$defs/ImportItem``."""
    payload = import_batch_dto(project, import_id)
    payload["items"] = [_project("ImportItem", row) for row in payload["items"]]
    return _project("ImportBatch", payload)


def _paper_record(state: V32State, project: V32Project, paper_id: str) -> dict[str, Any]:
    canonical = state.store.resolve_paper_id(project, paper_id)
    if canonical is None:
        raise V32Error(NOT_FOUND, "paper_not_found", "项目中没有该文献")
    return dict(project.doc["papers"][canonical])


def _paper_from_parsed_evidence(state: V32State, project: V32Project, item: Mapping[str, Any]) -> str:
    """A local seed created from the parsed metadata recorded on the item."""
    parsed = dict(item.get("parsed") or {})
    return state.store.upsert_paper(
        project,
        {
            "title": str(parsed.get("title") or item.get("display_name") or ""),
            "authors": list(parsed.get("authors") or []),
            "year": parsed.get("year"),
            "abstract": parsed.get("abstract"),
            "doi": parsed.get("doi"),
        },
    )


# ---------------------------------------------------------------------------
# router
# ---------------------------------------------------------------------------


def build_router(state: V32State) -> APIRouter:
    """Every v3.2 operation, bound to ``state``."""
    router = APIRouter()

    # -- session ----------------------------------------------------------

    @router.get("/session")
    def get_session(request: Request) -> JSONResponse:
        documented = session_cookie_names()[0]
        provided = None
        for name in session_cookie_names():
            provided = request.cookies.get(name)
            if provided:
                break
        session, created = state.sessions.get_or_create(provided)
        response = JSONResponse(_project("SessionStatus", session.status()))
        if created or provided != session.session_id:
            response.set_cookie(
                documented,
                session.session_id,
                httponly=True,
                samesite="lax",
                secure=request.url.scheme == "https",
                path="/",
            )
        return response

    # -- capabilities -----------------------------------------------------

    @router.get("/capabilities")
    def get_capabilities(request: Request) -> dict[str, Any]:
        return capabilities_payload(state, _optional_session(state, request))

    # -- projects ---------------------------------------------------------

    @router.post("/projects", status_code=201)
    def create_project(
        request: Request,
        body: ProjectCreateBody,
        idempotency_key: str = Header(
            ..., alias="Idempotency-Key", min_length=_IDEMPOTENCY_MIN, max_length=_IDEMPOTENCY_MAX
        ),
    ) -> dict[str, Any]:
        session = _mutate(state, request)
        body_key = body.model_dump()
        replay = _replay(
            state, session, project_id="", endpoint="create_project", key=idempotency_key, body=body_key
        )
        if replay is not None:
            return _project("Project", replay)
        project = state.store.create_project(
            owner_id=session.owner_id, name=body.name, entry_mode=body.entry_mode
        )
        payload = _project("Project", state.store.project_dto(project))
        _remember(
            state, session, project_id="", endpoint="create_project", key=idempotency_key,
            body=body_key, response=payload,
        )
        return payload

    # v3.2 之后新增：项目列表（S8）。只列当前会话 owner 的项目，最新在前；
    # 契约文档仍按 3.2 的 25 个操作冻结，这里是记录在案的扩展。
    @router.get("/projects")
    def list_projects(request: Request) -> dict[str, Any]:
        session = _session(state, request)
        rows = [
            _project("Project", state.store.project_dto(project))
            for project in state.store.all_for_owner(session.owner_id)
        ]
        rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        return {"items": rows, "total": len(rows)}

    @router.get("/projects/{pid}")
    def get_project(request: Request, pid: str) -> dict[str, Any]:
        session = _session(state, request)
        project = _owned_project(state, session, pid)
        return _project("Project", state.store.project_dto(project))

    @router.post("/projects/{pid}/discover", status_code=202)
    def discover(
        request: Request,
        pid: str,
        body: DiscoverBody,
        idempotency_key: str = Header(
            ..., alias="Idempotency-Key", min_length=_IDEMPOTENCY_MIN, max_length=_IDEMPOTENCY_MAX
        ),
    ) -> dict[str, Any]:
        session = _mutate(state, request)
        project = _owned_project(state, session, pid)
        body_key = body.model_dump()
        replay = _replay(
            state, session, project_id=pid, endpoint="discover", key=idempotency_key, body=body_key
        )
        if replay is not None:
            return _project("JobAccepted", replay)

        text = (body.research_text or "").strip()
        seeds = [str(value) for value in body.seed_paper_ids]
        if not text and not seeds:
            raise V32Error(422, UNPROCESSABLE, "请提供研究内容，或至少选择一篇项目内文献作为起点")
        resolved_seeds: list[str] = []
        unknown: list[str] = []
        for seed in seeds:
            canonical = state.store.resolve_paper_id(project, seed)
            if canonical is None:
                unknown.append(seed)
            else:
                resolved_seeds.append(canonical)
        if unknown:
            raise V32Error(422, UNPROCESSABLE, "起点文献必须属于当前项目", {"item_ids": unknown})
        state.store.check_revision(project, body.expected_revision)

        anchor = dict(project.doc.get("anchor") or {})
        anchor["original_text"] = text or str(anchor.get("original_text") or "")
        anchor["seed_paper_ids"] = resolved_seeds
        anchor["query_variants"] = query_variants(anchor["original_text"])
        anchor["context_version"] = project.context_version + 1
        # The chosen mode is remembered on the project so the reranks that
        # follow (judgements, rerank) stay in it; basic never calls a model.
        project.doc["ai_mode"] = str(body.mode)
        state.store.set_anchor(project, anchor, seeds=resolved_seeds)
        pinned = _submit_config(state, project)
        # 模型适配器不再挂在共享状态上：任务执行时按本会话凭据与模式解析（doc 10）。
        job = state.jobs.submit(
            project,
            "discover",
            stage=str(JobStage.QUEUED),
            priority=True,
            mode=body.mode,
            session_ref=session.session_id,
            **pinned,
        )
        accepted = _accepted(state, project, job)
        _remember(
            state, session, project_id=pid, endpoint="discover", key=idempotency_key,
            body=body_key, response=accepted,
        )
        return accepted

    @router.post("/projects/{pid}/rerank", status_code=202)
    def rerank(
        request: Request,
        pid: str,
        body: RevisionBody,
        idempotency_key: str = Header(
            ..., alias="Idempotency-Key", min_length=_IDEMPOTENCY_MIN, max_length=_IDEMPOTENCY_MAX
        ),
    ) -> dict[str, Any]:
        session = _mutate(state, request)
        project = _owned_project(state, session, pid)
        body_key = body.model_dump()
        replay = _replay(
            state, session, project_id=pid, endpoint="rerank", key=idempotency_key, body=body_key
        )
        if replay is not None:
            return _project("JobAccepted", replay)
        state.store.check_revision(project, body.expected_revision)
        pinned = _submit_config(state, project)
        # 适配器在任务执行时按本会话凭据与项目模式解析，不写入共享状态。
        job = state.jobs.submit(
            project,
            "rerank",
            stage=str(JobStage.QUEUED),
            priority=True,
            mode=str(project.doc.get("ai_mode") or "basic"),
            session_ref=session.session_id,
            **pinned,
        )
        accepted = _accepted(state, project, job)
        _remember(
            state, session, project_id=pid, endpoint="rerank", key=idempotency_key,
            body=body_key, response=accepted,
        )
        return accepted

    @router.post("/projects/{pid}/expand", status_code=202)
    def expand(
        request: Request,
        pid: str,
        body: RevisionBody,
        idempotency_key: str = Header(
            ..., alias="Idempotency-Key", min_length=_IDEMPOTENCY_MIN, max_length=_IDEMPOTENCY_MAX
        ),
    ) -> dict[str, Any]:
        session = _mutate(state, request)
        project = _owned_project(state, session, pid)
        body_key = body.model_dump()
        replay = _replay(
            state, session, project_id=pid, endpoint="expand", key=idempotency_key, body=body_key
        )
        if replay is not None:
            return _project("JobAccepted", replay)
        state.store.check_revision(project, body.expected_revision)
        if not project.doc.get("papers"):
            raise V32Error(422, UNPROCESSABLE, "请先完成一次检索或导入，再进行引文扩展")
        pinned = _submit_config(state, project)
        job = state.jobs.submit(
            project, "expand", stage=str(JobStage.QUEUED), priority=True,
            session_ref=session.session_id, **pinned,
        )
        accepted = _accepted(state, project, job)
        _remember(
            state, session, project_id=pid, endpoint="expand", key=idempotency_key,
            body=body_key, response=accepted,
        )
        return accepted

    # -- judgements -------------------------------------------------------

    @router.post("/projects/{pid}/judgements")
    def set_judgement(
        request: Request,
        pid: str,
        body: JudgementBody,
        idempotency_key: str = Header(
            ..., alias="Idempotency-Key", min_length=_IDEMPOTENCY_MIN, max_length=_IDEMPOTENCY_MAX
        ),
    ) -> dict[str, Any]:
        session = _mutate(state, request)
        project = _owned_project(state, session, pid)
        body_key = body.model_dump()
        replay = _replay(
            state, session, project_id=pid, endpoint="judgements", key=idempotency_key, body=body_key
        )
        if replay is not None:
            return _project("JudgementSaved", replay)
        saved = state.store.set_judgement(
            project,
            expected_revision=body.expected_revision,
            paper_id=body.paper_id,
            value=body.judgement,
            exclusion_reason=body.exclusion_reason or "unspecified",
            reason=body.reason or "",
        )
        job = state.jobs.schedule_rerank(
            project,
            session_ref=session.session_id,
            mode=str(project.doc.get("ai_mode") or "basic"),
        )
        payload = _project(
            "JudgementSaved",
            {
                "revision": int(saved["revision"]),
                "context_version": int(saved["context_version"]),
                "judgement": judgement_dto(saved["judgement"]),
                "rerank_job_id": job.job_id,
            },
        )
        _remember(
            state, session, project_id=pid, endpoint="judgements", key=idempotency_key,
            body=body_key, response=payload,
        )
        return payload

    # -- papers -----------------------------------------------------------

    @router.get("/projects/{pid}/papers")
    def list_papers(
        request: Request,
        pid: str,
        cursor: str | None = None,
        snapshot_id: str | None = None,
        view: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        session = _session(state, request)
        project = _owned_project(state, session, pid)
        resolved_view = str(view or "recommended")
        if resolved_view not in _query_enum("/api/projects/{pid}/papers", "get", "view"):
            raise V32Error(422, UNPROCESSABLE, "列表视图取值非法")
        if not 1 <= int(limit) <= 100:
            raise V32Error(422, UNPROCESSABLE, "每页数量必须在 1 到 100 之间")
        return papers_payload(
            state,
            project,
            cursor=cursor,
            snapshot_id=snapshot_id,
            view=resolved_view,
            limit=int(limit),
        )

    # -- jobs -------------------------------------------------------------

    @router.get("/projects/{pid}/jobs/{job_id}")
    def get_job(request: Request, pid: str, job_id: str) -> dict[str, Any]:
        session = _session(state, request)
        project = _owned_project(state, session, pid)
        return _job_dto(state, project, job_id)

    @router.post("/projects/{pid}/jobs/{job_id}/cancel")
    def cancel_job(
        request: Request,
        pid: str,
        job_id: str,
        idempotency_key: str = Header(
            ..., alias="Idempotency-Key", min_length=_IDEMPOTENCY_MIN, max_length=_IDEMPOTENCY_MAX
        ),
    ) -> dict[str, Any]:
        session = _mutate(state, request)
        project = _owned_project(state, session, pid)
        body_key = {"job_id": str(job_id)}
        replay = _replay(
            state, session, project_id=pid, endpoint="cancel_job", key=idempotency_key, body=body_key
        )
        if replay is not None:
            return _project("Job", replay)
        job = state.jobs.cancel(project, job_id)
        payload = _job_dto(state, project, job.job_id)
        _remember(
            state, session, project_id=pid, endpoint="cancel_job", key=idempotency_key,
            body=body_key, response=payload,
        )
        return payload

    @router.get("/projects/{pid}/jobs/{job_id}/zotero-results")
    def zotero_results(request: Request, pid: str, job_id: str) -> dict[str, Any]:
        session = _session(state, request)
        project = _owned_project(state, session, pid)
        job = state.jobs.get(project, job_id)
        rows = [dict(row) for row in (job.doc.get("zotero_results") or [])]
        return _project("ZoteroResults", {"job_id": job.job_id, "items": rows})

    # -- overview ---------------------------------------------------------

    @router.get("/projects/{pid}/overview")
    def get_overview(request: Request, pid: str) -> dict[str, Any]:
        session = _session(state, request)
        project = _owned_project(state, session, pid)
        return _project("Overview", overview_payload(state, project, session))

    # -- export (doc 16 "导出与工具衔接", AC-30) ---------------------------

    @router.get("/projects/{pid}/export")
    def export_project(request: Request, pid: str, format: str | None = None) -> Response:
        session = _session(state, request)
        project = _owned_project(state, session, pid)
        if format not in v32_export.EXPORT_FORMATS:
            raise V32Error(
                422, UNPROCESSABLE, "导出格式非法，可选 ris、bibtex、csv、evidence_pack、prisma"
            )
        with project.lock:
            current = project.doc.get("current_snapshot_id")
            snapshot = state.store.load_snapshot(project, current) if current else None
            edges = pipeline_citation_edges(state.store, project) if format == "evidence_pack" else []
            body, filename, media = v32_export.export_document(project, format, snapshot, edges)
        return Response(
            content=body,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # -- access and venue -------------------------------------------------

    @router.get("/projects/{pid}/papers/{paper_id}/access-options")
    def access_options(request: Request, pid: str, paper_id: str) -> dict[str, Any]:
        session = _session(state, request)
        project = _owned_project(state, session, pid)
        record = _paper_record(state, project, paper_id)
        from litsearch.v32.access_gateway import access_options as build_options

        return _project("AccessOptions", {"items": build_options(record)})

    @router.get("/projects/{pid}/papers/{paper_id}/venue-metrics")
    def venue_metrics(request: Request, pid: str, paper_id: str) -> dict[str, Any]:
        session = _session(state, request)
        project = _owned_project(state, session, pid)
        record = _paper_record(state, project, paper_id)
        from litsearch.v32.venue_metrics import venue_metrics as build_metrics

        venue = {
            "venue_id": record.get("venue_id"),
            "name": record.get("venue"),
            "venue_type": record.get("venue_type"),
            "issns": record.get("issns") or [],
            "stats": record.get("citation_stats"),
            "h_index": record.get("h_index"),
        }
        return _project("VenueMetrics", build_metrics(venue))

    # -- credentials ------------------------------------------------------

    @router.put("/settings/credentials/{provider_id}")
    def write_credential(
        request: Request,
        provider_id: str,
        body: CredentialWriteBody,
    ) -> dict[str, Any]:
        session = _mutate(state, request)
        status = state.credentials.put(
            session.session_id,
            provider_id,
            body.kind,
            body.secret,
            model=body.model,
            endpoint_profile_id=body.endpoint_profile_id,
        )
        # Store the adapter's non-secret companions on the capability path too, so
        # GET /api/capabilities can say which model service is configured without
        # ever reading the secret back out.
        if str(body.kind) == "model":
            state.ai_profile = {
                "provider_id": provider_id,
                "endpoint_profile_id": body.endpoint_profile_id,
                "model": body.model,
            }
        return _project("CredentialStatus", status.as_dict())

    @router.delete("/settings/credentials/{provider_id}", status_code=204)
    def delete_credential(request: Request, provider_id: str) -> JSONResponse:
        session = _mutate(state, request)
        state.credentials.delete(session.session_id, provider_id)
        if str(provider_id) == "ai_model":
            # S9：删除模型凭据时，本机记住的那份也要一起清掉，否则「删除」名不副实。
            from litsearch.v32 import model_settings

            model_settings.clear_persisted(state.data_dir)
        return JSONResponse(status_code=204, content=None)

    @router.get("/settings/credentials")
    def list_credentials(request: Request) -> dict[str, Any]:
        session = _session(state, request)
        return _project("CredentialStatuses", state.credentials.payload(session.session_id))

    # -- venue cache (S10) ------------------------------------------------

    @router.get("/venues")
    def list_venues(request: Request, limit: int = 50) -> dict[str, Any]:
        """Local journal cache: count per venue plus the bundled CCF rank.

        No licensed impact factor exists locally, so it is not reported here —
        the DTO simply omits it instead of showing a guess.
        """
        _session(state, request)
        from litsearch.v32 import venue_cache

        rows = venue_cache.summary(state.data_dir, limit=max(1, min(int(limit), 200)))
        return {"items": rows, "total": len(rows)}

    # -- local Zotero (S11) ----------------------------------------------
    # 只读本机 Zotero 的本地 API（127.0.0.1:23119），不写库、不需要 Web API Key。

    @router.get("/zotero/local/status")
    def zotero_local_status(request: Request) -> dict[str, Any]:
        _session(state, request)
        from litsearch.v32 import zotero_local

        return zotero_local.status()

    @router.get("/zotero/local/collections")
    def zotero_local_collections(request: Request) -> dict[str, Any]:
        _session(state, request)
        from litsearch.v32 import zotero_local

        return {"items": zotero_local.collections()}

    @router.get("/zotero/local/items")
    def zotero_local_items(
        request: Request, q: str = "", collection: str = "", limit: int = 25, offset: int = 0
    ) -> dict[str, Any]:
        _session(state, request)
        from litsearch.v32 import zotero_local

        rows = zotero_local.items(query=q, collection=collection, limit=limit, offset=offset)
        return {"items": rows, "total": len(rows)}

    @router.post("/projects/{pid}/zotero-local/import", status_code=201)
    def import_from_local_zotero(
        request: Request, pid: str, body: ZoteroLocalImportBody
    ) -> dict[str, Any]:
        session = _mutate(state, request)
        project = _owned_project(state, session, pid)
        state.store.check_revision(project, body.expected_revision)
        from litsearch.v32 import zotero_local

        rows = zotero_local.fetch_many(body.item_keys)
        before = len(project.doc.get("papers", {}))
        paper_ids: list[str] = []
        for row in rows:
            paper_ids.append(state.store.upsert_paper(project, zotero_local.to_paper(row)))
        created = len(project.doc.get("papers", {})) - before
        if paper_ids:
            state.store.save(project, touch_corpus=True)
            # 导入的条目是本项目真实持有的记录，重排一次，列表才有排序可用。
            try:
                from litsearch.v32.pipeline import rank_project

                rank_project(state, project)
            except Exception:  # noqa: BLE001 - 排序失败不该丢掉已导入的记录
                pass
        return {
            "imported": len(paper_ids),
            "created": max(0, created),
            "paper_ids": paper_ids,
            "revision": project.revision,
            "corpus_version": project.corpus_version,
        }

    # -- model endpoint (S9) ----------------------------------------------
    # 常见供应商预设 + 中转站 + 拉模型列表 + 「记住在本机」。

    @router.get("/settings/model/presets")
    def model_presets(request: Request) -> dict[str, Any]:
        _session(state, request)
        from litsearch.v32 import model_settings

        return {
            "items": [dict(row) for row in model_settings.PRESETS],
            "dialects": list(model_settings.DIALECTS),
            "deployment": "public" if model_settings.public_deployment() else "offline",
        }

    @router.post("/settings/model/probe")
    def model_probe(request: Request, body: ModelProbeBody) -> dict[str, Any]:
        _session(state, request)
        from litsearch.v32 import model_settings

        base = str(body.base_url or "").strip()
        if not base and body.preset_id:
            preset = model_settings.preset(body.preset_id) or {}
            base = str(preset.get("base_url") or "")
        try:
            result = model_settings.probe_endpoint(
                base, body.api_key, dialect=body.dialect
            )
        except model_settings.ModelEndpointError as error:
            raise V32Error(400, error.code, error.message) from error
        return {
            "ok": True,
            "dialect": result["dialect"],
            "host": result["host"],
            "models": result["models"],
            "latency_ms": result["latency_ms"],
        }

    @router.put("/settings/model")
    def write_model_config(request: Request, body: ModelConfigBody) -> dict[str, Any]:
        session = _mutate(state, request)
        from litsearch.v32 import model_settings

        preset_row = model_settings.preset(body.preset_id) or {}
        try:
            base_url = model_settings.normalize_base_url(
                str(body.base_url or preset_row.get("base_url") or "")
            )
        except model_settings.ModelEndpointError as error:
            raise V32Error(400, error.code, error.message) from error
        dialect = str(body.dialect or preset_row.get("dialect") or "openai_compatible")
        if dialect not in model_settings.DIALECTS:
            raise V32Error(400, "dialect_invalid", "未知的模型协议。")
        model = str(body.model or preset_row.get("default_model") or "").strip() or None
        profile = str(body.preset_id or "custom")
        status = state.credentials.put(
            session.session_id,
            "ai_model",
            "model",
            body.api_key,
            model=model,
            endpoint_profile_id=profile,
            base_url=base_url,
            dialect=dialect,
        )
        persisted = False
        if body.persist:
            model_settings.save_persisted(
                state.data_dir,
                {
                    "provider_id": "ai_model",
                    "base_url": base_url,
                    "dialect": dialect,
                    "model": model or "",
                    "api_key": body.api_key,
                    "profile": profile,
                },
            )
            persisted = True
        else:
            model_settings.clear_persisted(state.data_dir)
        state.ai_profile = {
            "provider_id": "ai_model",
            "endpoint_profile_id": profile,
            "model": model,
        }
        payload = dict(status.as_dict())
        payload["persisted"] = persisted
        payload["host"] = model_settings.endpoint_host(base_url)
        payload["dialect"] = dialect
        # 这是 3.2 契约之外的扩展端点，所以直接返回扩展后的对象，不套 _project
        # （_project 只保留 CredentialStatus 的四个字段）。
        return payload

    # -- import -----------------------------------------------------------

    @router.post("/projects/{pid}/papers/import", status_code=202)
    async def import_papers(
        request: Request,
        pid: str,
        expected_revision: int = Form(..., ge=0),
        files: list[UploadFile] = File(...),
        idempotency_key: str = Header(
            ..., alias="Idempotency-Key", min_length=_IDEMPOTENCY_MIN, max_length=_IDEMPOTENCY_MAX
        ),
    ) -> dict[str, Any]:
        session = _mutate(state, request)
        project = _owned_project(state, session, pid)
        if not files:
            raise V32Error(422, UNPROCESSABLE, "请至少选择一个 PDF 文件")
        payloads: list[tuple[str, bytes]] = []
        for upload in files:
            name = str(getattr(upload, "filename", "") or "document.pdf")
            payloads.append((name, await upload.read()))
        body_key = {
            "expected_revision": int(expected_revision),
            "files": [(name, len(data)) for name, data in payloads],
        }
        replay = _replay(
            state, session, project_id=pid, endpoint="papers_import", key=idempotency_key, body=body_key
        )
        if replay is not None:
            return _import_batch(state, project, str(replay["import_id"]))
        state.store.check_revision(project, int(expected_revision))

        from litsearch.v32.pdf_import import plan_batch

        plan_batch([(name, len(data)) for name, data in payloads])

        import_id = hashlib.sha256(
            f"{session.owner_id}|{pid}|{idempotency_key}".encode("utf-8")
        ).hexdigest()[:32]
        items: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for name, data in payloads:
            digest = content_hash(data)
            reused = digest in (project.doc.get("attachments") or {})
            attachment = state.store.add_attachment(project, data, name)
            item_id = str(attachment["attachment_id"])
            order.append(item_id)
            items[item_id] = {
                "item_id": item_id,
                "attachment_id": str(attachment["attachment_id"]),
                "display_name": str(attachment["display_name"]),
                "content_hash": str(attachment["content_hash"]),
                "status": "uploaded",
                "paper_id": None,
                "match_candidates": [],
                "match_score": None,
                "error_code": None,
                "retryable": False,
                "size_bytes": int(attachment.get("size") or 0),
                "reused": bool(reused),
            }
        record = {
            "import_id": import_id,
            "project_id": pid,
            "expected_revision": int(expected_revision),
            "item_ids": order,
            "items": items,
            "created_at": iso_now(),
            "updated_at": iso_now(),
            "job_id": "",
        }
        project.doc.setdefault("imports", {})[import_id] = record
        state.store.save(project)
        job = state.jobs.submit(
            project, "import", stage=str(JobStage.PARSING), import_id=import_id, priority=True,
            session_ref=session.session_id,
        )
        record["job_id"] = job.job_id
        state.store.save(project)
        payload = _import_batch(state, project, import_id)
        _remember(
            state, session, project_id=pid, endpoint="papers_import", key=idempotency_key,
            body=body_key, response=payload,
        )
        return payload

    @router.get("/projects/{pid}/imports/{import_id}")
    def get_import(request: Request, pid: str, import_id: str) -> dict[str, Any]:
        session = _session(state, request)
        project = _owned_project(state, session, pid)
        return _import_batch(state, project, import_id)

    @router.post("/projects/{pid}/imports/{import_id}/resolve")
    def resolve_import(
        request: Request,
        pid: str,
        import_id: str,
        body: ResolveBody,
        idempotency_key: str = Header(
            ..., alias="Idempotency-Key", min_length=_IDEMPOTENCY_MIN, max_length=_IDEMPOTENCY_MAX
        ),
    ) -> dict[str, Any]:
        session = _mutate(state, request)
        project = _owned_project(state, session, pid)
        body_key = body.model_dump()
        replay = _replay(
            state, session, project_id=pid, endpoint="resolve_import", key=idempotency_key, body=body_key
        )
        if replay is not None:
            return _import_batch(state, project, import_id)
        record = (project.doc.get("imports") or {}).get(import_id)
        if record is None:
            raise V32Error(NOT_FOUND, "import_not_found", "导入批次不存在")
        item = (record.get("items") or {}).get(body.item_id)
        if item is None:
            raise V32Error(NOT_FOUND, "import_item_not_found", "导入条目不存在")
        state.store.check_revision(project, body.expected_revision)
        candidates = {str(row.get("candidate_id")): row for row in item.get("match_candidates") or []}
        if body.action == "match":
            if not body.candidate_id:
                raise V32Error(422, UNPROCESSABLE, "选择匹配时必须提供候选文献")
            if body.candidate_id not in candidates:
                raise V32Error(422, UNPROCESSABLE, "候选文献不属于该条目", {"item_ids": [str(body.item_id)]})
            # The uncertain file already has a local placeholder record. Confirming a
            # candidate must not leave the placeholder behind as a duplicate: attach the
            # candidate as a paper, then fold the placeholder into it (aliases,
            # attachments and judgements move, the old id stays a redirect).
            candidate = candidates[body.candidate_id]
            kept_id = state.store.resolve_paper_id(project, str(body.candidate_id))
            if kept_id is None:
                kept_id = state.store.upsert_paper(
                    project,
                    {
                        "title": str(candidate.get("title") or ""),
                        "authors": list(candidate.get("authors") or []),
                        "year": candidate.get("year"),
                        "doi": candidate.get("doi"),
                        "aliases": [str(body.candidate_id)],
                    },
                )
            placeholder = str(item.get("paper_id") or "")
            if placeholder and placeholder != kept_id and placeholder in project.doc["papers"]:
                kept_id = state.store.merge_identity(project, kept_id, placeholder)
            item["status"] = "matched"
            item["paper_id"] = kept_id
            item["match_score"] = candidate.get("match_score")
            item["error_code"] = None
            item["retryable"] = False
        else:
            if body.candidate_id:
                raise V32Error(422, UNPROCESSABLE, "保留本地条目时不能指定候选文献")
            paper_id = state.store.resolve_paper_id(project, str(item.get("paper_id") or ""))
            if paper_id is None:
                paper_id = _paper_from_parsed_evidence(state, project, item)
            item["status"] = "local_seed"
            item["paper_id"] = paper_id
            item["error_code"] = None
            item["retryable"] = False
        record["updated_at"] = iso_now()
        state.store.save(project, touch_revision=True)
        payload = _import_batch(state, project, import_id)
        _remember(
            state, session, project_id=pid, endpoint="resolve_import", key=idempotency_key,
            body=body_key, response=payload,
        )
        return payload

    @router.post("/projects/{pid}/imports/{import_id}/retry", status_code=202)
    def retry_import(
        request: Request,
        pid: str,
        import_id: str,
        body: RetryBody,
        idempotency_key: str = Header(
            ..., alias="Idempotency-Key", min_length=_IDEMPOTENCY_MIN, max_length=_IDEMPOTENCY_MAX
        ),
    ) -> dict[str, Any]:
        session = _mutate(state, request)
        project = _owned_project(state, session, pid)
        body_key = body.model_dump()
        replay = _replay(
            state, session, project_id=pid, endpoint="retry_import", key=idempotency_key, body=body_key
        )
        if replay is not None:
            return _import_batch(state, project, import_id)
        record = (project.doc.get("imports") or {}).get(import_id)
        if record is None:
            raise V32Error(NOT_FOUND, "import_not_found", "导入批次不存在")
        state.store.check_revision(project, body.expected_revision)
        requested = [str(value) for value in body.item_ids]
        refused = [
            item_id
            for item_id in requested
            if (record.get("items") or {}).get(item_id) is None
            or not record["items"][item_id].get("retryable")
        ]
        if refused:
            raise V32Error(422, UNPROCESSABLE, "只能重试标记为可重试的条目", {"item_ids": refused})
        for item_id in requested:
            record["items"][item_id]["status"] = "uploaded"
            record["items"][item_id]["error_code"] = None
        job = state.jobs.submit(
            project, "import", stage=str(JobStage.PARSING), import_id=import_id, priority=True,
            session_ref=session.session_id,
        )
        record["job_id"] = job.job_id
        record["updated_at"] = iso_now()
        state.store.save(project)
        payload = _import_batch(state, project, import_id)
        _remember(
            state, session, project_id=pid, endpoint="retry_import", key=idempotency_key,
            body=body_key, response=payload,
        )
        return payload

    # -- zotero -----------------------------------------------------------

    @router.post("/projects/{pid}/zotero/import", status_code=202)
    def zotero_import(
        request: Request,
        pid: str,
        body: ZoteroImportBody,
        idempotency_key: str = Header(
            ..., alias="Idempotency-Key", min_length=_IDEMPOTENCY_MIN, max_length=_IDEMPOTENCY_MAX
        ),
    ) -> dict[str, Any]:
        session = _mutate(state, request)
        project = _owned_project(state, session, pid)
        body_key = body.model_dump()
        replay = _replay(
            state, session, project_id=pid, endpoint="zotero_import", key=idempotency_key, body=body_key
        )
        if replay is not None:
            return _project("JobAccepted", replay)
        state.store.check_revision(project, body.expected_revision)
        if not state.zotero_ready():
            raise V32Error(
                503,
                CAPABILITY_UNAVAILABLE,
                "未配置 Zotero 凭据，无法从 Zotero 导入",
                {"capability": "zotero:web_api"},
            )
        job = state.jobs.submit(
            project,
            "zotero_import",
            stage=str(JobStage.RETRIEVING),
            zotero_request=body_key,
            priority=True,
        )
        accepted = _accepted(state, project, job)
        _remember(
            state, session, project_id=pid, endpoint="zotero_import", key=idempotency_key,
            body=body_key, response=accepted,
        )
        return accepted

    @router.post("/projects/{pid}/zotero/export-preview")
    def zotero_export_preview(
        request: Request,
        pid: str,
        body: ExportPreviewBody,
        idempotency_key: str = Header(
            ..., alias="Idempotency-Key", min_length=_IDEMPOTENCY_MIN, max_length=_IDEMPOTENCY_MAX
        ),
    ) -> dict[str, Any]:
        session = _mutate(state, request)
        project = _owned_project(state, session, pid)
        body_key = body.model_dump()
        replay = _replay(
            state, session, project_id=pid, endpoint="zotero_export_preview", key=idempotency_key,
            body=body_key,
        )
        if replay is not None:
            return _project("ExportPreview", replay)
        state.store.check_revision(project, body.expected_revision)
        from litsearch.v32.zotero import build_export_preview

        papers = []
        for paper_id in body.paper_ids:
            canonical = state.store.resolve_paper_id(project, str(paper_id))
            if canonical is None:
                raise V32Error(
                    NOT_FOUND, "paper_not_found", "项目中没有该文献", {"item_ids": [str(paper_id)]}
                )
            record = dict(project.doc["papers"][canonical])
            record["zotero_mapping"] = (project.doc.get("zotero_map") or {}).get(canonical) or {}
            papers.append(record)
        preview = build_export_preview(
            papers,
            {
                "library_type": body.library_type,
                "library_id": body.library_id,
                "collection_key": body.collection_key,
            },
            preview_store=state.previews,
            owner_id=session.owner_id,
            project_id=pid,
            revision=project.revision,
        )
        payload = _project("ExportPreview", preview)
        _remember(
            state, session, project_id=pid, endpoint="zotero_export_preview", key=idempotency_key,
            body=body_key, response=payload,
        )
        return payload

    @router.post("/projects/{pid}/zotero/export", status_code=202)
    def zotero_export(
        request: Request,
        pid: str,
        body: ExportBody,
        idempotency_key: str = Header(
            ..., alias="Idempotency-Key", min_length=_IDEMPOTENCY_MIN, max_length=_IDEMPOTENCY_MAX
        ),
    ) -> dict[str, Any]:
        session = _mutate(state, request)
        project = _owned_project(state, session, pid)
        body_key = body.model_dump()
        replay = _replay(
            state, session, project_id=pid, endpoint="zotero_export", key=idempotency_key, body=body_key
        )
        if replay is not None:
            return _project("JobAccepted", replay)
        state.store.check_revision(project, body.expected_revision)
        if not state.zotero_ready():
            raise V32Error(
                503,
                CAPABILITY_UNAVAILABLE,
                "未配置 Zotero 凭据，无法保存到 Zotero",
                {"capability": "zotero:web_api"},
            )
        consumed = state.previews.consume(
            session.owner_id, pid, body.preview_id, revision=project.revision
        )
        job = state.jobs.submit(
            project,
            "zotero_export",
            stage=str(JobStage.EXPORTING),
            zotero_preview={
                "records": consumed.get("records") or [],
                "target": consumed.get("target") or {},
            },
            priority=True,
        )
        accepted = _accepted(state, project, job)
        _remember(
            state, session, project_id=pid, endpoint="zotero_export", key=idempotency_key,
            body=body_key, response=accepted,
        )
        return accepted

    return router


# ---------------------------------------------------------------------------
# installation
# ---------------------------------------------------------------------------


def install_v32(
    app: Any,
    *,
    data_dir: Any = None,
    store: V32Store | None = None,
    jobs: JobManager | None = None,
    sessions: SessionStore | None = None,
    credentials: Any = None,
    static_dir: Any = None,
    source_manager: Any = None,
    workflow_factory: Any = None,
    ai_adapter: Any = None,
    ai_model_revision: str | None = None,
    zotero_settings: Mapping[str, Any] | None = None,
    autostart: bool = True,
) -> V32State:
    """Wire the v3.2 surface onto ``app`` and return its state object.

    The two mounts are added here, in this order: ``/api/v3`` first, then
    ``/api``.  Registering the second one after the legacy routes is what keeps
    the four shared paths behaving exactly as before.
    """
    root = Path(
        data_dir
        or os.environ.get("LEEXTRACTOR_DATA_DIR", "").strip()
        or Path(__file__).resolve().parents[2] / ".web-data"
    )
    if store is None:
        store = V32Store(root / "v32")
    if jobs is None:
        jobs = JobManager(store, autostart=autostart)
    if sessions is None:
        sessions = SessionStore()
    if credentials is None:
        from litsearch.v32.credentials import CredentialStore

        credentials = CredentialStore()
    from litsearch.v32.zotero import PreviewStore

    state = V32State(
        store=store,
        jobs=jobs,
        sessions=sessions,
        credentials=credentials,
        data_dir=Path(root),
        static_dir=Path(static_dir).resolve() if static_dir else None,
        source_manager=source_manager,
        workflow_factory=workflow_factory,
        ai_adapter=ai_adapter,
        ai_model_revision=ai_model_revision,
        zotero_settings=zotero_settings,
        external_candidates=external_match.lookup if external_match.switch_on() else None,
        previews=PreviewStore(),
    )
    register_handlers(state)
    router = build_router(state)
    app.include_router(router, prefix="/api/v3")
    app.include_router(router, prefix="/api", include_in_schema=False)
    app.state.v32 = state

    legacy_validation = app.exception_handlers.get(RequestValidationError)
    _install_error_handlers(app, legacy_validation)
    return state


def _install_error_handlers(app: Any, legacy_validation: Any) -> None:
    """The closed error envelope, for both v3.2 and legacy statements.

    A statement error on a v3.2 operation is answered with the contract
    envelope; on a legacy path the previous handler is called unchanged, so the
    old surface keeps its old body.
    """

    @app.exception_handler(V32Error)
    async def v32_error_handler(request: Request, exc: V32Error) -> JSONResponse:
        return JSONResponse(error_envelope(exc), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def v32_validation_handler(request: Request, exc: RequestValidationError) -> Any:
        route = request.scope.get("route")
        endpoint = getattr(route, "endpoint", None)
        if str(getattr(endpoint, "__module__", "")).startswith("litsearch.v32"):
            return JSONResponse(error_envelope(V32Error(422, UNPROCESSABLE, "请求参数无效")), status_code=422)
        if legacy_validation is not None:
            return await legacy_validation(request, exc)
        return JSONResponse(
            {"error": {"code": "invalid_request", "message": "请求参数无效", "detail": None}},
            status_code=422,
        )


def v32_from_app(app: Any) -> V32State:
    """The state :func:`install_v32` attached, for tests and embedding apps."""
    state = getattr(getattr(app, "state", None), "v32", None)
    if state is None:
        raise V32Error(503, CAPABILITY_UNAVAILABLE, "v3.2 接口尚未安装")
    return state
