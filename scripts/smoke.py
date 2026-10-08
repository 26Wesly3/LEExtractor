"""End-to-end smoke test against the live APIs.

Usage:
    python scripts/smoke.py "graph neural networks" --max-papers 30

This script exercises the real providers, so it is a *transport and plumbing*
check, not a screening demo. It deliberately performs **no** automatic
screening: v0.9.0 called ``auto_screen`` here with a hardcoded 0.05, which
permanently rejected most of what it had just retrieved on the strength of a
number that means something different in every search. Screening decisions in a
smoke test would be meaningless anyway — nobody read the papers.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from litsearch.prisma import ScreeningDecision, ScreeningStage  # noqa: E402
from litsearch.search import LiteratureReviewWorkflow  # noqa: E402
from litsearch.sources import SourceManager, s2_stats  # noqa: E402
from litsearch.stop_reasons import http_budget_snapshot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("topic", nargs="?", default="plant phenotyping deep learning")
    parser.add_argument("--direction", default="")
    parser.add_argument("--max-papers", type=int, default=30)
    parser.add_argument("--years-back", type=int, default=10)
    parser.add_argument("--rounds", type=int, default=1)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    sources = SourceManager()
    wf = LiteratureReviewWorkflow(source_manager=sources)

    print(f"\n=== Scoping: {args.topic}")
    state = wf.scope_topic(
        args.topic,
        research_direction=args.direction or args.topic,
        years_back=args.years_back,
        initial_limit=args.max_papers,
    )
    print(f"papers={len(state.scoping_papers)} journals={len(state.key_journals)} "
          f"authors={len(state.key_authors)} clusters={len(state.topic_clusters)}")
    if state.key_journals:
        print("top journals:", state.key_journals[:3])
    with_abstract = sum(1 for p in state.scoping_papers if p.abstract)
    print(f"papers with abstract: {with_abstract}/{len(state.scoping_papers)}")

    print("\n=== Systematic search")
    state = wf.systematic_search(state, max_papers=args.max_papers, years_back=args.years_back)
    print(f"found={len(state.search_papers)}")
    for p in state.search_papers[:5]:
        print(f"  - [{p.relevance_score:.3f}] {p.title[:70]} ({p.year}, {p.source})")

    print("\n=== Snowballing")
    state = wf.run_snowballing(state, num_seeds=3, max_rounds=args.rounds, max_per_direction=20)
    sn = state.snowball_result
    print(f"rounds={len(sn.rounds)} discovered={sn.total_discovered} saturated={sn.saturated}")

    print("\n=== Similar papers")
    state = wf.find_similar(state, num_seeds=3, top_k=10)
    print(f"similar={len(state.similar_papers)}")

    print("\n=== Screening queue (no automatic decisions)")
    # The queue is what a human would screen. This script does not decide:
    # relevance scores are relative to this one result set, so a fixed cut-off
    # here would be an unjustified semi-automatic rejection.
    queue = wf.get_screening_queue(state, ScreeningStage.TITLE_ABSTRACT)
    print(f"pending at title/abstract: {len(queue)}")
    for record in queue[:5]:
        print(f"  - [{record.relevance_score:.3f}] {record.paper.title[:70]}")

    # Simulating a reviewer is legitimate *for the plumbing* as long as it is
    # clearly labelled: accept the top slice so the rest of the pipeline (which
    # needs a non-empty included set) has something to work with.
    for record in queue[: max(1, len(queue) // 3)]:
        wf.screen_paper(
            state, record.paper.id, ScreeningDecision.ACCEPT,
            reason="smoke test: simulated reviewer, not a real screening decision",
            stage=ScreeningStage.TITLE_ABSTRACT,
        )
    report = wf.generate_prisma_report(state)
    flow = report.to_flow_dict()
    print("identification:", flow["identification"])
    print("screening:", flow["screening"])
    print("eligibility:", flow["eligibility"])
    print("included:", flow["included"])
    if report.final_included == 0:
        print("!! WARNING: nothing included — relevance scoring did not propagate")

    print("\n=== Stage 2: full-text eligibility")
    full_text_queue = wf.get_screening_queue(state, ScreeningStage.FULL_TEXT)
    print(f"reports sought: {len(full_text_queue)}")
    # Simulate a reviewer: keep everything retrieved, drop half as "wrong outcome"
    for i, record in enumerate(full_text_queue[:10]):
        retrieved = i % 4 != 3
        wf.mark_full_text_retrieved(state, record.paper.id, retrieved)
        if retrieved:
            wf.screen_paper(
                state, record.paper.id,
                ScreeningDecision.ACCEPT if i % 2 == 0 else ScreeningDecision.REJECT,
                reason="结局指标不符 / Wrong outcome",
                stage=ScreeningStage.FULL_TEXT,
            )
    report = wf.generate_prisma_report(state)
    print("eligibility:", report.to_flow_dict()["eligibility"])
    print("included:", report.to_flow_dict()["included"])
    if report.full_text_assessed and report.full_text_excluded == 0:
        print("!! WARNING: full_text_excluded is 0 — the two stages are still conflated")

    print("\n=== Session persistence")
    try:
        from litsearch.persistence import load_state, save_state
        path = save_state(state)
        restored = load_state()
        ok = restored is not None and len(restored.search_papers) == len(state.search_papers)
        print(f"saved -> {path}; restored papers={len(restored.search_papers) if restored else 0}; "
              f"match={ok}")
    except Exception as e:
        print(f"!! persistence failed: {e}")

    print("\n=== Download URL for the top included paper")
    included = wf.get_included_papers(state)
    if included:
        print(included[0].title[:70], "->", wf.downloader.get_download_url(included[0]))
    else:
        print("(no included papers)")

    ok = len(state.search_papers) > 0 and report.records_after_dedup > 0
    print("\nSMOKE", "PASS" if ok else "FAIL")
    stats = s2_stats()
    budget = http_budget_snapshot()
    # Real HTTP accounting, counted at the transport layer: "papers x rounds"
    # is not a request count, because one call pages, retries and falls back.
    print(f"HTTP requests={budget['requests']} retries={budget['retries']} "
          f"cache_hits={budget['cache_hits']} rate_limited={budget['rate_limited']} "
          f"errors={budget['errors']} elapsed={budget['elapsed_seconds']}s")
    print(f"S2 requests={stats['requests']} rate_limited={stats['rate_limited']}")
    print(f"stop_reason={state.snowball_result.stop_reason if state.snowball_result else 'n/a'}")
    if stats["rate_limited"]:
        print("note: set S2_API_KEY to avoid 429s (see ONBOARDING.md)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
