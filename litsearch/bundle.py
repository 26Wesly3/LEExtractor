"""A reproducible evidence bundle for researchers and downstream tools."""

import io
import json
import zipfile
from dataclasses import asdict

from litsearch.evidence import EvidenceGraph, corpus_papers
from litsearch.export import to_bibtex, to_csv, to_ris
from litsearch.intent import parse_intent
from litsearch.landscape import build_landscape
from litsearch.persistence import paper_to_dict, state_to_dict
from litsearch.questions import candidate_questions
from litsearch.screening import paper_screening_status, screening_facts, screening_section


def build_evidence_pack(state) -> bytes:
    papers = corpus_papers(state)
    # Canonical relation names only ("text_similarity", not the v0.9.0 alias
    # "semantic") so nothing downstream sees two names for one relation.
    graph = EvidenceGraph(
        papers,
        edge_types=("citation", "bibliographic_coupling", "co_citation", "text_similarity"),
    )
    landscape = build_landscape(papers)
    questions = candidate_questions(
        papers, landscape=landscape,
        intent={"slots": parse_intent(state.topic, state.research_direction).slot_dict()},
    )
    screening = screening_facts(state)
    statuses = _screening_statuses(state)
    report = ["# LEExtractor Research Evidence", "", f"Research goal: {state.topic}",
              f"Focus: {state.research_direction}", "", f"Corpus: {len(papers)} papers; {graph.graph.number_of_edges()} observed citation links.",
              "", "Scores are lexical relevance signals within this corpus. They are not quality assessments.",
              "This pack contains metadata and discovery evidence; it does not assert novelty or full-text findings.",
              "", screening_section(state), "", "## Reading priorities", ""]
    for p in sorted(papers, key=lambda p: p.relevance_score, reverse=True)[:15]:
        methods = sorted({t.method for t in p.discovery_traces})
        report.append(f"- {p.title} ({p.year or '?'}), ID: {p.canonical_id}, relevance: {p.relevance_score:.3f}, discovered via: {', '.join(methods) or 'legacy import'}")
    report += ["", "## Next actions", "", "Check relevance manually, inspect available abstracts and full text, then expand citations if coverage is insufficient.",
               "", _search_strategy_section(state), "", _questions_section(questions),
               "", _landscape_section(landscape),
               "", "## Available structured files", "", "papers.jsonl, discovery_trace.jsonl, evidence_graph.json, landscape.json, questions.json, search_manifest.json, session.json, screening.json, papers.csv, references.ris, references.bib"]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("AGENT_HANDOFF.md", "\n".join(report))
        archive.writestr("papers.csv", "\ufeff" + to_csv(papers, include_abstract=True))
        archive.writestr("references.ris", to_ris(papers))
        archive.writestr("references.bib", to_bibtex(papers))
        archive.writestr("papers.jsonl", "\n".join(
            json.dumps({**paper_to_dict(p), "screening_status": statuses.get(p.canonical_id, "not_started")},
                       ensure_ascii=False)
            for p in papers))
        archive.writestr("discovery_trace.jsonl", "\n".join(json.dumps({"paper_id": p.canonical_id, **asdict(t)}, ensure_ascii=False) for p in papers for t in p.discovery_traces))
        archive.writestr("evidence_graph.json", json.dumps(graph.to_dict(), ensure_ascii=False, indent=2))
        archive.writestr("landscape.json", json.dumps(landscape, ensure_ascii=False, indent=2))
        archive.writestr("questions.json", json.dumps(questions, ensure_ascii=False, indent=2))
        archive.writestr("search_manifest.json", json.dumps(state.search_manifest, ensure_ascii=False, indent=2))
        archive.writestr("screening.json", json.dumps(screening, ensure_ascii=False, indent=2))
        archive.writestr("session.json", json.dumps(state_to_dict(state), ensure_ascii=False))
    return out.getvalue()


def _screening_statuses(state) -> dict[str, str]:
    """``canonical_id -> screening_status`` for the per-paper lines in the pack.

    Uses ``litsearch.screening`` (the single implementation) rather than the MCP
    adapter: the pack is built by the Streamlit app and by ``build_evidence_pack``
    callers that never start the MCP server, so the domain must not sit behind
    ``fastmcp``.
    """
    records = getattr(getattr(state, "prisma", None), "records", {}) or {}
    statuses: dict[str, str] = {}
    for record in records.values():
        paper = getattr(record, "paper", None)
        if paper is not None:
            statuses[paper.canonical_id] = paper_screening_status(record)
    return statuses


def _search_strategy_section(state) -> str:
    """Per-database search strings, for reporting (PRISMA 2020 item 7).

    The point of writing this out is that a review must be re-runnable: whoever
    reads the pack needs the exact string each database was given, not a
    paraphrase of the research question. If no parsed plan exists (older
    sessions, or a plan that was switched off), the raw query is reported and
    labelled as such instead of being reconstructed.
    """
    lines = ["## Search strategy (per database)", ""]
    plan = (state.search_manifest or {}).get("research_intent")
    if not plan:
        raw = (state.search_manifest or {}).get("query") or state.topic
        return "\n".join(lines + [
            f"No structured query plan was recorded for this session; every "
            f"provider received: {raw}",
            "",
            "This is not a per-database strategy and should not be reported as one.",
        ])

    slots = plan.get("slots", {})
    lines.append(f"Parsed from: {plan.get('direction') or plan.get('topic')}")
    lines.append(f"Parser confidence: {plan.get('confidence')} (rule-based, not a language model).")
    for label, key in (("object", "object"), ("method", "method"), ("task", "task"), ("scenario", "scenario")):
        terms = slots.get(key) or []
        lines.append(f"- {label}: {', '.join(terms) if terms else '— (not recovered)'}")
    if plan.get("unparsed"):
        lines.append(f"- not placed in any slot: {plan['unparsed']}")
    lines += ["", "Queries actually sent:"]
    for source, entry in (plan.get("queries") or {}).items():
        if not entry.get("query"):
            continue
        lines.append(f"- {source}: `{entry['query']}`")
        for extra in ("filter", "date_range"):
            if entry.get(extra):
                lines.append(f"    - {extra}: `{entry[extra]}`")
    for warning in plan.get("warnings", []):
        lines.append(f"- caveat: {warning}")
    if plan.get("degraded"):
        lines.append("- The per-database strategy was not applied; the recorded "
                     "strings are the raw query.")
    return "\n".join(lines)


def _questions_section(questions: dict) -> str:
    """Candidate questions, written out with their caveats attached.

    The risk line travels with every item on purpose: a question copied into a
    proposal without its caveat becomes a false claim of novelty.
    """
    lines = ["## Candidate research questions (hypotheses)", ""]
    if not questions.get("usable"):
        return "\n".join(lines + [questions.get("note", ""),
                                  "", "No questions are proposed rather than proposing weak ones."])
    if not questions.get("questions"):
        return "\n".join(lines + [questions.get("note", "")])
    for index, item in enumerate(questions["questions"], 1):
        lines.append(f"{index}. {item['question']}")
        lines.append(f"   - rationale: {item['rationale']}")
        for row in item.get("supporting_papers", [])[:3]:
            lines.append(f"   - supporting: {row.get('title')} ({row.get('year') or '?'})")
        for row in item.get("nearest_existing_work", [])[:3]:
            lines.append(f"   - nearest existing work: {row.get('title')} ({row.get('year') or '?'})")
        lines.append(f"   - gap: {item['coverage_gap']}")
        lines.append(f"   - risk: {item['risks']}")
        lines.append(f"   - suggested next search: {item['suggested_next_search']}")
        lines.append("")
    lines.append("All items are hypotheses derived from this corpus only; none is a "
                 "novelty verdict, and a missing gap does not prove a direction is unexplored.")
    return "\n".join(lines)


def _landscape_section(landscape) -> str:
    """Human-readable landscape summary for AGENT_HANDOFF.md.

    Written separately from the JSON so a downstream reader sees the caveats
    before the numbers, not only the numbers.
    """
    lines = ["## Landscape (computed from this corpus only)", "",
             landscape["topics"].get("note", "")]
    for topic in landscape["topics"]["topics"]:
        lines.append(f"- {topic['topic_id']}: {topic['size']} papers · "
                     f"{', '.join(topic['terms'][:5]) or '—'}")
    temporal = landscape["temporal"]
    if temporal["timeline"]:
        lines += ["", f"Latest year: {temporal['latest_year']}; "
                      f"share from the last three years: {temporal['recent_share']:.0%}."]
        for row in temporal["emerging"]:
            lines.append(f"- {row['topic_id']} is gaining recency share ({row['delta']:+.0%}).")
        for row in temporal["declining"]:
            lines.append(f"- {row['topic_id']} is losing recency share ({row['delta']:+.0%}).")
    scored = [row for row in landscape["novelty"]["rows"] if row["novelty"] is not None]
    if scored:
        lines += ["", "Most distinctive relative to earlier work in this sample:"]
        for row in scored[:5]:
            lines.append(f"- {row['title'][:90]} ({row['year']}) · {row['novelty']:.2f}")
    if landscape["coverage"]["topics"]:
        lines += ["", f"Coverage balance: {landscape['coverage']['balance']:.2f} (1.0 = evenly spread)."]
        for row in landscape["coverage"]["gaps"]:
            lines.append(f"- Thinly covered: {row['topic_id']} · "
                         f"{', '.join(row['terms'][:4]) or '—'}")
    lines += ["", "These describe retrieved papers, not the field as a whole. "
                   "Low coverage means under-retrieval, not unimportance."]
    return "\n".join(lines)
