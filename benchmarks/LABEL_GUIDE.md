# Benchmark 数据集格式与标注说明

本文件说明 `benchmarks/` 下的检索 / 图 benchmark 数据格式、标签语义、指标分母，
以及**哪些工作必须由人工完成**。工具已就绪；真实专家标签尚未提供，因此当前不存在
可对外报告的真实指标。

## 1. 文件

| 文件 | 作用 |
| --- | --- |
| `dataset.sample.json` | **合成样例**，只用于验证 harness 与指标计算。`labelled_by` 明确写为 `SYNTHETIC-SAMPLE-NOT-A-HUMAN`，其中任何数字都不得当作结果报告。 |
| `dataset.json` | 真实冻结数据集。**目前不存在**，需人工标注后创建（见 §5）。 |
| `LABEL_GUIDE.md` | 本文件。 |

## 2. 顶层结构

```json
{
  "name": "leextractor-benchmark-v0.9.1",
  "frozen_on": "YYYY-MM-DD",
  "label_guide": "benchmarks/LABEL_GUIDE.md",
  "environment": {"python": "...", "deps": {}, "providers": {}},
  "cases": [ ... ]
}
```

* `frozen_on`：冻结日期。冻结后**不得**再增删 case 或改动标签而不重新标注版本。
* `environment`：运行环境记录，便于复现。

## 3. Case 结构

```json
{
  "case_id": "plant-phenotyping-uav",
  "domain": "plant phenotyping / crops",
  "query": "deep learning for plant phenotyping using UAV imagery",
  "search_date": "2026-10-08",
  "providers": ["semantic_scholar", "openalex", "arxiv"],
  "notes": "为什么选这个题、检索式如何构造、有哪些已知局限",
  "labelled_by": "标注人姓名或标识",
  "papers": [
    {"paper_id": "10.xxxx/yyy", "label": "core_relevant",
     "title": "...", "year": 2021, "evidence_notes": "为什么这样标注"}
  ]
}
```

`paper_id` 使用 **canonical ID**（有 DOI 用 DOI，否则 `openalex:W…` / `arxiv:…`）。

### 覆盖领域（规范 S1 要求 3–5 个，可按资源调整并说明）

| # | 领域 | 状态 |
| --- | --- | --- |
| 1 | 植物表型 / 作物 | 待人工标注 |
| 2 | 分子生物学 | 待人工标注 |
| 3 | Physical AI / Robotics | 待人工标注 |
| 4 | 医学或生命科学 | 待人工标注 |
| 5 | 另一跨学科方向 | 待人工标注 |

## 4. 标签语义

| 标签 | 含义 | 计入 Recall 分母 |
| --- | --- | --- |
| `core_relevant` | 直接回答该研究问题，缺了它综述会有实质缺口 | 是 |
| `peripheral_relevant` | 相关（方法、平台、邻近任务），但不是核心证据 | 是 |
| `key_review` | 该主题的重要综述；按 peripheral relevant 计权 | 是 |
| `known_irrelevant` | 人工确认不相关，用于衡量假阳性 | 否 |
| `seed` | 用于启动 snowball 的起始论文 | **否**（除非同时另有相关标签） |

标注规则：

1. **先看标题 + 摘要**；只有这样才能判断时再取全文。
2. 每个标签必须写 `evidence_notes`，说明判断依据。没有依据的标签视为未完成。
3. `known_irrelevant` 要选**检索式容易命中但确实不相关**的论文，否则衡量不出假阳性。
4. 不得用"系统检索到了"作为相关性的理由（循环论证）。
5. `labelled_by` 必须真实填写。空值或不填会使该 case 被判为不可评分。

## 5. 人工待办（不可由代码代替）

* 建立 3–5 个真实 case 并完成上述标注 → `benchmarks/dataset.json`。
* 规范明确要求：**缺标签时交付工具并标注缺口，不生成虚假的人工金标准。**
  本仓库遵守该要求，因此 `dataset.json` 目前不存在，任何 recall / nDCG 数字都
  尚未产生。

## 6. 指标与分母

| 指标 | 定义 | 分母 |
| --- | --- | --- |
| `recall@k` | 前 k 命中数 / 已标注相关总数 | **已标注相关集合**（不是全领域） |
| `precision@k` | 前 k 命中数 / k | k |
| `ndcg@k` | 分级增益（core=3, review/peripheral=2, 其他=0）折损累计 / 理想值 | 理想排序 |
| `mrr` | 第一个相关结果的秩的倒数 | — |
| `keyword_missed_recovery` | 本法在前 k 中找到、而 lexical 基线**未**找到的相关论文 | — |
| `requests` / `latency` / `rate_limited` / `failures` | 取自语料层真实计数 | — |

必须同时报告：

* `recall_denominator`：该 case 的标注相关总数。
* `top_k_unlabelled`：前 k 中**未被标注**的论文数。数字大说明判断覆盖不足；
  recall 仅描述已标注相关集合，不能解释为全领域召回率的估计或下限。
* 标签覆盖率（`cases_labelled` / `cases_total`）与 `label_gaps`。

**不得**把"已知论文集合"当作全领域完备真值。

自建标注默认采用 `judgment_policy: require_judged`：TopK 存在未知标签时，P@K、nDCG@K 留空，MRR 在存储排名存在未知标签时留空；同时报告 `judged_fraction@K`。公开稀疏 qrels 可显式使用 `unjudged_as_nonrelevant`，按检索评测惯例不给未标注文献相关性收益，但不能将它们描述为已确认无关。短于 K 的正常结果仍用 K 作为 precision 分母。

每个系统分别对问题取宏平均，使用 `aggregate_by_system`；旧 `aggregate` 混合系统，仅保留兼容诊断。系统发生调用失败时跳过质量评分并保留状态。DOI 别名归一化并去重后计算排名，重复输出不能提高召回率；种子排除于发现结果。真实试跑说明见 [PILOT_GUIDE.md](PILOT_GUIDE.md)。

## 7. 基线分组（规范 S1）

| 组 | 名称 | 说明 |
| --- | --- | --- |
| B0 | `lexical` | 仅关键词排序 |
| B1 | `lexical_snowball` | + 引文雪球 |
| B2 | `lexical_bc` | + 文献耦合 |
| B3 | `lexical_cc` | + 共被引 |
| B4 | `lexical_snowball_bc_cc` | 三者合并 |

BC 与 CC **必须独立成组**，不得合并省略对照。语义组（`semantic_only`、
`lexical_semantic_rrf`、`lexical_semantic_citation`、
`lexical_semantic_weighted_sum`）在基线冻结**之后**才启用，并单独报告。

`shared_candidate_set` 必须显式声明：若所有系统重排的是**同一候选集**，则该比较
只能衡量排序质量，**不能**说明找回了候选集之外的论文。要衡量关键词漏检补回，
必须让各系统各自生成候选集，或单独评测语义候选检索 / 查询扩展。

## 8. Graph Benchmark（单独报告）

* 已知核心论文的 PageRank 名次
* 社区与人工主题的一致性
* 路径逐边证据可回溯性
* BC / CC 对 lexical miss 的补回
* 按关系去除的消融
* 节点数、边数、运行时间

关系消融只能解释该算法**实际使用**的图层：citation-only PageRank 不应受未使用的
派生边变化影响（v0.9.1 已修复该隔离，并有回归测试覆盖）。
