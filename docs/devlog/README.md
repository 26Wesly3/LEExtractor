# DevLog 约定（LEExtractor）

> 本目录从 v0.9.14 起强制使用：**任何"代码改完但没有记录"的工作，视为未完成。**

## 1. 文件组织

```
docs/devlog/
├── README.md                 ← 本文件：约定 + 版本索引
├── v0.9.11.followup.md       ← v0.9.11 之后的界面/模型发现/性能基线工作（本次会话）
├── v0.9.12.md                ← 不存在（见 BASELINE_DRIFT_2026-10-12.md）
├── v0.9.13.md                ← 不存在（同上）
├── v0.9.14.md                ← 当前版本
└── BASELINE_DRIFT_2026-10-12.md
```

## 2. 每个版本必须包含

1. **基线**：开发前版本、Git branch、base commit、开发环境（OS / Python / Node / 关键依赖版本）
2. **本次目标**：为什么改、解决什么问题、**不解决什么问题**、哪些语义**禁止改变**
3. **修改记录**：每项一条，固定字段
4. **明确未改动**：单列一节，避免后续开发误判版本行为
5. **Development Summary**：Completed / Fixed / Performance / Tests / Benchmark / Known Issues / Deferred / Risks / Rollback Point

## 3. 修改记录固定字段

```text
问题：
根因：
修改：
涉及文件：
验证方法：
结果：
风险：
```

**Bug 修复必须写完整故障链**，禁止只写"修复模型加载失败"这类症状描述；目的是防止下次再把症状当根因。

## 4. 性能类结论的硬性要求

任何"更快 / 更稳定 / 更省内存 / 更少请求 / 成功率更高"的说法，必须附 Before/After 实测：

| 指标 | Before | After | 变化 |
|---|---:|---:|---:|
| Total latency | | | |
| P50 / P95 / P99 | | | |
| Request count | | | |
| Retry count | | | |
| Peak RAM | | | |
| Failure rate | | | |

**没有数据就不得写"性能明显提升 / 稳定性显著改善 / 大幅优化"**，只能记录真实结果，包括"未测到改善"。

## 5. 三级记录分工

| 层级 | 文件 | 读者 | 只保留 |
|---|---|---|---|
| CHANGELOG | `CHANGELOG.md` | 使用者 | Added / Changed / Fixed / Performance / Known Issues |
| DevLog | `docs/devlog/vX.Y.Z.md` | 开发人员 | 具体做了什么、怎么验证的 |
| ADR | `docs/decisions/ADR-*.md` | 架构/接手者 | 为什么这么决策、否决了什么、怎么回滚 |

历史遗留：仓库原有 `CHANGES.md`（面向使用者的中文更新日志，记录到 v0.9.11）。`CHANGELOG.md` 为本约定新增，两者关系与漂移见 `docs/devlog/BASELINE_DRIFT_2026-10-12.md`。

## 6. 接手前必读（强约束）

开始新版本前必须依次阅读：`CHANGELOG.md` → `docs/devlog/` 最近两个版本 → `docs/decisions/` → 相关测试。

若历史文档与真实代码不一致：

1. **以真实代码与测试结果为准**；
2. 明确记录文档漂移；
3. 修正文档；
4. **不得静默覆盖历史**。

## 7. 版本完成标准

功能、测试、Benchmark、DevLog、CHANGELOG、必要 ADR、Known Issues、Rollback Point 八项齐备才算完成；否则标注 `Release Status: INCOMPLETE`。
