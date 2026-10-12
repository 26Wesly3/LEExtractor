# ADR-003：任务级受限并发（bounded job concurrency）

- 状态：**Proposed**（v0.9.14 阶段 P4 实施）
- 日期：2026-10-12
- 相关：`litsearch/web/jobs.py`、`litsearch/web/api.py`、`litsearch/sources.py`

## Context

当前每个检索任务由 `threading.Thread` 直接启动（`jobs.py`），**没有并发上限**：

- 20 个 semantic 任务同时进入，会同时持有 20 份「检索 + 排序」状态，其中排序阶段需要常驻的 embedding 模型（ONNX，常驻内存约数百 MB 级别的模型 + 每任务向量矩阵）；
- 共享资源存在写竞争：`http_budget`（HTTP 预算计数）、provider 429 冷却表（`_PROVIDER_COOLDOWNS`）、`cache.db`（按源缓存）、`~/.cache/leextractor/embeddings.db`（向量缓存）；
- 上游（Semantic Scholar / OpenReview）对同一凭据共享限额，无限并发只会把 429 放大；
- 目前没有任务排队语义：请求即起线程，用户无法区分"排队中"和"运行中"。

## Decision

采用**进程内、可配置上限的信号量 + 简单等待队列**：

- 新增配置 `LEEXTRACTOR_MAX_ACTIVE_SEARCH_JOBS`（默认值由 P7 资源测量决定，**不采用"CPU 核数 × 4"这类未经测试的经验公式**）；
- 超过上限的作业进入 `queued` 状态并能被取消，获得执行权后转 `running`；
- 作业状态机保持简单：`queued → running → finished`（含 `failed / partial / canceled`），不引入外部队列中间件；
- 并发控制只限制**同时执行的作业数**，不取消 provider 级并发（provider 并发是延迟优化，见 ADR-004 预留）。

## Alternatives

| 方案 | 否决理由 |
|---|---|
| 全局无限线程池 | 直接把上游 429 与内存峰值放大；破坏 HTTP 预算与冷却的可控性 |
| 外部队列（Redis / Celery / RQ） | 引入服务依赖，与"本地单机优先、含模型交接包"的交付形态冲突 |
| 只在前端限制并发 | 后端仍可被 API 直接打满，问题未解决 |
| 完全串行化（1 个活跃作业） | 用户等待显著变差，且掩盖真实资源上限——任务书明确要求"不要为了通过测试简单取消所有并发" |

## Why

信号量 + 队列是**改动最小、语义最清晰**的方案：既不改变作业结果，也不改变检索算法；同时把内存与上游压力变成**可测、可配**的量。默认值待 P7 资源测量（单任务 CPU/RAM 峰值、5/10 并发下的峰值）后确定，并在 `STABILITY_REPORT_v0.9.14.md` 中写明依据。

## Consequences

- 正面：内存与上游压力可控；`queued` 让"排队 vs 卡住"对用户可见；稳定性压测可复现；
- 负面：超载时延迟上升（用户等待而非失败）；需新增队列状态的 DTO 字段与前端展示；
- 需同步更新：作业 DTO/schema、前端作业面板文案、`/api/jobs` 轮询、取消语义（排队中即可取消）。

## Rollback

- 配置上限设为极大值（或不设上限）即恢复当前行为；代码层面删除信号量分支即可；
- 无数据迁移；无对外 schema 破坏（新增字段为可选）。
