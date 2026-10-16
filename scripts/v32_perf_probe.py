"""Reproducible, network-free performance probe for the LEExtractor v3.2 delivery.

It measures the budgets that ``docs/v3.2/15_PERFORMANCE_DEPLOYMENT.md`` and
``litsearch/contracts/defaults.json`` (``performance_targets``) actually define,
against a fixed synthetic corpus of 300 papers with a fixed deterministic vector
set, on this machine, with **no network** and no AI provider.

    $env:PYTHONUTF8="1"; python scripts/v32_perf_probe.py

Output: a single JSON document on stdout.  Cold-start numbers are recorded
separately from the warm-cache statistics and are never mixed into them.

What is measured, and its boundary (doc 15 "测量边界"):

* ``fast_ranking_seconds``     candidates obtained -> initial snapshot, no network,
                               no semantic model, no AI (``core_rank`` fast path).
* ``warm_enhancement_seconds`` all vectors present, no AI: the same ranking plus
                               the starter-list selection (doc 04 "starter_list").
* ``feedback_rerank_seconds``  a user judgement -> new local snapshot (the 300 ms
                               debounce is outside the measured region; it is a
                               scheduler delay, not compute).
* ``cached_first_screen_seconds``  a committed snapshot loaded from disk plus the
                               first page of ``PaperList``.
* ``metadata_api_ms``          one warm ``PaperList`` page with no recomputation.
* ``probe_rss_mib``            peak RSS **of this single process**.  Doc 15's
                               1400 MiB target covers web + worker + model +
                               parser together, so this number is a lower bound
                               and the report says so; it is not the target.

The corpus is deterministic: paper ids, titles, years, citation counts,
``reference_ids`` and vectors are all derived from ``sha256`` of the index, so
two runs on the same machine produce the same inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: The query the synthetic corpus is built around; it is fixed so the lexical
#: topic scores are reproducible.
QUERY = "mint drought stress transcriptome"

#: Corpus size and vector dimension come from the contract where the contract
#: defines them.
VECTOR_DIM = 32

_WORDS = (
    "mint drought stress transcriptome salinity root shoot leaf gene expression "
    "tolerance yield arabidopsis photosynthesis metabolism pathway cultivar "
    "sequencing network regulation hormone signal"
).split()


def _percentile(values: list[float], quantile: float) -> float | None:
    """Nearest-rank percentile: deterministic, and never interpolates in a way
    that could report a value no run actually produced."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def _summary(values: list[float]) -> dict:
    return {
        "repetitions": len(values),
        "p50": round(statistics.median(values), 6),
        "p95": round(_percentile(values, 0.95), 6),
        "max": round(max(values), 6),
        "min": round(min(values), 6),
    }


def _deterministic_papers(count: int) -> list[dict]:
    """A fixed corpus of ``count`` papers; index -> sha256 -> every field."""
    papers = []
    for index in range(count):
        digest = hashlib.sha256(f"leextractor-perf-paper-{index}".encode("ascii")).digest()
        # Every paper carries four query tokens; the rest is deterministic filler.
        tokens = [_WORDS[digest[offset] % len(_WORDS)] for offset in range(10)]
        title = " ".join(tokens[:4]) + f" study {index}"
        abstract = " ".join(tokens) + " " + QUERY
        references = []
        if index >= 2:
            # 298 observed citation edges, well above the graph's 5-edge floor.
            references = [f"paper-{index - 1:03d}", f"paper-{index - 2:03d}"]
        papers.append(
            {
                "paper_id": f"paper-{index:03d}",
                "title": title,
                "abstract": abstract,
                "year": 2000 + (digest[0] % 26),
                "citation_count": int.from_bytes(digest[1:3], "big") % 500,
                "doi": None,
                "reference_ids": references,
            }
        )
    return papers


def _deterministic_vectors(paper_ids: list[str], dim: int = VECTOR_DIM) -> dict[str, list[float]]:
    """A fixed unit vector per paper, derived only from its id."""
    vectors: dict[str, list[float]] = {}
    for paper_id in paper_ids:
        raw = hashlib.sha256(f"vec::{paper_id}".encode("ascii")).digest()
        values = [((raw[i % len(raw)] / 255.0) * 2.0) - 1.0 for i in range(dim)]
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        vectors[paper_id] = [value / norm for value in values]
    return vectors


def _peak_rss_mib() -> float:
    try:
        import psutil

        info = psutil.Process().memory_info()
        peak = getattr(info, "peak_wset", None) or info.rss
        return round(peak / (1024 * 1024), 2)
    except Exception:  # noqa: BLE001 - the probe reports, it never fails on this
        return None


def _machine() -> dict:
    memory_mib = None
    try:
        import psutil

        memory_mib = round(psutil.virtual_memory().total / (1024 * 1024), 1)
    except Exception:  # noqa: BLE001
        pass
    return {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "cpu_logical": __import__("os").cpu_count(),
        "memory_total_mib": memory_mib,
    }


def pin_cpus(count: int | None) -> list[int] | None:
    """Optionally restrict this process to ``count`` logical CPUs.

    Doc 15's budget is stated for a ~2 vCPU reference host; this machine is much
    wider, so the flag lets the same probe produce a narrower, more comparable
    number.  It never changes the corpus or the work done.
    """
    if not count:
        return None
    try:
        import psutil
    except Exception:  # noqa: BLE001
        return None
    process = psutil.Process()
    available = sorted(process.cpu_affinity())
    chosen = available[: max(1, min(int(count), len(available)))]
    process.cpu_affinity(chosen)
    return chosen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--cpus",
        type=int,
        default=None,
        help="restrict this process to N logical CPUs (doc 15's reference is 2 vCPU)",
    )
    parser.add_argument(
        "--repetitions", type=int, default=None, help="override contracts repetitions (default 20)"
    )
    args = parser.parse_args(argv)
    affinity = pin_cpus(args.cpus)

    started_wall = time.perf_counter()

    from litsearch.v32 import contracts, pipeline
    from litsearch.v32.core_rank import starter_list
    from litsearch.v32.store import V32Store

    targets = contracts.performance_targets()
    paper_count = int(targets["fixture_papers"])
    repetitions = int(args.repetitions or targets["repetitions"])

    papers = _deterministic_papers(paper_count)
    paper_ids = [paper["paper_id"] for paper in papers]
    vectors = _deterministic_vectors(paper_ids)

    result: dict = {
        "probe": "scripts/v32_perf_probe.py",
        "network_used": False,
        "ai_provider_used": False,
        "corpus_papers": paper_count,
        "vector_dim": VECTOR_DIM,
        "repetitions": repetitions,
        "query": QUERY,
        "targets": targets,
        "machine": _machine(),
        "cpu_affinity": affinity,
    }

    with tempfile.TemporaryDirectory(prefix="v32-perf-probe-") as tmp:
        data_dir = Path(tmp)
        store = V32Store(data_dir / "projects")
        anchor = {
            "entry_mode": "topic",
            "original_text": QUERY,
            "objects": [],
            "tasks": [],
            "methods": [],
            "constraints": [],
            "exclusions": [],
            "query_variants": [],
            "seed_paper_ids": [paper_ids[0]],
            "context_version": 1,
            "initial_vector_cache_key": None,
        }
        project = store.create_project(
            owner_id="perf-probe", name="perf-probe", entry_mode="topic", anchor=anchor
        )
        for paper in papers:
            store.upsert_paper(project, paper)

        # A few judgements so the feedback signal is real rather than absent.
        for paper_id, value in (
            (paper_ids[0], "core"),
            (paper_ids[1], "relevant"),
            (paper_ids[2], "excluded"),
        ):
            store.set_judgement(
                project,
                expected_revision=project.revision,
                paper_id=paper_id,
                value=value,
                exclusion_reason="not_relevant" if value == "excluded" else "unspecified",
            )

        state = pipeline.V32State(
            store=store,
            jobs=None,
            sessions=None,
            credentials=None,
            data_dir=data_dir,
            vectors=None,
            query_vector=vectors[paper_ids[0]],
        )

        as_of = pipeline.as_of_year()
        config = pipeline.ranking_config(as_of)
        config_hash = pipeline.config_hash(as_of, None)
        # The snapshot commit compares the frozen versions against the project, so
        # the configuration identity has to be recorded first (doc 14).
        store.set_config_hash(project, config_hash, None)
        frozen = {
            "context_version": int(project.context_version),
            "corpus_version": int(project.corpus_version),
            "config_hash": config_hash,
            "model_revision": None,
        }

        # -- cold start, measured once and reported separately ----------------
        cold_ranking = time.perf_counter()
        cold_result = pipeline.rank_project(state, project, year=as_of)
        cold_ranking = time.perf_counter() - cold_ranking

        cold_start = {
            "first_fast_ranking_seconds": round(cold_ranking, 6),
            "first_fast_ranking_items": len(cold_result.items),
            "note": (
                "includes the lazy import of litsearch.v32.core_rank and the first "
                "build of the ranking; excluded from every repetition statistic below"
            ),
        }

        # -- fast ranking: no vectors, initial snapshot ------------------------
        fast: list[float] = []
        state.vectors = None
        for _ in range(repetitions):
            tick = time.perf_counter()
            ranking = pipeline.rank_project(state, project, year=as_of, config=config)
            pipeline.build_snapshot(state, project, ranking, frozen=frozen)
            fast.append(time.perf_counter() - tick)

        # -- warm enhancement and feedback re-rank: vectors present ------------
        state.vectors = vectors
        warm: list[float] = []
        feedback: list[float] = []
        for _ in range(repetitions):
            tick = time.perf_counter()
            ranking = pipeline.rank_project(state, project, year=as_of, config=config)
            starter_list(ranking, vectors=vectors, pool=paper_ids[:50])
            pipeline.build_snapshot(state, project, ranking, frozen=frozen)
            warm.append(time.perf_counter() - tick)

            tick = time.perf_counter()
            ranking = pipeline.rank_project(state, project, year=as_of, config=config)
            snapshot = pipeline.build_snapshot(state, project, ranking, frozen=frozen)
            feedback.append(time.perf_counter() - tick)

        # Commit one snapshot so the cached-read paths have something real.
        commit_status = store.commit_snapshot(project, snapshot, frozen=frozen)
        if commit_status != "committed":
            raise SystemExit(f"snapshot commit refused: {commit_status}")
        committed = str(snapshot["snapshot_id"])

        # -- cached first screen: cold disk read, then warm reads --------------
        from litsearch.v32 import api

        cold_read = time.perf_counter()
        pipeline.load_current_snapshot(state, project, committed)
        cold_read = time.perf_counter() - cold_read
        cold_start["first_snapshot_load_seconds"] = round(cold_read, 6)

        first_screen: list[float] = []
        metadata: list[float] = []
        for _ in range(repetitions):
            tick = time.perf_counter()
            loaded = pipeline.load_current_snapshot(state, project, committed)
            page = api.papers_payload(
                state, project, cursor=None, snapshot_id=committed, view="recommended", limit=20
            )
            first_screen.append(time.perf_counter() - tick)
            assert loaded is not None and page["items"] is not None

            tick = time.perf_counter()
            api.papers_payload(
                state, project, cursor=None, snapshot_id=committed, view="recommended", limit=20
            )
            metadata.append(time.perf_counter() - tick)

    rss = _peak_rss_mib()
    timings = {
        "fast_ranking_seconds": _summary(fast),
        "warm_enhancement_seconds": _summary(warm),
        "feedback_rerank_seconds": _summary(feedback),
        "cached_first_screen_seconds": _summary(first_screen),
        "metadata_api_ms": {
            "repetitions": len(metadata),
            "p50": round(statistics.median(metadata) * 1000, 3),
            "p95": round(_percentile(metadata, 0.95) * 1000, 3),
            "max": round(max(metadata) * 1000, 3),
            "min": round(min(metadata) * 1000, 3),
        },
    }

    def verdict(p95: float, target: float) -> str:
        return "met" if p95 <= target else "MISSED"

    verdicts = {
        "fast_ranking": verdict(timings["fast_ranking_seconds"]["p95"], targets["fast_ranking_p95_seconds"]),
        "warm_enhancement": verdict(
            timings["warm_enhancement_seconds"]["p95"], targets["warm_enhancement_p95_seconds"]
        ),
        "feedback_rerank": verdict(
            timings["feedback_rerank_seconds"]["p95"], targets["feedback_p95_seconds"]
        ),
        "cached_first_screen": verdict(
            timings["cached_first_screen_seconds"]["p95"], targets["cached_first_result_p95_seconds"]
        ),
        "metadata_api": verdict(
            timings["metadata_api_ms"]["p95"] / 1000.0, targets["metadata_api_p95_ms"] / 1000.0
        ),
    }

    result.update(
        {
            "cold_start": cold_start,
            "timings": timings,
            "verdicts": verdicts,
            "probe_peak_rss_mib": rss,
            "probe_peak_rss_note": (
                "single-process lower bound; doc 15's "
                f"{targets['app_total_peak_rss_mib']} MiB budget covers web + worker + "
                "model + parser processes together and is NOT measured here"
            ),
            "network_first_result_seconds": None,
            "network_first_result_status": "external_blocked",
            "network_first_result_reason": (
                "no user approval for network use in this session; the probe never "
                "opens a socket.  The provider smoke check is scripts/v32_provider_probe.py"
            ),
            "wall_seconds": round(time.perf_counter() - started_wall, 3),
        }
    )

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())