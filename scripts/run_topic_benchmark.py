"""Capture one real topic, source rankings and a blind human annotation sheet."""

import argparse
import csv
import json
import random
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from litsearch.benchmark import BenchmarkCase, BenchmarkDataset  # noqa: E402
from litsearch.cache import Cache  # noqa: E402
from litsearch.config import current_year  # noqa: E402
from litsearch.filters import RelevanceFilter  # noqa: E402
from litsearch.local_models import LocalEmbedding  # noqa: E402
from litsearch.query_context import focused_plan, prepare_query  # noqa: E402
from litsearch.sources import SourceManager, retrieval_status_for_exception  # noqa: E402
from litsearch.stop_reasons import http_budget_snapshot, reset_http_budget  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", default="深度学习")
    parser.add_argument("--direction", default="计算机视觉领域的多智能体合作问题")
    parser.add_argument("--english", default="", help="optional manual translation; omitted uses local Marian")
    parser.add_argument("--out", default="artifacts/benchmark-topic")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--year-from", type=int, default=2020)
    parser.add_argument("--year-to", type=int, default=current_year())
    args = parser.parse_args(argv)
    if args.limit <= 0 or args.year_from > args.year_to:
        parser.error("positive limit and valid year range required")
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    context = prepare_query(args.topic, args.direction, args.english)
    plan = focused_plan(context, args.year_from, args.year_to)
    manager = SourceManager(cache=Cache(str(output / "sources.db")))
    sources = [manager.s2, manager.oa, manager.arxiv, manager.cr, manager.openreview, manager.scholar]
    snapshot = {"cases": {"topic-001": {}}}
    systems = snapshot["cases"]["topic-001"]
    candidates, manifests = [], []
    reset_http_budget()
    for source in sources:
        # Same result limit and explicit transport budget for every provider.
        source.TOTAL_TIMEOUT_SECONDS = 45
        source.set_request_budget(5)
        source.MAX_HTTP_RETRIES = 1
        query = plan["queries"][source.name]["query"]
        print(f"Searching {source.name}...", flush=True)
        started = time.perf_counter()
        try:
            result = source._search_papers_result(query, args.limit, args.year_from, args.year_to)
            manifest = result.to_dict()
            papers = result.papers
        except Exception as exc:
            manifest = {"provider": source.name, "status": retrieval_status_for_exception(exc).value,
                        "error": str(exc)[:200], "paper_count": 0, "complete": False}
            papers = []
        elapsed = time.perf_counter() - started
        stats = http_budget_snapshot()["by_source"].get(source.name, {})
        manifest["transport_accounting"] = dict(stats)
        manifest.update({"query": query, "limit": args.limit, "latency_seconds": round(elapsed, 3)})
        manifests.append(manifest)
        candidates.extend(papers)
        systems["provider_" + source.name] = {"ranked_ids": [p.canonical_id for p in papers],
            "candidate_ids": [p.canonical_id for p in papers], "latency_seconds": elapsed,
            "requests": stats.get("requests", 0), "rate_limited": stats.get("rate_limited", 0),
            "failures": int(bool(manifest.get("error"))),
            "status": "ok" if papers or manifest["status"] == "success_empty" else manifest["status"],
            "notes": f"source snapshot, {manifest['status']}; no human relevance labels yet"}
        print(f"{source.name}: {manifest['status']}, {len(papers)} records", flush=True)
        (output / "providers.json").write_text(json.dumps({"query_context": context, "plan": plan, "providers": manifests}, ensure_ascii=False, indent=2), encoding="utf-8")
    papers = RelevanceFilter().deduplicate_by_doi(candidates)
    for name, semantic in (("lexical", False), ("current_hybrid", True)):
        filter_ = RelevanceFilter()
        if semantic:
            filter_.embedding_model = LocalEmbedding()
        started = time.perf_counter()
        ranking = filter_.compute_relevance(list(papers), context["translated"])
        systems[name] = {"ranked_ids": [p.canonical_id for p in ranking[:50]],
                         "candidate_ids": [p.canonical_id for p in papers],
                         "latency_seconds": time.perf_counter() - started,
                         "notes": "same pooled candidate set, local ranking only; awaiting human labels"}
    data = BenchmarkDataset(name="Computer vision multi-agent topic trial", frozen_on=time.strftime("%Y-%m-%d"),
                            cases=[BenchmarkCase("topic-001", "computer vision multi-agent cooperation", context["translated"],
                                                 time.strftime("%Y-%m-%d"), providers=[s.name for s in sources],
                                                 notes="Original Chinese query and translations in providers.json; human annotation pending")])
    (output / "dataset.json").write_text(json.dumps(data.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "systems.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "candidates.json").write_text(json.dumps([asdict(p) for p in papers], ensure_ascii=False, indent=2), encoding="utf-8")
    pooled = {pid for row in systems.values() for pid in row["ranked_ids"][:20]}
    review = [p for p in papers if p.canonical_id in pooled]
    random.Random(42).shuffle(review)
    with (output / "annotation.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["case_id", "paper_id", "title", "abstract", "year", "url", "label", "evidence_notes"])
        writer.writeheader()
        for paper in review:
            writer.writerow({"case_id": "topic-001", "paper_id": paper.canonical_id, "title": paper.title,
                             "abstract": paper.abstract or "", "year": paper.year, "url": paper.url,
                             "label": "", "evidence_notes": ""})
    print(f"Saved {len(papers)} identifier-deduplicated candidates; {len(review)} blind annotation rows. No relevance labels assigned.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
