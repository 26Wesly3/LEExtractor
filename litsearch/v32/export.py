"""Export of a v3.2 project: RIS, BibTeX, CSV, evidence pack and PRISMA counts.

Doc 16 "导出与工具衔接": RIS, BibTeX and CSV are kept and reuse the existing
exporters in :mod:`litsearch.export`; the evidence pack is a JSON document that
records paper_id/aliases, discovery sources, relation witnesses, the query, the
ranking snapshot, the user's judgements, the time and a licence note, and never
a key or full text.  Doc 09 AC-30: export carries provenance and an ordinary
judgement does not change PRISMA.

Everything here is built from an explicit whitelist of fields, so a field the
project document gains later (an attachment storage path, a credential) cannot
reach an export by accident.  Attachments are deliberately not exported at all:
their identity is a storage handle, not provenance.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Mapping

from litsearch.export import to_bibtex, to_csv, to_ris
from litsearch.models import Author, Paper
from litsearch.v32.store import V32Project, _reject_secrets
from litsearch.v32.types import iso_now

__all__ = ["EXPORT_FORMATS", "export_document", "evidence_pack", "prisma_counts", "v32_papers"]

#: format -> (filename, media type)
EXPORT_FORMATS: dict[str, tuple[str, str]] = {
    "ris": ("references.ris", "application/x-research-info-systems"),
    "bibtex": ("references.bib", "application/x-bibtex"),
    "csv": ("papers.csv", "text/csv; charset=utf-8"),
    "evidence_pack": ("evidence_pack.json", "application/json"),
    "prisma": ("prisma.json", "application/json"),
}

EVIDENCE_PACK_VERSION = "3.2"

LICENCE_NOTE = (
    "Bibliographic metadata as returned by the listed discovery sources, each under "
    "its own terms of use; abstracts and full texts are not included. Rankings and "
    "judgements are this project's own records. No credential, key, token or local "
    "file path is part of this export."
)

#: Counts a PRISMA 2020 flow needs that a v3.2 project does not record, with why.
#: A v3.2 judgement is a reading preference, not a screening decision (AC-30), so
#: none of these is derived from judgements.
_PRISMA_NOT_AVAILABLE: tuple[tuple[str, str], ...] = (
    ("provider_raw_returns", "v3.2 stores the deduplicated records, not each provider's raw hit count"),
    ("records_removed_before_screening", "v3.2 applies no recorded pre-screening filters"),
    ("records_screened", "v3.2 has no title/abstract screening stage; judgements are not screening"),
    ("records_excluded", "an 'excluded' judgement hides a paper from recommendations; it is not a PRISMA exclusion"),
    ("reports_sought", "v3.2 records no full-text retrieval stage"),
    ("reports_not_retrieved", "v3.2 records no full-text retrieval stage"),
    ("reports_assessed", "v3.2 has no full-text eligibility stage"),
    ("reports_excluded_with_reasons", "v3.2 has no full-text eligibility stage"),
    ("studies_included", "v3.2 has no inclusion decision; 'core' marks reading priority only"),
)


def _ordered_records(project: V32Project, snapshot: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Paper records in the current snapshot's order, then the rest by id."""
    papers = project.doc.get("papers") or {}
    order: list[str] = []
    seen: set[str] = set()
    for item in (snapshot or {}).get("items") or []:
        pid = str(item.get("paper_id") or "")
        if pid in papers and pid not in seen:
            seen.add(pid)
            order.append(pid)
    order.extend(sorted(pid for pid in papers if pid not in seen))
    return [papers[pid] for pid in order]


def _first_provider(record: Mapping[str, Any]) -> str:
    for row in record.get("source_records") or []:
        if isinstance(row, Mapping) and row.get("provider"):
            return str(row["provider"])
    return ""


def _http(value: Any) -> str | None:
    return value if isinstance(value, str) and value.startswith(("http://", "https://")) else None


def v32_papers(records: list[Mapping[str, Any]]) -> list[Paper]:
    """v3.2 paper records as legacy :class:`Paper` objects for the shared exporters."""
    out: list[Paper] = []
    for record in records:
        citations = record.get("citation_count")
        out.append(
            Paper(
                id=str(record.get("doi") or record.get("paper_id") or ""),
                title=str(record.get("title") or ""),
                abstract=record.get("abstract") if isinstance(record.get("abstract"), str) else None,
                authors=[Author(name=str(name)) for name in record.get("authors") or [] if isinstance(name, str)],
                year=record.get("year") if isinstance(record.get("year"), int) else None,
                venue=str(record["venue"]) if record.get("venue") else None,
                doi=str(record["doi"]) if record.get("doi") else None,
                citation_count=int(citations) if isinstance(citations, int) and citations >= 0 else 0,
                source=_first_provider(record),
                url=_http(record.get("landing_url")),
            )
        )
    return out


def _discovery_sources(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in record.get("source_records") or []:
        if not isinstance(row, Mapping):
            continue
        external = row.get("external_id")
        rows.append(
            {
                "provider": str(row.get("provider") or "unknown")[:128],
                "external_id": str(external) if external not in (None, "") else None,
                "source_url": _http(row.get("source_url")),
                "retrieved_at": str(row.get("retrieved_at") or "") or None,
            }
        )
    return rows


def _judgement(record: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    return {
        "paper_id": str(record.get("paper_id") or ""),
        "value": str(record.get("value") or "unjudged"),
        "exclusion_reason": str(record.get("exclusion_reason") or "unspecified"),
        "reason": str(record.get("reason") or ""),
        "updated_at": str(record.get("updated_at") or "") or None,
        "event_sequence": int(record.get("event_sequence") or 0),
    }


def _ranking(item: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    return {
        "rank": item.get("rank"),
        "core_score": item.get("core_score"),
        "core_role": item.get("core_role"),
        "eligibility": item.get("eligibility"),
        "recommendation_evidence": [
            {key: entry.get(key) for key in ("code", "text", "paper_ids", "evidence_ids") if key in entry}
            for entry in item.get("recommendation_evidence") or []
            if isinstance(entry, Mapping)
        ],
    }


def _relations(edges: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "from_paper_id": str(edge.get("from_paper_id") or ""),
            "to_paper_id": str(edge.get("to_paper_id") or ""),
            "relation": str(edge.get("relation") or ""),
            "directed": bool(edge.get("directed")),
            "witness_paper_ids": [str(pid) for pid in edge.get("witness_paper_ids") or []],
        }
        for edge in edges
        if isinstance(edge, Mapping)
    ]


def evidence_pack(
    project: V32Project,
    snapshot: Mapping[str, Any] | None,
    edges: list[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """The doc 16 evidence pack, from a whitelist of fields only."""
    doc = project.doc
    anchor = doc.get("anchor") or {}
    judgements = doc.get("judgements") or {}
    items = {str(item.get("paper_id")): item for item in (snapshot or {}).get("items") or []}
    papers = []
    for record in _ordered_records(project, snapshot):
        pid = str(record.get("paper_id") or "")
        papers.append(
            {
                "paper_id": pid,
                "aliases": sorted(str(alias) for alias in record.get("aliases") or [] if alias),
                "title": str(record.get("title") or ""),
                "year": record.get("year") if isinstance(record.get("year"), int) else None,
                "doi": str(record["doi"]) if record.get("doi") else None,
                "discovery_sources": _discovery_sources(record),
                "ranking": _ranking(items.get(pid)),
                "judgement": _judgement(judgements.get(pid)),
            }
        )
    pack = {
        "evidence_pack_version": EVIDENCE_PACK_VERSION,
        "project_id": project.project_id,
        "project_name": str(doc.get("name") or ""),
        "exported_at": iso_now(),
        "access_date": iso_now()[:10],
        "revision": int(project.revision),
        "snapshot_id": (snapshot or {}).get("snapshot_id"),
        "ranking_stage": (snapshot or {}).get("ranking_stage"),
        "query": {
            "entry_mode": str(anchor.get("entry_mode") or doc.get("entry_mode") or ""),
            "original_text": str(anchor.get("original_text") or ""),
            "query_variants": [
                str(row.get("text") if isinstance(row, Mapping) else row)
                for row in anchor.get("query_variants") or []
            ],
            "seed_paper_ids": [str(pid) for pid in anchor.get("seed_paper_ids") or []],
        },
        "papers": papers,
        "judgements": sorted(
            (_judgement(row) for row in judgements.values()),
            key=lambda row: (row["event_sequence"], row["paper_id"]),
        ),
        "relations": _relations(list(edges)),
        "licence_note": LICENCE_NOTE,
    }
    # Belt to the whitelist's braces: a credential-shaped name or value refuses
    # the export instead of shipping it (doc 10).
    _reject_secrets(pack)
    return pack


def prisma_counts(project: V32Project) -> dict[str, Any]:
    """PRISMA identification counts from the v3.2 records, with the gaps named.

    Nothing here reads ``judgements``: AC-30 requires that an ordinary judgement
    leaves PRISMA unchanged.  A migrated legacy PRISMA state is passed through
    unchanged under ``legacy_prisma`` (R19).
    """
    doc = project.doc
    papers = doc.get("papers") or {}
    by_source: Counter[str] = Counter()
    source_records = 0
    for record in papers.values():
        providers = {
            str(row.get("provider") or "unknown")
            for row in record.get("source_records") or []
            if isinstance(row, Mapping)
        }
        source_records += len(providers)
        by_source.update(providers)
    unique = len(papers)
    return {
        "basis": "v3.2_project_records",
        "units": "record",
        "identification": {
            "records_by_source": dict(sorted(by_source.items())),
            "source_records_total": source_records,
            "unique_records": unique,
            "cross_source_duplicates_merged": max(0, source_records - unique),
            "identity_merges": len(doc.get("redirects") or {}),
        },
        "not_available": [{"count": name, "reason": reason} for name, reason in _PRISMA_NOT_AVAILABLE],
        "judgements_affect_counts": False,
        "legacy_prisma": dict(doc.get("prisma") or {}) or None,
    }


def export_document(
    project: V32Project,
    fmt: str,
    snapshot: Mapping[str, Any] | None,
    edges: list[Mapping[str, Any]] = (),
) -> tuple[bytes, str, str]:
    """``(body, filename, media_type)`` for one export format."""
    filename, media = EXPORT_FORMATS[fmt]
    if fmt in {"ris", "bibtex", "csv"}:
        papers = v32_papers(_ordered_records(project, snapshot))
        if fmt == "ris":
            return to_ris(papers).encode("utf-8"), filename, media
        if fmt == "bibtex":
            return to_bibtex(papers).encode("utf-8"), filename, media
        return to_csv(papers, include_abstract=True).encode("utf-8-sig"), filename, media
    payload = evidence_pack(project, snapshot, edges) if fmt == "evidence_pack" else prisma_counts(project)
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"), filename, media
