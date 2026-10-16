# v3.2 性能报告（PERFORMANCE_REPORT）

- 日期：2026-10-17
- 分支：`feature/v3.2`（未提交、未切换分支）
- 测量机：Windows 11（`Windows-11-10.0.22631-SP0`），AMD Ryzen 7 5800H，**16 逻辑 CPU**，**14188.1 MiB RAM**，Python 3.13.1
- 参考部署（`docs/v3.2/15_PERFORMANCE_DEPLOYMENT.md:5`、`contracts/defaults.json` 的 `performance_targets.reference_vcpu/reference_memory_mib`）：**2 vCPU / 2048 MiB**
- 目标来源：`litsearch/contracts/defaults.json` → `performance_targets`（经 `litsearch.v32.contracts.performance_targets()` 读取，不手抄）
- 可复现脚本：`scripts/v32_perf_probe.py`（只打印 JSON，**不联网**，不使用任何 AI provider）

> **本报告的边界，先说清楚。** 下列数字是在**比参考机快得多**的机器上、对**单进程内计算**测得的。它们证明这套本地排序/反馈/缓存读取路径本身远低于预算，**不能**证明 2 vCPU / 2 GB 参考部署达标——参考部署还包含 provider 网络 I/O、PDF 解析子进程、语义模型加载与编码、AI 调用和 ASGI/HTTP 传输层，这些都不在本脚本的测量范围内。凡未实测的项，本报告一律标 `not_measured` 或 `external_blocked`，不给估计值。

## 1. 测量方法

固定语料：`performance_targets.fixture_papers = 300` 篇合成论文 + 固定确定性向量集（`vector_dim = 32`，每个向量由 `sha256("vec::" + paper_id)` 导出并单位化），20 次重复（`performance_targets.repetitions`）。论文 id / 年份 / 被引数 / `reference_ids` / 向量全部由 `sha256` 决定，同一台机器两次运行输入完全一致。语料与向量不含任何真实论文、真实凭据或个人数据。

记录项：p50、p95、max、min。p95 取**最近秩**（nearest-rank），不对未实际出现的值做插值。冷启动单独记录，不并入 20 次统计。

| 指标（本报告名） | 测量边界 | 契约目标 |
|---|---|---|
| `fast_ranking_seconds` | 已取得候选 → 初步快照；无网络、无语义模型、无 AI | `fast_ranking_p95_seconds` = 2 s |
| `warm_enhancement_seconds` | 全部向量已有、无 AI：同一排序 + 起始文献选择（`core_rank.starter_list`） | `warm_enhancement_p95_seconds` = 5 s |
| `feedback_rerank_seconds` | 用户判断后 → 新本地快照（300 ms 去抖是调度延迟，不在测量区内） | `feedback_p95_seconds` = 1 s |
| `cached_first_screen_seconds` | 已提交快照**从磁盘加载** + `PaperList` 第一页 | `cached_first_result_p95_seconds` = 5 s |
| `metadata_api_ms` | 暖快照上**一次 `PaperList` 分页**，无重计算 | `metadata_api_p95_ms` = 500 ms |

驱动的是产品代码本身：`litsearch/v32/pipeline.py::rank_project`、`build_snapshot`、`load_current_snapshot`、`litsearch/v32/api.py::papers_payload`、`litsearch/v32/core_rank.py::starter_list`；语料通过 `litsearch/v32/store.py::V32Store.create_project/upsert_paper/set_judgement/commit_snapshot` 落盘。判断信号是真实数据：1 篇 `core`、1 篇 `relevant`、1 篇 `excluded`（`exclusion_reason="not_relevant"`）；引用图为 298 条真实 `reference_ids` 边，远高于图谱 5 条边下限。

### 复现命令

```
# 默认（不限制 CPU）
$env:PYTHONUTF8="1"; python scripts/v32_perf_probe.py

# 近似参考机的 2 vCPU 口径（把本进程绑到 2 个逻辑 CPU）
$env:PYTHONUTF8="1"; python scripts/v32_perf_probe.py --cpus 2
```

## 2. 实测结果

两次运行的退出码均为 **0**（脚本 `EXIT=0`）。

### 2.1 默认（整机 16 逻辑 CPU）— 本次报告主口径

| 指标 | p50 | p95 | max | min | 目标 | 判定 |
|---|---|---|---|---|---|---|
| 快速排序 | 0.034190 s | **0.036499 s** | 0.037854 s | 0.031439 s | ≤ 2 s | met |
| 暖缓存本地增强 | 0.082814 s | **0.093027 s** | 0.104148 s | 0.075619 s | ≤ 5 s | met |
| 判断后本地重排 | 0.061328 s | **0.071682 s** | 0.077039 s | 0.052476 s | ≤ 1 s | met |
| 缓存命中完整首屏 | 0.029906 s | **0.036783 s** | 0.086435 s | 0.026665 s | ≤ 5 s | met |
| 元数据 API 分页 | 16.835 ms | **18.214 ms** | 18.482 ms | 13.328 ms | ≤ 500 ms | met |
| 单进程峰值 RSS | — | — | 58.56 MiB | — | 见 §2.4 | not_measured（边界不符） |

### 2.2 绑定 2 逻辑 CPU（`--cpus 2`，`"cpu_affinity": [0, 1]`）

| 指标 | p50 | p95 | max | min | 目标 | 判定 |
|---|---|---|---|---|---|---|
| 快速排序 | 0.039204 s | **0.041870 s** | 0.045332 s | 0.036583 s | ≤ 2 s | met |
| 暖缓存本地增强 | 0.084676 s | **0.087799 s** | 0.088830 s | 0.081483 s | ≤ 5 s | met |
| 判断后本地重排 | 0.061737 s | **0.075241 s** | 0.085839 s | 0.055045 s | ≤ 1 s | met |
| 缓存命中完整首屏 | 0.030499 s | **0.036295 s** | 0.082617 s | 0.027579 s | ≤ 5 s | met |
| 元数据 API 分页 | 15.646 ms | **17.900 ms** | 18.103 ms | 12.281 ms | ≤ 500 ms | met |
| 单进程峰值 RSS | — | — | 58.15 MiB | — | 见 §2.4 | not_measured（边界不符） |

绑定 2 CPU 后 p95 只上升约 10–15%，说明这些路径是内存/解释器开销主导，不是 16 核并行带来的假象。**但这仍不等于 2 vCPU / 2 GB 参考机**：核数相同不代表单核性能、内存带宽、磁盘与后台负载相同。

### 2.3 冷启动（单独记录，未混入上表）

| 项 | 默认 | 2 CPU | 边界 |
|---|---|---|---|
| 首次快速排序（含 `core_rank` 惰性导入） | 0.029027 s | 0.034029 s | 300 篇候选；本进程冷启动，非整机冷启动 |
| 首次快照磁盘加载 | 0.023508 s | 0.023390 s | 首次从磁盘读该快照 |

冷启动口径仅覆盖「进程内首次调用该路径」。**未测量**：「Python 解释器 + 应用导入」整机冷启动、语义模型冷加载（无模型）、HTTP 服务冷启动。标记 `not_measured`。

### 2.4 内存（未达标测量，明确说明）

- 契约 `app_total_peak_rss_mib` = **1400 MiB**，测量边界是「Web、worker、模型和解析器**合计**」（`docs/v3.2/15_PERFORMANCE_DEPLOYMENT.md:33`）。
- 本脚本只能给出**单进程**峰值：**58.56 MiB**（默认）/ **58.15 MiB**（2 CPU）。
- 单进程数字是合计预算的**下界**，既不能证明也不能否定 1400 MiB 目标。本项判定 **`not_measured`**（原因：离线单进程探针无法覆盖多进程合计驻留）。要关闭它需要一次真实部署（Web + worker + 解析子进程 + 模型）的 RSS 采样。

### 2.5 未缓存远程检索首批（≤ 20 s）— `external_blocked`

**判定：`external_blocked`，未尝试。** 理由：本机确实配置了 Semantic Scholar Key（`~/.leextractor/secrets.env` 中存在 `SEMANTIC_SCHOLAR_API_KEY`，只核验存在性，未读取值），但**本次会话没有获得联网授权**，脚本本身也确定不打开任何 socket（`"network_used": false`、`"ai_provider_used": false`）。因此该目标既未通过也未失败，按规范记为 `external_blocked`，不给估计值。要关闭它需要：用户明确批准联网，然后运行 `scripts/v32_provider_probe.py`（该脚本已有，用真实 provider 报告耗时与条数）。

### 2.6 HTTP 传输层 — `not_measured`

上表的「元数据 API」测的是 `papers_payload` 的**进程内**构造耗时，**不含** ASGI/HTTP 传输与序列化后写 socket 的开销。本脚本刻意不经 HTTP 客户端，原因之一是本机 `NO_PROXY` 环境变量含字面量 `[::1]`，会让 httpx 抛 `InvalidURL`——同一环境问题也使 `tests/test_mcp_host_compat.py` 的 3 个用例失败（见 `KNOWN_LIMITATIONS.md`，并在 `ACCEPTANCE_MATRIX.md` 中复现记录）。因此「端到端 HTTP p95」标 **`not_measured`**，不给数字。

## 3. 结论

| 目标 | 结果 |
|---|---|
| 快速排序 p95 ≤ 2 s | **met**（本机 0.036499 s；2 CPU 0.041870 s） |
| 暖缓存本地增强 p95 ≤ 5 s | **met**（本机 0.093027 s；2 CPU 0.087799 s） |
| 判断后本地重排 p95 ≤ 1 s | **met**（本机 0.071682 s；2 CPU 0.075241 s） |
| 缓存命中完整首屏 p95 ≤ 5 s | **met**（本机 0.036783 s；2 CPU 0.036295 s） |
| 元数据 API p95 ≤ 500 ms | **met**（本机 18.214 ms；2 CPU 17.900 ms；进程内口径） |
| 未缓存远程检索首批 ≤ 20 s | **external_blocked**（无联网授权，未尝试） |
| 应用合计峰值内存 ≤ 1400 MiB | **not_measured**（只能给出单进程下界 58.56 MiB） |
| HTTP 端到端 p95 | **not_measured**（本机 httpx/`NO_PROXY` 环境问题，未测） |

**没有任何一项被记为「通过」而没有实测**；没有删除或缩小语料来掩盖结果；`AC-26` 因此记为「本地预算已达标、外部/合计项未测」。

**这不是效果结论。** 本报告只测工程预算，不是 `docs/v3.2/09_ACCEPTANCE_BENCHMARK.md` 的 24 主题专家标注效果评测；后者本轮未执行（`effectiveness_validated` 不可声明，见 `ACCEPTANCE_MATRIX.md`）。
