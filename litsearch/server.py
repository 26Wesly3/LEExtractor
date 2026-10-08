"""MCP server for LEExtractor — literature discovery with optional PRISMA reporting.

Exposes tools for the workflow Scoping → Systematic Search → Snowballing →
Title/abstract screening → PRISMA reporting → PDF download.

Two facts about the workflow are load-bearing and are enforced here:

* **Automatic screening is off by default.** ``literature_review_search`` used
  to default to ``auto_screen=True`` with a fixed ``0.15`` relevance cut-off,
  which permanently rejected papers on a score that is only a within-run
  ranking signal. v0.9.0 defaults to ``auto_screen=False`` and only screens
  when a *calibrated* threshold exists (``filters.CalibrationRecord`` with
  ``status == "calibrated"``).
* **Full-text inclusion is a human decision.** No tool here screens the
  ``FULL_TEXT`` stage automatically; the core API refuses it outright.

The screening fields below are the one implementation of that status; the UI,
the MCP tools, the export bundle and the Evidence Pack all read them from here.

Usage:
    python server.py            # root shim
    python -m litsearch.server
    litsearch-mcp               # console script
    fastmcp run litsearch/server.py
"""

from fastmcp import FastMCP

mcp = FastMCP(
    "LEExtractor — Literature Review",
    instructions=(
        "Literature discovery with PRISMA 2020 reporting. Automatic screening is "
        "off by default; title/abstract decisions are preliminary and full-text "
        "inclusion requires explicit human assessment."
    ),
)

# ---------------------------------------------------------------------------
# Screening status (single implementation — contract §13)
# ---------------------------------------------------------------------------

#: Allowed values of ``screening_status``.
SCREENING_STATUS_VALUES = (
    "not_started",
    "preliminary_included",
    "final_included",
    "excluded",
)

#: What each exposed field means, so callers do not have to guess.
SCREENING_FIELD_DEFINITIONS = {
    "screening_status": (
        "Session-level screening state. 'not_started' = no decision recorded yet, or "
        "decisions were recorded but nothing is included while assessments are still open "
        "(an unfinished review is never reported as 'excluded'). "
        "'preliminary_included' = at least one paper passed title/abstract screening and the "
        "full-text stage is not complete; inclusion is preliminary. "
        "'final_included' = the full-text stage is complete and at least one paper was "
        "accepted there (explicit human assessment). "
        "'excluded' = the review is complete at its last stage and no paper is included."
    ),
    "threshold_calibrated": (
        "True only when a calibration record for this session exists and reports "
        "status == 'calibrated' (labelled relevant + irrelevant papers, separated by the "
        "score). Uncalibrated relevance scores rank results and nothing else: they never "
        "justify an automatic reject."
    ),
    "requires_manual_review": (
        "True while any paper is still pending/maybe at either stage, or while the full-text "
        "stage has not been completed. Automation never marks full text as reviewed."
    ),
    "full_text_review_completed": (
        "True only when the full-text stage has been run and every paper accepted at "
        "title/abstract screening has an explicit full-text decision (accept or reject)."
    ),
}

#: Backwards-compatibility note published next to the fields.
SCREENING_COMPATIBILITY_NOTE = (
    "v0.8.0 exposed only 'included_papers' plus PRISMA totals, and "
    "'literature_review_search' auto-screened by relevance >= 0.15 by default. "
    "v0.9.0 keeps 'included_papers'/'studies_included' working but adds "
    "screening_status / threshold_calibrated / requires_manual_review / "
    "full_text_review_completed, defaults auto_screen to False, and returns "
    "candidate papers plus a restorable session so a caller can continue manually. "
    "'included_papers' now means 'passed the last completed stage' and is empty "
    "when screening has not started — read 'candidates' in that case."
)


def calibration_record(state):
    """The session's calibration record (frozen contract §8: ``ReviewState.calibration``).

    ``None`` means the session was never calibrated, which is the normal state
    for a fresh run and the reason automatic screening is off by default.
    """
    return getattr(state, "calibration", None)


def calibration_usable(record) -> bool:
    """Whether an automatic threshold may be applied at all (contract §8).

    ``True`` only for a record whose ``status`` is ``"calibrated"`` — a
    single-class calibration is an ``insufficient_labels`` ranking aid, never a
    boundary. ``CalibrationRecord.usable`` (which also requires a threshold and
    no invalidation) is the authoritative check when the record provides it.
    """
    if record is None:
        return False
    status = getattr(record, "status", None)
    if status is not None and status != "calibrated":
        return False
    usable = getattr(record, "usable", None)
    if isinstance(usable, bool):
        return usable
    return status == "calibrated" and getattr(record, "threshold", None) is not None


def threshold_calibrated(state) -> bool:
    """Whether this session has a usable calibration (contract §13)."""
    return calibration_usable(calibration_record(state))


def calibration_summary(state) -> dict:
    """The calibration behind ``threshold_calibrated``, for display and audit."""
    record = calibration_record(state)
    if record is None:
        return {
            "present": False,
            "status": "",
            "threshold": None,
            "reliable": False,
            "note": "本会话没有标定记录：分数只用于排序。No calibration record: scores only rank.",
        }
    summary = {
        "present": True,
        "status": str(getattr(record, "status", "") or ""),
        "threshold": getattr(record, "threshold", None),
        "reliable": bool(getattr(record, "reliable", False)),
        "query": str(getattr(record, "query", "") or ""),
        "created_at": str(getattr(record, "created_at", "") or ""),
        "labels": dict(getattr(record, "labels", {}) or {}),
        "note": str(getattr(record, "note", "") or ""),
    }
    to_dict = getattr(record, "to_dict", None)
    if callable(to_dict):
        # Round-trippable: pass this back as `literature_review_search(calibration=...)`.
        summary["record"] = to_dict()
    return summary


def _prisma_records(state) -> list:
    prisma = getattr(state, "prisma", None)
    return list(getattr(prisma, "records", {}).values())


def paper_screening_status(record) -> str:
    """Per-paper status using the same four values as ``screening_status``."""
    from litsearch.prisma import ScreeningDecision

    title = record.screening_decision
    full = record.full_text_decision
    if title == ScreeningDecision.REJECT or full == ScreeningDecision.REJECT:
        return "excluded"
    if record.passed_screening and full == ScreeningDecision.ACCEPT:
        return "final_included"
    if record.passed_screening:
        return "preliminary_included"
    return "not_started"


def screening_facts(state) -> dict:
    """Screening status for a session: the four contract fields plus counts.

    See :data:`SCREENING_FIELD_DEFINITIONS` for the exact meaning of each
    value. The counts are included because a coarse status can hide an
    unfinished review, and the caller should be able to see that.
    """
    from litsearch.prisma import ScreeningDecision

    records = _prisma_records(state)
    title_pending = [
        r for r in records
        if r.screening_decision in (ScreeningDecision.PENDING, ScreeningDecision.MAYBE)
    ]
    title_accepted = [r for r in records if r.passed_screening]
    full_pending = [
        r for r in title_accepted
        if r.full_text_decision in (ScreeningDecision.PENDING, ScreeningDecision.MAYBE)
    ]
    full_decided = [
        r for r in title_accepted
        if r.full_text_decision in (ScreeningDecision.ACCEPT, ScreeningDecision.REJECT)
    ]
    included = [
        r for r in records
        if r.passed_screening and r.full_text_decision == ScreeningDecision.ACCEPT
    ]
    prisma = getattr(state, "prisma", None)
    flagged = [r for r in records if getattr(r, "requires_manual_review", False)]
    full_text_review_completed = bool(
        getattr(prisma, "full_text_stage_enabled", False) and full_decided and not full_pending
    )
    decided = [
        r for r in records
        if r.screening_decision != ScreeningDecision.PENDING or full_decided
    ]

    if not decided:
        status = "not_started"
    elif full_text_review_completed:
        status = "final_included" if included else "excluded"
    elif title_accepted:
        status = "preliminary_included"
    elif title_pending or full_pending:
        status = "not_started"
    else:
        status = "excluded"

    return {
        "screening_status": status,
        "threshold_calibrated": threshold_calibrated(state),
        "requires_manual_review": bool(
            not full_text_review_completed or title_pending or full_pending or flagged
        ),
        "full_text_review_completed": full_text_review_completed,
        # Counts, so the coarse status cannot hide an unfinished review.
        "records_total": len(records),
        "title_abstract_decided": sum(
            1 for r in records if r.screening_decision != ScreeningDecision.PENDING
        ),
        "title_abstract_accepted": len(title_accepted),
        "title_abstract_pending": len(title_pending),
        "full_text_decided": len(full_decided),
        "full_text_pending": len(full_pending),
        "included": len(included),
        "flagged_for_manual_review": len(flagged),
        "calibration": calibration_summary(state),
        "definitions": dict(SCREENING_FIELD_DEFINITIONS),
        "compatibility": SCREENING_COMPATIBILITY_NOTE,
    }


def screening_section(state) -> str:
    """Markdown block for AGENT_HANDOFF.md / the Evidence Pack."""
    facts = screening_facts(state)
    lines = [
        "## Screening status",
        "",
        f"- screening_status: {facts['screening_status']} — "
        f"{SCREENING_FIELD_DEFINITIONS['screening_status']}",
        f"- threshold_calibrated: {facts['threshold_calibrated']} — "
        f"{SCREENING_FIELD_DEFINITIONS['threshold_calibrated']}",
        f"- requires_manual_review: {facts['requires_manual_review']} — "
        f"{SCREENING_FIELD_DEFINITIONS['requires_manual_review']}",
        f"- full_text_review_completed: {facts['full_text_review_completed']} — "
        f"{SCREENING_FIELD_DEFINITIONS['full_text_review_completed']}",
        "",
        f"Records: {facts['records_total']}; title/abstract decided: "
        f"{facts['title_abstract_decided']} (accepted {facts['title_abstract_accepted']}, "
        f"pending {facts['title_abstract_pending']}); full-text decided: "
        f"{facts['full_text_decided']} (pending {facts['full_text_pending']}); "
        f"included at full text: {facts['included']}.",
        "",
        ("Inclusion in this pack is **preliminary**: it reflects recorded screening "
         "decisions only. Full-text inclusion requires explicit human assessment, and "
         "uncalibrated relevance scores rank results rather than judging them."
         if not facts["full_text_review_completed"] else
         "The full-text stage is complete for every paper that passed title/abstract "
         "screening; inclusion still reflects human decisions, not an automatic rule."),
        "",
        f"Backwards compatibility: {SCREENING_COMPATIBILITY_NOTE}",
    ]
    return "\n".join(lines)


def apply_calibrated_screening(state, record) -> str:
    """Apply a calibrated threshold as *assistance*, and say what happened.

    Goes through the core API (``PRISMATracker.screen_by_calibrated_threshold``)
    so the refusals live in one place: no calibration, an ``insufficient_labels``
    record, a stale record, a queue mixing scoring batches, or the full-text
    stage all end without a single paper being rejected. Returns a
    human-readable note for the caller — never raises for a missing
    calibration, because "nothing was screened" is a valid outcome that has to
    be reported rather than an error.
    """
    from litsearch.filters import corpus_hash as _corpus_hash
    from litsearch.prisma import AutoScreeningRefused
    from litsearch.search import ranking_query

    if not calibration_usable(record):
        return (
            "请求了 auto_screen=True，但本会话没有可用的标定阈值（CalibrationRecord "
            "status != 'calibrated'），因此没有自动排除任何文献，分数只用于排序。"
            "auto_screen was requested but no usable calibration exists for this session: "
            "nothing was rejected, scores only rank the candidates. "
            "Calibrate from labelled papers first, or keep screening manually."
        )
    try:
        changed = state.prisma.screen_by_calibrated_threshold(
            record,
            query=ranking_query(state),
            corpus_hash=_corpus_hash(state.search_papers),
        )
    except AutoScreeningRefused as exc:
        return (
            f"标定阈值不可用于本次候选集，未做任何自动筛选：{exc} "
            f"Calibration refused by the core API, nothing was screened: {exc}"
        )
    note = (
        f"已按标定阈值 {float(record.threshold):.3f} 对标题/摘要阶段给出 {len(changed)} 条"
        "自动建议（可人工复核与撤销）。全文纳入仍需人工明确评估。"
        "Automatic title/abstract assistance applied with the calibrated threshold "
        f"({len(changed)} decisions, reversible by a reviewer); full-text inclusion still "
        "requires explicit human assessment."
    )
    if not getattr(record, "reliable", False):
        note += (
            " 标定两类样本有重叠，边界附近的判定已标记为需要人工复核。"
            " The calibration's classes overlap, so boundary decisions are flagged for review."
        )
    return note


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

    Automatic screening is **off by default** (changed in v0.9.0): relevance is
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
