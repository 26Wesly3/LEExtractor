# ROADMAP — 未完成与后续项

本文件**只写尚未完成或属于后续版本的内容**。已完成能力见 [README.md](README.md)，已完成改动见 [CHANGES.md](CHANGES.md)。

状态用词：`planned`（已计划）/ `in_progress`（进行中）/ `deferred`（本版不做）/ `pending_external`（等外部条件：人工标签、凭据、机构权限）/ `verified`（本版已实现并有回归测试；仅用于纠正过期的「未完成」条目）。

## 1. 正确性收口（v0.9.1 内仍未完成的部分）

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 【v0.9.1 已修复，此处保留供核对】PRISMA「未获取」与「全文排除」分开 | `verified` | 已修复：`mark_full_text_retrieved(..., False)` 不再自动置 `REJECT`，记录保持 `PENDING`，只计入 `reports_not_retrieved`；`retrieval_attempted` 区分「尝试过但失败」与「尚未尝试」；人工仍可显式排除并计入 `full_text_excluded`。回归测试：`tests/test_acceptance_abc.py::test_acceptance_g6_*`（3 项）。 |
| 本机 Web 多项目与多标签隔离 | `verified` | v0.9.7 接入项目级文件、任务绑定与 revision 冲突检测；共享数据源统计要求检索任务串行。多人部署仍需要认证与用户级隔离，不能把本机项目隔离当作账号隔离。 |
| 重复来源的计数一致性 | `planned` | 目前计数单位是 record/report；同一研究的多个报告尚未合并到研究层面。 |

## 2. Benchmark 与评测

**v0.9.1 已交付的部分（`verified`）**：评测数据格式、标签语义与分母规则（`benchmarks/LABEL_GUIDE.md`）、指标与对照报告实现（`litsearch/benchmark.py`）、运行脚本（`scripts/run_benchmark.py`）、合成样例与录制快照（`benchmarks/dataset.sample.json`、`systems.json`），27 项测试覆盖指标算术与「未标注必须跳过而非臆造」。**这些都不是评测结果。**

**仍未完成的部分：**

- `pending_external`：3–5 个真实案例的冻结数据集 `benchmarks/dataset.json`（植物表型/作物、分子生物学、Physical AI/Robotics、医学或生命科学、跨学科方向各一）。标签含 core relevant / peripheral relevant / known irrelevant / key reviews / seed papers，并记录 query、检索日期、provider、canonical ID 与证据备注。**人工标签必须由人提供**，代码不生成假金标准。
- `planned`：五组检索基线对照的实际运行与数字：Lexical、Lexical+Snowball、Lexical+BC、Lexical+CC、Lexical+Snowball+BC+CC（BC 与 CC 必须分开，不能合并成一组）。框架已支持这五组命名，但需要先有 `dataset.json`。
- `planned`：Graph Benchmark 的实际报告：已知核心论文的 PageRank 名次、社群与人工主题一致性、路径逐边可回溯性、BC/CC 对词法漏检的补回、按关系消融、节点/边/运行时间。
- `planned`：标定集与最终评估集分离，冻结日期、去重规则、论文 ID 与配置，报告失败案例。

## 3. Semantic Retrieval 与融合（基线冻结后）

- `verified`：v0.9.9 接入固定修订 FastEmbed 多语言 MiniLM 的 CPU 重排，query 与 title+abstract 编码、cosine、长摘要分块与缓存；manifest 保留模型和评分上下文。可替换多模型及人工评测仍 `planned`。
- `planned`：RRF 与 weighted sum 两种融合对照；实验覆盖 Lexical、Semantic only、Lexical+Semantic RRF、Lexical+Semantic+Citation。
- `verified`：v0.9.9 明确实现 **semantic reranking**，候选仍由数据库召回；全库向量召回仍 `planned`。
- `verified`：翻译约 116 MB、Embedding 约 252 MB，首次下载后复用缓存；模型不可用时提示，用户可改用英文查询与词法模式。

## 4. 智能分析增强

- `planned`：Research Intent v2 后续结构化解析（v0.9.9 已实现中文方向本地翻译、可编辑英文与实际分库请求）—— UI 编辑的检索式真正进入 provider 请求并写入 manifest；中文分词/概念拆分、中英文术语映射；可选 LLM 结构化解析（仅用于概念拆解与改写，论文与统计仍绑定真实数据）。
- `planned`：Landscape v2 —— semantic clusters / citation communities / OpenAlex topics / 时间演变并列或融合，注明各层来源；Topic Card 含 Representative Papers、Why It Matters、Growth Trend、Current Density、Existing Reviews、Under-covered Combinations、Evidence Links；缺项显示「数据不足」。Leiden 对比、BERTopic 可行性、自动选 k 均为实验项。
- `planned`：Novelty / Coverage v2 —— Object×Method×Task×Context×Outcome 五维覆盖矩阵、同义词归一化、5/5·4/5·3/5 组件重叠、semantic gap detection；结论一律用 Potential Gap，并评测误报与遗漏。
- `planned`：大语料稀疏计算、分块相似度与 top-k（当前多处为稠密 N×N）。

## 5. 集成与基础设施（比赛后）

- `deferred`：FullTextResolver 完整顺序（本地/Zotero → OA resolver → arXiv → 机构 provider → 出版社落地页 → 人工兜底）、机构认证（用户授权流程，不保存校园密码）。
- `deferred`：Zotero —— RIS/BibTeX/Evidence Pack → Pyzotero 本地读取 POC → 元数据同步 → collection 同步 → PDF 附件 → 双向同步（先解决冲突、删除与重复实体语义）。
- `deferred`：Scientific PDF —— GROBID → 章节切分 → 参考文献解析 → 图表元数据 → 证据抽取 → 全文证据图/RAG；逐条绑定文件哈希、页码/区段与抽取来源。
- `verified`：v0.9.7 本机 FastAPI / OpenAPI 已接入 Web 界面，复用现有领域算法；通用 CLI 与 MCP SDK 标准化仍 `deferred`。
- `deferred`：Screening 从 PRISMA 状态容器中抽为通用组件，PRISMA/PRISMA-ScR 作为可选报告适配层（含数据迁移与计数单位说明）。
- `deferred`：多用户隔离、Institution framework、多机构适配、Agent orchestration、大型向量服务；规模与场景决定是否实现。
- `deferred`：自动综述写作、移动端、复杂代理系统（不阻塞比赛冻结）。

## 6. 待补齐的验证材料

- `pending_external`：真实专家标签（Benchmark 人工金标准）。
- `pending_external`：API 凭据（`S2_API_KEY`、`OPENALEX_API_KEY`、`LEEXTRACTOR_UNPAYWALL_EMAIL`）下的真实 provider smoke —— 未配置时不得声称已跑通。
- `pending_external`：Python 3.10/3.13 边界安装由 CI 矩阵验证；本轮本机环境为 Python 3.12.4，不能当作边界测试。
- `planned`：每个发布提交核对 GitHub Actions 真实结果（Python 3.10/3.13 × Ubuntu/Windows，加 Node 24 前端测试/构建）。
- `pending_external`：比赛机器上的浏览器视觉验收；本轮因电脑控制工具无法判定浏览器网址而停止，DOM 测试不能替代视觉验收。
- `planned`：PPT / 展示材料核对（未收到材料时只能记录「待检查」）。
