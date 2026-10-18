"""在冻结的 SciFact 试点上跑一轮「接入真实模型」的 AI 重排对比。

    python scripts/run_benchmark_ai_rerank.py --run-dir artifacts/benchmark-scifact-ai-2026-10-17
    python scripts/run_benchmark_ai_rerank.py --run-dir ... --limit 2      # 先花两题试水
    python scripts/run_benchmark_ai_rerank.py --run-dir ... --window 20

做三件事：
1. 用同一个评分函数把冻结运行里的五个系统重算一遍（顺带校验能否复现 report.csv）；
2. 取 current_hybrid 的前 N 名，交给产品里真实的 AI 重排窗口（litsearch.v32.ai_rerank）
   和真实模型（本机保存的模型配置）重排，窗口外的顺序原样保留（AC-17）；
3. 输出第六行 `ai_rerank`，并与五行参照一起写进对比表。

诚实边界：窗口大小、模型名、调用次数、失败题都写进报告；调用失败的那一题保持
本地顺序并计入 skipped，不把失败当成功。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import time
import zipfile
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from litsearch.benchmark import (  # noqa: E402
    BenchmarkCase,
    LabelledPaper,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from litsearch.v32 import ai_rerank, model_settings  # noqa: E402
from litsearch.v32.ai_providers import create_adapter  # noqa: E402

METRICS = ("precision@20", "ndcg@20", "recall@50", "mrr@50")
#: 冻结报告里的键名（MRR 在 report.json 里叫 mrr）。
FROZEN_KEYS = {"precision@20": "precision@20", "ndcg@20": "ndcg@20", "recall@50": "recall@50", "mrr@50": "mrr"}


def frozen_value(reference: dict, metric: str) -> float:
    return float(reference.get(FROZEN_KEYS[metric], 0.0) or 0.0)


def load_corpus(archive: Path) -> dict[str, dict[str, str]]:
    with zipfile.ZipFile(archive) as zipped:
        rows = [
            json.loads(line)
            for line in zipped.read("scifact/corpus.jsonl").decode().splitlines()
            if line.strip()
        ]
    return {str(row["_id"]): {"title": row.get("title") or "", "text": row.get("text") or ""} for row in rows}


def load_cases(dataset_path: Path) -> list[BenchmarkCase]:
    document = json.loads(dataset_path.read_text(encoding="utf-8"))
    cases: list[BenchmarkCase] = []
    for raw in document["cases"]:
        cases.append(
            BenchmarkCase(
                case_id=str(raw["case_id"]),
                domain=str(raw.get("domain") or "BEIR SciFact"),
                query=str(raw.get("query") or ""),
                search_date=str(raw.get("search_date") or ""),
                providers=list(raw.get("providers") or []),
                notes=str(raw.get("notes") or ""),
                labelled_by=str(raw.get("labelled_by") or ""),
                papers=[
                    LabelledPaper(paper_id=str(p["paper_id"]), label=str(p["label"]))
                    for p in raw.get("papers") or []
                ],
                judgment_policy="unjudged_as_nonrelevant",
            )
        )
    return cases


def score(ranked_ids: list[str], case: BenchmarkCase) -> dict[str, float]:
    relevant = {paper.paper_id for paper in case.papers}
    return {
        "precision@20": precision_at_k(ranked_ids, relevant, 20),
        "ndcg@20": ndcg_at_k(ranked_ids, case, 20),
        "recall@50": recall_at_k(ranked_ids, relevant, 50),
        "mrr@50": reciprocal_rank(ranked_ids[:50], relevant),
    }


def aggregate(rows: list[dict[str, float]]) -> dict[str, float]:
    if not rows:
        return {}
    return {key: mean(row[key] for row in rows) for key in METRICS}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="冻结试点输出目录（含 dataset.json / systems.json）")
    parser.add_argument("--archive", default="artifacts/benchmark-data/scifact.zip")
    parser.add_argument("--out", default="docs/benchmark/scifact-ai-rerank-2026-10-17")
    parser.add_argument("--window", type=int, default=20, help="送进 AI 窗口的条数")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题（试水用）")
    parser.add_argument("--budget", type=float, default=90.0, help="单次模型调用预算（秒）")
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    dataset = json.loads((run_dir / "dataset.json").read_text(encoding="utf-8"))
    systems = json.loads((run_dir / "systems.json").read_text(encoding="utf-8"))["cases"]
    cases = {case.case_id: case for case in load_cases(run_dir / "dataset.json")}
    corpus = load_corpus(Path(args.archive))

    frozen = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    frozen_rows = frozen["aggregate_by_system"]

    baseline_rows: dict[str, list[dict[str, float]]] = {}
    for case_id, per_system in systems.items():
        case = cases.get(str(case_id))
        if case is None:
            continue
        for system, row in per_system.items():
            baseline_rows.setdefault(system, []).append(score(row["ranked_ids"], case))
    recomputed = {system: aggregate(rows) for system, rows in baseline_rows.items()}

    checks = []
    for system, metrics in recomputed.items():
        reference = frozen_rows.get(system, {})
        pair = {
            key: round(abs(metrics[key] - frozen_value(reference, key)), 6)
            for key in METRICS
        }
        checks.append({"system": system, "recomputed": metrics, "frozen": reference, "abs_diff": pair})

    credentials = model_settings.load_persisted(ROOT / ".web-data")
    if not credentials:
        print("没有本机模型配置（.web-data/secrets/model.json），无法跑 AI 行", file=sys.stderr)
        return 2
    adapter = create_adapter(
        credentials.get("endpoint_profile_id") or "custom",
        credentials["secret"],
        model=credentials.get("model"),
        base_url=credentials.get("base_url"),
        dialect=credentials.get("dialect"),
        # 适配器自己的 timeout 才是硬上限；只改 rerank 的 config 不够。
        timeout=float(args.budget),
    )

    ordered_case_ids = [str(case_id) for case_id in systems]
    if args.limit:
        ordered_case_ids = ordered_case_ids[: args.limit]

    ai_rows: list[dict[str, float]] = []
    applied_rows: list[dict[str, float]] = []
    per_case: list[dict] = []
    skipped: list[str] = []
    # 产品默认 20 秒调用预算；基准里 20 篇元数据常超过它，这里用 --budget 放宽
    # 并在报告里写明，否则测出来的是"超时"而不是重排能力。
    config = {"top_n": args.window, "call_budget_seconds": float(args.budget)}
    for index, case_id in enumerate(ordered_case_ids, start=1):
        case = cases[case_id]
        hybrid = list(systems[case_id]["current_hybrid"]["ranked_ids"])[:50]
        items = []
        for paper_id in hybrid[: args.window]:
            row = corpus.get(paper_id) or {}
            items.append(
                ai_rerank.RerankInput(
                    paper_id=paper_id,
                    title=row.get("title") or "",
                    abstract=(row.get("text") or "")[:1200] or None,
                )
            )
        context_hash = hashlib.sha256(case.query.encode("utf-8")).hexdigest()[:16]
        window_ids = "bench-" + case_id
        window = ai_rerank.build_window(
            items,
            context_hash=context_hash,
            intent=case.query,
            judgements_summary="",
            parent_snapshot_id=window_ids,
            config=config,
        )
        started = time.perf_counter()
        outcome = ai_rerank.rerank(
            window,
            adapter,
            parent_snapshot_id=window_ids,
            config=config,
        )
        elapsed = time.perf_counter() - started
        final = ai_rerank.apply_window(hybrid, outcome.ordered_paper_ids, parent_snapshot_id=window_ids)
        metrics = score(final, case)
        ai_rows.append(metrics)
        if outcome.status == "applied":
            applied_rows.append(metrics)
        per_case.append(
            {
                "case_id": case_id,
                "status": outcome.status,
                "error_code": outcome.error_code,
                "detail": (outcome.detail or "")[:160],
                "window": len(outcome.ordered_paper_ids),
                "changed": final != hybrid,
                "moved": sum(1 for a, b in zip(final, hybrid, strict=False) if a != b),
                "ai_order_head": list(outcome.ordered_paper_ids[:5]),
                "local_order_head": hybrid[:5],
                "seconds": round(elapsed, 2),
                **{key: round(value, 4) for key, value in metrics.items()},
            }
        )
        if outcome.status != "applied":
            skipped.append(case_id)
        print(f"[{index}/{len(ordered_case_ids)}] {case_id} {outcome.status} nDCG@20={metrics['ndcg@20']:.4f} {elapsed:.1f}s", flush=True)

    ai_aggregate = aggregate(ai_rows)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "dataset": dataset.get("name"),
        "run_dir": str(run_dir),
        "window": args.window,
        "model": {
            "profile": credentials.get("endpoint_profile_id"),
            "model": credentials.get("model"),
            "host": model_settings.endpoint_host(credentials.get("base_url") or ""),
        },
        "cases": len(ordered_case_ids),
        "skipped": skipped,
        "ai_rerank": {**{key: round(value, 4) for key, value in ai_aggregate.items()}, "cases": len(ai_rows)},
        "ai_rerank_applied_only": {**{key: round(value, 4) for key, value in aggregate(applied_rows).items()}, "cases": len(applied_rows)},
        "call_budget_seconds": config["call_budget_seconds"],
        "recomputed_frozen": {system: {key: round(value, 4) for key, value in rows.items()} for system, rows in recomputed.items()},
        "per_case": per_case,
    }
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = [("current_hybrid", frozen_rows.get("current_hybrid", {}))]
    rows += [(system, frozen_rows[system]) for system in ("bm25", "lexical", "minilm_title", "minilm_title_abstract") if system in frozen_rows]
    rows.append(("ai_rerank", ai_aggregate))
    header = ["system", "ndcg@20", "recall@50", "precision@20", "mrr@50"]
    with (out_dir / "report.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for name, metrics in rows:
            writer.writerow(
                [name]
                + [
                    f"{frozen_value(metrics, key) if name != 'ai_rerank' else float(metrics.get(key, 0.0)):.4f}"
                    for key in ("ndcg@20", "recall@50", "precision@20", "mrr@50")
                ]
            )
    print(json.dumps({"ai_rerank": report["ai_rerank"], "recomputed_frozen": report["recomputed_frozen"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
