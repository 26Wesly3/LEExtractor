"""Run the retrieval/graph benchmark and write a reproducible report.

Usage
-----
    python -m scripts.run_benchmark --dataset benchmarks/dataset.json --out reports/bench.json
    python -m scripts.run_benchmark --dataset benchmarks/dataset.sample.json   # harness self-check

Real provider calls are **opt-in** via ``--live``. Without it the script only
scores pre-recorded systems (``--snapshot``), which is what a frozen benchmark
should do: the numbers must come from a snapshot, not from today's network.
Live runs are recorded into the report together with the exact per-source query
strings, the search date and the real HTTP accounting, so a reader can see what
was actually asked of which provider.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from litsearch.benchmark import (  # noqa: E402
    BASELINES,
    SEMANTIC_BASELINES,
    BenchmarkDataset,
    CaseResult,
    compare_systems,
)
from litsearch.stop_reasons import http_budget_snapshot, reset_http_budget  # noqa: E402

#: Recorded rankings look like: {"case_id": {"lexical": ["10.1/a", ...]}}
SNAPSHOT_NAME = "systems.json"


def environment_record() -> dict:
    """Everything a reader needs to judge whether a rerun is comparable."""
    record = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    try:
        from importlib.metadata import version

        record["dependencies"] = {
            name: version(name)
            for name in ("streamlit", "scikit-learn", "networkx", "requests", "fastmcp", "numpy", "fastembed", "onnxruntime")
        }
    except Exception as exc:  # pragma: no cover - metadata is best-effort
        record["dependencies_error"] = str(exc)[:200]
    return record


def load_snapshot(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def build_results(
    dataset: BenchmarkDataset, snapshot: dict, live: bool, limit: int
) -> list[CaseResult]:
    """Turn recorded rankings (or a live run) into CaseResults."""
    results: list[CaseResult] = []
    for case in dataset.cases:
        recorded = (snapshot.get("cases") or {}).get(case.case_id) or {}
        for system, ranked in recorded.items():
            results.append(CaseResult(
                case_id=case.case_id,
                system=system,
                ranked_ids=list(ranked.get("ranked_ids", [])) if isinstance(ranked, dict) else list(ranked),
                requests=int((ranked or {}).get("requests", 0)) if isinstance(ranked, dict) else 0,
                latency_seconds=float((ranked or {}).get("latency_seconds", 0.0)) if isinstance(ranked, dict) else 0.0,
                rate_limited=int((ranked or {}).get("rate_limited", 0)) if isinstance(ranked, dict) else 0,
                failures=int((ranked or {}).get("failures", 0)) if isinstance(ranked, dict) else 0,
                candidate_ids=set(ranked.get("candidate_ids", snapshot.get("candidate_ids", []))) if isinstance(ranked, dict) else set(snapshot.get("candidate_ids", [])),
                status=ranked.get("status", "ok") if isinstance(ranked, dict) else "ok",
                notes="recorded snapshot; " + ranked.get("notes", "") if isinstance(ranked, dict) else "recorded snapshot",
            ))
        if live:
            results.extend(run_live_case(case, limit))
    return results


def run_live_case(case, limit: int) -> list[CaseResult]:
    """Record keyword retrieval/ranking only; graph expansion requires real runs."""
    from litsearch.filters import RelevanceFilter
    from litsearch.sources import SourceManager

    reset_http_budget()
    manager = SourceManager()
    started = time.time()
    papers = manager.search_all_sources(
        case.query, limit=limit, year_from=case.year_from if hasattr(case, "year_from") else 1900,
    )
    latency = time.time() - started
    budget = http_budget_snapshot()

    ranked = RelevanceFilter().compute_relevance(list(papers), case.query)
    lexical_ids = [p.canonical_id for p in ranked]

    results = [CaseResult(
        case_id=case.case_id, system="lexical", ranked_ids=lexical_ids,
        requests=budget["requests"], latency_seconds=latency,
        rate_limited=budget["rate_limited"], failures=budget["errors"],
        candidate_ids={p.canonical_id for p in papers},
        notes=f"per-source queries: {json.dumps(manager.last_search_manifest.get('per_source_queries', {}), ensure_ascii=False)}",
    )]

    # Graph construction alone neither retrieves nor ranks new papers. Do not
    # label copies of this ranking as snowball/BC/CC experimental arms.
    if not papers and budget["errors"]:
        results[0].status = "provider_failed"
    return results


def report_payload(dataset: BenchmarkDataset, results: list[CaseResult], live: bool) -> dict:
    comparison = compare_systems(
        results, dataset,
        shared_candidate_set=shared_candidates(results),
    )
    payload = comparison.to_dict()
    payload["dataset"] = {
        "name": dataset.name,
        "frozen_on": dataset.frozen_on,
        "cases_total": len(dataset.cases),
        "cases_labelled": len(dataset.labelled_cases),
        "domains": dataset.domain_coverage,
        "label_gaps": dataset.label_gaps(),
    }
    payload["environment"] = {**dataset.environment, **environment_record()}
    payload["baselines_expected"] = list(BASELINES)
    payload["semantic_baselines_expected"] = list(SEMANTIC_BASELINES)
    payload["warnings"] = build_warnings(dataset, results, live)
    payload["warnings"].append("aggregate is a legacy pooled diagnostic; compare aggregate_by_system instead.")
    return payload


def build_warnings(dataset: BenchmarkDataset, results: list[CaseResult], live: bool) -> list[str]:
    """Say plainly what this report cannot support."""
    warnings = []
    if dataset.label_gaps():
        warnings.append(
            "Unlabelled cases were skipped, not scored: "
            + "; ".join(dataset.label_gaps())
            + ". Metrics below cover only the labelled cases."
        )
    if any("SYNTHETIC" in case.labelled_by.upper() for case in dataset.cases):
        warnings.append(
            "This dataset contains SYNTHETIC labels. No metric here is a result; "
            "it only demonstrates that the harness computes what it claims to."
        )
    present = {result.system for result in results}
    missing = [name for name in BASELINES if name not in present]
    if missing and present:
        warnings.append(f"Baselines not yet recorded: {missing}")
    if not live:
        warnings.append(
            "Scored from a recorded snapshot. A shared candidate pool must be verified "
            "from candidate_ids; ranking alone CANNOT show recovery outside that pool."
        )
    else:
        warnings.append(
            "Live run: provider results are today's, so numbers are not comparable "
            "with a previously frozen snapshot unless the search date matches."
        )
    if any(result.system in SEMANTIC_BASELINES for result in results):
        warnings.append(
            "Semantic arms are reported separately from the frozen lexical baselines."
        )
    return warnings


def shared_candidates(results: list[CaseResult]) -> bool:
    by_case = {}
    for result in results:
        if result.status == "ok":
            by_case.setdefault(result.case_id, []).append(result.candidate_ids)
    return bool(by_case) and all(pools and all(pools) and all(pool == pools[0] for pool in pools)
                                 for pools in by_case.values())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the LEExtractor retrieval benchmark")
    parser.add_argument("--dataset", default="benchmarks/dataset.sample.json")
    parser.add_argument("--snapshot", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--live", action="store_true",
                        help="query the real providers (network, API keys required)")
    args = parser.parse_args(argv)

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        print(f"dataset not found: {dataset_path}", file=sys.stderr)
        print(
            "The real frozen dataset does not exist yet: it needs human relevance "
            "labels. See benchmarks/LABEL_GUIDE.md section 5.",
            file=sys.stderr,
        )
        return 2

    dataset = BenchmarkDataset.load(dataset_path)
    snapshot = load_snapshot(Path(args.snapshot) if args.snapshot else dataset_path.parent / SNAPSHOT_NAME)
    results = build_results(dataset, snapshot, args.live, args.limit)

    if not results:
        print(
            "No rankings to score. Provide a snapshot "
            f"({SNAPSHOT_NAME}) or pass --live with API credentials.",
            file=sys.stderr,
        )
        return 3

    payload = report_payload(dataset, results, args.live)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        from litsearch.benchmark_reports import write_exports

        out = Path(args.out)
        write_exports(payload, out)
        print(f"wrote {out}")
    else:
        print(text)

    for warning in payload["warnings"]:
        print(f"WARNING: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
