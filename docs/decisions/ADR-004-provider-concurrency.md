# ADR-004：provider 级并发（provider-level concurrency）

- 状态：**Accepted**（2026-10-12 已实施，v0.9.12）
- 相关：`litsearch/sources.py`、`litsearch/cache.py`、`tests/test_provider_concurrency.py`
- 关联：ADR-003（任务级并发上限，未实施）

## Context

v0.9.11 的 `SourceManager.search_all_sources_result` 用单个 `for` 循环逐个抓取来源，各来源的等待时间被相加：

- 冷启动实测 115s / 12 次请求，平均 9.6s/请求，其中一次 Crossref 超时独占约 30s；
- 来源越多、上游越慢，用户等待线性增长；
- 每个 provider 已经自带 pacing、429 冷却（`_PROVIDER_COOLDOWNS`）、HTTP 预算与取消检查，
  但这些只有在"来源之间互不阻塞"时才有意义。

## Decision

在 **provider 之间**做受控并发，provider 内部保持串行；不重写任何 provider 适配器：

- `ThreadPoolExecutor(max_workers=workers)`，`workers = min(provider_concurrency(), 来源数)`；
- `provider_concurrency()` 读 `LEEXTRACTOR_PROVIDER_CONCURRENCY`，默认 5、硬上限 16，非法值回退默认并告警；
- 先提交全部任务，再**按 provider 计划顺序** `future.result()`：下游读到的顺序与串行完全一致；
- 异常语义不变：单来源异常仍经 `retrieval_status_for_exception` 转为
  canceled / timeout / budget_exhausted / rate_limited / auth_error / provider_error，不影响其它来源；
- `workers <= 1` 或只有 1 个来源时走原串行路径（`=1` 即完全回退）；
- 按源 HTTP 缓存的 sqlite 连接加 `timeout=15` 忙等待，避免并发写入互相锁死。

## Alternatives

| 方案 | 否决理由 |
|---|---|
| 全局无限线程池 | 同时放大上游 429 与内存峰值；破坏 HTTP 预算与冷却的可控性 |
| asyncio 重写 provider | 6 个适配器与测试替身全部基于 `requests.Session`，重写面远大于收益 |
| 保持串行、只调小超时 | 只能缓解最坏情况，无法改变"等待相加"的本质 |
| 用 `as_completed` 边完成边处理 | 会打乱 provider 结果顺序，下游（manifest、UI 表格）依赖计划顺序 |

## Why

- 收益来自"等待相加 → 取最大"：受控 A/B 实测 `provider_total` 10.68s → **3.73s**（−65%），
  墙钟 15.6s → **8.3s**（−47%），而 Σ(各来源耗时) 基本不变（10.68s → 11.82s），即工作量未减少；
- 保持 provider 内部串行 = pacing / 冷却 / 重试语义零改动，风险集中在"来源之间"；
- 上限可配且默认保守（5），部署环境可按上游配额调整，不需要改代码。

## Consequences

- 正面：冷启动显著变快；故障隔离可量化（5 个来源全部超时仍只需 ≈ 单来源最坏耗时 8.62s）；
  顺序与异常语义不变，现有测试无需改动即通过；
- 负面：并发会同时占用多个上游配额，"429 同时到达"概率上升（冷却仍是 provider-local，总请求数由预算把关）；
  共享状态（预算、冷却表、缓存库）的争用需要在 soak 中持续观察；
- 观测：每个 provider 的耗时单独记录（`timings.providers`），`provider_total` 由"求和"变为"取最大"。

## Rollback

- `LEEXTRACTOR_PROVIDER_CONCURRENCY=1` 立即回到串行行为（无需改代码、无需重启数据）；
- 代码层面删除 `ThreadPoolExecutor` 分支即可回到 v0.9.11 的循环；
- 无数据迁移、无 schema 变更。
