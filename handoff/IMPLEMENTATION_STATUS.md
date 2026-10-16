# v3.2 实施记录

本文件是交给代码 AI 填写的真实执行记录。所有结论均附实际命令、退出码与产物位置；
未运行标 `not_run`，缺少外部授权标 `external_blocked`，实现缺口不用这两种状态掩盖。

## 仓库基线（M0 核验产物）

| 项 | 实测值 | 证据 |
|---|---|---|
| repository_url | https://github.com/26Wesly3/LEExtractor | `git remote -v` |
| workspace | `E:\DOCTOR 'S work content\LEExtractor_v0.9.14_work` | 本次从远端**新克隆**（用户要求） |
| branch | `feature/v3.2`（从 `master` 创建） | `git rev-parse --abbrev-ref HEAD` |
| 基线 commit | `e601861` | `git log --oneline -1` |
| 远端 tag | `v0.9.14`、`v0.9.13-cloud-checkpoint` | `git tag --sort=-creatordate` |
| 基线软件版本 | 0.9.14（与远端一致） | `litsearch/version.py` |
| contract_version | 3.2（新增，独立于软件版本与 schema_version） | `litsearch/v32/contracts.py` |
| persisted schema_version | 1（整数，**不**等于文档版本 3.2） | `litsearch/v32/store.py:SCHEMA_VERSION` |
| uncommitted_changes | 无（克隆后工作树干净） | `git status --porcelain` |
| python | 3.13.1 | `python --version` |
| node / npm | v24.13.0 / 11.6.2 | `node --version`, `npm --version` |
| 依赖锁 | `constraints.txt`；`pip install -e ".[dev,web]" -c constraints.txt` 退出码 0 | pip 输出 |
| 启动命令 | `python scripts/launch_web.py` / `python scripts/launch_gui.py` / `litsearch-mcp` | 见 `docs/v3.2/qa/ENVIRONMENT.md` |
| 测试命令 | `python -m pytest -q`（`testpaths=["tests"]`） | `pyproject.toml` |
| 基线测试结果 | **873 passed, 5 failed**（259.02 s） | 见下 |
| 前端 | `cd web; npm test`（vitest）、`npm run build`、`python scripts/sync_web_assets.py` | `web/package.json` |

### 基线既有失败（**非本次引入，也未隐藏**）

| 测试 | 原因 |
|---|---|
| `tests/test_mcp_host_compat.py::test_the_server_completes_a_real_mcp_handshake_over_stdio` | stdio 握手下层子进程未应答（本机管道/环境限制） |
| `tests/test_mcp_host_compat.py::test_the_server_lists_tool_schemas_a_host_can_render` | 同上 |
| `tests/test_mcp_host_compat.py::test_a_network_free_tool_call_succeeds_over_stdio` | 同上 |
| `tests/test_release_self_contained.py::test_every_document_reference_resolves_inside_the_release` | `README.md` 引用 `DEPLOYMENT.md`/`THIRD_PARTY_NOTICES.md`，发布白名单未收录 |
| `tests/test_web_release.py::test_root_documents_and_web_guide_links_resolve_in_source_zip` | 同一白名单缺口（`LICENSE`、`docs/demo/*.gif`、`docs/devlog/`、`download/*.zip`） |

两类均为**基线缺陷**，与本轮 v3.2 功能无关；按 START_HERE 第 5 条不擅自降低阈值或删除失败测试，
也不把修改前就存在的失败当作新功能导致。

## 规范包落地位置

| 文档包路径 | 仓库实际位置 | 说明 |
|---|---|---|
| `contracts/*.json` | `litsearch/contracts/*.json` | 运行时契约随包发布（`pyproject` package-data）。放在包内而非仓库根，避免 Dev 可用而 wheel/冻结版不可用 |
| `fixtures/*.json` | `fixtures/*.json` | 仅测试使用 |
| `docs/*.md`、`START_HERE.md`、`manifest.json` | `docs/v3.2/` | 规范随仓库留存，作为唯一解释来源 |
| `handoff/IMPLEMENTATION_STATUS.md` | `handoff/IMPLEMENTATION_STATUS.md` | 本文件 |
| `scripts/validate_pack.py` | `scripts/validate_v32_pack.py` | `PYTHONUTF8=1` 下输出 `status: pass`（25 operations / 32 AC / 22 requirements） |

文档 06 允许“路径不同可映射到实际位置”，上表即映射记录。

## 模块映射（文档 06 名称 → 实际模块）

| 文档名称 | 实际模块 |
|---|---|
| core_rank | `litsearch/v32/core_rank.py` |
| feedback | `litsearch/v32/feedback.py` |
| pdf_import | `litsearch/v32/pdf_import.py` |
| ai_rerank | `litsearch/v32/ai_rerank.py` |
| overview / field insights | `litsearch/v32/overview.py`、`litsearch/v32/mapping.py` |
| credentials | `litsearch/v32/credentials.py` |
| access_gateway | `litsearch/v32/access_gateway.py` |
| venue_metrics | `litsearch/v32/venue_metrics.py` |
| zotero_adapter | `litsearch/v32/zotero.py` |
| web/repository.py 增量迁移 | `litsearch/v32/store.py`（旧 `litsearch/web/repository.py` 原样保留） |
| web/jobs.py | `litsearch/v32/jobs.py` |
| 新增接口 | `litsearch/v32/api.py`、`litsearch/v32/pipeline.py` |
| auth/cas_adapter | **不创建**（AC-29 延期） |

## 阶段

| 阶段 | 状态 | 改动与提交 | 实际命令及结果 | 阻塞或风险 |
|---|---|---|---|---|
| M0 基线与契约 | implemented | `fb6b866`（契约层/项目存储/会话/凭据守卫）、`eb08ea7`（发布工具） | `python -m pytest tests/test_v32_contracts.py tests/test_v32_store.py tests/test_v32_errors.py tests/test_v32_secret_hygiene.py tests/test_v32_release_tooling.py -q` → 60 passed | 无 |
| M1 最小发现闭环 | in_progress | 见各阶段提交 | `not_run`（等待 `litsearch/v32/api.py`） | 依赖 api/pipeline |
| M2 排序与反馈 | implemented（core） | `eb08ea7` | `python -m pytest tests/test_v32_core_rank.py tests/test_v32_feedback.py -q` → 43 passed | 无 |
| M3 AI 与个人额度 | in_progress | `ai_rerank.py` 已落盘 | `not_run` | **无 AI Provider Key** → 真实模型调用 `external_blocked` |
| M4 研究概览 | in_progress | `overview.py`/`mapping.py` 已落盘 | `not_run` | 无 |
| M5 资源与交付 | in_progress | `credentials.py`/`access_gateway.py`/`venue_metrics.py`/`zotero.py` 已落盘 | `not_run` | Zotero、官方期刊指标 `external_blocked` |

## 真实外部联调证据

| 能力 | 状态 | 证据 |
|---|---|---|
| Semantic Scholar 检索 | **integration_verified** | `python scripts/v32_provider_probe.py` 退出码 0；1.954 s 返回 5 篇，含 DOI／摘要／被引数；`get_citations` 探测返回 3 |
| Semantic Scholar 参考文献 | observed_limitation | 同次探测对所选论文 `get_references` 返回 0 条——该记录的参考文献未被索引。属真实观察，不记为失败，也不写成“已实现引用图完整性” |
| AI 模型重排 | **external_blocked** | 无任何模型 Provider Key（用户仅提供 S2 检索 Key）。未发任何真实模型请求；契约测试与注入式 adapter 已覆盖 |
| 官方 JIF / JCR / 中科院分区 | **external_blocked** | 无许可且可核验的来源；按 AC-24 返回 unavailable，不造数 |
| Zotero 读写 | **external_blocked** | 无用户 Zotero 授权、库 ID 与写权限 |
| 浙大直接 CAS / CARSI SP | **deferred** | 按 AC-29 与 `docs/20_ZJU_CAS_DEFERRED.md` 不纳入本轮；代码中无 CAS 客户端、无票据校验、无校园登录入口 |

## 每阶段记录模板

需求 ID；变更文件；数据/接口兼容；实际测试命令及退出码；日志/截图；性能；未通过项；回滚步骤；下一阶段入口。

**回滚步骤（通用）**：本轮改动集中在 `feature/v3.2` 分支的新增路径
（`litsearch/v32/`、`litsearch/contracts/`、`docs/v3.2/`、`fixtures/`、`tests/test_v32_*.py`、`web/src/v32/`）
与一个挂载调用点（`litsearch/web/api.py`）。
`git revert <commit>` 或切回 `master` 即可恢复上一稳定排序与旧数据；旧项目文件不被覆写
（迁移在备份副本上运行并记录备份路径；`schema_version` 高于本程序支持值时拒绝写入并保留原文件）。
特性开关：`core_rank_enabled`、`ai_rerank_enabled`。

## 最终交付

分别列 implemented、integration_verified、effectiveness_validated、external_blocked、deferred。
不得将未执行的命令填为通过。直接 SSO 固定 deferred。

> 本表在 M5 结束时补全最终状态；当前为进行中快照，尚未做效果评测
> （`effectiveness_validated` 需要 24 主题专家标注集与 8 位新手任务实测，见 `docs/09_ACCEPTANCE_BENCHMARK.md`，
> 本轮未执行，标记 `not_run`，不以未测数字宣称达标）。
