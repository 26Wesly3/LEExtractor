# LEExtractor 架构

版本 v0.9.8。本文件描述**当前实现的模块边界与数据流**，不描述计划中的能力（计划见 [ROADMAP.md](ROADMAP.md)），也不重复改动历史（见 [CHANGES.md](CHANGES.md)）。

## 1. 分层

```text
入口层      web/（Vue + Vuetify） → litsearch/web/（FastAPI、DTO、项目与后台任务）
            app.py（Streamlit）        server.py → litsearch/server.py（FastMCP，12 工具）
工作流层    litsearch/search.py（LiteratureReviewWorkflow / ReviewState）
            litsearch/snowball.py     litsearch/similar.py
数据源层    litsearch/sources.py      litsearch/retrieval.py  litsearch/cache.py
            litsearch/stop_reasons.py litsearch/diagnostics.py  litsearch/identifiers.py
分析层      litsearch/filters.py      litsearch/prisma.py    litsearch/screening.py
            litsearch/evidence.py     litsearch/landscape.py  litsearch/questions.py
            litsearch/intent.py
获取层      litsearch/downloader.py
输出层      litsearch/export.py  litsearch/bundle.py
持久层      litsearch/persistence.py  litsearch/session_schema.py
基础        litsearch/config.py  litsearch/version.py  litsearch/models.py  litsearch/demo.py
```

依赖方向自上而下；分析层与获取层不互相调用，输出层只读取上面的结果。

## 2. 主数据流

```text
研究问题 ──intent.parse_intent──▶ 分库检索式（写入 search_manifest）
        ──sources.SourceManager──▶ 原始记录（带 DiscoveryTrace；HTTP 预算在 stop_reasons.HttpBudget 计数）
        ──filters.RelevanceFilter──▶ 去重 + 词法相关度（score_context_id 标记评分批次）
        ──prisma.PRISMATracker──▶ 计数账本 ledger + 逐条决定（title_abstract / full_text）
        ──snowball / similar──▶ 引文与关系扩展（可中断、可续跑，stop_reason 记录终止原因）
        ──evidence.EvidenceGraph──▶ 引文图 + 三种关系（bibliographic_coupling / co_citation / text_similarity）
        ──landscape / questions──▶ 主题·时间·覆盖·新颖度与候选问题（全部限定当前样本）
        ──export / bundle──▶ CSV·JSON·RIS·BibTeX·Evidence Pack
        ──persistence + session_schema──▶ 可校验、可迁移、可恢复的会话
```

## 3. 模块职责（一句话一个）

| 模块 | 职责 |
| --- | --- |
| `litsearch/version.py` | 版本唯一来源（`__version__`、`version_tag()`、`package_name()`）；无第三方导入，供打包用 `ast` 解析 |
| `litsearch/config.py` | 路径、年份、密钥读取；`max_pdf_size()`（默认 100 MiB，`LEEXTRACTOR_MAX_PDF_SIZE` 覆盖）与 `max_pdf_size_mib()` |
| `litsearch/models.py` | `Paper` / `Author` / `SearchResult` / `CitationNetwork` / `DiscoveryTrace`；`score_context_id`、`stop_reason`、`http_budget` 字段 |
| `litsearch/identifiers.py` | DOI / OpenAlex / arXiv 归一化、别名集合、兜底键 |
| `litsearch/cache.py` | SQLite 键值缓存（TTL），缓存值由调用方附带完整性信息 |
| `litsearch/stop_reasons.py` | 停止原因枚举与「是否算完成」的判定；`HttpBudget` 真实 HTTP 计数（请求/重试/缓存命中/限流/错误/取消/耗时/按来源） |
| `litsearch/diagnostics.py` | 错误分类与诊断日志（限流、解析失败等） |
| `litsearch/sources.py` | 各 provider 适配器（Semantic Scholar / OpenAlex / arXiv / Crossref 元数据）、分页与重试；search/references/citations 内部返回 `RetrievalResult`，公开 list API 仅作兼容薄包装 |
| `litsearch/retrieval.py` | `RetrievalResult` / `RetrievalStatus` / `RequestStats`；显式区分真空结果、调用方截断、部分失败、限流、超时、取消与预算耗尽 |
| `litsearch/search.py` | `LiteratureReviewWorkflow` 与 `ReviewState`；阶段推进、统一 scored corpus、content-addressed score context、检查点与停止原因 |
| `litsearch/snowball.py` | 正向/反向引文扩展，按轮次记录 raw/unique/relevant/累计，支持断点续跑 |
| `litsearch/similar.py` | 基于文献耦合 / 共被引的相似论文发现 |
| `litsearch/filters.py` | 词法相关度（word/char/coverage 混合）、去重、`CalibrationRecord` 标定记录 |
| `litsearch/prisma.py` | PRISMA 状态容器、决定冲突留痕、计数账本 `RetrievalLedger`（record/report 级别）、自动筛选的唯一闸门 |
| `litsearch/evidence.py` | `EvidenceGraph`：引文图（PageRank/社群/路径只跑引文边）+ 多关系边（每对可同时有 3 种关系，不互相加权） |
| `litsearch/landscape.py` | 主题聚类、时间演变、新颖度、覆盖平衡（全部限定当前样本） |
| `litsearch/questions.py` | 候选研究问题（组合空白 / 覆盖不足 / 无人跟进），全部 `status=hypothesis` |
| `litsearch/intent.py` | 规则式研究意图解析与分库检索式生成（可解释、可降级） |
| `litsearch/downloader.py` | PDF 获取边界：流式 + 体积上限 + 每一跳地址校验 + provenance + Windows 文件名处理 |
| `litsearch/export.py` | CSV / JSON / RIS / BibTeX / Mermaid；CSV 公式注入中和；官方指标与 Citation Proxy 分栏 |
| `litsearch/bundle.py` | Evidence Pack（ZIP）：AGENT_HANDOFF.md + 结构化文件；筛选状态从 `litsearch/screening.py` domain 层读取 |
| `litsearch/persistence.py` | `ReviewState` ↔ dict、存盘/恢复、校准随会话保存并在语料变化时失效 |
| `litsearch/session_schema.py` | 会话 schema 校验/迁移/未来版本拒绝、损坏文件备份、`project_id` 与写入冲突检测 |
| `litsearch/screening.py` | 通用筛选 domain：`screening_facts` / `screening_section` / `paper_screening_status` 与标定辅助逻辑；不依赖 FastMCP |
| `litsearch/server.py` | MCP 12 工具适配层；复用 `litsearch/screening.py`，不承载筛选 domain 真值 |
| `litsearch/demo.py` | 离线演示数据（标注 SYNTHETIC DEMO） |
| `app.py` | Streamlit 界面（研究探索 / 文献结果 / 引文扩展 / 证据版图 / 研究机会 / 系统综述 / 保存与导出） |
| `scripts/package_release.py` | 白名单打包 + `ast` 读版本 + SHA-256 清单（不导入运行依赖） |
| `scripts/launch_gui.py` | 启动 Streamlit、轮询健康接口、就绪后再开浏览器 |

## 4. 关键不变量（改动前先读）

1. **事实与判断分离**：获取状态（下载成功/未获取）、排序信号（相关度）、筛选决定（纳入/排除）是三件事，任何一个都不自动决定另一个。没下到全文 ⇒ `not_retrieved`，不是排除理由。
2. **引文图只跑引文**：PageRank / 社群 / 证据路径只用 `citation` 边；`bibliographic_coupling`、`co_citation`、`text_similarity` 是并列关系，不合并加权。
3. **关系命名唯一**：`text_similarity` 是规范名，`semantic` 只是历史别名（`LEGACY_RELATION_ALIASES`）。`edge_type_counts()` 会同时返回两者（同值），消费端必须按规范名过滤，否则出现幻影重复。
4. **计数单位是 record/report**：`ledger` 从 provider 原始返回建账；研究层面合并尚未实现。
5. **自动筛选是例外，不是默认**：`auto_screen=False` 为默认；未标定 ⇒ 只排序；`FULL_TEXT` 阶段核心 API 拒绝自动筛选。
6. **版本唯一来源**：`litsearch/version.py`。`pyproject.toml` 由测试断言一致；打包脚本用 `ast` 读取，不导入 `litsearch`。
7. **获取边界**：初始 URL 与每一跳重定向都做协议 + 地址校验；体积按实际解码字节计；失败清理 `.part` 并关闭响应；已有可用文件不覆盖。
8. **会话是用户数据**：加载前必须过 `session_schema`；失败先备份、不修改原文件、不破坏自动保存；未来版本拒绝而非降级。
9. **导出面向电子表格**：CSV 文本单元格做公式注入中和，原始文本保留在 JSON。
10. **指标不冒充官方**：官方指标与内部 Citation Proxy 分栏；硬编码值标 `needs_source_verification`，内部代理永远 `verified=False`。

## 5. 测试与验证结构

- `tests/conftest.py`：`no_external_network` 自动拦截任何真实请求；`tmp_path`/`tmpdir` 重定向到仓库内 `tests/_workspace_tmp/<测试名>`（本机系统临时目录不可用）。
- 测试按主题分文件：检索/分页（`test_pagination.py`）、证据关系（`test_evidence_relations.py`）、停止原因（`test_stop_reasons.py`）、PRISMA 账本（`test_prisma_ledger.py`）、筛选状态（`test_screening_state.py`）、下载与报告（`test_download_and_reporting.py`）、导出安全（`test_export_security.py`）、会话 schema（`test_session_schema.py`）、发布（`test_release_version.py`）、界面（`test_app.py`）、MCP（`test_mcp.py`）、离线集成（`test_offline.py`、`test_stabilization.py`）。
