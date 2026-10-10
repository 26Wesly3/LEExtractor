# LEExtractor Benchmark 试验报告

数据集：BEIR SciFact test pilot (30 queries)

按问题分别计算，再按系统取宏平均。空值表示标签或执行条件不足，不表示零分。

| 系统 | 问题数 | P@20 | nDCG@20 | 已知相关文献 Recall@50 | MRR | 每题排序秒数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| bm25 | 30 | 0.045 | 0.6631 | 0.8433 | 0.6367 | 0.0033 |
| current_hybrid | 30 | 0.0467 | 0.68 | 0.8767 | 0.6379 | 16.961 |
| lexical | 30 | 0.045 | 0.5941 | 0.81 | 0.5339 | 14.7106 |
| minilm_title | 30 | 0.035 | 0.5438 | 0.73 | 0.504 | 0.0184 |
| minilm_title_abstract | 30 | 0.0383 | 0.4856 | 0.7367 | 0.4268 | 0.0176 |

## 运行协议

```json
{
  "candidate_documents": 5183,
  "pool_id_sha256": "9948d896b35ae4e7c41b492f221009b39d23b61738c2b7ff63e8591c78b418d6",
  "test_queries_total": 300,
  "evaluated_queries": [
    "1014",
    "1086",
    "1088",
    "1099",
    "1100",
    "1204",
    "1216",
    "124",
    "1266",
    "1290",
    "132",
    "133",
    "142",
    "185",
    "198",
    "208",
    "213",
    "230",
    "239",
    "312",
    "324",
    "491",
    "50",
    "54",
    "619",
    "628",
    "693",
    "808",
    "887",
    "922"
  ],
  "sample_seed": 42,
  "sampling": "sorted random.sample of test qrel query IDs; no relevance-dependent selection",
  "systems": [
    "bm25",
    "lexical",
    "minilm_title",
    "minilm_title_abstract",
    "current_hybrid"
  ],
  "embedding_model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
  "embedding_revision": "faf4aa4225822f3bc6376869cb1164e8e3feedd0",
  "pool_preparation_seconds": 784.907,
  "hardware": "CPU, ONNX threads=4",
  "bm25": "k1=1.2 b=0.75 sklearn English stopwords; title x3 + abstract",
  "lexical": "actual RelevanceFilter word/char/coverage; query+corpus TF-IDF fit per question",
  "hybrid": "actual RelevanceFilter 0.60 semantic + 0.15 lexical + 0.25 concept coverage",
  "gain": "binary public qrels mapped to equal gain 2; equivalent binary nDCG",
  "unjudged": "standard public-qrels evaluation: unjudged treated nonrelevant, not verified irrelevant",
  "rank_cutoff": 50,
  "mrr_cutoff": 50,
  "training": "none; no parameter tuning on test qrels",
  "run_mode": "first corpus encoding and 30-query execution; latest scorer applied to frozen rankings; separate one-query cached rerun"
}
```

## 对照差异与不确定性

```json
[
  {
    "paired_cases": 30,
    "metric": "ndcg@20",
    "challenger": "current_hybrid",
    "baseline": "bm25",
    "mean_delta": 0.016927,
    "ci95": [
      -0.061229,
      0.101497
    ],
    "seed": 42,
    "draws": 2000,
    "method": "paired percentile bootstrap over questions; exploratory, no multiple-test correction"
  },
  {
    "paired_cases": 30,
    "metric": "ndcg@20",
    "challenger": "lexical",
    "baseline": "bm25",
    "mean_delta": -0.06897,
    "ci95": [
      -0.147612,
      -0.003118
    ],
    "seed": 42,
    "draws": 2000,
    "method": "paired percentile bootstrap over questions; exploratory, no multiple-test correction"
  },
  {
    "paired_cases": 30,
    "metric": "ndcg@20",
    "challenger": "minilm_title",
    "baseline": "bm25",
    "mean_delta": -0.119273,
    "ci95": [
      -0.236857,
      0.000695
    ],
    "seed": 42,
    "draws": 2000,
    "method": "paired percentile bootstrap over questions; exploratory, no multiple-test correction"
  },
  {
    "paired_cases": 30,
    "metric": "ndcg@20",
    "challenger": "minilm_title_abstract",
    "baseline": "bm25",
    "mean_delta": -0.177523,
    "ci95": [
      -0.330527,
      -0.024307
    ],
    "seed": 42,
    "draws": 2000,
    "method": "paired percentile bootstrap over questions; exploratory, no multiple-test correction"
  },
  {
    "paired_cases": 30,
    "metric": "ndcg@20",
    "challenger": "current_hybrid",
    "baseline": "lexical",
    "mean_delta": 0.085897,
    "ci95": [
      0.008038,
      0.177257
    ],
    "seed": 42,
    "draws": 2000,
    "method": "paired percentile bootstrap over questions; exploratory, no multiple-test correction"
  },
  {
    "paired_cases": 30,
    "metric": "ndcg@20",
    "challenger": "minilm_title_abstract",
    "baseline": "minilm_title",
    "mean_delta": -0.05825,
    "ci95": [
      -0.204892,
      0.099808
    ],
    "seed": 42,
    "draws": 2000,
    "method": "paired percentile bootstrap over questions; exploratory, no multiple-test correction"
  }
]
```

## 解释边界

- Baselines not yet recorded: ['lexical_snowball', 'lexical_bc', 'lexical_cc', 'lexical_snowball_bc_cc']
- Scored from a recorded snapshot. A shared candidate pool must be verified from candidate_ids; ranking alone CANNOT show recovery outside that pool.
- aggregate is a legacy pooled diagnostic; compare aggregate_by_system instead.
- SciFact tests biomedical claim-to-abstract retrieval; it does not establish CCF conference coverage, Chinese translation quality or competitor superiority.
- This is a seeded subset pilot unless --queries 0 was used. P@20 is diluted because most questions have only one or a few positive qrels.
- Public qrels are sparse; unjudged_as_nonrelevant follows IR evaluation convention. Custom human topic benchmarks default to require_judged.
- MRR is truncated at the stored top 50. Shared setup cost is reported separately; timings are exploratory, unreplicated and do not include network retrieval.
- BM25 here is a local algorithm reference, not the Google Scholar/Semantic Scholar/Connected Papers product. No external product score has been measured.
- No citation edges are included in this corpus run, so snowball/BC/CC are not scored.

完整逐题分数见同名 JSON；系统宏平均见同名 CSV。
