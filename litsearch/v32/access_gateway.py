"""External access entry points for the v3.2 layer (doc 11, doc 20, AC-23/AC-28).

This module answers one question: *which official page may the user open for
this paper?*  It answers it with links and honest labels, and with nothing
else:

* institution entries come **only** from ``contracts/access_links.json``
  (``carsi``, ``zju_library``, ``zju_sciencedirect``); no URL, database
  internal id or callback is ever composed here;
* a per-article page comes from a real recorded ``landing_url`` or from the
  official DOI resolver — never from a guess;
* when no single-article page is verifiable the generic database navigation
  entry is offered with ``availability="entry_only"``, and a
  publisher-specific database guide is only offered when that publisher was
  actually recognised (doc 20: "无法确定出版商时，不将所有论文都送到
  Elsevier");
* ``verified`` says a link or metadata source was checked — it is never a
  claim of entitlement, and nothing here changes the application session.

Direct institutional single sign-on is out of scope for this round
(``access.application_sso_enabled`` and ``access.federation_sp_enabled`` are
both false): this module contains no request machinery at all, so there is no
code path that could contact a campus authentication endpoint, hold a campus
secret or validate a one-time credential.  Doc 20 keeps that work deferred.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Mapping, Sequence

from litsearch.identifiers import normalize_doi
from litsearch.v32 import contracts
from litsearch.v32.types import (
    AccessGranularity,
    AccessKind,
    Availability,
    CapabilityStatus,
    iso_now,
)

__all__ = [
    "DOI_RESOLVER_PREFIX",
    "HTTPS_SCHEME",
    "access_options",
    "capabilities_for_access",
    "doi_resolver",
    "landing_url",
]

#: The only transport scheme a link may use.  Kept as one named constant so the
#: module contains exactly two URL-shaped literals: this scheme and the
#: official DOI resolver below.  Every institution link is read from the
#: registry instead of being written here.
HTTPS_SCHEME = "https://"

#: The official DOI resolution service.  Doc 20: "优先使用 DOI 官方解析地址".
DOI_RESOLVER_PREFIX = "https://doi.org/"

#: Registry ids that belong to one publisher's database.  A guide for a
#: database is only useful when the paper really is in that database, so each
#: id maps to the publisher hints that unlock it.
_DATABASE_GUIDE_HINTS: dict[str, tuple[str, ...]] = {
    "zju_sciencedirect": ("elsevier", "sciencedirect", "10.1016/"),
}

#: Institution the registry entries belong to.  Doc 20 names the second and
#: third entries as 浙江大学 resources ("浙江大学数据库导航", "浙江大学
#: ScienceDirect 说明页").  The CARSI portal is deliberately generic — the user
#: picks the school on the official portal — so it carries no institution and
#: therefore no claim about this application's access.
_ENTRY_INSTITUTIONS: dict[str, str | None] = {
    "carsi": None,
    "zju_library": "浙江大学",
    "zju_sciencedirect": "浙江大学",
}

_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Keys a paper record may use for its recorded publisher landing page.
_LANDING_KEYS = ("landing_url", "url")
#: Keys that identify the publisher / container of a paper.
_PUBLISHER_KEYS = ("publisher", "container_title", "journal", "venue")
#: Keys that carry a recorded open-access verdict.
_OA_BOOL_KEYS = ("is_oa", "open_access", "is_open_access")
_OA_STATUS_KEYS = ("oa_status", "open_access_status")
_PUBLIC_OA_STATUS = frozenset(
    {"gold", "green", "hybrid", "bronze", "diamond", "public", "free_to_read"}
)


def doi_resolver(doi: Any) -> str | None:
    """The official DOI resolution URL for ``doi``, or ``None``.

    Isolation is delegated to :func:`litsearch.identifiers.normalize_doi`, so
    this module never invents a DOI-shaped path of its own.
    """
    normalized = normalize_doi(str(doi) if doi is not None else "")
    return f"{DOI_RESOLVER_PREFIX}{normalized}" if normalized else None


def landing_url(paper: Any, resolver: Callable[[Any], str | None] | None = None) -> dict[str, Any] | None:
    """The per-article page for ``paper`` as an AccessOption, or ``None``.

    A recorded ``landing_url`` is preferred (kind ``fulltext_link``); a DOI is
    resolved through the official resolver (kind ``resolver``).  Nothing else
    produces an article-level link.
    """
    resolve = resolver or doi_resolver
    recorded = _first(paper, _LANDING_KEYS)
    if isinstance(recorded, str) and recorded.startswith(HTTPS_SCHEME):
        source = _first(paper, ("landing_url_source_url",))
        is_oa = _is_open_access(paper)
        return _option(
            provider=_publisher_for(paper, recorded),
            kind=str(AccessKind.FULLTEXT_LINK),
            url=recorded,
            auth_required=not is_oa,
            institution=None,
            availability=str(Availability.PUBLIC if is_oa else Availability.UNKNOWN),
            verified=bool(_first(paper, ("landing_url_verified",), default=True)),
            source_url=source if isinstance(source, str) and source.startswith(HTTPS_SCHEME) else None,
            granularity=str(AccessGranularity.ARTICLE),
        )

    url = resolve(_first(paper, ("doi",)))
    if not url:
        return None
    return _option(
        provider="doi",
        kind=str(AccessKind.RESOLVER),
        url=url,
        auth_required=False,
        institution=None,
        availability=str(Availability.PUBLIC if _is_open_access(paper) else Availability.UNKNOWN),
        verified=True,
        source_url=None,
        granularity=str(AccessGranularity.ARTICLE),
    )


def access_options(
    paper: Any,
    *,
    registry: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]] | None = None,
    resolver: Callable[[Any], str | None] | None = None,
) -> list[dict[str, Any]]:
    """Every access entry for ``paper``, each one an ``$defs/AccessOption``.

    Order: the article-level page (if any), then the registry entries in
    registry order.  The result is deterministic and contains no request
    outcome, because opening a link is not a retrieval operation.
    """
    entries = _registry_entries(registry)
    document_checked_at = _registry_document_checked_at(registry)
    options: list[dict[str, Any]] = []
    article = landing_url(paper, resolver=resolver)
    if article is not None:
        options.append(article)
    for entry_id, entry in entries:
        kind = str(entry.get("kind") or AccessKind.INSTITUTION_LINK)
        if kind != str(AccessKind.INSTITUTION_LINK):
            continue
        if not _entry_applies(entry_id, entry, paper):
            continue
        url = entry.get("url")
        if not isinstance(url, str) or not url.startswith(HTTPS_SCHEME):
            # A registry entry that is not an https link is a contract defect;
            # it is skipped rather than repaired by guessing a URL.
            continue
        availability = str(entry.get("availability") or Availability.ENTRY_ONLY)
        if availability not in {str(value) for value in Availability}:
            availability = str(Availability.UNKNOWN)
        granularity = str(entry.get("granularity") or AccessGranularity.PORTAL)
        if granularity not in {str(value) for value in AccessGranularity}:
            granularity = str(AccessGranularity.PORTAL)
        options.append(
            _option(
                provider=entry_id,
                kind=str(AccessKind.INSTITUTION_LINK),
                url=url,
                auth_required=bool(entry.get("auth_required", False)),
                institution=entry.get("institution", _ENTRY_INSTITUTIONS.get(entry_id)),
                availability=availability,
                verified=bool(entry.get("verified", False)),
                source_url=_https_or_none(entry.get("source_url")),
                granularity=granularity,
                checked_at=_as_datetime(entry.get("checked_at") or document_checked_at),
            )
        )
    return options


def capabilities_for_access() -> dict[str, Any]:
    """``$defs/Capabilities`` for the access layer, derived from the contract.

    The three flags doc 11 names (``application_sso_enabled``,
    ``federation_sp_enabled``, ``external_resource_links_enabled``) decide the
    statuses, so a contract change moves this report with it instead of
    leaving a stale claim in the code.
    """
    access = contracts.access_defaults()
    external = bool(access["external_resource_links_enabled"])
    items = [
        _capability(
            "external_resource_links",
            CapabilityStatus.AVAILABLE if external else CapabilityStatus.UNAVAILABLE,
            "contract_registry_only" if external else "disabled_by_contract",
        ),
        _capability(
            "institution_link_registry",
            CapabilityStatus.AVAILABLE,
            "official_public_documentation_not_authenticated_entitlement",
        ),
        _capability(
            "application_sso",
            CapabilityStatus.AVAILABLE if access["application_sso_enabled"] else CapabilityStatus.EXTERNAL_BLOCKED,
            "enabled_by_contract" if access["application_sso_enabled"] else "deferred_this_round",
        ),
        _capability(
            "federation_sp",
            CapabilityStatus.AVAILABLE if access["federation_sp_enabled"] else CapabilityStatus.EXTERNAL_BLOCKED,
            "enabled_by_contract" if access["federation_sp_enabled"] else "deferred_this_round",
        ),
        _capability(
            "cn_metadata_api",
            CapabilityStatus.EXTERNAL_BLOCKED,
            "awaiting_official_interface_and_licence",
        ),
    ]
    return {
        "deployment_mode": str(contracts.runtime_defaults()["default_mode"]),
        "items": items,
    }


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _option(
    *,
    provider: str,
    kind: str,
    url: str,
    auth_required: bool,
    institution: str | None,
    availability: str,
    verified: bool,
    source_url: str | None,
    granularity: str,
    checked_at: str | None = None,
) -> dict[str, Any]:
    """One ``$defs/AccessOption``: exactly the ten declared keys."""
    return {
        "provider": str(provider),
        "kind": kind,
        "url": url,
        "auth_required": bool(auth_required),
        "institution": institution,
        "availability": availability,
        "verified": bool(verified),
        "checked_at": checked_at or iso_now(),
        "source_url": source_url,
        "granularity": granularity,
    }


def _capability(capability_id: str, status: CapabilityStatus, reason_code: str) -> dict[str, Any]:
    return {
        "id": capability_id,
        "status": str(status),
        "reason_code": reason_code,
        "checked_at": iso_now(),
    }


def _registry_entries(
    registry: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]] | None,
) -> list[tuple[str, Mapping[str, Any]]]:
    """Normalise either registry shape into ``[(id, entry)]`` in registry order."""
    if registry is None:
        return list(contracts.access_link_registry().items())
    if isinstance(registry, Mapping):
        return [(str(key), value) for key, value in registry.items()]
    return [(str(entry.get("id") or ""), entry) for entry in registry]


def _registry_document_checked_at(
    registry: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]] | None,
) -> str | None:
    value = None if registry is not None else contracts.access_links().get("checked_at")
    return str(value) if value else None


def _entry_applies(entry_id: str, entry: Mapping[str, Any], paper: Any) -> bool:
    """Whether a registry entry is relevant to this paper.

    Portal entries always apply; a database guide applies only when the paper's
    publisher was actually recognised, so an unknown publisher never gets
    pushed towards one vendor's database.
    """
    hints = _DATABASE_GUIDE_HINTS.get(entry_id)
    if hints is None:
        return True
    blob = " ".join(
        str(value).lower()
        for value in (
            _publisher_for(paper, None),
            _first(paper, ("doi",)),
            _first(paper, _PUBLISHER_KEYS),
        )
        if value
    )
    return any(hint in blob for hint in hints)


def _publisher_for(paper: Any, url: str | None) -> str:
    """A stable provider label for an article-level link."""
    for key in _PUBLISHER_KEYS:
        value = _first(paper, (key,))
        if isinstance(value, str) and value.strip():
            return value.strip()
    if url:
        host = re.sub(r"^https://", "", url).split("/")[0]
        host = host[4:] if host.startswith("www.") else host
        if host:
            return host
    return "doi"


def _is_open_access(paper: Any) -> bool:
    """Whether the record itself says the work is openly available.

    Absent evidence is *not* a public claim: the caller then sees
    ``availability="unknown"`` and ``auth_required=True``.
    """
    for key in _OA_BOOL_KEYS:
        value = _first(paper, (key,))
        if isinstance(value, bool):
            return value
    for key in _OA_STATUS_KEYS:
        value = _first(paper, (key,))
        if isinstance(value, str) and value.strip().lower() in _PUBLIC_OA_STATUS:
            return True
    return False


def _first(paper: Any, names: Iterable[str], default: Any = None) -> Any:
    """Read the first present field from a mapping or an object."""
    for name in names:
        if isinstance(paper, Mapping):
            if name in paper and paper[name] is not None:
                return paper[name]
        else:
            value = getattr(paper, name, None)
            if value is not None:
                return value
    return default


def _https_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value.startswith(HTTPS_SCHEME) else None


def _as_datetime(value: Any) -> str | None:
    """RFC3339 for a ``$defs/AccessOption.checked_at``.

    The registry records a check *date*; the schema declares ``date-time``, so
    a date-only value is anchored at UTC midnight instead of being emitted in a
    shape the contract does not declare.
    """
    if not value:
        return None
    text = str(value)
    if _DATE_ONLY_RE.match(text):
        return f"{text}T00:00:00Z"
    return text
