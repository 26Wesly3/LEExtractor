"""A reproducible evidence bundle for researchers and downstream tools."""

import io
import json
import zipfile
from dataclasses import asdict

from litsearch.evidence import EvidenceGraph, corpus_papers
from litsearch.export import to_bibtex, to_csv, to_ris
from litsearch.persistence import paper_to_dict, state_to_dict


def build_evidence_pack(state) -> bytes:
    papers = corpus_papers(state)
    graph = EvidenceGraph(papers)
    report = ["# LEExtractor Research Evidence", "", f"Research goal: {state.topic}",
              f"Focus: {state.research_direction}", "", f"Corpus: {len(papers)} papers; {graph.graph.number_of_edges()} observed citation links.",
              "", "Scores are lexical relevance signals within this corpus. They are not quality assessments.",
              "This pack contains metadata and discovery evidence; it does not assert novelty or full-text findings.", "", "## Reading priorities", ""]
    for p in sorted(papers, key=lambda p: p.relevance_score, reverse=True)[:15]:
        methods = sorted({t.method for t in p.discovery_traces})
        report.append(f"- {p.title} ({p.year or '?'}), ID: {p.canonical_id}, relevance: {p.relevance_score:.3f}, discovered via: {', '.join(methods) or 'legacy import'}")
    report += ["", "## Next actions", "", "Check relevance manually, inspect available abstracts and full text, then expand citations if coverage is insufficient.",
               "", "## Available structured files", "", "papers.jsonl, discovery_trace.jsonl, evidence_graph.json, search_manifest.json, session.json, papers.csv, references.ris, references.bib"]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("AGENT_HANDOFF.md", "\n".join(report))
        archive.writestr("papers.csv", "\ufeff" + to_csv(papers, include_abstract=True))
        archive.writestr("references.ris", to_ris(papers))
        archive.writestr("references.bib", to_bibtex(papers))
        archive.writestr("papers.jsonl", "\n".join(json.dumps(paper_to_dict(p), ensure_ascii=False) for p in papers))
        archive.writestr("discovery_trace.jsonl", "\n".join(json.dumps({"paper_id": p.canonical_id, **asdict(t)}, ensure_ascii=False) for p in papers for t in p.discovery_traces))
        archive.writestr("evidence_graph.json", json.dumps(graph.to_dict(), ensure_ascii=False, indent=2))
        archive.writestr("search_manifest.json", json.dumps(state.search_manifest, ensure_ascii=False, indent=2))
        archive.writestr("session.json", json.dumps(state_to_dict(state), ensure_ascii=False))
    return out.getvalue()
