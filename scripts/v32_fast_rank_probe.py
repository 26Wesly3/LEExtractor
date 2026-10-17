"""Fast-ranking probe on the fixed 300-paper synthetic fixture (doc 15, AC-26).

    python scripts/v32_fast_rank_probe.py [--repetitions N] [--out PATH]

It measures only the fast-ranking path (doc 15 "快速阶段"): candidates already
in the project -> ``rank_project`` -> ``build_snapshot``, with no network, no AI
and no semantic model (lexical topic, age-adjusted impact and the observed
citation graph only).  The corpus is the deterministic one of
``scripts/v32_perf_probe.py``.  Cold start is recorded once and kept out of the
repetition statistics.

The result is written as UTF-8 JSON, by default to
``benchmarks/v32/fast_ranking_probe.json``.  It is labelled **local host, not
the reference host**: doc 15's budget is defined for a 2 vCPU / 2 GiB host with
no concurrent load, and this probe does not run there.  The JSON therefore
reports the target next to the measurement and never states that the target is
met.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_OUT = REPO_ROOT / "benchmarks" / "v32" / "fast_ranking_probe.json"
HOST_LABEL = "local host, not the reference host"


def _perf_probe():
    """The shared deterministic corpus helpers of ``scripts/v32_perf_probe.py``."""
    path = Path(__file__).resolve().with_name("v32_perf_probe.py")
    spec = importlib.util.spec_from_file_location("_v32_perf_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(repetitions: int | None = None, out_path: Path | str | None = DEFAULT_OUT) -> dict:
    """Measure the fast-ranking path and (optionally) write the JSON result."""
    perf = _perf_probe()
    from litsearch.v32 import contracts, pipeline, semantic
    from litsearch.v32.store import V32Store

    targets = contracts.performance_targets()
    paper_count = int(targets["fixture_papers"])
    reps = int(repetitions or targets["repetitions"])
    papers = perf._deterministic_papers(paper_count)
    paper_ids = [paper["paper_id"] for paper in papers]

    # The fast stage never waits for a semantic model (doc 15); make that true
    # here even on a machine that has the pinned weights on disk.
    original_reason = semantic.model_reason
    semantic.model_reason = lambda: "probe_fast_path_lexical_only"
    try:
        with tempfile.TemporaryDirectory(prefix="v32-fast-rank-probe-") as tmp:
            data_dir = Path(tmp)
            store = V32Store(data_dir / "projects")
            project = store.create_project(owner_id="fast-rank-probe", name="fast-rank-probe", entry_mode="topic")
            project.doc["anchor"]["original_text"] = perf.QUERY
            for paper in papers:
                store.upsert_paper(project, paper)
            state = pipeline.V32State(
                store=store, jobs=None, sessions=None, credentials=None, data_dir=data_dir
            )
            as_of = pipeline.as_of_year()
            config = pipeline.ranking_config(as_of)
            config_hash = pipeline.config_hash(as_of, None)
            store.set_config_hash(project, config_hash, None)
            frozen = {
                "context_version": int(project.context_version),
                "corpus_version": int(project.corpus_version),
                "config_hash": config_hash,
                "model_revision": None,
            }

            tick = time.perf_counter()
            ranking = pipeline.rank_project(state, project, year=as_of, config=config)
            pipeline.build_snapshot(state, project, ranking, frozen=frozen)
            cold = time.perf_counter() - tick
            items = len(ranking.items)

            timings: list[float] = []
            for _ in range(reps):
                tick = time.perf_counter()
                ranking = pipeline.rank_project(state, project, year=as_of, config=config)
                pipeline.build_snapshot(state, project, ranking, frozen=frozen)
                timings.append(time.perf_counter() - tick)
    finally:
        semantic.model_reason = original_reason

    reference = {
        "vcpu": targets["reference_vcpu"],
        "memory_mib": targets["reference_memory_mib"],
    }
    result = {
        "probe": "scripts/v32_fast_rank_probe.py",
        "measured_path": "fast_ranking: rank_project + build_snapshot (lexical, no semantic model, no AI)",
        "host": HOST_LABEL,
        "reference_host": reference,
        "measured_on_reference_host": False,
        "target_status": "not_assessed: measured on the local host, not the reference host",
        "fast_ranking_p95_target_seconds": targets["fast_ranking_p95_seconds"],
        "machine": perf._machine(),
        "network_used": False,
        "ai_provider_used": False,
        "semantic_model_used": False,
        "corpus_papers": paper_count,
        "ranked_items": items,
        "repetitions": reps,
        "cold_start_seconds": round(cold, 6),
        "fast_ranking_seconds": {
            "repetitions": len(timings),
            "p50": round(statistics.median(timings), 6),
            "p95": round(perf._percentile(timings, 0.95), 6),
            "max": round(max(timings), 6),
            "min": round(min(timings), 6),
        },
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if out_path is not None:
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repetitions", type=int, default=None, help="default: contracts repetitions (20)")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="JSON output path")
    args = parser.parse_args(argv)
    result = run(args.repetitions, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
