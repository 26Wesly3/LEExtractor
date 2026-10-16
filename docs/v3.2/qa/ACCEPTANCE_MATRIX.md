# v3.2 验收覆盖表（ACCEPTANCE_MATRIX）

- 日期：2026-10-17
- 分支：`feature/v3.2`（未提交、未切分支）
- 依据：`docs/v3.2/18_REQUIREMENTS_TRACEABILITY.md`（R01–R22 与三种完成定义）、`docs/v3.2/09_ACCEPTANCE_BENCHMARK.md`（AC-01–AC-32 场景与证据要求）、`fixtures/acceptance_cases.json`（每个 AC 的预期结果与 `required_this_release`）
- 本表的每一行都记录：需求、规范文件、状态、证据用例、**实际运行的命令**、**退出码**、观察结果。

## 0. 状态词汇与读取规则

| 状态 | 含义 |
|---|---|
| `implemented` | 代码存在且相应用例在本机通过（本表绝大多数行） |
| `integration_verified` | **真实外部服务成功应答**。本轮唯一可用的是 Semantic Scholar，且其证据来自 M0 记录而非本工作项复测（见 §5） |
| 效果验证状态（本轮未执行，本表无任何一行使用） | 需要 24 主题专家标注集与 8 位新手任务实测。**本轮未执行** |
| `external_blocked` | 外部缺少明确授权或服务（AI Key、Zotero 授权、官方期刊指标源、联网授权） |
| `deferred` | 本包明确延期项（仅 AC-29） |

三种完成定义**不能互相代替**（`docs/v3.2/18_REQUIREMENTS_TRACEABILITY.md:32`）。表中每个 AC 的「观察结果」末尾标出它属于哪种完成定义；凡是本地用例通过但真实外部服务未联调的，写作 **implemented only**，不升级为 `integration_verified`。

**证据基准（本表引用的模块全部真实存在）：** `tests/test_v32_ai_rerank.py`、`tests/test_v32_api.py`、`tests/test_v32_api_contract.py`、`tests/test_v32_contracts.py`、`tests/test_v32_core_rank.py`、`tests/test_v32_credentials.py`、`tests/test_v32_errors.py`、`tests/test_v32_feedback.py`、`tests/test_v32_jobs.py`、`tests/test_v32_legacy_fixtures.py`、`tests/test_v32_mapping.py`、`tests/test_v32_overview.py`、`tests/test_v32_pdf_import.py`、`tests/test_v32_release_tooling.py`、`tests/test_v32_repo_integrity.py`、`tests/test_v32_secret_hygiene.py`、`tests/test_v32_store.py`、`tests/test_v32_venue_metrics.py`、`tests/test_v32_zotero.py`、`tests/test_v32_acceptance_matrix.py`。前端证据为 `web/src/v32/*.test.js` 与 `web/src/App.dom.test.js`。

## 1. AC-01 — AC-32

所有命令均在仓库根目录、`$env:PYTHONUTF8="1"` 下运行；「命令」列给出命令本体。退出码为进程真实退出码。

| ID | 需求（一行） | 规范 | 状态 | 证据（测试/节点） | 命令 | 退出码 | 观察结果与完成定义 |
|---|---|---|---|---|---|---|---|
| AC-01 | 一句主题、零标记即得到列表与初始概览 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_api.py；tests/test_v32_overview.py | python -m pytest tests/test_v32_api.py tests/test_v32_api_contract.py -q | 0 | `44 passed`；含「零判断时 overview 仍非空」用例。完成定义：implemented only |
| AC-02 | 单篇/多篇 PDF 入口，逐文件状态，不要求手填 DOI | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_pdf_import.py；tests/test_v32_api.py | python -m pytest tests/test_v32_pdf_import.py -q | 0 | `42 passed`；逐文件 ParseOutcome 状态与批内部分失败。完成定义：implemented only |
| AC-03 | 无 DOI、有题名或摘要→本地种子从文本继续检索 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_pdf_import.py | python -m pytest tests/test_v32_pdf_import.py -q | 0 | `42 passed`；title+abstract 无 DOI 生成 local_seed。完成定义：implemented only |
| AC-04 | 相同 PDF 重复上传不重复建身份或附件 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_pdf_import.py；tests/test_v32_store.py | python -m pytest tests/test_v32_pdf_import.py -q | 0 | `42 passed`；SHA-256 内容哈希复用同一 attachment 身份。完成定义：implemented only |
| AC-05 | 批量部分失败：成功项保留，失败项可单独重试 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_pdf_import.py；tests/test_v32_api.py | python -m pytest tests/test_v32_pdf_import.py -q | 0 | `42 passed`；含「11 文件整批拒绝」等边界。完成定义：implemented only |
| AC-06 | 固定 RRF 候选与并列：原始分/展示分/顺序符合 fixture | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_core_rank.py；fixtures/ranking_cases.json | python -m pytest tests/test_v32_core_rank.py tests/test_v32_feedback.py -q | 0 | `43 passed`；`fixtures/ranking_cases.json` 5 例端到端，算术表达式按 1e-12 比对。完成定义：implemented only |
| AC-07 | 引用/年份/图/摘要缺失：null 与 0 分开，允许降级，无 NaN | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_core_rank.py；tests/test_v32_feedback.py | python -m pytest tests/test_v32_core_rank.py tests/test_v32_feedback.py -q | 0 | `43 passed`；含 `json.dumps(..., allow_nan=False)` 与「未知≠0」。完成定义：implemented only |
| AC-08 | 排除、撤销、再次扩展：排除不复活，撤销清除反馈作用 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_feedback.py；tests/test_v32_store.py；tests/test_v32_api.py | python -m pytest tests/test_v32_core_rank.py tests/test_v32_feedback.py -q | 0 | `43 passed`；仅 `exclusion_reason="not_relevant"` 计入负样本。完成定义：implemented only |
| AC-09 | 高频判断及倒序任务完成：结果只对应最新上下文 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_jobs.py；tests/test_v32_api.py | python -m pytest tests/test_v32_jobs.py -q | 0 | `19 passed`；含 `test_v32_jobs.py:127` 与 `test_v32_api.py:415` 的 superseded 用例。完成定义：implemented only |
| AC-10 | 限流/超时/来源全失败：部分完成、失败、真实零结果严格区分 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_api.py | python -m pytest tests/test_v32_api.py tests/test_v32_api_contract.py -q | 0 | `44 passed`；`test_v32_api.py:454` 失败来源→coverage.partial 且不丢已存论文。完成定义：implemented only（注入式来源，非真实 provider 故障） |
| AC-11 | 取消、刷新、进程重启：保留已提交结果，任务可恢复或明确中断 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_jobs.py；tests/test_v32_api.py | python -m pytest tests/test_v32_jobs.py -q | 0 | `19 passed`；取消保留已提交快照，遗留任务转 interrupted。完成定义：implemented only |
| AC-12 | 旧会话迁移与回滚：判断/证据/附件完整可读 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_legacy_fixtures.py；tests/test_v32_store.py | python -m pytest tests/test_v32_contracts.py tests/test_v32_store.py tests/test_v32_errors.py tests/test_v32_secret_hygiene.py tests/test_v32_repo_integrity.py tests/test_v32_release_tooling.py tests/test_v32_legacy_fixtures.py -q | 0 | `70 passed`；3 个 legacy 会话 fixture 经产品迁移路径加载。完成定义：implemented only |
| AC-13 | canonical identity 合并：aliases/附件/判断/证据正确迁移 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_store.py | python -m pytest tests/test_v32_contracts.py tests/test_v32_store.py tests/test_v32_errors.py tests/test_v32_secret_hygiene.py tests/test_v32_repo_integrity.py tests/test_v32_release_tooling.py tests/test_v32_legacy_fixtures.py -q | 0 | `70 passed`；合并选创建时间更早者并迁移 aliases。完成定义：implemented only |
| AC-14 | 无 Key / AI 超时 / 非法 JSON：本地流程完整有效 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_ai_rerank.py；tests/test_v32_api.py | python -m pytest tests/test_v32_ai_rerank.py -q | 0 | `29 passed`；注入式 adapter。**无模型 Key，真实 AI 调用 external_blocked（L1）**。完成定义：implemented only |
| AC-15 | AI 重复/未知/遗漏 ID：严格拒绝整批，不生成新论文 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_ai_rerank.py | python -m pytest tests/test_v32_ai_rerank.py -q | 0 | `29 passed`；schema（含 additionalProperties:false）后语义双段校验，失败保留本地顺序。完成定义：implemented only |
| AC-16 | Key 与 PDF 隐私：日志/导出/DTO 无秘密，默认不传全文 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_secret_hygiene.py；tests/test_v32_credentials.py；tests/test_v32_api.py | python -m pytest tests/test_v32_contracts.py tests/test_v32_store.py tests/test_v32_errors.py tests/test_v32_secret_hygiene.py tests/test_v32_repo_integrity.py tests/test_v32_release_tooling.py tests/test_v32_legacy_fixtures.py -q | 0 | `70 passed`；另 `tests/test_v32_credentials.py` 经独立命令通过 `73 passed`（exit 0）；API 层断言明文子串不出现在响应原文。完成定义：implemented only |
| AC-17 | AI 候选窗口外论文：原相对顺序不变，无虚构 AI 加分 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_ai_rerank.py | python -m pytest tests/test_v32_ai_rerank.py -q | 0 | `29 passed`；窗口外相对顺序与 core_score_raw 不变。完成定义：implemented only |
| AC-18 | 新手与视觉回归：无必要算法配置，核心组件及动效保留 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | web/src/v32/Workspace.test.js；web/src/App.dom.test.js；docs/v3.2/qa/VISUAL_BASELINE.md | cd web; npm test | 0 | `13 test files / 75 tests passed`（36.88 s）。**1440×900/1024×768/390×844 三视口截图为未采集（L6），只有源码级证据**。完成定义：implemented only |
| AC-19 | 孤立图与少量文献：不凭空生成完整领域结构 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_mapping.py | python -m pytest tests/test_v32_mapping.py tests/test_v32_overview.py -q | 0 | `28 passed`；<20 节点/<20 边/孤立比例>0.50 → degraded=True 并标注关系不足。完成定义：implemented only |
| AC-20 | 热点和潜在切入点：有证据范围与核查建议，无保证创新表述 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_mapping.py；tests/test_v32_overview.py | python -m pytest tests/test_v32_mapping.py tests/test_v32_overview.py -q | 0 | `28 passed`；prior<5 或覆盖<4 年时抑制增速排名，证据不足返回空线索。完成定义：implemented only |
| AC-21 | Zotero 导入：仅导入选择项，保留键与库来源 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_zotero.py | python -m pytest tests/test_v32_credentials.py tests/test_v32_access_gateway.py tests/test_v32_venue_metrics.py tests/test_v32_zotero.py -q | 0 | `73 passed`；网络全部注入。**真实 Zotero 往返 external_blocked（L9）**。完成定义：implemented only |
| AC-22 | Zotero 部分写入/版本冲突：逐项记录，不重复写入，不覆盖他人修改 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_zotero.py | python -m pytest tests/test_v32_credentials.py tests/test_v32_access_gateway.py tests/test_v32_venue_metrics.py tests/test_v32_zotero.py -q | 0 | `73 passed`；逐项结果与版本冲突不覆盖。完成定义：implemented only（未对真实库验证） |
| AC-23 | 校园与数据库访问：入口状态真实，未声称完成统一认证 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_access_gateway.py | python -m pytest tests/test_v32_credentials.py tests/test_v32_access_gateway.py tests/test_v32_venue_metrics.py tests/test_v32_zotero.py -q | 0 | `73 passed`；仅注册表官方链接，无 CAS/CARSI 认证代码。完成定义：implemented only |
| AC-24 | 期刊指标缺失/未核验：不造 JIF，不阻塞发现，不冒充官方指标 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_venue_metrics.py | python -m pytest tests/test_v32_credentials.py tests/test_v32_access_gateway.py tests/test_v32_venue_metrics.py tests/test_v32_zotero.py -q | 0 | `73 passed`；无许可来源一律 `unavailable`。**官方 JIF/JCR/CAS 分区 external_blocked（L8）**。完成定义：implemented only |
| AC-25 | 非授权项目访问：拒绝读取附件/任务/项目/凭据 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_api.py | python -m pytest tests/test_v32_api.py tests/test_v32_api_contract.py -q | 0 | `44 passed`；他人项目一律 404，不返回 403 也不返回数据。完成定义：implemented only |
| AC-26 | 弱服务器固定语料：达到性能预算或明确未达标 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | scripts/v32_perf_probe.py；docs/v3.2/qa/PERFORMANCE_REPORT.md | python scripts/v32_perf_probe.py | 0 | 5 项本地预算全部 **met**（快速排序 p95 0.0365 s、暖增强 0.0930 s、反馈重排 0.0717 s、缓存首屏 0.0368 s、元数据 API 18.2 ms）。**远程首批 ≤20 s = external_blocked（L4）；应用合计 RSS ≤1400 MiB 与 HTTP 端到端 = not_measured（L11）**。完成定义：implemented only（本地预算已实测，外部/合计项未测） |
| AC-27 | PDF 参考文献区 DOI：不误当成上传论文 DOI，冲突进确认 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_pdf_import.py | python -m pytest tests/test_v32_pdf_import.py -q | 0 | `42 passed`；`test_v32_pdf_import.py:88` 参考文献 DOI 只进 `reference_dois`，`parsed.doi is None`。完成定义：implemented only |
| AC-28 | 未批准 CAS 的当前版本：无校园登录按钮，不调用 CAS | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_access_gateway.py；tests/test_v32_api_contract.py | python -m pytest tests/test_v32_api.py tests/test_v32_api_contract.py -q | 0 | `44 passed`；断言应用不存在路径含 `cas`/`sso` 的路由；另 `tests/test_v32_access_gateway.py` 经独立命令通过（`73 passed`，exit 0）。完成定义：implemented only |
| AC-29 | 将来正式 CAS 验证：当前延期，不纳入本轮完成条件 | docs/v3.2/20_ZJU_CAS_DEFERRED.md；fixtures/acceptance_cases.json | deferred | tests/test_v32_contracts.py；fixtures/acceptance_cases.json；docs/v3.2/20_ZJU_CAS_DEFERRED.md | python -m pytest tests/test_v32_contracts.py tests/test_v32_store.py tests/test_v32_errors.py tests/test_v32_secret_hygiene.py tests/test_v32_repo_integrity.py tests/test_v32_release_tooling.py tests/test_v32_legacy_fixtures.py -q | 0 | `70 passed`；`test_v32_contracts.py:159` 断言 `deferred == {"AC-29"}`，与 fixture 的 `required_this_release: false` 一致；代码中无 CAS 客户端。状态：deferred（本表唯一） |
| AC-30 | 导出与 PRISMA：导出有来源，普通判断不改变 PRISMA | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_legacy_fixtures.py；tests/test_v32_api.py | python -m pytest tests/test_v32_contracts.py tests/test_v32_store.py tests/test_v32_errors.py tests/test_v32_secret_hygiene.py tests/test_v32_repo_integrity.py tests/test_v32_release_tooling.py tests/test_v32_legacy_fixtures.py -q | 0 | `70 passed`；PRISMA 记录随迁移保留且判断值不写入 PRISMA。完成定义：implemented only |
| AC-31 | 图谱近重复折叠：仅视图聚合，论文/证据身份不丢失 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_mapping.py | python -m pytest tests/test_v32_mapping.py tests/test_v32_overview.py -q | 0 | `28 passed`；`test_v32_mapping.py:182` `test_fold_view_never_removes_an_identity_or_an_edge`。完成定义：implemented only |
| AC-32 | 预算与自动循环：自动最多 1 轮，达到上限明确停止 | docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | implemented | tests/test_v32_jobs.py；tests/test_v32_contracts.py；tests/test_v32_api.py | python -m pytest tests/test_v32_jobs.py -q | 0 | `19 passed`；`contracts.discovery["auto_rounds"]==1`（`test_v32_contracts.py:85`），超预算停止并记录 stop_reasons。完成定义：implemented only |

## 2. R01 — R22

规范列按 `docs/v3.2/18_REQUIREMENTS_TRACEABILITY.md:7-28` 的「规范」列映射到实际文档路径；「验收」列映射到本表的 AC 与证据。

| ID | 需求（一行） | 规范 | 状态 | 证据（测试/节点） | 命令 | 退出码 | 观察结果与完成定义 |
|---|---|---|---|---|---|---|---|
| R01 | 从研究方向开始 | docs/v3.2/02_USER_WORKFLOW.md | implemented | tests/test_v32_api.py；tests/test_v32_overview.py | python -m pytest tests/test_v32_api.py tests/test_v32_api_contract.py -q | 0 | `44 passed`；对应 AC-01。完成定义：implemented only |
| R02 | 上传单篇或多篇 PDF 即开始 | docs/v3.2/16_PDF_ZOTERO_INTEGRATIONS.md | implemented | tests/test_v32_pdf_import.py | python -m pytest tests/test_v32_pdf_import.py -q | 0 | `42 passed`；对应 AC-02/03/05/27。完成定义：implemented only |
| R03 | 自动相关度及核心文献排序 | docs/v3.2/04_ALGORITHM_DESIGN.md | implemented | tests/test_v32_core_rank.py；tests/test_v32_feedback.py；fixtures/ranking_cases.json | python -m pytest tests/test_v32_core_rank.py tests/test_v32_feedback.py -q | 0 | `43 passed`；对应 AC-06/07。完成定义：implemented only |
| R04 | 扩展后自动重排 | docs/v3.2/14_JOBS_IDENTITY_MIGRATION.md | implemented | tests/test_v32_jobs.py；tests/test_v32_api.py | python -m pytest tests/test_v32_jobs.py -q | 0 | `19 passed`；对应 AC-09/32，含 300 ms 去抖合并。完成定义：implemented only |
| R05 | 新手无需算法知识和核心判断 | docs/v3.2/02_USER_WORKFLOW.md；docs/v3.2/05_UX_UI_SPEC.md | implemented | web/src/v32/Workspace.test.js；tests/test_v32_api.py | cd web; npm test | 0 | `75 passed`（13 files）；零判断即可获得列表与概览。完成定义：implemented only |
| R06 | 保留 UI 设计语言及书面文案 | docs/v3.2/05_UX_UI_SPEC.md | implemented | web/src/v32/Workspace.test.js；web/src/App.dom.test.js；docs/v3.2/qa/VISUAL_BASELINE.md | cd web; npm test | 0 | `75 passed`；设计令牌与抽屉/导航动效源码级保留（`docs/v3.2/qa/VISUAL_BASELINE.md` §2/§6）。三视口截图未采集（L6）。完成定义：implemented only |
| R07 | 研究概况、分支、热点、切入点 | docs/v3.2/17_AI_RERANK_FIELD_INSIGHTS.md | implemented | tests/test_v32_mapping.py；tests/test_v32_overview.py | python -m pytest tests/test_v32_mapping.py tests/test_v32_overview.py -q | 0 | `28 passed`；对应 AC-19/20。完成定义：implemented only |
| R08 | 人工核心、相关、排除与撤销 | docs/v3.2/04_ALGORITHM_DESIGN.md；docs/v3.2/14_JOBS_IDENTITY_MIGRATION.md | implemented | tests/test_v32_feedback.py；tests/test_v32_store.py；tests/test_v32_api.py | python -m pytest tests/test_v32_core_rank.py tests/test_v32_feedback.py -q | 0 | `43 passed`；对应 AC-08/09。完成定义：implemented only |
| R09 | 模型 API Key 与可选 AI 精排 | docs/v3.2/10_SECURITY_PRIVACY.md；docs/v3.2/17_AI_RERANK_FIELD_INSIGHTS.md | implemented | tests/test_v32_ai_rerank.py；tests/test_v32_credentials.py | python -m pytest tests/test_v32_ai_rerank.py -q | 0 | `29 passed`；凭据存储与轮换另有 `73 passed`（exit 0）。**无模型 Key：真实 AI 精排 external_blocked（L1）**。完成定义：implemented only |
| R10 | 个人检索 Key 与额度 | docs/v3.2/08_API_DATA_MODEL_SPEC.md；docs/v3.2/10_SECURITY_PRIVACY.md | implemented | tests/test_v32_api.py；tests/test_v32_secret_hygiene.py | python -m pytest tests/test_v32_api.py tests/test_v32_api_contract.py -q | 0 | `44 passed`；凭据只回状态不回明文；来源失败计入 coverage。完成定义：implemented only |
| R11 | Zotero 导入和保存筛选结果 | docs/v3.2/16_PDF_ZOTERO_INTEGRATIONS.md | implemented | tests/test_v32_zotero.py | python -m pytest tests/test_v32_credentials.py tests/test_v32_access_gateway.py tests/test_v32_venue_metrics.py tests/test_v32_zotero.py -q | 0 | `73 passed`；对应 AC-21/22。**真实 Zotero 授权 external_blocked（L9）**。完成定义：implemented only |
| R12 | 校园资源及中文数据库入口 | docs/v3.2/11_CHINA_ACADEMIC_ACCESS.md；docs/v3.2/20_ZJU_CAS_DEFERRED.md | implemented | tests/test_v32_access_gateway.py | python -m pytest tests/test_v32_credentials.py tests/test_v32_access_gateway.py tests/test_v32_venue_metrics.py tests/test_v32_zotero.py -q | 0 | `73 passed`；仅注册表官方入口。完成定义：implemented only |
| R13 | 暂不直接接入浙大统一认证 | docs/v3.2/20_ZJU_CAS_DEFERRED.md | implemented | tests/test_v32_api_contract.py；tests/test_v32_access_gateway.py | python -m pytest tests/test_v32_api.py tests/test_v32_api_contract.py -q | 0 | `44 passed`；无 `cas`/`sso` 路由（AC-28）。完成定义：implemented only |
| R14 | CARSI Elsevier 官方访问跳转 | docs/v3.2/20_ZJU_CAS_DEFERRED.md | implemented | tests/test_v32_access_gateway.py | python -m pytest tests/test_v32_credentials.py tests/test_v32_access_gateway.py tests/test_v32_venue_metrics.py tests/test_v32_zotero.py -q | 0 | `73 passed`；只打开注册表登记的官方链接，不自动标认证成功（AC-23）。完成定义：implemented only |
| R15 | 期刊影响因子等背景信息 | docs/v3.2/13_VENUE_INTELLIGENCE.md | implemented | tests/test_v32_venue_metrics.py | python -m pytest tests/test_v32_credentials.py tests/test_v32_access_gateway.py tests/test_v32_venue_metrics.py tests/test_v32_zotero.py -q | 0 | `73 passed`；无许可来源返回 `unavailable`。**官方 JIF/JCR/CAS 分区 external_blocked（L8）**。完成定义：implemented only |
| R16 | 已有代码复用与不重复造轮子 | docs/v3.2/06_REUSE_CHANGE_MAP.md；docs/v3.2/12_OPEN_SOURCE_ASSET_POLICY.md | implemented | docs/v3.2/qa/VISUAL_BASELINE.md；litsearch/v32/pipeline.py；tests/test_v32_repo_integrity.py | python -m pytest tests/test_v32_contracts.py tests/test_v32_store.py tests/test_v32_errors.py tests/test_v32_secret_hygiene.py tests/test_v32_repo_integrity.py tests/test_v32_release_tooling.py tests/test_v32_legacy_fixtures.py -q | 0 | `70 passed`；复用点：v3.2 检索复用 `litsearch.sources.SourceManager`，标识复用 `litsearch.identifiers`（`litsearch/v32/pipeline.py:11`）；M0 资产记录为 `docs/v3.2/qa/VISUAL_BASELINE.md`。**未按契约强制 provider 并发/超时/重试（L3）**。完成定义：implemented only |
| R17 | 节省时间及弱服务器可用 | docs/v3.2/15_PERFORMANCE_DEPLOYMENT.md | implemented | scripts/v32_perf_probe.py；docs/v3.2/qa/PERFORMANCE_REPORT.md | python scripts/v32_perf_probe.py | 0 | 本地 5 项预算全部 met（AC-26）；远程首批 external_blocked、合计 RSS 与 HTTP 端到端 not_measured（L4/L11）。完成定义：implemented only |
| R18 | 可追溯、跨学科、对比通用 AI | docs/v3.2/01_PRODUCT_VISION.md；docs/v3.2/09_ACCEPTANCE_BENCHMARK.md；docs/v3.2/17_AI_RERANK_FIELD_INSIGHTS.md | implemented | tests/test_v32_overview.py；tests/test_v32_mapping.py；docs/v3.2/09_ACCEPTANCE_BENCHMARK.md | python -m pytest tests/test_v32_mapping.py tests/test_v32_overview.py -q | 0 | `28 passed`；证据范围/覆盖/限制字段已实现。**「效果评测独立报告」验收未完成：24 主题专家标注集与 8 位新手任务实测本轮 not_run（L10）。R18 不得据此声称效果达标**。完成定义：implemented only（可追溯机制），效果维度 not_run |
| R19 | 旧项目 PRISMA 导出兼容 | docs/v3.2/08_API_DATA_MODEL_SPEC.md；docs/v3.2/14_JOBS_IDENTITY_MIGRATION.md；docs/v3.2/16_PDF_ZOTERO_INTEGRATIONS.md | implemented | tests/test_v32_legacy_fixtures.py；tests/test_v32_api.py | python -m pytest tests/test_v32_contracts.py tests/test_v32_store.py tests/test_v32_errors.py tests/test_v32_secret_hygiene.py tests/test_v32_repo_integrity.py tests/test_v32_release_tooling.py tests/test_v32_legacy_fixtures.py -q | 0 | `70 passed`；对应 AC-12/30。完成定义：implemented only |
| R20 | 身份合并、取消、重试、并发安全 | docs/v3.2/14_JOBS_IDENTITY_MIGRATION.md；docs/v3.2/16_PDF_ZOTERO_INTEGRATIONS.md | implemented | tests/test_v32_store.py；tests/test_v32_jobs.py；tests/test_v32_pdf_import.py | python -m pytest tests/test_v32_contracts.py tests/test_v32_store.py tests/test_v32_errors.py tests/test_v32_secret_hygiene.py tests/test_v32_repo_integrity.py tests/test_v32_release_tooling.py tests/test_v32_legacy_fixtures.py -q | 0 | `70 passed`；对应 AC-04/09/11/13。完成定义：implemented only |
| R21 | 隐私、凭据、项目隔离 | docs/v3.2/10_SECURITY_PRIVACY.md | implemented | tests/test_v32_secret_hygiene.py；tests/test_v32_credentials.py；tests/test_v32_api.py | python -m pytest tests/test_v32_contracts.py tests/test_v32_store.py tests/test_v32_errors.py tests/test_v32_secret_hygiene.py tests/test_v32_repo_integrity.py tests/test_v32_release_tooling.py tests/test_v32_legacy_fixtures.py -q | 0 | `70 passed`；对应 AC-16/25；另有凭据模块 `73 passed`（exit 0）。完成定义：implemented only |
| R22 | 文档统一为 3.2 且可一次交给代码 AI | docs/v3.2/START_HERE.md；litsearch/contracts | implemented | scripts/validate_v32_pack.py；docs/v3.2/START_HERE.md；fixtures/acceptance_cases.json | python scripts/validate_v32_pack.py | 1 | **MISSED：验收命令在本仓库不可复现。** ①仓库根运行 → exit 1，`FileNotFoundError: contracts/defaults.json`（契约实际在 `litsearch/contracts/`）；②按脚本 docstring 重建「文档包根」布局后 → exit 1，`AssertionError: handoff/IMPLEMENTATION_STATUS.md -> docs/v3.2/qa/ENVIRONMENT.md` 不存在；③再补齐嵌套 qa 文档后 → exit 1，`handoff/IMPLEMENTATION_STATUS.md -> docs/demo/*.gif` 不存在（与 §4 的既有发布链接缺口同源）。`handoff/IMPLEMENTATION_STATUS.md:48` 宣称的 `status: pass` 本轮**无法复现**。契约文档版本 3.2 本身已由 `70 passed` 的契约用例覆盖。完成定义：implemented only（文档统一），验收工具 MISSED |

## 3. 汇总

| 指标 | 实测 |
|---|---|
| AC 行数 | 32（AC-01..AC-32 各一次） |
| R 行数 | 22（R01..R22 各一次） |
| `deferred` | 1（仅 AC-29，`required_this_release: false`） |
| `external_blocked` 的外部联调项 | 5（AI 模型、Zotero、官方 JIF/JCR、联网首批预算、CAS/CARSI 真环境）；均写在对应行的观察结果中，不改变该行 `implemented` 状态 |
| 声称效果验证状态 | 0 |
| MISSED | 1（R22 的验收命令） |
| not_measured | 2（应用合计 RSS、HTTP 端到端 p95） |

## 4. 既有失败（本轮复现，非 v3.2 引入，未修）

| 失败 | 命令 | 退出码 | 观察结果 |
|---|---|---|---|
| `tests/test_release_self_contained.py::test_every_document_reference_resolves_inside_the_release` | python -m pytest tests/test_release_self_contained.py tests/test_web_release.py -q | 1 | `2 failed, 8 passed`；README 引用的 `LICENSE`/`DEPLOYMENT.md`/`docs/demo/*.gif`/`docs/devlog/`/`download/*.zip` 不在发布白名单（L13）。基线即存在 |
| `tests/test_web_release.py::test_root_documents_and_web_guide_links_resolve_in_source_zip` | python -m pytest tests/test_release_self_contained.py tests/test_web_release.py -q | 1 | 同上，同一白名单缺口 |

**环境相关用例的实际情况（与预期不同，照实记录）：** `tests/test_mcp_host_compat.py` 本轮**两次**运行均 **11 passed，退出码 0**，未复现此前记录的 3 个失败：

```
$env:PYTHONUTF8="1"; python -m pytest tests/test_mcp_host_compat.py -q --tb=line
...........                                                              [100%]
11 passed in 23.47s        (exit 0)
```

同机 `NO_PROXY` 当前值仍含字面量 `[::1]`：`10.195.152.43,.local,localhost,127.0.0.1,::1,[::1]`，同时设置了 `HTTP_PROXY`/`HTTPS_PROXY`。根因本身**已直接复现**：`python -c "import httpx; httpx.Client()"` → `httpx.InvalidURL: Invalid port: ':1]'`（httpx 0.28.1）。**未复现的是那 3 个 MCP 用例的失败**（该模块本轮两次运行均 11 passed / exit 0）。本工作项**未修改**该模块、相关环境变量或任何代码，只报告实测。（MCP 证据记为 **not reproduced**，不记为通过也不记为失败；详见 `KNOWN_LIMITATIONS.md` L12。）

## 5. 真实外部联调（`integration_verified`）现状

| 能力 | 状态 | 证据 |
|---|---|---|
| Semantic Scholar 检索 | `integration_verified`（**M0 记录，非本轮复测**） | `handoff/IMPLEMENTATION_STATUS.md:85`：`python scripts/v32_provider_probe.py` 退出码 0，1.954 s 返回 5 篇，含 DOI/摘要/被引数；`get_citations` 返回 3 |
| Semantic Scholar 参考文献 | observed_limitation | `handoff/IMPLEMENTATION_STATUS.md:86`：同一记录 `get_references` 返回 0 条（未被索引），不据此声称引用图完整 |
| AI 模型重排 | `external_blocked` | 无模型 provider Key（L1）；本轮未发任何真实模型请求 |
| 官方 JIF / JCR / 中科院分区 | `external_blocked` | 无许可且可核验的来源（L8） |
| Zotero 读写 | `external_blocked` | 无授权、库 ID、写权限（L9） |
| 未缓存远程检索首批 ≤20 s | `external_blocked` | 会话内无联网授权（L4） |
| 浙大直接 CAS / CARSI SP | `deferred` | AC-29 与 `docs/v3.2/20_ZJU_CAS_DEFERRED.md`（L7） |

**本轮没有重跑 `scripts/v32_provider_probe.py`**，因此上表第一行的 `integration_verified` 是**转引 M0 记录**，不是本工作项的复测结果；本工作项的复测状态为 `external_blocked`（无联网授权）。

## 6. 本表自身的防漂移检查

`tests/test_v32_acceptance_matrix.py` 解析本文件并要求：AC-01..AC-32 与 R01..R22 各出现且仅出现一次；AC-29 是唯一的 `deferred` 且与 `fixtures/acceptance_cases.json` 一致；每一行的状态都属于闭集；没有任何一行声称效果验证状态（本轮未执行，见 `docs/v3.2/09_ACCEPTANCE_BENCHMARK.md`）；每行至少引用一个真实存在于磁盘的证据文件；每行都记录命令与退出码。矩阵与实现漂移时该测试失败。

## 7. 本工作项的验收命令与真实结果

全部在仓库根目录、`$env:PYTHONUTF8="1"` 下运行。

| 序 | 命令 | 退出码 | 输出尾部 |
|---|---|---|---|
| a | python -m pytest tests/test_v32_acceptance_matrix.py -q | 0 | `7 passed in 0.28s` |
| b | python scripts/v32_perf_probe.py | 0 | JSON；`verdicts` 五项全 `met`；`network_first_result_status: external_blocked`；`probe_peak_rss_mib: 58.56` |
| c | python -m pytest tests/ -q -k v32 | 0 | `355 passed, 878 deselected in 39.30s` |
| d | python -m pytest tests/ -q | 1 | 三次运行分别 `3 failed, 1230 passed` / `2 failed, 1231 passed` / `5 failed, 1228 passed`；见下 |

**关于 (c) 的总数：** 本工作项新增 `tests/test_v32_acceptance_matrix.py`（7 个用例），因此 v3.2 过滤总数由任务说明中的 346 变为 **355**；其中 C1–C8 的分模块运行合计 348（见 §1 各行退出码），加本模块 7 个。该命令三次运行均为 `355 passed`，稳定。

**关于 (d)：整套运行在本工作树上是**不稳定的**，照实记录三次运行。**

| 运行 | 期间工作树状态 | 结果 | 相对既有 2 个失败多出的项 | 隔离复跑 |
|---|---|---|---|---|
| 1 | 前端工作项 task-7 正在并发写 `web/src/v32/` | `3 failed, 1230 passed` | `PermissionError: [Errno 13] Permission denied: web/src/v32/client.js`，抛自 `scripts/package_release.py:210` | `tests/test_release_version.py` 单跑 `19 passed`，exit 0 |
| 2 | 较安静 | `2 failed, 1231 passed` | 无 | — |
| 3 | 前端 task-7 仍在并发写入 | `5 failed, 1228 passed` | `test_release_self_contained.py::test_the_launcher_ships_under_the_documented_name`、`test_release_version.py::test_packaging_run_survives_a_poisoned_litsearch_import`、`test_release_version.py::test_manifest_lists_every_file_with_its_sha256` | 这三个模块合并单跑：`1 failed, 22 passed`，唯一的失败正是 §4 的既有文档链接检查 |

**判读：**
- 每次运行都出现的失败只有 §4 记录的**既有发布文档链接检查**（基线即有，与本轮 v3.2 无关），未被隐藏、未被修改。
- 运行 1 与运行 3 多出的失败全部落在**发布打包/清单**类测试上，且都能在隔离复跑中通过；其出现时机与另一 teammate 对同一工作树的并发写入吻合（打包测试遍历整棵树，读到正在被改写的 `web/src/v32/*.js`、`web/dist/*`、`litsearch/web/static/*`）。因此判定为**共享工作树的并发伪失败，不是 v3.2 回归**。
- 本工作项**没有**修改这些测试、发布白名单或任何被测代码；也没有为了让它们通过而删除/跳过用例。
- **给 Lead 的建议：** 等前端工作项 task-7 结束、工作树静止后，在干净树上重跑一次 `python -m pytest tests/ -q` 作为最终 (d) 数字；预期应为任务说明给出的 2 个既有失败。
- 任务说明给出的基线为 1175 passed / 2 failed；本工作项实测工作树已比该基线多出测试（其中 7 个为本工作项新增），总数因此不同。


