"""MCP server for LEExtractor — Systematic Literature Review Toolkit.

Exposes tools for the 4-phase review workflow:
Scoping → Systematic Search → Snowballing → PRISMA → PDF Download.

Usage:
    python server.py            # root shim
    python -m litsearch.server
    litsearch-mcp               # console script
    fastmcp run litsearch/server.py
"""

from fastmcp import FastMCP

mcp = FastMCP(
    "LEExtractor — Literature Review",
    instructions="Systematic literature review with PRISMA 2020 + TARCiS methodology",
)

@mcp.tool()
def literature_review_search(
    topic: str,
    research_direction: str = "",
    years_back: int = 20,
    max_papers: int = 200,
    snowball_rounds: int = 2,
    auto_screen: bool = True,
) -> dict:
    """Run the complete 4-phase systematic literature review workflow.

    Phases:
    1. Scoping — field landscape (topic clusters, key journals/authors)
    2. Systematic Search — multi-database search (S2 + OpenAlex + arXiv + Crossref)
    3. Snowballing — TARCiS-aligned forward+backward citation tracing
    4. PRISMA Report — screening + PRISMA 2020 flow diagram

    Use this when you need to: write a literature review, understand a research
    field's evolution, find key papers for a systematic review.

    Args:
        topic: Research topic or keywords.
        research_direction: Focus description for relevance scoring.
        years_back: How many years of literature to cover.
        max_papers: Maximum papers for systematic search phase.
        snowball_rounds: Iterations of citation tracing (1-5).
        auto_screen: If True, auto-screen by relevance >= 0.15 threshold.

    Returns:
        Complete review results with scoping, search, snowballing, PRISMA data,
        and included papers with download URLs.
    """
    from litsearch.search import LiteratureReviewWorkflow

    workflow = LiteratureReviewWorkflow()

    if auto_screen:
        state = workflow.run_full_workflow(
            topic=topic,
            research_direction=research_direction or topic,
            years_back=years_back,
            max_search_papers=max_papers,
            snowball_rounds=snowball_rounds,
        )
    else:
        state = workflow.scope_topic(topic, research_direction or topic, years_back)
        workflow.systematic_search(state, max_papers=max_papers, years_back=years_back)
        workflow.run_snowballing(state, max_rounds=snowball_rounds)
        workflow.find_similar(state)

    report = workflow.generate_prisma_report(state)
    flow = report.to_flow_dict()

    included = workflow.get_included_papers(state)
    included_out = []
    for p in included[:50]:
        dl = workflow.downloader.get_download_url(p)
        included_out.append({
            "title": p.title,
            "year": p.year,
            "doi": p.doi,
            "venue": p.venue,
            "citation_count": p.citation_count,
            "relevance_score": round(p.relevance_score, 4),
            "download_url": dl,
            "authors": [a.name for a in p.authors[:3]],
            "abstract": (p.abstract or "")[:300],
        })

    return {
        "topic": topic,
        "research_direction": research_direction or topic,
        "phases_completed": state.phase.value,
        "prisma_flow": flow,
        "scoping": {
            "topic_clusters": dict(list(state.topic_clusters.items())[:6]),
            "key_journals": state.key_journals[:10],
            "key_authors": state.key_authors[:10],
            "year_range": (
                f"{min(state.year_distribution.keys())}-{max(state.year_distribution.keys())}"
                if state.year_distribution else "N/A"
            ),
        },
        "included_papers": included_out,
        "total_tracked": report.records_after_dedup,
        "total_screened": report.records_screened,
        "total_included": report.studies_included,
        "download_dir": workflow.downloader.get_download_dir(),
        "tip": "Use snowball_from_seeds() to expand from specific papers, or download_paper() to get PDFs.",
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
    return {"session": state_to_dict(state), "paper_count": len(state.search_papers), "manifest": state.search_manifest}


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
    work, the coverage gap behind it, its risks and a suggested next search —
    and every item is marked ``status: hypothesis``. Nothing here is a novelty
    verdict: metadata alone cannot establish that something is unexplored.
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
    """Return structured papers, provenance, manifest and graph for AI handoff."""
    from litsearch.evidence import EvidenceGraph, corpus_papers
    from litsearch.persistence import paper_to_dict, state_from_dict

    state = state_from_dict(session)
    papers = corpus_papers(state)
    return {"papers": [paper_to_dict(p) for p in papers], "search_manifest": state.search_manifest,
            "evidence_graph": EvidenceGraph(papers).to_dict(), "session": session}


def main():
    mcp.run()


if __name__ == "__main__":
    main()
