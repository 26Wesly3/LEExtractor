# 从 0.9.15 线迁入的评测数据（只读记录）

- `pilot_corerank_2026-10-17/`：3 个主题、8 篇论文的 AI 初评标签（非专家金标准）、OpenAlex 元数据、语义与词法评测结果。
- `pilot_transformer_2026-10-17/`：单查询 "transformer" 的候选池快照与诊断。

这些是开发检查数据，不是效果结论。脚本（`*.py`）依赖 0.9.15 线的 `litsearch.core_view` 与 `litsearch.topic_signal` 模块，未迁移；需要重跑时请改为调用 `litsearch.v32` 的排序入口。
