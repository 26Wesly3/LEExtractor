"""Readable exports and paired, question-level uncertainty for benchmarks."""

import csv
import json
from pathlib import Path

import numpy as np


def paired_bootstrap(per_case, baseline, challenger, metric="ndcg@20", seed=42, draws=2000):
    pairs = [(rows[baseline].get(metric), rows[challenger].get(metric))
             for rows in per_case.values() if baseline in rows and challenger in rows]
    delta = np.array([b - a for a, b in pairs if isinstance(a, (int, float)) and isinstance(b, (int, float))])
    if len(delta) < 2:
        return {"paired_cases": len(delta), "status": "insufficient_cases"}
    rng = np.random.default_rng(seed)
    means = delta[rng.integers(0, len(delta), size=(draws, len(delta)))].mean(axis=1)
    return {"paired_cases": len(delta), "metric": metric, "challenger": challenger,
            "baseline": baseline, "mean_delta": round(float(delta.mean()), 6),
            "ci95": [round(float(v), 6) for v in np.quantile(means, [0.025, 0.975])],
            "seed": seed, "draws": draws,
            "method": "paired percentile bootstrap over questions; exploratory, no multiple-test correction"}


def write_exports(payload, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    aggregates = payload["aggregate_by_system"]
    columns = ["system", "cases_scored", "cases_skipped", "precision@20", "ndcg@20", "recall@50", "mrr", "latency_seconds"]
    with path.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for name, row in aggregates.items():
            writer.writerow({key: name if key == "system" else row.get(key) for key in columns})
    lines = ["# LEExtractor Benchmark 试验报告", "", f"数据集：{payload['dataset']['name']}", "",
             "按问题分别计算，再按系统取宏平均。空值表示标签或执行条件不足，不表示零分。", "",
             "| 系统 | 问题数 | P@20 | nDCG@20 | 已知相关文献 Recall@50 | MRR | 每题排序秒数 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, row in aggregates.items():
        values = [row.get(key) for key in columns if key not in {"system", "cases_skipped"}]
        lines.append("| " + " | ".join([name] + ["—" if v is None else str(v) for v in values]) + " |")
    lines.extend(["", "## 运行协议", "", "```json", json.dumps(payload.get("protocol", {}), ensure_ascii=False, indent=2), "```", "",
                  "## 对照差异与不确定性", "", "```json", json.dumps(payload.get("paired_comparisons", []), ensure_ascii=False, indent=2), "```", "",
                  "## 解释边界", ""])
    lines.extend("- " + warning for warning in payload.get("warnings", []))
    lines.extend(["", "完整逐题分数见同名 JSON；系统宏平均见同名 CSV。"])
    path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
