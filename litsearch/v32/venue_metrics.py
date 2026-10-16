"""Venue metrics for the v3.2 layer (doc 13, AC-24).

Three rules decide everything in this module:

* **No fabrication.**  An official Journal Impact Factor, a JCR quartile and a
  Chinese Academy of Sciences partition are *licensed* products.  Without a
  licensed, verifiable source they are reported as ``unavailable`` with a
  ``null`` value — never guessed, never derived from something else.
* **No relabelling.**  The internal citation proxy is labelled
  "内部引用代理指标" and is never presented as an impact factor.
* **No reuse of ranking power.**  This round CoreRank does not use venue
  metrics at all (AC-24), so this module neither imports nor exposes any score
  that could enter the ranking.  It only describes a venue.

The two existing, already-verified artefacts are reused rather than
re-implemented (doc 13: "先经 M0 核验后复用；不得另建并行指标体系"):

``litsearch.venues``            the CCF 2026 catalogue accessor
``litsearch.sources``           the Citation Proxy implementation
"""

from __future__ import annotations

from functools import lru_cache
from types import SimpleNamespace
from typing import Any, Iterable, Mapping, Sequence

from litsearch.sources import ImpactFactorLookup
from litsearch.v32.types import MetricType, VerificationStatus, iso_now
from litsearch.venues import catalog as ccf_catalog
from litsearch.venues import venue_classification

__all__ = [
    "cas_partition",
    "citation_proxy",
    "ccf_metric",
    "jcr_quartile",
    "official_jif",
    "openalex_h_index",
    "venue_metrics",
]

#: The metric types that need a licensed, verifiable source.
LICENSED_METRIC_TYPES = (
    str(MetricType.OFFICIAL_JIF),
    str(MetricType.JCR_QUARTILE),
    str(MetricType.CAS_PARTITION),
)

#: Venue types whose official metrics are worth stating as "unavailable".
_PUBLISHED_VENUE_TYPES = frozenset({"journal", "conference", "book_series"})

_VENUE_TYPE_ALIASES = {
    "journal": "journal",
    "journals": "journal",
    "conference": "conference",
    "proceedings": "conference",
    "conferencepaper": "conference",
    "preprint": "preprint",
    "posted-content": "preprint",
    "posted_content": "preprint",
    "book_series": "book_series",
    "book-series": "book_series",
    "bookseries": "book_series",
    "book": "book_series",
}

#: The label that must accompany every citation proxy value (doc 13).
CITATION_PROXY_LABEL = "内部引用代理指标"

_MISSING_OFFICIAL_NOTE = (
    "无可验证的许可来源：按规范不展示官方 JIF、JCR 分区或中科院分区数值。"
    "缺失的指标不影响文献发现与排序。"
)


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------


def venue_metrics(venue: Any, *, adapters: Sequence[Any] | None = None) -> dict[str, Any]:
    """``$defs/VenueMetrics`` for one venue.

    Args:
        venue: a mapping (an object with the same attributes also works) with
            ``venue_id``/``id``, ``name``, ``venue_type``, ``issns`` and
            optionally ``stats`` (recorded citation statistics) and
            ``h_index``.
        adapters: licensed metric sources.  Each adapter may expose a
            ``metric(venue, metric_type)`` method or one method per metric
            type; anything it returns is only displayed when it proves a
            licensed, verified source (see :func:`official_jif`).
    """
    venue_type = _venue_type(venue)
    metrics: list[dict[str, Any]] = []

    ccf = ccf_metric(venue)
    if ccf is not None:
        metrics.append(ccf)

    proxy = citation_proxy(venue, stats=_stats(venue))
    if proxy is not None:
        metrics.append(proxy)

    h_index = openalex_h_index(venue)
    if h_index is not None:
        metrics.append(h_index)

    jif = official_jif(venue, adapters=adapters)
    if jif is not None:
        metrics.append(jif)
    elif venue_type in _PUBLISHED_VENUE_TYPES:
        metrics.append(_unavailable(str(MetricType.OFFICIAL_JIF)))

    quartile = jcr_quartile(venue, adapters=adapters)
    if quartile is not None:
        metrics.append(quartile)
    elif venue_type in _PUBLISHED_VENUE_TYPES:
        metrics.append(_unavailable(str(MetricType.JCR_QUARTILE)))

    partitions = cas_partition(venue, adapters=adapters)
    if partitions:
        metrics.extend(partitions)
    elif venue_type in _PUBLISHED_VENUE_TYPES:
        metrics.append(_unavailable(str(MetricType.CAS_PARTITION)))

    return {
        "venue_id": _venue_id(venue),
        "name": _venue_name(venue),
        "venue_type": venue_type,
        "issns": _issns(venue),
        "metrics": metrics,
    }


def ccf_metric(venue: Any) -> dict[str, Any] | None:
    """The CCF rank of ``venue`` from the existing catalogue, or ``None``.

    Delegates to :func:`litsearch.venues.venue_classification`, which already
    refuses to award a rank when matching is ambiguous (an unknown name, a
    workshop/Findings track, or several candidate entries).  Doc 13: "不能仅按
    字符串相似度赋予 CCF A" — that decision stays in the one catalogue
    accessor instead of being re-implemented here.
    """
    name = _venue_name(venue)
    if not name:
        return None
    classification = venue_classification(_catalogue_paper(name, _dblp_key(venue)))
    rank = classification.get("rank")
    if not rank:
        return None
    note_parts = [str(classification.get("note") or "").strip()]
    acronym = classification.get("acronym")
    if acronym:
        note_parts.append(f"目录条目 {acronym}（第 {classification.get('page')} 页）。")
    return _metric(
        metric_type=str(MetricType.CCF_RANK),
        value=str(rank),
        metric_year=None,
        category=_optional_str(classification.get("kind")),
        edition=_optional_str(classification.get("edition")),
        source_name="CCF 推荐国际学术会议和期刊目录",
        source_url=_optional_str(classification.get("source")),
        verification_status=str(VerificationStatus.UNVERIFIED),
        license_scope="ccf_public_catalogue_unverified_metadata",
        note=" ".join(part for part in note_parts if part),
    )


def citation_proxy(
    venue: Any,
    *,
    stats: Mapping[str, Any] | None = None,
    lookup: Any | None = None,
) -> dict[str, Any] | None:
    """The internal citation proxy for ``venue``, or ``None`` when unknown.

    The number comes from :class:`litsearch.sources.ImpactFactorLookup` — the
    single M0-verified implementation and its documented formula — never from
    a second formula written here.  ``stats`` supplies recorded OpenAlex-style
    statistics (``cited_by_count`` / ``works_count`` / ``h_index``) and is fed
    through that same lookup; with no statistics and no cached value the lookup
    answers from the vendored table only and **no request is made**.
    """
    name = _venue_name(venue)
    if not name:
        return None
    source_lookup = lookup if lookup is not None else _offline_lookup(stats)
    record = source_lookup.citation_proxy(name, _issn(venue))
    value = record.get("value")
    if value is None:
        return None
    note_parts = [CITATION_PROXY_LABEL]
    for key in ("note", "display", "formula"):
        text = record.get(key)
        if text:
            note_parts.append(f"{key}: {text}")
    return _metric(
        metric_type=str(MetricType.CITATION_PROXY),
        value=float(value),
        metric_year=_optional_int(record.get("year")),
        category=None,
        edition=None,
        source_name=_optional_str(record.get("source")) or "unknown",
        source_url=None,
        # A proxy is never a verified official metric, whatever its source.
        verification_status=str(VerificationStatus.UNVERIFIED),
        license_scope="internal_proxy_not_for_official_display",
        note="；".join(note_parts),
    )


def official_jif(venue: Any, *, adapters: Sequence[Any] | None = None) -> dict[str, Any] | None:
    """The official JIF for ``venue``, or ``None``.

    ``None`` is the honest answer whenever no licensed, verifiable source is
    configured — which is this round's state — and also when an adapter
    returns a value that cannot prove its licence, its source, its release or
    its verification.  A citation proxy never qualifies.
    """
    return _licensed_metric(venue, str(MetricType.OFFICIAL_JIF), adapters)


def jcr_quartile(venue: Any, *, adapters: Sequence[Any] | None = None) -> dict[str, Any] | None:
    """The JCR quartile for ``venue``, or ``None`` (same gate as JIF)."""
    return _licensed_metric(venue, str(MetricType.JCR_QUARTILE), adapters)


def cas_partition(venue: Any, *, adapters: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    """Every recorded CAS partition for ``venue``, one per discipline.

    A list, because a journal can sit in different partitions in different
    categories: each entry keeps its own ``category`` rather than being
    compressed into one unsourced number (doc 13).
    """
    found = _licensed_metrics(venue, str(MetricType.CAS_PARTITION), adapters)
    return found


def openalex_h_index(venue: Any) -> dict[str, Any] | None:
    """A recorded venue h-index, or ``None``.

    Only a value the venue record already carries is reported; nothing is
    fetched, so an absent h-index stays absent instead of being estimated.
    """
    raw = _pick(venue, ("h_index",))
    if raw is None:
        stats = _stats(venue) or {}
        raw = stats.get("h_index")
    value = _optional_int(raw)
    if value is None:
        return None
    return _metric(
        metric_type=str(MetricType.OPENALEX_H_INDEX),
        value=value,
        metric_year=None,
        category=None,
        edition=None,
        source_name=_optional_str(_pick(venue, ("h_index_source",))) or "recorded venue metadata",
        source_url=_optional_str(_pick(venue, ("h_index_source_url",))),
        verification_status=str(VerificationStatus.UNVERIFIED),
        license_scope="recorded_metadata_unverified",
        note="记录中的场馆 h 指数；未与官方接口再次核对。",
    )


# --------------------------------------------------------------------------
# licensed adapters
# --------------------------------------------------------------------------


def _licensed_metric(
    venue: Any, metric_type: str, adapters: Sequence[Any] | None
) -> dict[str, Any] | None:
    records = _licensed_metrics(venue, metric_type, adapters)
    return records[0] if records else None


def _licensed_metrics(
    venue: Any, metric_type: str, adapters: Sequence[Any] | None
) -> list[dict[str, Any]]:
    """Accept only records that prove a licensed, verified official source."""
    results: list[dict[str, Any]] = []
    for adapter in adapters or ():
        for raw in _adapter_records(adapter, venue, metric_type):
            record = _licensed_record(raw, metric_type)
            if record is not None:
                results.append(record)
    return results


def _adapter_records(adapter: Any, venue: Any, metric_type: str) -> list[Any]:
    method = getattr(adapter, "metric", None)
    if callable(method):
        try:
            produced = method(venue, metric_type)
        except Exception:  # a broken licensed adapter must not break the card
            return []
    else:
        method = getattr(adapter, metric_type, None)
        if not callable(method):
            return []
        try:
            produced = method(venue)
        except Exception:
            return []
    if produced is None:
        return []
    if isinstance(produced, Mapping):
        return [produced]
    if isinstance(produced, Iterable) and not isinstance(produced, (str, bytes)):
        return [item for item in produced if item is not None]
    return []


def _licensed_record(raw: Any, metric_type: str) -> dict[str, Any] | None:
    """Normalise an official metric, or refuse it.

    Display permission and truthfulness are checked separately (doc 13): the
    record must name a source, a licence scope, an https source and a release
    (year or edition), and it must declare ``verification_status="verified"``.
    A plausible-looking number that fails any of these is dropped instead of
    being shown with a caveat.
    """
    if not isinstance(raw, Mapping):
        return None
    value = raw.get("value")
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    source_name = _optional_str(raw.get("source_name") or raw.get("source"))
    source_url = _optional_str(raw.get("source_url"))
    license_scope = _optional_str(raw.get("license_scope"))
    metric_year = _optional_int(raw.get("metric_year") or raw.get("year"))
    edition = _optional_str(raw.get("edition"))
    declared = str(raw.get("verification_status") or raw.get("verified") or "").lower()
    if declared not in {str(VerificationStatus.VERIFIED), "true"}:
        return None
    if not source_name or not license_scope:
        return None
    if not source_url or not source_url.startswith("https://"):
        return None
    if metric_year is None and not edition:
        return None
    return _metric(
        metric_type=metric_type,
        value=value,
        metric_year=metric_year,
        category=_optional_str(raw.get("category")),
        edition=edition,
        source_name=source_name,
        source_url=source_url,
        verification_status=str(VerificationStatus.VERIFIED),
        license_scope=license_scope,
        note=_optional_str(raw.get("note")) or "来源已核验且许可允许展示。",
    )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _metric(
    *,
    metric_type: str,
    value: Any,
    metric_year: int | None,
    category: str | None,
    edition: str | None,
    source_name: str,
    source_url: str | None,
    verification_status: str,
    license_scope: str,
    note: str,
) -> dict[str, Any]:
    """One ``$defs/VenueMetric``: exactly the eleven declared keys."""
    return {
        "metric_type": metric_type,
        "value": value,
        "metric_year": metric_year,
        "category": category,
        "edition": edition,
        "source_name": source_name,
        "source_url": source_url,
        "retrieved_at": iso_now(),
        "verification_status": verification_status,
        "license_scope": license_scope,
        "note": note,
    }


def _unavailable(metric_type: str) -> dict[str, Any]:
    return _metric(
        metric_type=metric_type,
        value=None,
        metric_year=None,
        category=None,
        edition=None,
        source_name="未配置许可来源",
        source_url=None,
        verification_status=str(VerificationStatus.UNAVAILABLE),
        license_scope="unavailable",
        note=_MISSING_OFFICIAL_NOTE,
    )


class _MemoryCache:
    """The ``Cache`` surface ``ImpactFactorLookup`` uses, without any file."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], Any] = {}

    def get(self, namespace: str, key: str) -> Any:
        return self._values.get((namespace, key))

    def set(self, value: Any, namespace: str, key: str) -> None:
        self._values[(namespace, key)] = value


class _StaticResponse:
    def __init__(self, payload: Any) -> None:
        self.status_code = 200
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _OfflineSession:
    """A session that answers from ``payload`` and can never open a socket.

    ``ImpactFactorLookup`` is the verified implementation of the proxy; giving
    it a statistics record as its only data source keeps one formula in the
    codebase while making the test suite hermetic.
    """

    def __init__(self, payload: Any) -> None:
        self.headers: dict[str, str] = {}
        self.calls = 0
        self._payload = payload

    def get(self, url: str, params: Any = None, timeout: Any = None) -> _StaticResponse:
        self.calls += 1
        return _StaticResponse(self._payload)


def _offline_lookup(stats: Mapping[str, Any] | None) -> ImpactFactorLookup:
    lookup = ImpactFactorLookup(cache=_MemoryCache())
    if stats is None:
        # No statistics were recorded, so only the vendored table may answer.
        payload: Any = {"results": []}
    else:
        payload = {
            "results": [
                {
                    "cited_by_count": _optional_int(stats.get("cited_by_count")) or 0,
                    "works_count": _optional_int(stats.get("works_count")) or 0,
                    "summary_stats": {"h_index": _optional_int(stats.get("h_index")) or 0},
                }
            ]
        }
    lookup._session = _OfflineSession(payload)  # type: ignore[attr-defined]
    return lookup


@lru_cache(maxsize=1)
def _catalogue_source() -> str:
    return str(ccf_catalog()["source"])


def _catalogue_paper(venue_name: str, dblp_key: str) -> Any:
    """The minimal shape :func:`litsearch.venues.venue_classification` reads."""
    return SimpleNamespace(venue=venue_name, identifiers=SimpleNamespace(dblp_key=dblp_key))


def _pick(venue: Any, names: Iterable[str], default: Any = None) -> Any:
    for name in names:
        if isinstance(venue, Mapping):
            if name in venue and venue[name] is not None:
                return venue[name]
        else:
            value = getattr(venue, name, None)
            if value is not None:
                return value
    return default


def _venue_id(venue: Any) -> str | None:
    value = _pick(venue, ("venue_id", "id", "openalex_id", "source_id"))
    return _optional_str(value)


def _venue_name(venue: Any) -> str:
    if isinstance(venue, str):
        return venue.strip()
    value = _pick(venue, ("name", "venue", "title", "container_title"))
    return str(value).strip() if value else ""


def _dblp_key(venue: Any) -> str:
    value = _pick(venue, ("dblp_key",))
    if value:
        return str(value)
    identifiers = _pick(venue, ("identifiers",))
    if identifiers is not None:
        inner = _pick(identifiers, ("dblp_key",))
        if inner:
            return str(inner)
    return ""


def _issn(venue: Any) -> str:
    issns = _issns(venue)
    return issns[0] if issns else ""


def _issns(venue: Any) -> list[str]:
    raw = _pick(venue, ("issns",))
    if raw is None:
        single = _pick(venue, ("issn", "issn_l"))
        raw = [single] if single else []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, Iterable):
        return []
    seen: list[str] = []
    for item in raw:
        text = _optional_str(item)
        if text and text not in seen:
            seen.append(text)
    return seen


def _stats(venue: Any) -> Mapping[str, Any] | None:
    stats = _pick(venue, ("stats", "citation_stats"))
    return stats if isinstance(stats, Mapping) else None


def _venue_type(venue: Any) -> str:
    raw = _pick(venue, ("venue_type", "type", "kind"))
    if isinstance(raw, str):
        key = raw.strip().lower()
        if key in _VENUE_TYPE_ALIASES:
            return _VENUE_TYPE_ALIASES[key]
    return "unknown"


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None
