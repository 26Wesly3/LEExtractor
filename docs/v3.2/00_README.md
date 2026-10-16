# 00 文档索引与使用方法

版本 3.2。本包将产品愿景、现有资产复用、两条入口、确定性算法、可配置 AI、数据与任务契约，以及分阶段验收统一为一份执行基线。

## 文件用途

| 文件 | 用途 |
|---|---|
| 01_PRODUCT_VISION.md | 产品目标和不能破坏的约束 |
| 02_USER_WORKFLOW.md | 两条入口、无反馈闭环及状态文案 |
| 03_SYSTEM_ARCHITECTURE.md | 服务边界、运行位置和能力协商 |
| 04_ALGORITHM_DESIGN.md | CoreRank、反馈、多样性及解释 |
| 05_UX_UI_SPEC.md | 保留视觉体系、页面和交互规则 |
| 06_REUSE_CHANGE_MAP.md | 仓库核验、复用和迁移清单 |
| 07_DEVELOPMENT_ROADMAP.md | M0 至 M5 的依赖、产物和退出条件 |
| 08_API_DATA_MODEL_SPEC.md | API、身份、字段和错误行为 |
| 09_ACCEPTANCE_BENCHMARK.md | 功能、回归、算法与用户任务验收 |
| 10_SECURITY_PRIVACY.md | 项目隔离、上传、凭据和远程传输 |
| 11_CHINA_ACADEMIC_ACCESS.md | 校园资源及中文数据源能力边界 |
| 12_OPEN_SOURCE_ASSET_POLICY.md | 开源资产评估与 adapter 规范 |
| 13_VENUE_INTELLIGENCE.md | 期刊会议信息与来源核验 |
| 14_JOBS_IDENTITY_MIGRATION.md | 并发、幂等、任务状态与数据迁移 |
| 15_PERFORMANCE_DEPLOYMENT.md | 弱服务器预算、缓存与部署策略 |
| 16_PDF_ZOTERO_INTEGRATIONS.md | PDF 匹配、Zotero 和导出 |
| 17_AI_RERANK_FIELD_INSIGHTS.md | AI 重排、地图、热点及切入点 |
| 18_REQUIREMENTS_TRACEABILITY.md | 用户要求到模块及验收的映射 |
| 19_SOURCES_DECISIONS.md | 来源、工程决策和待核实事实 |
| 20_ZJU_CAS_DEFERRED.md | CARSI与出版社资源跳转及直接SSO延期 |

`contracts/` 提供配置、OpenAPI、JSON Schema 和状态机。`fixtures/` 提供确定性算例与验收场景。`handoff/` 提供实施记录模板。总纲 Markdown 由上述章节拼接；单章修改后必须重新生成总纲与校验清单，避免维护两份不同规范。

## 完成状态

本包已经补齐工程规则及契约，尚未核验当前仓库分支、运行产品测试或标定排序参数。工程实现者必须完成 M0 基线核验。文档检查通过不等于软件无 bug。

## 交付边界

目标功能包含 Zotero 导入与显式保存、个人检索额度、AI BYOK、中文学术访问、期刊指标和研究概览。无许可的中文数据库自动检索、没有权限的官方期刊指标、没有本地伴随端的用户电脑计算不得模拟成功。离线/免费基础模式必须可用。
