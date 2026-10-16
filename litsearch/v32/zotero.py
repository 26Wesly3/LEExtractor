"""Zotero integration for the v3.2 layer (doc 16, AC-21/AC-22).

Two halves, deliberately separated:

* :class:`ZoteroWebAdapter` speaks to the **official Web API**.  Every request
  goes through an injected transport, so this module owns no socket of its own
  and the whole suite runs without a network.  It never contacts a local
  desktop client: the public server must not reach into the user's own
  machine (doc 16), and a loopback or link-local base URL is refused outright.
* :class:`PreviewStore` + :func:`build_export_preview` / :func:`run_export`
  implement the write discipline: preview first, then an explicit
  confirmation, per-item outcomes, and the official version precondition
  instead of an overwrite.

Identity rules that the implementation is built around:

* ``library_type`` + ``library_id`` + ``item_key`` (+ ``item_version`` and
  ``collection_key``) are the external keys and are preserved verbatim;
* the same library + ``item_key`` updates idempotently; a *different* item
  with the same title is never overwritten;
* an HTTP 200 is not a success report — each item's own outcome is parsed;
* after an uncertain outcome the library is re-read before anything is
  written again, so a timeout cannot create the same item twice.

The live Zotero integration is ``external_blocked`` in this environment: no
credential or endpoint is available to verify a real round trip, so no test
claims one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Callable, Iterable, Mapping, Sequence

from litsearch.identifiers import normalize_doi
from litsearch.v32 import contracts
from litsearch.v32.credentials import redact_text, scrub_error_body
from litsearch.v32.errors import REVISION_CONFLICT, V32Error
from litsearch.v32.types import CapabilityStatus, iso_now, utc_now

__all__ = [
    "SUPPORTED_ITEM_TYPES",
    "PreviewStore",
    "ZoteroResponse",
    "ZoteroTransportFailure",
    "ZoteroTimeout",
    "ZoteroWebAdapter",
    "apply_import",
    "build_export_preview",
    "create_adapter",
    "normalize_item",
    "plan_import",
    "run_export",
]

logger = logging.getLogger(__name__)

#: The official Zotero Web API.  Configurable, but never pointed at a loopback
#: address: the desktop client's Local API belongs to the user's own machine.
ZOTERO_API_BASE = "https://api.zotero.org"
ZOTERO_API_VERSION = "3"

#: Zotero item types this product imports.  Anything else (a bare attachment, a
#: note, an annotation, …) is skipped with a per-item reason rather than being
#: forced into a paper record.
SUPPORTED_ITEM_TYPES = frozenset(
    {
        "journalArticle",
        "conferencePaper",
        "preprint",
        "bookSection",
        "book",
        "report",
        "thesis",
        "manuscript",
        "document",
        "newspaperArticle",
        "magazineArticle",
        "encyclopediaArticle",
        "dictionaryEntry",
        "letter",
    }
)

#: Refused hosts: the server must never reach the user's desktop client or a
#: cloud metadata endpoint.
_FORBIDDEN_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"})
_FORBIDDEN_HOST_SUFFIXES = (".localhost", ".local")
_LINK_LOCAL_PREFIXES = ("169.254.", "fe80:")

_MAX_ERROR_TEXT = 200


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------


@dataclass
class ZoteroResponse:
    """The part of an HTTP response this adapter actually reads."""

    status: int
    body: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        if not self.body:
            return None
        try:
            return json.loads(self.body)
        except ValueError:
            return None


class ZoteroTransportFailure(V32Error):
    """The exchange did not complete; the outcome on the server is unknown."""


class ZoteroTimeout(ZoteroTransportFailure):
    """The exchange timed out."""


class _NoTransport:
    """The transport of an unconfigured adapter: it never opens anything."""

    def request(self, method: str, url: str, **_: Any) -> ZoteroResponse:
        raise V32Error(
            503,
            "zotero_not_configured",
            "未配置 Zotero 凭据或传输通道，无法访问 Zotero Web API。",
        )


class UrllibTransport:
    """Default transport: one ``urllib`` request, no retries of its own.

    Retry policy lives in :class:`ZoteroWebAdapter` so reads and writes can
    differ — a write is reconciled before it is ever repeated.
    """

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Any = None,
        timeout: float | None = None,
    ) -> ZoteroResponse:
        data = None
        if json_body is not None:
            data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                body = response.read().decode("utf-8", "replace")
                return ZoteroResponse(
                    status=int(getattr(response, "status", 200)),
                    body=body,
                    headers={str(k): str(v) for k, v in dict(response.headers).items()},
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
            return ZoteroResponse(
                status=int(exc.code),
                body=raw,
                headers={str(k): str(v) for k, v in dict(exc.headers or {}).items()},
            )
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TimeoutError("zotero transport timeout") from None
            raise OSError(str(exc.reason)) from None


# --------------------------------------------------------------------------
# adapter
# --------------------------------------------------------------------------


class ZoteroWebAdapter:
    """The official Web API adapter.

    Args:
        settings: mapping with ``api_key`` (or ``token``), optional
            ``base_url``, ``library_type``, ``library_id``, ``collection_key``,
            ``timeout_seconds``, ``retries``.
        transport: injected transport with
            ``request(method, url, *, headers, json_body, timeout)``.  Tests
            inject a fake; production uses :class:`UrllibTransport`.
    """

    def __init__(
        self,
        settings: Mapping[str, Any] | None = None,
        *,
        transport: Any | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        settings = dict(settings or {})
        self._api_key = str(
            settings.get("api_key") or settings.get("token") or settings.get("zotero_api_key") or ""
        )
        self.base_url = str(settings.get("base_url") or ZOTERO_API_BASE).rstrip("/")
        _guard_base_url(self.base_url)
        self.library_type = str(settings.get("library_type") or "user")
        self.library_id = str(settings.get("library_id") or "")
        self.collection_key = settings.get("collection_key") or None
        discovery = contracts.discovery()
        self.timeout_seconds = float(
            settings.get("timeout_seconds") or discovery["provider_total_timeout_seconds"]
        )
        self.retries = int(settings.get("retries", discovery["provider_retries"]))
        self._clock = clock or utc_now
        #: An injected transport stands in for a live server; a real adapter
        #: refuses to act without a credential.
        self._injected_transport = transport is not None
        if transport is not None:
            self.transport = transport
        elif self._api_key:
            self.transport = UrllibTransport()
        else:
            # Nothing to authenticate with, so nothing may be opened.
            self.transport = _NoTransport()

    # -- introspection ----------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return f"ZoteroWebAdapter(configured={self.configured}, base_url={self.base_url!r})"

    @property
    def configured(self) -> bool:
        """Whether a credential is available.  No value is ever echoed."""
        return bool(self._api_key)

    def capabilities(self) -> dict[str, Any]:
        """What this adapter can do, and what it deliberately will not do."""
        zotero = contracts.zotero_defaults()
        return {
            "adapter": str(zotero.get("default_adapter") or "web_api"),
            "configured": self.configured,
            "status": str(
                CapabilityStatus.AVAILABLE if self.configured else CapabilityStatus.NOT_CONFIGURED
            ),
            "reason_code": None if self.configured else "api_key_required",
            "library_type": self.library_type or None,
            "library_id": self.library_id or None,
            "write_supported": True,
            "local_desktop_api": False,
            "upload_fulltext_by_default": bool(zotero["upload_fulltext_by_default"]),
            "auto_export": bool(zotero["auto_export"]),
            "attachment_download": "requires_explicit_selection",
            "timeout_seconds": self.timeout_seconds,
            "retries": self.retries,
            "checked_at": iso_now(),
        }

    # -- reads ------------------------------------------------------------

    def list_collections(self, library_type: str, library_id: str) -> list[dict[str, Any]]:
        """Every collection of one library, as ``[{collection_key, name, …}]``."""
        prefix = _library_prefix(library_type, library_id)
        response = self._request("GET", f"{prefix}/collections?limit=100")
        payload = response.json() or []
        collections: list[dict[str, Any]] = []
        for entry in payload if isinstance(payload, list) else []:
            data = _mapping(entry.get("data"))
            key = str(entry.get("key") or data.get("key") or "")
            if not key:
                continue
            collections.append(
                {
                    "collection_key": key,
                    "name": str(data.get("name") or ""),
                    "parent_collection_key": data.get("parentCollection") or None,
                    "library_type": library_type,
                    "library_id": str(library_id),
                    "version": _optional_int(entry.get("version")),
                }
            )
        return collections

    def fetch_items(
        self, library_type: str, library_id: str, item_keys: Sequence[str]
    ) -> list[dict[str, Any]]:
        """Fetch the named items, in the API's order, normalised."""
        keys = [str(key) for key in item_keys if str(key).strip()]
        if not keys:
            return []
        prefix = _library_prefix(library_type, library_id)
        query = urllib.parse.urlencode({"itemKey": ",".join(keys), "limit": max(len(keys), 1)})
        response = self._request("GET", f"{prefix}/items?{query}")
        payload = response.json() or []
        items = [
            normalize_item(entry, library_type=library_type, library_id=str(library_id))
            for entry in (payload if isinstance(payload, list) else [])
            if isinstance(entry, Mapping)
        ]
        return items

    # -- writes -----------------------------------------------------------

    def write_items(
        self,
        library_type: str,
        library_id: str,
        items: Sequence[Mapping[str, Any]],
        *,
        if_unmodified_since: int | None = None,
    ) -> list[dict[str, Any]]:
        """Write items, returning one ``$defs/ZoteroItemResult`` per input item.

        ``items`` entries carry ``paper_id`` (echoed back), optional
        ``item_key``/``item_version`` (an update) and the Zotero payload under
        ``data`` (or ``payload``).
        """
        prepared = [_write_item(item) for item in items]
        if not prepared:
            return []
        prefix = _library_prefix(library_type, library_id)
        headers: dict[str, str] = {}
        if if_unmodified_since is not None:
            headers["If-Unmodified-Since-Version"] = str(int(if_unmodified_since))

        attempt = 0
        results: dict[str, dict[str, Any]] = {}
        pending = prepared
        while pending:
            attempt += 1
            try:
                response = self._request(
                    "POST",
                    f"{prefix}/items",
                    headers=headers,
                    json_body=[_request_payload(item) for item in pending],
                )
            except ZoteroTransportFailure as exc:
                reconciled, missing, authoritative = self._reconcile(
                    library_type, library_id, pending
                )
                results.update(reconciled)
                if not missing:
                    pending = []
                    break
                # Repeating a create is only allowed after the library proved
                # the item is not there; otherwise the outcome stays unknown.
                repeatable = (
                    authoritative
                    and attempt <= self.retries
                    and all(item["item_key"] is None for item in missing)
                )
                if repeatable:
                    pending = missing
                    continue
                for item in missing:
                    results[item["paper_id"]] = _item_result(
                        item["paper_id"],
                        "unknown_outcome",
                        item["item_key"],
                        item["item_version"],
                        exc.code,
                    )
                pending = []
                break
            except V32Error as exc:
                # A rejected batch is still a per-item answer: a version
                # conflict means "do not overwrite", everything else is a
                # failure for each item in the batch.
                status = "conflict" if exc.code == "zotero_version_conflict" else "failed"
                for item in pending:
                    results[item["paper_id"]] = _item_result(
                        item["paper_id"],
                        status,
                        item["item_key"],
                        item["item_version"],
                        exc.code,
                    )
                pending = []
                break
            for result in _parse_write_response(response, pending):
                results[result["paper_id"]] = result
            pending = []

        return [results[item["paper_id"]] for item in prepared if item["paper_id"] in results]

    # -- internals --------------------------------------------------------

    def _reconcile(
        self, library_type: str, library_id: str, items: Sequence[Mapping[str, Any]]
    ) -> tuple[dict[str, dict[str, Any]], list[Mapping[str, Any]], bool]:
        """Ask the library what actually landed, before repeating anything.

        Returns ``(results, missing, authoritative)``.  ``authoritative`` means
        every read succeeded, which is the only state in which "it is not
        there" may license another write.
        """
        results: dict[str, dict[str, Any]] = {}
        missing: list[Mapping[str, Any]] = []
        authoritative = True
        for item in items:
            paper_id = str(item["paper_id"])
            try:
                found = self._lookup_item(library_type, library_id, item)
            except (ZoteroTransportFailure, V32Error):
                authoritative = False
                missing.append(item)
                continue
            if found is None:
                missing.append(item)
                continue
            results[paper_id] = _item_result(
                paper_id,
                "successful",
                found.get("item_key"),
                found.get("item_version"),
                None,
            )
        return results, missing, authoritative

    def _lookup_item(
        self, library_type: str, library_id: str, item: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        if item.get("item_key"):
            fetched = self.fetch_items(library_type, library_id, [str(item["item_key"])])
            return fetched[0] if fetched else None
        data = _mapping(item.get("data"))
        query = normalize_doi(data.get("DOI")) or str(data.get("title") or "").strip()
        if not query:
            return None
        prefix = _library_prefix(library_type, library_id)
        params = urllib.parse.urlencode({"q": query, "limit": 25})
        response = self._request("GET", f"{prefix}/items?{params}")
        payload = response.json() or []
        for entry in payload if isinstance(payload, list) else []:
            candidate = normalize_item(
                entry, library_type=library_type, library_id=str(library_id)
            )
            if _same_work(candidate, data):
                return candidate
        return None

    def _request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Any = None,
        timeout: float | None = None,
    ) -> ZoteroResponse:
        """One authenticated request.  A failure carries no response body."""
        if not self.configured and not self._injected_transport:
            raise V32Error(
                503,
                "zotero_not_configured",
                "未配置 Zotero 凭据，无法访问 Zotero Web API。",
            )
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        _guard_base_url(url)
        merged = {
            "Accept": "application/json",
            "Zotero-API-Version": ZOTERO_API_VERSION,
            "User-Agent": "LEExtractor/3.2 (+zotero-web-api)",
        }
        if self._api_key:
            merged["Zotero-API-Key"] = self._api_key
        merged.update(dict(headers or {}))

        attempt = 0
        while True:
            attempt += 1
            try:
                raw = self.transport.request(
                    method,
                    url,
                    headers=merged,
                    json_body=json_body,
                    timeout=timeout or self.timeout_seconds,
                )
            except TimeoutError as exc:
                if method == "GET" and attempt <= self.retries:
                    logger.debug(
                        "zotero_read_retry",
                        extra={"event": "zotero_read_retry", "component": "zotero", "reason": "timeout"},
                    )
                    continue
                raise ZoteroTimeout(
                    503, "zotero_timeout", "访问 Zotero 超时，结果未知，需先核对再重试。"
                ) from exc
            except OSError as exc:
                if method == "GET" and attempt <= self.retries:
                    logger.debug(
                        "zotero_read_retry",
                        extra={"event": "zotero_read_retry", "component": "zotero", "reason": "transport"},
                    )
                    continue
                raise ZoteroTransportFailure(
                    503, "zotero_transport_error", "访问 Zotero 失败，结果未知。"
                ) from exc
            response = _coerce_response(raw)
            if response.status >= 400:
                raise _http_error(response)
            return response


# --------------------------------------------------------------------------
# export preview
# --------------------------------------------------------------------------


@dataclass
class _Preview:
    preview_id: str
    owner_id: str
    project_id: str
    revision: int
    target: dict[str, Any]
    items: list[dict[str, Any]]
    payloads: list[dict[str, Any] | None]
    item_hash: str
    created_at: datetime
    expires_at: datetime
    consumed: bool = False


class PreviewStore:
    """Short-lived, single-use export previews bound to owner + revision.

    The expiry comes from ``zotero.export_preview_ttl_seconds`` (doc 16:
    "预览绑定当前用户、项目版本、条目 hash，10 分钟有效").
    """

    def __init__(
        self,
        *,
        ttl_seconds: int | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.ttl_seconds = int(
            contracts.zotero_defaults()["export_preview_ttl_seconds"]
            if ttl_seconds is None
            else ttl_seconds
        )
        self._clock = clock or utc_now
        self._previews: dict[str, _Preview] = {}

    def create(
        self,
        owner_id: str,
        project_id: str,
        revision: int,
        target: Mapping[str, Any],
        items: Sequence[Mapping[str, Any]],
        *,
        payloads: Sequence[Mapping[str, Any] | None] | None = None,
    ) -> dict[str, Any]:
        """Record a preview and return the ``$defs/ExportPreview`` document.

        ``payloads`` carries the per-item write material that the export will
        use: ``{"data": <Zotero item data>, "item_key": …, "item_version": …}``.
        It is kept in the store only — the preview document has a closed schema
        and never carries the metadata or the version precondition.
        """
        owner = _require_id(owner_id, "owner_id")
        project = _require_id(project_id, "project_id")
        expected_revision = _require_revision(revision)
        resolved_target = _normalise_target(target)
        resolved_items = [_preview_item(item, index) for index, item in enumerate(items)]
        resolved_payloads: list[dict[str, Any] | None] = (
            [dict(item) if item is not None else None for item in payloads]
            if payloads is not None
            else [None] * len(resolved_items)
        )
        if len(resolved_payloads) != len(resolved_items):
            raise V32Error(400, "preview_payload_mismatch", "预览条目与导出内容数量不一致。")

        now = self._clock()
        preview_id = uuid.uuid4().hex
        record = _Preview(
            preview_id=preview_id,
            owner_id=owner,
            project_id=project,
            revision=expected_revision,
            target=resolved_target,
            items=resolved_items,
            payloads=resolved_payloads,
            item_hash=_hash_json(resolved_items),
            created_at=now,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
        )
        self._previews[preview_id] = record
        self._drop_expired(now)
        return _preview_document(record)

    def consume(
        self, owner_id: str, project_id: str, preview_id: str, *, revision: int
    ) -> dict[str, Any]:
        """Validate and burn a preview.

        Returns the hand-off record::

            {"preview": <ExportPreview>, "preview_id": ..., "records": [...],
             "target": {...}}

        ``records`` carries the metadata payloads and the per-item version
        preconditions; ``preview`` is the schema-shaped document.
        """
        owner = _require_id(owner_id, "owner_id")
        project = _require_id(project_id, "project_id")
        expected_revision = _require_revision(revision)
        record = self._previews.get(str(preview_id))
        # A preview the caller may not see is reported as absent: the response
        # must not confirm that someone else's preview exists.
        if record is None or record.owner_id != owner or record.project_id != project:
            raise V32Error(404, "preview_not_found", "预览不存在。")
        now = self._clock()
        if now >= record.expires_at:
            self._previews.pop(record.preview_id, None)
            raise V32Error(422, "preview_expired", "预览已过期，请重新生成预览。")
        if record.consumed:
            raise V32Error(409, "preview_already_used", "该预览已被使用。")
        if _hash_json(record.items) != record.item_hash:
            raise V32Error(409, "preview_items_changed", "预览条目已变化，请重新生成。")
        if record.revision != expected_revision:
            raise V32Error(
                409,
                REVISION_CONFLICT,
                "项目版本已变化，请基于最新版本重新生成预览。",
                details={"current_revision": record.revision},
            )
        record.consumed = True
        return {
            "preview": _preview_document(record),
            "preview_id": record.preview_id,
            "revision": record.revision,
            "target": dict(record.target),
            "records": [_handoff_record(record, index) for index in range(len(record.items))],
        }

    def _drop_expired(self, now: datetime) -> int:
        stale = [key for key, record in self._previews.items() if now >= record.expires_at]
        for key in stale:
            self._previews.pop(key, None)
        return len(stale)


def _handoff_record(record: _Preview, index: int) -> dict[str, Any]:
    """One item of the internal export hand-off (never a wire document).

    ``item_key``/``item_version`` are the recorded external keys of an update
    and become the official version precondition; ``payload`` is the Zotero
    item data (metadata plus the user's tags and notes).
    """
    item = record.items[index]
    entry = record.payloads[index] if index < len(record.payloads) else None
    payload = _mapping(entry.get("data")) if isinstance(entry, Mapping) else {}
    recorded_key = item["existing_item_key"]
    return {
        "paper_id": item["paper_id"],
        "action": item["action"],
        "existing_item_key": recorded_key,
        "title": item["title"],
        "payload": payload or None,
        "item_key": (entry.get("item_key") if isinstance(entry, Mapping) else None) or recorded_key,
        "item_version": _optional_int(entry.get("item_version")) if isinstance(entry, Mapping) else None,
        "collection_key": record.target["target_collection_key"],
    }


def build_export_preview(
    project_papers: Sequence[Mapping[str, Any]],
    library: Mapping[str, Any],
    *,
    preview_store: PreviewStore,
    owner_id: str,
    project_id: str,
    revision: int,
) -> dict[str, Any]:
    """Preview what "保存至 Zotero" would do, item by item.

    Metadata plus the user's own tags and notes only: full text is never part
    of the payload (``zotero.upload_fulltext_by_default`` is false).
    """
    target = _normalise_target(library)
    items: list[dict[str, Any]] = []
    payloads: list[dict[str, Any] | None] = []
    seen_titles: dict[str, str] = {}
    for paper in project_papers:
        paper_id = _require_id(_pick(paper, ("paper_id", "id")), "paper_id")
        title = str(_pick(paper, ("title",)) or "")
        payload = _export_payload(paper, target)
        action, existing_key, reason = _preview_action(paper, target, payload, seen_titles)
        recorded = _recorded_zotero(paper)
        items.append(
            {
                "paper_id": paper_id,
                "action": action,
                "existing_item_key": existing_key,
                "title": title,
                "reason": reason,
            }
        )
        if action in {"create", "update"}:
            payloads.append(
                {
                    "data": payload,
                    "item_key": str(recorded["item_key"]) if recorded and recorded.get("item_key") else None,
                    "item_version": _optional_int(recorded.get("item_version")) if recorded else None,
                }
            )
        else:
            payloads.append(None)
        key = _normalise_title(title)
        if key:
            seen_titles.setdefault(key, paper_id)
    return preview_store.create(
        owner_id, project_id, revision, target, items, payloads=payloads
    )


def run_export(
    preview: Mapping[str, Any], adapter: Any, *, confirm: bool
) -> list[dict[str, Any]]:
    """Write a consumed preview, reporting one outcome per item.

    An unconfirmed request is refused, a conflicting item is never written,
    and an unchanged item is not sent at all.
    """
    if confirm is not True:
        raise V32Error(422, "export_not_confirmed", "保存到 Zotero 需要显式确认。")
    records = list(preview.get("records") or [])
    target = _normalise_target(preview.get("target") or {})
    results: dict[str, dict[str, Any]] = {}
    to_write: list[dict[str, Any]] = []
    for record in records:
        paper_id = _require_id(record.get("paper_id"), "paper_id")
        action = str(record.get("action") or "")
        if action == "unchanged":
            results[paper_id] = _item_result(
                paper_id, "unchanged", record.get("item_key"), record.get("item_version"), None
            )
        elif action == "conflict":
            results[paper_id] = _item_result(
                paper_id, "conflict", record.get("item_key"), record.get("item_version"), "preview_conflict"
            )
        else:
            payload = record.get("payload")
            if not isinstance(payload, Mapping):
                results[paper_id] = _item_result(
                    paper_id, "failed", None, None, "preview_payload_missing"
                )
                continue
            to_write.append(
                {
                    "paper_id": paper_id,
                    "item_key": record.get("item_key") if action == "update" else None,
                    "item_version": record.get("item_version") if action == "update" else None,
                    "collection_key": record.get("collection_key"),
                    "data": dict(payload),
                }
            )
    if to_write:
        if adapter is None:
            raise V32Error(
                503, "zotero_not_configured", "未配置 Zotero 适配器，无法写入。"
            )
        produced = adapter.write_items(
            target["library_type"],
            target["library_id"],
            to_write,
            if_unmodified_since=None,
        )
        for result in produced or []:
            paper_id = str(result.get("paper_id") or "")
            if paper_id:
                results[paper_id] = _item_result(
                    paper_id,
                    str(result.get("status") or "failed"),
                    result.get("item_key"),
                    result.get("item_version"),
                    result.get("error_code"),
                )
    ordered: list[dict[str, Any]] = []
    for record in records:
        paper_id = str(record.get("paper_id") or "")
        ordered.append(
            results.get(
                paper_id,
                _item_result(paper_id, "failed", None, None, "unknown_outcome"),
            )
        )
    return ordered


def create_adapter(settings: Mapping[str, Any] | None = None) -> ZoteroWebAdapter:
    """Build the Web API adapter from injected settings.

    No credential of any kind is read from the environment here and none is
    written anywhere: an unconfigured adapter simply refuses to act.
    """
    return ZoteroWebAdapter(settings)


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------


def normalize_item(
    raw: Mapping[str, Any],
    *,
    library_type: str | None = None,
    library_id: str | None = None,
) -> dict[str, Any]:
    """Normalise one Zotero API item, preserving its external keys."""
    entry = _mapping(raw)
    data = _mapping(entry.get("data")) or entry
    meta = _mapping(entry.get("meta"))
    library = _mapping(entry.get("library"))
    key = str(entry.get("key") or data.get("key") or meta.get("key") or "")
    collections = data.get("collections") if isinstance(data.get("collections"), list) else []
    return {
        "library_type": str(library_type or library.get("type") or ""),
        "library_id": str(library_id or library.get("id") or ""),
        "item_key": key,
        "item_version": _optional_int(entry.get("version") or meta.get("version")),
        "collection_key": str(collections[0]) if collections else None,
        "item_type": str(data.get("itemType") or entry.get("itemType") or ""),
        "title": str(data.get("title") or ""),
        "doi": normalize_doi(data.get("DOI") or data.get("doi")),
        "year": _year_from(data.get("date")),
        "authors": _authors(data.get("creators")),
        "tags": _tags(data.get("tags")),
        "notes": _notes(data),
        "attachments": _attachments(entry, data),
    }


def plan_import(
    existing_papers: Sequence[Mapping[str, Any]],
    remote_items: Sequence[Mapping[str, Any]],
    *,
    library_type: str,
    library_id: str,
    selected_keys: Sequence[str] | None = None,
    include_attachments: bool = False,
    selected_attachment_keys: Sequence[str] = (),
) -> dict[str, Any]:
    """Decide, per user-selected Zotero item, what importing it would do.

    Only user-selected entries are considered (doc 16); an unsupported item
    type or an unselected attachment is skipped with its own reason.  The
    decision is pure — nothing is fetched and nothing is stored.
    """
    library = _normalise_target(
        {"library_type": library_type, "library_id": library_id, "collection_key": None}
    )
    selected = {str(key) for key in selected_keys} if selected_keys is not None else None
    attachment_selection = {str(key) for key in selected_attachment_keys}
    known: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    titles: dict[str, str] = {}
    for paper in existing_papers:
        recorded = _recorded_zotero(paper)
        if not recorded or not recorded.get("item_key"):
            continue
        known[
            (
                str(recorded.get("library_type") or ""),
                str(recorded.get("library_id") or ""),
                str(recorded["item_key"]),
            )
        ] = paper
        title_key = _normalise_title(str(_pick(paper, ("title",)) or ""))
        if title_key:
            titles.setdefault(title_key, str(_pick(paper, ("paper_id", "id")) or ""))

    decisions: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for raw in remote_items:
        item = normalize_item(raw, library_type=library["library_type"], library_id=library["library_id"])
        item_key = item["item_key"]
        decision: dict[str, Any] = {
            "item_key": item_key,
            "title": item["title"],
            "item_type": item["item_type"],
            "item_version": item["item_version"],
            "paper_id": None,
            "action": "skipped",
            "reason": "",
            "external": {
                "library_type": item["library_type"],
                "library_id": item["library_id"],
                "item_key": item_key,
                "item_version": item["item_version"],
                "collection_key": item["collection_key"],
            },
            "attachments": _plan_attachments(
                item, include_attachments=include_attachments, selected=attachment_selection
            ),
        }
        if not item_key:
            decision["reason"] = "missing_item_key"
        elif selected is not None and item_key not in selected:
            decision["reason"] = "not_selected_by_user"
        elif item["item_type"] not in SUPPORTED_ITEM_TYPES:
            decision["reason"] = f"unsupported_item_type:{item['item_type'] or 'unknown'}"
        else:
            existing = known.get(
                (item["library_type"], item["library_id"], item_key)
            )
            if existing is not None:
                decision["paper_id"] = str(_pick(existing, ("paper_id", "id")) or "")
                recorded_version = _optional_int(
                    (_recorded_zotero(existing) or {}).get("item_version")
                )
                if recorded_version is not None and recorded_version == item["item_version"]:
                    decision["action"] = "unchanged"
                    decision["reason"] = "same_item_version"
                else:
                    decision["action"] = "update"
                    decision["reason"] = "idempotent_update_same_item_key"
            else:
                title_key = _normalise_title(item["title"])
                if title_key and title_key in titles:
                    decision["paper_id"] = titles[title_key]
                    decision["action"] = "conflict"
                    decision["reason"] = "same_title_different_item"
                else:
                    decision["action"] = "create"
                    decision["reason"] = "new_item_key"
                    if title_key:
                        titles.setdefault(title_key, "")
        counts[decision["action"]] = counts.get(decision["action"], 0) + 1
        decisions.append(decision)
    return {
        "library_type": library["library_type"],
        "library_id": library["library_id"],
        "items": decisions,
        "counts": counts,
    }


def apply_import(
    existing_papers: Sequence[Mapping[str, Any]],
    plan: Mapping[str, Any],
    *,
    id_factory: Callable[[Mapping[str, Any]], str] | None = None,
) -> dict[str, Any]:
    """Apply an import plan to a copy of the project's papers.

    Creates get an opaque id from ``id_factory`` (default: a fresh uuid), never
    one composed from an external key.  Existing papers are replaced in place
    for ``update``, so a repeated import cannot duplicate an item.
    """
    papers = [dict(paper) for paper in existing_papers]
    index: dict[tuple[str, str, str], int] = {}
    for position, paper in enumerate(papers):
        recorded = _recorded_zotero(paper)
        if recorded and recorded.get("item_key"):
            index[
                (
                    str(recorded.get("library_type") or ""),
                    str(recorded.get("library_id") or ""),
                    str(recorded["item_key"]),
                )
            ] = position
    created = 0
    updated = 0
    for decision in plan.get("items") or []:
        action = str(decision.get("action") or "")
        external = dict(decision.get("external") or {})
        if not external.get("item_key") or action not in {"create", "update"}:
            continue
        slot = index.get(
            (external["library_type"], external["library_id"], external["item_key"])
        )
        if slot is None:
            paper_id = (
                str(id_factory(decision))
                if id_factory is not None
                else uuid.uuid4().hex
            )
            papers.append(
                {
                    "paper_id": paper_id,
                    "title": str(decision.get("title") or ""),
                    "zotero": external,
                    "import_action": "create",
                }
            )
            index[
                (external["library_type"], external["library_id"], external["item_key"])
            ] = len(papers) - 1
            created += 1
        else:
            papers[slot]["zotero"] = external
            papers[slot]["import_action"] = "update"
            updated += 1
    return {"papers": papers, "created": created, "updated": updated}


# --------------------------------------------------------------------------
# helpers: requests
# --------------------------------------------------------------------------


def _library_prefix(library_type: str, library_id: str) -> str:
    if library_type not in _library_types():
        raise V32Error(400, "invalid_library_type", "library_type 只能是 user 或 group。")
    identifier = _require_id(library_id, "library_id")
    return f"/{'users' if library_type == 'user' else 'groups'}/{urllib.parse.quote(identifier)}"


@lru_cache(maxsize=1)
def _library_types() -> tuple[str, ...]:
    schema = contracts.domain_schema()
    return tuple(schema["$defs"]["ZoteroImportRequest"]["properties"]["library_type"]["enum"])


def _guard_base_url(url: str) -> None:
    """Refuse a loopback / link-local / metadata endpoint."""
    try:
        parsed = urllib.parse.urlparse(str(url))
    except ValueError:
        raise V32Error(400, "invalid_base_url", "base_url 无效。") from None
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        raise V32Error(400, "invalid_base_url", "base_url 必须是 https。")
    if (
        host in _FORBIDDEN_HOSTS
        or host.endswith(_FORBIDDEN_HOST_SUFFIXES)
        or host.startswith(_LINK_LOCAL_PREFIXES)
        or not host
    ):
        raise V32Error(
            400,
            "zotero_local_api_forbidden",
            "服务器不访问用户本机的 Zotero 服务，仅使用官方 Web API。",
        )


def _coerce_response(raw: Any) -> ZoteroResponse:
    if isinstance(raw, ZoteroResponse):
        return raw
    if isinstance(raw, (tuple, list)) and len(raw) == 3:
        status, headers, body = raw
        return ZoteroResponse(
            status=int(status), body=_body_text(body), headers=dict(headers or {})
        )
    status = getattr(raw, "status", None) or getattr(raw, "status_code", None)
    if status is None:
        raise V32Error(500, "transport_invalid", "传输通道返回了无法识别的响应。")
    body = getattr(raw, "body", None)
    if body is None:
        body = getattr(raw, "text", None)
    if body is None:
        payload = getattr(raw, "json", None)
        body = payload() if callable(payload) else ""
    headers = getattr(raw, "headers", None)
    return ZoteroResponse(
        status=int(status),
        body=_body_text(body),
        headers={str(k): str(v) for k, v in dict(headers or {}).items()},
    )


def _body_text(body: Any) -> str:
    if body is None:
        return ""
    if isinstance(body, bytes):
        return body.decode("utf-8", "replace")
    if isinstance(body, str):
        return body
    try:
        return json.dumps(body, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(body)


def _http_error(response: ZoteroResponse) -> V32Error:
    """Map an HTTP status onto the envelope without echoing the body.

    ``ZoteroTimeout``/``ZoteroTransportFailure`` handle the no-response case;
    this only sees a response, and the body is discarded (see
    :func:`litsearch.v32.credentials.scrub_error_body`).
    """
    status = int(response.status)
    body_note = scrub_error_body(response.body)
    detail = f"（{body_note}）" if response.body else ""
    if status in (401, 403):
        return V32Error(403, "zotero_forbidden", f"Zotero 拒绝了该请求{detail}")
    if status == 404:
        return V32Error(404, "zotero_not_found", f"Zotero 资源不存在{detail}")
    if status in (412, 428):
        return V32Error(409, "zotero_version_conflict", "Zotero 条目版本冲突，未覆盖。")
    if status == 409:
        return V32Error(409, "zotero_conflict", f"Zotero 报告冲突{detail}")
    if status == 429:
        return V32Error(429, "zotero_rate_limited", "Zotero 请求过于频繁。", retryable=True)
    if status >= 500:
        return V32Error(503, "zotero_unavailable", f"Zotero 服务不可用{detail}", retryable=True)
    return V32Error(400, "zotero_request_rejected", f"Zotero 拒绝了该请求（HTTP {status}）。")


def _write_item(item: Mapping[str, Any]) -> dict[str, Any]:
    data = _mapping(item.get("data")) or _mapping(item.get("payload")) or _mapping(item)
    return {
        "paper_id": _require_id(item.get("paper_id") or item.get("id"), "paper_id"),
        "item_key": item.get("item_key") or item.get("existing_item_key") or None,
        "item_version": _optional_int(item.get("item_version")),
        "collection_key": item.get("collection_key") or None,
        "data": dict(data),
    }


def _request_payload(item: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(item["data"])
    if item.get("collection_key"):
        collections = list(data.get("collections") or [])
        if item["collection_key"] not in collections:
            collections.append(item["collection_key"])
        data["collections"] = collections
    payload: dict[str, Any] = {"data": data}
    if item.get("item_key"):
        payload["key"] = item["item_key"]
        if item.get("item_version") is not None:
            payload["version"] = item["item_version"]
    return payload


def _parse_write_response(
    response: ZoteroResponse, items: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    payload = response.json()
    if not isinstance(payload, Mapping):
        # A 2xx without the documented batch document proves nothing per item.
        return [
            _item_result(item["paper_id"], "unknown_outcome", item["item_key"], item["item_version"], "unparsable_response")
            for item in items
        ]
    successful = _mapping(payload.get("successful"))
    unchanged = _mapping(payload.get("unchanged"))
    failed = _mapping(payload.get("failed"))
    results: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        key = str(index)
        paper_id = str(item["paper_id"])
        if key in successful:
            entry = _mapping(successful[key])
            results.append(
                _item_result(
                    paper_id,
                    "successful",
                    entry.get("key") or item["item_key"],
                    _optional_int(entry.get("version")) or item["item_version"],
                    None,
                )
            )
        elif key in unchanged:
            entry = _mapping(unchanged[key])
            results.append(
                _item_result(
                    paper_id,
                    "unchanged",
                    entry.get("key") or item["item_key"],
                    _optional_int(entry.get("version")) or item["item_version"],
                    None,
                )
            )
        elif key in failed:
            entry = _mapping(failed[key])
            code = _optional_int(entry.get("code"))
            status = "conflict" if code in (412, 428) else "failed"
            results.append(
                _item_result(
                    paper_id,
                    status,
                    item["item_key"],
                    item["item_version"],
                    f"zotero_{code}" if code is not None else "zotero_failed",
                )
            )
        else:
            results.append(
                _item_result(paper_id, "unknown_outcome", item["item_key"], item["item_version"], "missing_in_response")
            )
    return results


def _item_result(
    paper_id: str,
    status: str,
    item_key: Any,
    item_version: Any,
    error_code: Any,
) -> dict[str, Any]:
    """Exactly the five keys ``$defs/ZoteroItemResult`` declares."""
    key = str(item_key) if item_key else None
    version = _optional_int(item_version)
    code = redact_text(error_code)[:_MAX_ERROR_TEXT] if error_code else None
    return {
        "paper_id": str(paper_id),
        "status": str(status),
        "item_key": key,
        "item_version": version,
        "error_code": code,
    }


# --------------------------------------------------------------------------
# helpers: previews
# --------------------------------------------------------------------------


def _normalise_target(target: Mapping[str, Any]) -> dict[str, Any]:
    source = _mapping(target)
    library_type = str(source.get("library_type") or "user")
    if library_type not in _library_types():
        raise V32Error(400, "invalid_library_type", "library_type 只能是 user 或 group。")
    library_id = _require_id(source.get("library_id") or source.get("target_library_id"), "library_id")
    collection_key = source.get("collection_key", source.get("target_collection_key"))
    return {
        "library_type": library_type,
        "library_id": library_id,
        "target_library_id": library_id,
        "collection_key": str(collection_key) if collection_key else None,
        "target_collection_key": str(collection_key) if collection_key else None,
    }


def _preview_item(item: Mapping[str, Any], index: int) -> dict[str, Any]:
    source = _mapping(item)
    paper_id = _require_id(source.get("paper_id"), f"items[{index}].paper_id")
    action = str(source.get("action") or "create")
    if action not in {"create", "update", "unchanged", "conflict"}:
        raise V32Error(400, "invalid_preview_action", f"items[{index}].action 非法。")
    existing = source.get("existing_item_key")
    return {
        "paper_id": paper_id,
        "action": action,
        "existing_item_key": str(existing) if existing else None,
        "title": str(source.get("title") or ""),
        "reason": str(source.get("reason") or ""),
    }


def _preview_document(record: _Preview) -> dict[str, Any]:
    return {
        "preview_id": record.preview_id,
        "project_id": record.project_id,
        "revision": record.revision,
        "expires_at": record.expires_at.isoformat(),
        "target_library_id": record.target["target_library_id"],
        "target_collection_key": record.target["target_collection_key"],
        "items": [dict(item) for item in record.items],
    }


def _preview_action(
    paper: Mapping[str, Any],
    target: Mapping[str, Any],
    payload: Mapping[str, Any],
    seen_titles: Mapping[str, str],
) -> tuple[str, str | None, str]:
    recorded = _recorded_zotero(paper)
    title_key = _normalise_title(str(_pick(paper, ("title",)) or ""))
    if title_key and title_key in seen_titles:
        return "conflict", None, "与本次导出中的其他条目题名重复，需人工确认目标条目。"
    if recorded and recorded.get("item_key"):
        same_library = (
            str(recorded.get("library_type") or "") == str(target["library_type"])
            and str(recorded.get("library_id") or "") == str(target["library_id"])
        )
        if not same_library:
            return (
                "conflict",
                str(recorded["item_key"]),
                "该项目已记录到其他文献库或集合，需人工确认后再写入。",
            )
        if recorded.get("content_hash") == _hash_json(payload):
            return "unchanged", str(recorded["item_key"]), "本条目内容与已保存条目一致，无需写入。"
        return "update", str(recorded["item_key"]), "按 item_key 幂等更新同一 Zotero 条目。"
    return "create", None, "尚未关联 Zotero 条目，将新建。"


def _export_payload(paper: Mapping[str, Any], target: Mapping[str, Any]) -> dict[str, Any]:
    """Metadata plus the user's own tags and notes — never full text."""
    authors = _pick(paper, ("authors",)) or []
    creators: list[dict[str, Any]] = []
    for author in authors if isinstance(authors, Iterable) and not isinstance(authors, str) else []:
        name = author.get("name") if isinstance(author, Mapping) else str(author)
        if not name:
            continue
        parts = str(name).split()
        creators.append(
            {
                "creatorType": "author",
                "firstName": " ".join(parts[:-1]) if len(parts) > 1 else "",
                "lastName": parts[-1] if parts else str(name),
            }
        )
    payload: dict[str, Any] = {
        "itemType": str(_pick(paper, ("item_type",)) or "journalArticle"),
        "title": str(_pick(paper, ("title",)) or ""),
        "creators": creators,
    }
    doi = normalize_doi(_pick(paper, ("doi",)))
    if doi:
        payload["DOI"] = doi
    year = _optional_int(_pick(paper, ("year",)))
    if year is not None:
        payload["date"] = str(year)
    venue = _pick(paper, ("venue", "container_title"))
    if venue:
        payload["publicationTitle"] = str(venue)
    abstract = _pick(paper, ("abstract",))
    if abstract:
        payload["abstractNote"] = str(abstract)
    tags = _pick(paper, ("tags",)) or []
    payload["tags"] = [{"tag": str(tag)} for tag in tags if isinstance(tag, (str, int))]
    notes = _pick(paper, ("notes",)) or []
    payload["notes"] = [str(note) for note in notes if isinstance(note, (str, int))]
    if target.get("collection_key"):
        payload["collections"] = [str(target["collection_key"])]
    return payload


def _plan_attachments(
    item: Mapping[str, Any], *, include_attachments: bool, selected: set[str]
) -> list[dict[str, Any]]:
    planned: list[dict[str, Any]] = []
    for attachment in item.get("attachments") or []:
        key = str(attachment.get("key") or "")
        if not include_attachments:
            status, reason = "skipped", "attachment_requires_explicit_selection"
        elif key and key in selected:
            # Selected for download; this module never fetches it.
            status, reason = "selected", "explicit_selection_recorded"
        else:
            status, reason = "skipped", "attachment_not_selected"
        planned.append(
            {
                "attachment_key": key,
                "content_type": attachment.get("content_type"),
                "status": status,
                "reason": reason,
            }
        )
    return planned


# --------------------------------------------------------------------------
# helpers: values
# --------------------------------------------------------------------------


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _pick(source: Any, names: Iterable[str], default: Any = None) -> Any:
    for name in names:
        if isinstance(source, Mapping):
            if name in source and source[name] is not None:
                return source[name]
        else:
            value = getattr(source, name, None)
            if value is not None:
                return value
    return default


def _recorded_zotero(paper: Mapping[str, Any]) -> dict[str, Any] | None:
    recorded = _pick(paper, ("zotero", "zotero_item", "external"))
    if not isinstance(recorded, Mapping) or not recorded.get("item_key"):
        return None
    return dict(recorded)


def _hash_json(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalise_title(title: str) -> str:
    text = re.sub(r"[\W_]+", " ", (title or "").casefold())
    return re.sub(r"\s+", " ", text).strip()


def _same_work(candidate: Mapping[str, Any], data: Mapping[str, Any]) -> bool:
    left = normalize_doi(candidate.get("doi"))
    right = normalize_doi(data.get("DOI"))
    if left and right:
        return left == right
    return bool(
        _normalise_title(str(candidate.get("title") or ""))
        and _normalise_title(str(candidate.get("title") or ""))
        == _normalise_title(str(data.get("title") or ""))
    )


def _authors(creators: Any) -> list[str]:
    if not isinstance(creators, list):
        return []
    names: list[str] = []
    for creator in creators:
        if isinstance(creator, Mapping):
            name = creator.get("name") or " ".join(
                part for part in (creator.get("firstName"), creator.get("lastName")) if part
            )
            if name:
                names.append(str(name))
        elif creator:
            names.append(str(creator))
    return names


def _tags(tags: Any) -> list[str]:
    if not isinstance(tags, list):
        return []
    names: list[str] = []
    for tag in tags:
        if isinstance(tag, Mapping) and tag.get("tag"):
            names.append(str(tag["tag"]))
        elif isinstance(tag, str) and tag:
            names.append(tag)
    return names


def _notes(data: Mapping[str, Any]) -> list[str]:
    notes: list[str] = []
    raw = data.get("notes")
    if isinstance(raw, list):
        for note in raw:
            if isinstance(note, Mapping) and note.get("note"):
                notes.append(str(note["note"]))
            elif isinstance(note, str) and note:
                notes.append(note)
    elif isinstance(data.get("note"), str) and data["note"]:
        notes.append(data["note"])
    return notes


def _attachments(entry: Mapping[str, Any], data: Mapping[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    links = _mapping(entry.get("links"))
    link = _mapping(links.get("attachment"))
    if link:
        key = _attachment_key(link.get("href"))
        if key:
            found.append(
                {
                    "key": key,
                    "content_type": link.get("attachmentType") or link.get("type"),
                }
            )
    raw = data.get("attachments")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, Mapping):
                key = str(item.get("key") or _attachment_key(item.get("href")) or "")
                if key:
                    found.append({"key": key, "content_type": item.get("content_type")})
    return found


def _attachment_key(href: Any) -> str:
    if not isinstance(href, str) or not href:
        return ""
    return href.rstrip("/").split("/")[-1]


def _year_from(value: Any) -> int | None:
    if value is None:
        return None
    match = re.search(r"(\d{4})", str(value))
    if not match:
        return None
    year = int(match.group(1))
    return year if 1000 <= year <= 3000 else None


def _require_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise V32Error(400, "invalid_identifier", f"{name} 必须是非空字符串。")
    if len(value) > 128:
        raise V32Error(400, "invalid_identifier", f"{name} 超长。")
    return value


def _require_revision(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise V32Error(400, "invalid_revision", "revision 必须是非负整数。")
    return value


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None
