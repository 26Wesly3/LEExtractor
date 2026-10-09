"""Explicit browser DTOs; domain dataclasses and NetworkX never escape directly."""

from __future__ import annotations

import hashlib
import os
import re
from collections import Counter

from litsearch.evidence import EvidenceGraph, corpus_papers
from litsearch.screening import paper_screening_status, screening_facts
from litsearch.stop_reasons import is_complete

RELATION_TYPES = ("citation", "bibliographic_coupling", "co_citation", "text_similarity")


def paper_key(paper) -> str:
    return hashlib.sha256(paper.canonical_id.encode("utf-8")).hexdigest()


def public(value):
    """Redact credentials and local paths even from imported legacy metadata."""
    if isinstance(value, dict):
        return {str(key): public(item) for key, item in value.items()
                if str(key).lower() not in {
                    "api_key", "s2_api_key", "openalex_api_key", "serpapi_api_key", "password", "token",
                    "authorization", "headers", "path", "filepath", "download_path",
                }}
    if isinstance(value, (tuple, list)):
        return [public(item) for item in value]
    if isinstance(value, str):
        for name in ("S2_API_KEY", "OPENALEX_API_KEY", "LEEXTRACTOR_UNPAYWALL_EMAIL", "SERPAPI_API_KEY"):
            secret = os.environ.get(name, "")
            if secret:
                value = value.replace(secret, "[redacted]")
        value = re.sub(r"(?i)(api_key|apikey|token|password)=([^&\s]+)", r"\1=[redacted]", value)
        value = re.sub(r"(?<![A-Za-z0-9])[A-Za-z]:[/\\][^\s\"<>]+", "[local file]", value)
        if value.startswith("file:"):
            value = "[local file]"
        return value
    return value


def record_for(state, paper):
    key = state.prisma._find_key(paper.canonical_id)
    return state.prisma.records.get(key) if key else None


def screening_dto(record) -> dict:
    if record is None:
        return {"title_decision": "pending", "full_text_decision": "pending",
                "full_text_retrieved": False, "retrieval_attempted": False,
                "reason": "", "full_text_reason": "", "retrieval_failure_reason": "",
                "status": "not_started", "requires_manual_review": False}
    return {"title_decision": record.screening_decision.value,
            "full_text_decision": record.full_text_decision.value,
            "full_text_retrieved": record.full_text_retrieved,
            "retrieval_attempted": record.retrieval_attempted,
            "reason": record.screening_reason, "full_text_reason": record.full_text_reason,
            "retrieval_failure_reason": record.retrieval_failure_reason,
            "status": paper_screening_status(record),
            "requires_manual_review": record.requires_manual_review}


def paper_dto(state, paper, detail=False) -> dict:
    from litsearch.venues import venue_classification
    providers = sorted({t.provider for t in paper.discovery_traces if t.provider} | {paper.source})
    row = {
        "paper_key": paper_key(paper), "canonical_id": paper.canonical_id,
        "title": paper.title, "abstract": paper.abstract,
        "authors": [{"name": a.name} for a in paper.authors],
        "year": paper.year, "venue": paper.venue, "doi": paper.doi,
        "citation_count": paper.citation_count, "reference_count": paper.reference_count,
        "source": paper.source, "providers": providers,
        "topics": list(paper.topics), "url": paper.url,
        "relevance_score": paper.relevance_score, "score_context_id": paper.score_context_id,
        "score_breakdown": dict(paper.score_breakdown),
        "publication_status": paper.publication_status,
        "search_snippet": paper.search_snippet,
        "abstract_available": bool(paper.abstract),
        "ccf": venue_classification(paper),
        "discovery_traces": [{"method": t.method, "provider": t.provider,
                              "query": t.query, "seed_id": t.seed_id, "round_no": t.round_no,
                              "score": t.score, "evidence_ids": list(t.evidence_ids),
                              "timestamp": t.timestamp} for t in paper.discovery_traces],
        "screening": screening_dto(record_for(state, paper)),
    }
    if detail:
        record = record_for(state, paper)
        row["reference_ids"] = list(paper.reference_ids)
        row["citation_ids"] = list(paper.citation_ids)
        row["history"] = list(record.history) if record else []
        row["conflicts"] = list(record.conflicts) if record else []
    return public(row)


def project_dto(project) -> dict:
    state = project.state
    report = state.prisma.generate_report()
    return public({
        "project_id": project.project_id, "name": project.name, "revision": project.revision,
        "created_at": project.created_at, "updated_at": project.updated_at, "demo": project.demo,
        "topic": state.topic, "research_direction": state.research_direction,
        "phase": state.phase.value, "score_context_id": state.score_context_id,
        "stop_reason": state.stop_reason, "complete_for_retrieval": is_complete(state.stop_reason),
        "http_budget": state.http_budget, "run_history": state.run_history,
        "search_manifest": state.search_manifest, "screening": screening_facts(state),
        "counts": {"records_in_corpus": len(corpus_papers(state)),
                   "records_identified": report.database_results + report.snowball_results
                   + report.similar_results + report.manual_additions,
                   "reports_sought": report.reports_sought,
                   "reports_retrieved": sum(r.full_text_retrieved for r in state.prisma.records.values()
                                            if r.passed_screening),
                   "records_final_included": report.final_included,
                   "units": report.units},
        "active_job_id": project.active_job_id,
    })


def facets_dto(state) -> dict:
    papers = corpus_papers(state)
    counters = {"years": Counter(), "providers": Counter(), "methods": Counter(), "statuses": Counter()}
    for paper in papers:
        row = paper_dto(state, paper)
        counters["years"][paper.year] += 1
        counters["providers"].update(row["providers"])
        counters["methods"].update({t.method for t in paper.discovery_traces})
        counters["statuses"][row["screening"]["status"]] += 1
    return {name: [{"value": value, "count": count} for value, count in
                   sorted(counter.items(), key=lambda row: str(row[0]))]
            for name, counter in counters.items()}


def graph_dto(state) -> dict:
    papers = corpus_papers(state)
    graph = EvidenceGraph(papers, edge_types=RELATION_TYPES)
    payload = graph.to_dict()
    keys = {p.canonical_id: paper_key(p) for p in papers}
    nodes = [{"paper_key": keys[row["id"]], "canonical_id": row["id"],
              "title": row.get("title"), "year": row.get("year"),
              "relevance_score": row.get("relevance", 0), "score_context_id": state.score_context_id}
             for row in payload["nodes"]]
    edges = [{"source": keys[row["source"]], "target": keys[row["target"]],
              "edge_type": row["edge_type"], "score": row.get("score"),
              "witness_count": row.get("witness_count"), "evidence": row["evidence"]}
             for row in [*payload["links"], *payload.get("relations", [])]]
    counts = {name: sum(row["edge_type"] == name for row in edges) for name in RELATION_TYPES}
    summary = graph.summary()
    summary["edge_types"] = counts
    summary["central_papers"] = [{"paper_key": keys[row["paper_id"]], "score": row["score"]}
                                  for row in summary.get("central_papers", [])]
    summary["communities"] = [[keys[pid] for pid in group] for group in summary.get("communities", [])]
    return public({"nodes": nodes, "edges": edges, "edge_type_counts": counts,
                   "summary": summary, "score_context_id": state.score_context_id})


def evidence_dto(state, payload):
    """Attach stable route keys to domain landscape/question evidence briefs."""
    keys = {p.canonical_id: paper_key(p) for p in corpus_papers(state)}

    def convert(item):
        if isinstance(item, list):
            return [convert(value) for value in item]
        if isinstance(item, dict):
            row = {key: convert(value) for key, value in item.items()}
            if row.get("paper_id") in keys:
                row["paper_key"] = keys[row["paper_id"]]
            if isinstance(row.get("paper_ids"), list):
                row["paper_keys"] = [keys[pid] for pid in row["paper_ids"] if pid in keys]
            return row
        return item

    return public(convert(payload))
