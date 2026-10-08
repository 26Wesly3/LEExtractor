"""MCP server for LEExtractor — literature discovery with optional PRISMA reporting.

Exposes tools for the workflow Scoping → Systematic Search → Snowballing →
Title/abstract screening → PRISMA reporting → PDF download.

Two facts about the workflow are load-bearing and are enforced here:

* **Automatic screening is off by default.** ``literature_review_search`` used
  to default to ``auto_screen=True`` with a fixed ``0.15`` relevance cut-off,
  which permanently rejected papers on a score that is only a within-run
  ranking signal. v0.9.1 defaults to ``auto_screen=False`` and only screens
  when a *calibrated* threshold exists (``filters.CalibrationRecord`` with
  ``status == "calibrated"``).
* **Full-text inclusion is a human decision.** No tool here screens the
  ``FULL_TEXT`` stage automatically; the core API refuses it outright.

The screening fields are the one implementation of that status; the UI, the MCP
tools, the export bundle and the Evidence Pack all read them. That
implementation now lives in :mod:`litsearch.screening` — this module is only the
MCP adapter (server creation, tool declaration, input validation, domain call,
serialization) and re-exports the domain names it needs, so
``from litsearch.server import screening_facts`` keeps working.

Usage:
    python server.py            # root shim
    python -m litsearch.server
    litsearch-mcp               # console script
    fastmcp run litsearch/server.py
"""

from fastmcp import FastMCP

from litsearch.screening import (  # noqa: F401  (re-exported for existing callers)
    SCREENING_COMPATIBILITY_NOTE,
    SCREENING_FIELD_DEFINITIONS,
    SCREENING_STATUS_VALUES,
    apply_calibrated_screening,
    calibration_record,
    calibration_summary,
    calibration_usable,
    paper_screening_status,
    screening_facts,
    screening_section,
    threshold_calibrated,
)

mcp = FastMCP(
    "LEExtractor — Literature Review",
    instructions=(
        "Literature discovery with PRISMA 2020 reporting. Automatic screening is "
        "off by default; title/abstract decisions are preliminary and full-text "
        "inclusion requires explicit human assessment."
    ),
)


@mcp.tool()
def literature_review_search(
    topic: str,
    research_direction: str = "",
    years_back: int = 20,
    max_papers: int = 200,
    snowball_rounds: int = 2,
    auto_screen: bool = False,
    calibration: dict | None = None,
) -> dict:
    """Run the literature discovery workflow: scope, search, snowball, report.

    Phases:
    1. Scoping — field landscape (topic clusters, key journals/authors)
    2. Systematic Search — multi-database search (S2 + OpenAlex + arXiv; Crossref
       resolves metadata, it is not a primary search source)
    3. Snowballing — forward+backward citation tracing
    4. Reporting — recorded screening decisions as a PRISMA 2020 flow diagram

    Automatic screening is **off by default** (changed in v0.9.1): relevance is
    a within-run ranking signal, so an uncalibrated threshold must not reject
    papers. The result therefore carries candidate papers plus a restorable
    ``session`` so a caller can screen manually and continue. Passing
    ``auto_screen=True`` only screens when a calibrated threshold exists; the
    full-text stage is never screened automatically.

    Args:
        topic: Research topic or keywords.
        research_direction: Focus description for relevance scoring.
        years_back: How many years of literature to cover.
        max_papers: Maximum papers for the systematic search phase.
        snowball_rounds: Iterations of citation tracing (1-5).
        auto_screen: Apply a *calibrated* threshold at title/abstract level.
            Defaults to False. Ignored (with an explanation in the result) when
            no calibrated threshold exists.
        calibration: Optional ``CalibrationRecord`` as a dict (the
            ``screening.calibration`` field of a previous session). Only used
            when ``auto_screen=True``; a fresh call has no calibration of its
            own, so this is how a caller reuses one it already derived.

    Returns:
        Scoping summary, PRISMA flow, candidate papers with their per-paper
        screening status, the included papers recorded so far, a restorable
        session, and the screening fields (screening_status,
        threshold_calibrated, requires_manual_review,
        full_text_review_completed).
    """
    from litsearch.persistence import state_to_dict
    from litsearch.search import LiteratureReviewWorkflow

    workflow = LiteratureReviewWorkflow()
    state = workflow.scope_topic(topic, research_direction or topic, years_back)
    workflow.systematic_search(state, max_papers=max_papers, years_back=years_back)
    workflow.run_snowballing(state, max_rounds=snowball_rounds)
    workflow.find_similar(state)

    note = (
        "自动筛选默认关闭：relevance 分数只在单次检索内相对有效，未标定时仅用于排序，"
        "不会自动排除任何文献。请人工完成标题/摘要与全文筛选，或用已知文献标定阈值后再启用。"
        "Automatic screening is off: uncalibrated relevance only ranks results. "
        "Screen manually, or calibrate a threshold from labelled papers first."
    )
    if auto_screen:
        record = calibration_record(state)
        if record is None and calibration:
            from litsearch.filters import CalibrationRecord
            record = CalibrationRecord.from_dict(calibration)
        note = apply_calibrated_screening(state, record)

    report = workflow.generate_prisma_report(state)
    facts = screening_facts(state)

    candidates = []
    for record in state.prisma.records.values():
        paper = record.paper
        candidates.append({
            "title": paper.title,
            "year": paper.year,
            "doi": paper.doi,
            "venue": paper.venue,
            "citation_count": paper.citation_count,
            "relevance_score": round(paper.relevance_score, 4),
            "screening_status": paper_screening_status(record),
            "screening_decision": record.screening_decision.value,
            "full_text_decision": record.full_text_decision.value,
            "download_url": workflow.downloader.get_download_url(paper),
        })
    candidates.sort(key=lambda row: -row["relevance_score"])

    included = workflow.get_included_papers(state)
    included_out = [
        {
            "title": p.title,
            "year": p.year,
            "doi": p.doi,
            "venue": p.venue,
            "citation_count": p.citation_count,
            "relevance_score": round(p.relevance_score, 4),
            "download_url": workflow.downloader.get_download_url(p),
            "authors": [a.name for a in p.authors[:3]],
            "abstract": (p.abstract or "")[:300],
        }
        for p in included[:50]
    ]

    return {
        "topic": topic,
        "research_direction": research_direction or topic,
        "phases_completed": state.phase.value,
        "prisma_flow": report.to_flow_dict(),
        "scoping": {
            "topic_clusters": dict(list(state.topic_clusters.items())[:6]),
            "key_journals": state.key_journals[:10],
            "key_authors": state.key_authors[:10],
            "year_range": (
                f"{min(state.year_distribution.keys())}-{max(state.year_distribution.keys())}"
                if state.year_distribution else "N/A"
            ),
        },
        # Candidates are always returned, so auto_screen=False never leaves the
        # caller with nothing to work on.
        "candidates": candidates[:200],
        "candidate_count": len(candidates),
        "included_papers": included_out,
        "session": state_to_dict(state),
        "screening_status": facts["screening_status"],
        "threshold_calibrated": facts["threshold_calibrated"],
        "requires_manual_review": facts["requires_manual_review"],
        "full_text_review_completed": facts["full_text_review_completed"],
        "screening": facts,
        "calibration": facts["calibration"],
        "screening_note": note,
        "total_tracked": report.records_after_dedup,
        "total_screened": report.records_screened,
        "total_included": getattr(report, "final_included", report.studies_included),
        "download_dir": workflow.downloader.get_download_dir(),
        "tip": (
            "Continue with the returned session: screen_paper decisions in the UI, "
            "explain_paper() for discovery traces, export_research_bundle() for handoff."
        ),
        "compatibility": SCREENING_COMPATIBILITY_NOTE,
    }


# ---------------------------------------------------------------------------
# Tool: snowball_from_seeds
# ---------------------------------------------------------------------------


@mcp.tool()
def snowball_from_seeds(
    paper_dois: list[str],
    research_direction: str = "",
    max_rounds: int = 3,
    max_per_direction: int = 50,
) -> dict:
    """Run forward+backward snowballing from a list of seed paper DOIs.

    Implements the TARCiS methodology: iteratively trace references (backward)
    and citations (forward) until saturation.

    Args:
        paper_dois: List of DOIs to use as seeds.
        research_direction: Focus description for relevance scoring.
        max_rounds: Maximum snowball iterations.
        max_per_direction: Max papers to fetch per seed per direction.

    Returns:
        Snowballing rounds and discovered papers.
    """
    from litsearch.filters import RelevanceFilter
    from litsearch.snowball import SnowballEngine
    from litsearch.sources import SourceManager

    sources = SourceManager()
    filters = RelevanceFilter()

    seeds = []
    for doi in paper_dois:
        paper = sources.resolve_doi(doi.strip())
        if paper:
            seeds.append(paper)

    if not seeds:
        return {"error": "No valid seed papers found from provided DOIs"}

    engine = SnowballEngine(sources=sources, filters=filters)
    result = engine.run(
        seed_papers=seeds,
        research_direction=research_direction,
        max_rounds=max_rounds,
        max_per_direction=max_per_direction,
    )

    rounds_out = []
    for rnd in result.rounds:
        rounds_out.append({
            "round": rnd.round_number,
            "new_papers_count": rnd.count,
            "new_papers": [
                {
                    "title": p.title,
                    "year": p.year,
                    "doi": p.doi,
                    "citation_count": p.citation_count,
                    "relevance_score": round(p.relevance_score, 4),
                }
                for p in rnd.new_papers[:15]
            ],
        })

    return {
        "seed_papers": [s.title[:100] for s in seeds],
        "total_rounds": len(result.rounds),
        "total_discovered": result.total_discovered,
        "saturated": result.saturated,
        "saturation_reason": result.saturation_reason,
        "rounds": rounds_out,
    }


# ---------------------------------------------------------------------------
# Tool: find_similar_papers
# ---------------------------------------------------------------------------


@mcp.tool()
def find_similar_papers(
    paper_dois: list[str],
    top_k: int = 20,
) -> dict:
    """Find similar papers via bibliographic coupling and co-citation analysis.

    Two methods are combined:
    1. Bibliographic coupling — papers that share references with your seeds
    2. Co-citation — papers frequently cited alongside your seeds

    Args:
        paper_dois: List of seed paper DOIs.
        top_k: Maximum number of similar papers to return.

    Returns:
        List of similar papers with similarity scores and discovery method.
    """
    from litsearch.similar import SimilarPaperFinder
    from litsearch.sources import SourceManager

    sources = SourceManager()

    seeds = []
    for doi in paper_dois:
        paper = sources.resolve_doi(doi.strip())
        if paper:
            seeds.append(paper)

    if not seeds:
        return {"error": "No valid seed papers found from provided DOIs"}

    finder = SimilarPaperFinder(sources=sources)
    results = finder.find_similar(seeds, top_k=top_k)

    out = []
    for paper, score, method in results:
        out.append({
            "title": paper.title,
            "year": paper.year,
            "doi": paper.doi,
            "venue": paper.venue,
            "citation_count": paper.citation_count,
            "similarity_score": round(score, 4),
            "method": method,
            "authors": [a.name for a in paper.authors[:3]],
        })

    return {
        "seed_papers": [s.title[:100] for s in seeds],
        "similar_papers": out,
        "total_found": len(out),
        "by_bibliographic_coupling": sum(1 for _, _, m in results if m == "bibliographic_coupling"),
        "by_cocitation": sum(1 for _, _, m in results if m == "co_citation"),
    }


# ---------------------------------------------------------------------------
# Tool: download_paper
# ---------------------------------------------------------------------------


@mcp.tool()
def download_paper(paper_doi_or_title: str) -> dict:
    """Download an open-access PDF for a paper by DOI or title.

    Tries legitimate sources in order: arXiv preprint → OpenAlex OA →
    Unpaywall (publisher-hosted OA copy).  Paywalled papers are reported as
    unavailable rather than fetched from shadow libraries.

    Args:
        paper_doi_or_title: DOI (e.g. "10.1038/nature12345") or paper title.

    Returns:
        Download result with file path or error.
    """
    from litsearch.downloader import PaperDownloader
    from litsearch.sources import SourceManager

    sources = SourceManager()

    if paper_doi_or_title.startswith("10."):
        paper = sources.resolve_doi(paper_doi_or_title)
    else:
        papers = sources.search_papers(paper_doi_or_title, limit=1)
        paper = papers[0] if papers else None

    if paper is None:
        return {"error": f"Paper not found: {paper_doi_or_title}"}

    downloader = PaperDownloader()
    filepath = downloader.download_pdf(paper)

    return {
        "title": paper.title,
        "doi": paper.doi,
        "year": paper.year,
        "venue": paper.venue,
        "downloaded": filepath is not None,
        "file_path": filepath or "",
        "download_dir": downloader.get_download_dir(),
    }


# ---------------------------------------------------------------------------
# Tool: batch_download
# ---------------------------------------------------------------------------


@mcp.tool()
def batch_download(dois: list[str]) -> dict:
    """Download multiple papers by DOI list.

    Args:
        dois: List of DOIs to download.

    Returns:
        Summary of downloads (success count, failures, file paths).
    """
    from litsearch.downloader import PaperDownloader
    from litsearch.sources import SourceManager

    sources = SourceManager()
    downloader = PaperDownloader()

    success = []
    failed = []

    for doi in dois:
        paper = sources.resolve_doi(doi.strip())
        if paper is None:
            failed.append({"doi": doi, "reason": "Not found"})
            continue
        filepath = downloader.download_pdf(paper)
        if filepath:
            success.append({"doi": doi, "title": paper.title[:100], "path": filepath})
        else:
            failed.append({"doi": doi, "title": paper.title[:100], "reason": "Download failed"})

    return {
        "success_count": len(success),
        "failed_count": len(failed),
        "success": success,
        "failed": failed,
        "download_dir": downloader.get_download_dir(),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


@mcp.tool()
def plan_search_strategy(topic: str, research_direction: str = "", years_back: int = 20) -> dict:
    """Parse a research question into slots and build one query per database.

    Purely local — no network call. Use it to inspect and edit the search
    strings before searching; the same record is stored in the search manifest
    afterwards, which is what PRISMA 2020 asks to be reported per database.

    The parser is rule-based: slots it could not fill stay empty and are listed
    in ``warnings`` rather than being filled with a plausible-looking guess.
    """
    from litsearch.config import current_year, year_from_years_back
    from litsearch.intent import describe_plan, plan_payload

    if not topic.strip() or not 1 <= years_back <= 100:
        raise ValueError("Provide a topic and 1–100 years back")
    year_from = year_from_years_back(years_back, floor=1990)
    payload = plan_payload(topic.strip(), research_direction.strip(), year_from, current_year())
    payload["readable"] = describe_plan(payload["queries"])
    return payload


@mcp.tool()
def search_literature(topic: str, research_direction: str = "", years_back: int = 20, max_papers: int = 100) -> dict:
    """Discover literature without automatic screening or PDF downloads.

    Returns a serializable session for the evidence and export tools.
    Crossref is a metadata resolver; search providers are S2, OpenAlex and arXiv.

    No screening decision is made here, so the returned screening fields read
    ``screening_status="not_started"``, ``threshold_calibrated=False``,
    ``requires_manual_review=True`` and ``full_text_review_completed=False``:
    what comes back is a candidate set, not a review result.
    """
    from litsearch.diagnostics import reset_diagnostics
    from litsearch.persistence import state_to_dict
    from litsearch.search import LiteratureReviewWorkflow, ReviewState

    if not topic.strip() or not 1 <= max_papers <= 500 or not 1 <= years_back <= 100:
        raise ValueError("Provide a topic, 1–500 papers and 1–100 years back")
    reset_diagnostics()
    workflow = LiteratureReviewWorkflow()
    state = ReviewState(topic.strip(), research_direction.strip() or topic.strip())
    workflow.systematic_search(state, max_papers=max_papers, years_back=years_back)
    facts = screening_facts(state)
    return {
        "session": state_to_dict(state),
        "paper_count": len(state.search_papers),
        "manifest": state.search_manifest,
        "screening_status": facts["screening_status"],
        "threshold_calibrated": facts["threshold_calibrated"],
        "requires_manual_review": facts["requires_manual_review"],
        "full_text_review_completed": facts["full_text_review_completed"],
        "screening": facts,
        "screening_note": (
            "本工具不做筛选：返回的是候选集。分数只用于排序，纳入需要人工判断。"
            "No screening was performed: this is a candidate set; relevance only ranks it."
        ),
    }


@mcp.tool()
def explain_paper(session: dict, paper_id: str) -> dict:
    """Return observed discovery traces and score components for one paper."""
    from litsearch.evidence import corpus_papers
    from litsearch.identifiers import identifier_key, paper_aliases
    from litsearch.persistence import paper_to_dict, state_from_dict

    for paper in corpus_papers(state_from_dict(session)):
        if identifier_key(paper_id) in paper_aliases(paper):
            return paper_to_dict(paper)
    return {"error": "Paper not found in this session"}


@mcp.tool()
def build_research_landscape(session: dict, include_relations: bool = True) -> dict:
    """Analyze the observed corpus: citation structure, topics, temporal trends.

    Citation structure comes from observed references only. Topics, temporal
    trends, novelty and coverage are computed over the supplied sample — they
    describe what was retrieved, not the field as a whole.
    """
    from litsearch.evidence import EvidenceGraph, corpus_papers
    from litsearch.landscape import build_landscape
    from litsearch.persistence import state_from_dict

    papers = corpus_papers(state_from_dict(session))
    edge_types = ("citation", "bibliographic_coupling", "co_citation", "semantic") \
        if include_relations else ("citation",)
    graph = EvidenceGraph(papers, edge_types=edge_types)
    return {"summary": graph.summary(), "graph": graph.to_dict(),
            "landscape": build_landscape(papers)}


@mcp.tool()
def candidate_research_questions(session: dict, max_questions: int = 6) -> dict:
    """Suggest candidate research questions grounded in the collected evidence.

    Each item carries its rationale, supporting papers, the nearest existing
    work, the coverage gap behind it, its risks, ``computation_basis`` (the
    counts and thresholds it came from) and ``suggested_next_search`` (the entry
    point for checking it) — and every item is marked ``status: hypothesis``.
    Nothing here is a novelty verdict: metadata alone cannot establish that
    something is unexplored.

    Citation claims come from the same observed facts the evidence graph uses.
    ``coverage_unknown``/``needs_verification`` mark items whose citation
    coverage is incomplete, so "no citation observed" is never reported as a
    confirmed zero; the payload's ``citation_coverage`` block gives the
    corpus-level view.
    """
    from litsearch.evidence import corpus_papers
    from litsearch.intent import parse_intent
    from litsearch.landscape import build_landscape
    from litsearch.persistence import state_from_dict
    from litsearch.questions import candidate_questions

    if not 1 <= max_questions <= 20:
        raise ValueError("max_questions must be between 1 and 20")
    state = state_from_dict(session)
    papers = corpus_papers(state)
    landscape = build_landscape(papers)
    intent = parse_intent(state.topic, state.research_direction)
    payload = candidate_questions(papers, landscape=landscape,
                                  intent={"slots": intent.slot_dict()},
                                  max_questions=max_questions)
    payload["paper_count"] = len(papers)
    return payload


@mcp.tool()
def get_evidence_path(session: dict, source_id: str, target_id: str) -> dict:
    """Find a directed citation path supported by the supplied session."""
    from litsearch.evidence import EvidenceGraph, corpus_papers
    from litsearch.persistence import state_from_dict

    graph = EvidenceGraph(corpus_papers(state_from_dict(session)))
    path = graph.path(source_id, target_id)
    return {"path": path, "found": bool(path), "edge_direction": "citing -> cited"}


@mcp.tool()
def export_research_bundle(session: dict) -> dict:
    """Return structured papers, provenance, manifest and graph for AI handoff.

    The screening fields are part of the handoff: a downstream reader must be
    able to tell "nothing was screened yet" from "screened and included", and
    must see that uncalibrated relevance is a ranking signal.
    """
    from litsearch.evidence import EvidenceGraph, corpus_papers
    from litsearch.persistence import paper_to_dict, state_from_dict

    state = state_from_dict(session)
    papers = corpus_papers(state)
    facts = screening_facts(state)
    return {
        "papers": [paper_to_dict(p) for p in papers],
        "search_manifest": state.search_manifest,
        "evidence_graph": EvidenceGraph(papers).to_dict(),
        "session": session,
        "screening_status": facts["screening_status"],
        "threshold_calibrated": facts["threshold_calibrated"],
        "requires_manual_review": facts["requires_manual_review"],
        "full_text_review_completed": facts["full_text_review_completed"],
        "screening": facts,
        "agent_handoff_notes": screening_section(state),
    }


def main():
    mcp.run()


if __name__ == "__main__":
    main()
