# v3.2 实施记录

本文件是交给代码 AI 填写的真实执行记录。所有结论均附实际命令、退出码与产物位置；
未运行标 `not_run`，缺少外部授权标 `external_blocked`，实现缺口不用这两种状态掩盖。

## 仓库基线（M0 核验产物）

| 项 | 实测值 | 证据 |
|---|---|---|
| repository_url | https://github.com/26Wesly3/LEExtractor | `git remote -v` |
| workspace | `E:\DOCTOR 'S work content\LEExtractor_v0.9.14_work` | 本次从远端**新克隆**（用户要求） |
| branch | `feature/v3.2`（从 `master` 创建，已推送） | `git rev-parse --abbrev-ref HEAD`；`git ls-remote --heads origin feature/v3.2` |
| 基线 commit | `e601861` | `git log --oneline -1` @ clone |
| 远端 tag | `v0.9.14`、`v0.9.13-cloud-checkpoint` | `git tag --sort=-creatordate` |
| 基线软件版本 | 0.9.14（与远端一致） | `litsearch/version.py` |
| contract_version | 3.2（新增，独立于软件版本与 schema_version） | `litsearch/v32/contracts.py` |
| persisted schema_version | 1（整数，**不**等于文档版本 3.2） | `litsearch/v32/store.py:SCHEMA_VERSION` |
| uncommitted_changes | 无（克隆后工作树干净） | `git status --porcelain` |
| python | 3.13.1 | `python --version` |
| node / npm | v24.13.0 / 11.6.2 | `node --version`, `npm --version` |
| 依赖锁 | `constraints.txt`；`pip install -e ".[dev,web]" -c constraints.txt` 退出码 0 | pip 输出 |
| 启动命令 | `python scripts/launch_web.py`（FastAPI）/ `python scripts/launch_gui.py`（Streamlit）/ `litsearch-mcp`（MCP） | `docs/v3.2/qa/ENVIRONMENT.md` |
| 测试命令 | `python -m pytest -q`（`testpaths=["tests"]`） | `pyproject.toml` |
| 基线测试结果 | **873 passed, 5 failed**（259.02 s） | 见下 |
| 交付时测试结果 | **1242 passed, 2 failed**（159.41 s，安静工作树） | `python -m pytest -q`；另见「阶段」表 |
| 前端 | `cd web; npm test`（vitest 14 文件 / 81 通过）、`npm run build`、`python scripts/sync_web_assets.py` | 见「阶段」表 |

### 基线既有失败（**非本次引入，也未隐藏**）

| 测试 | 实测原因 |
|---|---|
| `tests/test_release_self_contained.py::test_every_document_reference_resolves_inside_the_release` | `README.md` 引用 `DEPLOYMENT.md`/`THIRD_PARTY_NOTICES.md`，`scripts/package_release.py` 白名单未收录 |
| `tests/test_web_release.py::test_root_documents_and_web_guide_links_resolve_in_source_zip` | 同一白名单缺口（`LICENSE`、`docs/demo/*.gif`、`docs/devlog/`、`download/*.zip`、`README.en.md`） |
| `tests/test_mcp_host_compat.py`（3 项） | **环境性，且不可稳定复现**：环境变量 `NO_PROXY` 含字面量 `[::1]`，`httpx` 将其解析为 URLPattern 并抛 `InvalidURL: Invalid port: ':1]'`，fastmcp 启动即死。三次基线运行中出现 3 次失败，交付前的多次运行中 11 passed / exit 0。已独立复现根因：`python -c "import httpx; httpx.Client()"` → `InvalidURL` |

两类均与本轮 v3.2 功能无关；按 START_HERE 第 5 条不擅自降低阈值、不删除失败测试，也不把修改前就存在的失败当作新功能导致。
**基线失败清单在交付时只保留了前两项**；MCP 三项为环境性且不可稳定复现，按「不把未复现的失败写成通过、也不写成失败」记录为 *not reproduced*。

## 规范包落地位置

| 文档包路径 | 仓库实际位置 | 说明 |
|---|---|---|
| `contracts/*.json` | `litsearch/contracts/*.json` | 运行时契约随包发布（`pyproject` package-data）。放在包内而非仓库根，避免 Dev 可用而 wheel/冻结版不可用 |
| `fixtures/*.json` | `fixtures/*.json` | 仅测试使用 |
| `docs/*.md`、`START_HERE.md`、`manifest.json` | `docs/v3.2/` | 规范随仓库留存，作为唯一解释来源 |
| `handoff/IMPLEMENTATION_STATUS.md` | `handoff/IMPLEMENTATION_STATUS.md` | 本文件（**实现者填写**，内容与包内模板不同属预期） |
| `scripts/validate_pack.py` | `scripts/validate_v32_pack.py` | 已适配本仓库路径映射（**内容与包内模板不同属预期**） |

R22 由 `scripts/validate_v32_pack.py` 校验：`PYTHONUTF8=1; python scripts/validate_v32_pack.py` → **exit 0，`status: pass`**，
`layout: repository-mapped`，25 operations / 5 ranking fixtures / 32 acceptance specifications / 22 requirements，
35 个 manifest 文件 SHA256 校验通过，2 个「预期不同」文件各自附理由与两个摘要（不隐藏差异）。
由 `tests/test_v32_pack_validator.py` 固定，防止回归。

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
| web/jobs.py | `litsearch/v32/jobs.py`（新）＋ `litsearch/web/jobs.py`（旧，仅修复信号量缺陷） |
| 新增接口 | `litsearch/v32/api.py`、`litsearch/v32/pipeline.py` |
| 契约加载 | `litsearch/v32/contracts.py`、`litsearch/v32/types.py`、`litsearch/v32/errors.py` |
| 原子写入 | `litsearch/jsonio.py`（由 `litsearch/web/repository.py` 再导出，消除循环导入） |
| auth/cas_adapter | **不创建**（AC-29 延期） |

## 阶段

| 阶段 | 状态 | 提交 | 实际命令及结果 |
|---|---|---|---|
| M0 基线与契约 | implemented | `fb6b866` | `python -m pytest tests/test_v32_contracts.py tests/test_v32_store.py tests/test_v32_errors.py tests/test_v32_secret_hygiene.py tests/test_v32_release_tooling.py tests/test_v32_repo_integrity.py -q` → 通过 |
| M1 最小发现闭环 | implemented | `894beea`、`6cfd3e0`、`25fd9d3`(v0.9.15) | `pytest tests/test_v32_pdf_import.py tests/test_v32_api.py tests/test_v32_api_contract.py -q` → 通过；真实 HTTP 端到端见下 |
| M2 排序与反馈 | implemented | `eb08ea7`、`e1ab2ca`(v0.9.16) | `pytest tests/test_v32_core_rank.py tests/test_v32_feedback.py tests/test_v32_jobs.py tests/test_v32_store.py -q` → 通过 |
| M3 AI 与个人额度 | implemented（真实模型调用 external_blocked） | `a8346b6`(v0.9.17) | `pytest tests/test_v32_ai_rerank.py tests/test_v32_credentials.py -q` → 通过 |
| M4 研究概览 | implemented | `4bf84af`(v0.9.18) | `pytest tests/test_v32_mapping.py tests/test_v32_overview.py -q` → 通过 |
| M5 资源与交付 | implemented（Zotero / 官方指标 external_blocked） | 见「版本」 | `pytest tests/test_v32_credentials.py tests/test_v32_access_gateway.py tests/test_v32_venue_metrics.py tests/test_v32_zotero.py -q` → 73 passed |

### 版本

| 版本 | 阶段 | 推送 |
|---|---|---|
| 0.9.15 | M1 | ✅ `origin/feature/v3.2` |
| 0.9.16 | M2 | ✅ |
| 0.9.17 | M3 | ✅ |
| 0.9.18 | M4 | ✅ |
| 0.9.19 | M5 | ✅ |

版本更新由 `scripts/v32_stage_release.py` 执行：一次更新 `litsearch/version.py`、`pyproject.toml`、`CHANGES.md`、`CHANGELOG.md` 与三个 README（徽章＋便携包链接），
**全部成功或全部不动**；提交前必须通过指定的 pytest 门禁；推送**从不 force**。便携 ZIP 不存在时如实报告 `NOT PRESENT`，不伪造产物。

## 真实外部联调证据

| 能力 | 状态 | 证据 |
|---|---|---|
| Semantic Scholar 检索 | **integration_verified** | `python scripts/v32_provider_probe.py` 退出码 0；1.954 s 返回 5 篇，含 DOI／摘要／被引；`get_citations` 返回 3 |
| 真实 HTTP 端到端闭环 | **integration_verified** | `python scripts/v32_http_smoke.py` 退出码 0：session 200 带 CSRF；capabilities 200 `server_basic`／14 项且含 `available/not_configured/external_blocked`；project 201 revision=1；同幂等键重放返回**同一个** project；discover 202 queued；任务终态 `partial`、stage `fast_ranking`、`failed_sources=[arxiv, semantic_scholar]`、**保留 20 篇候选**；overview `partial` 且 5 条 statement、10 个字段齐备；不存在项目 404；`/api/v3/auth/cas/login` **404**（AC-28） |
| Semantic Scholar 参考文献 | observed_limitation | 同次探测 `get_references` 对所选论文返回 0 条——该记录参考文献未被索引。属真实观察，不记为失败 |
| AI 模型重排 | **external_blocked** | 无任何模型 Provider Key（用户仅提供 S2 检索 Key）。未发真实模型请求；窗口/校验/合并由注入式 adapter 覆盖，AC-14/15/17 均为 implemented |
| 官方 JIF / JCR / 中科院分区 | **external_blocked** | 无许可且可核验的来源；按 AC-24 返回 unavailable，不造数 |
| Zotero 读写 | **external_blocked** | 无用户 Zotero 授权、库 ID 与写权限；import/export 在无凭据时返回 503 `capability_unavailable`，export-preview 离线可用 |
| 可复现启动方式 | **integration_verified** | `python scripts/v32_http_smoke.py` 自行启动真实 uvicorn 并走完整流程 |

## 最终交付

规模：`python -m pytest -q -k v32` → **361 passed**（交付前最后实测）；全量 `python -m pytest -q` → **1242 passed, 2 failed**（两项均为基线既有发布文档链接检查）；
前端 `cd web; npm test` → **14 文件 / 81 通过**。

交付基线：分支 `master` 于 commit `86cbaa9`，标签 `v0.9.19`；功能分支 `feature/v3.2` 于 commit `c4b8241`。两者均已推送到 `origin`。

| 分类 | 内容 |
|---|---|
| **implemented** | M0 契约层与项目存储、M1 双入口与 PDF 逐文件导入与身份归一、M2 CoreRank/反馈/快照与并发、M3 AI 局部重排与凭据、M4 证据化概览与领域地图、M5 访问入口/期刊指标/Zotero adapter、25 个增量 API 操作、四阶段工作区界面。全部有通过的 pytest/vitest 用例与 `docs/v3.2/qa/ACCEPTANCE_MATRIX.md` 中记录的实测命令与退出码 |
| **integration_verified** | Semantic Scholar 真实检索；真实 uvicorn 上的端到端闭环（见上） |
| **effectiveness_validated** | **none** —— 24 主题专家标注集与 8 位新手任务实测（doc 09）本轮未执行，标 `not_run`；不以未测数字宣称达标 |
| **external_blocked** | AI 模型重排（无 Provider Key）；官方 JIF/JCR/中科院分区（无许可来源）；Zotero 读写（无授权）；未缓存的远程首批 ≤20 s（探针不开 socket） |
| **deferred** | AC-29 浙大直接 CAS / CARSI SP（唯一 deferred，`required_this_release: false`） |
| **not_measured** | 应用总峰值 RSS ≤1400 MiB（仅得单进程下界 58.56 MiB，目标含 web+worker+模型+parser 合计）；HTTP 端到端 p95（本机 httpx 无法构造 client） |

性能（300 篇固定语料、固定 32 维确定性向量、20 次重复；本机 16 逻辑核 / 14188 MiB，参考环境 2 vCPU / 2048 MiB）：
快速排序 p95 **0.0365 s**（目标 2）、暖缓存增强 p95 **0.0930 s**（5）、判断后重排 p95 **0.0717 s**（1）、缓存首屏 p95 **0.0368 s**（5）、元数据 API p95 **18.2 ms**（500），**全部 met**；`--cpus 2` 复测仍全部 met。
**诚实边界**：这些是进程内数字且本机远快于参考环境，**不能**证明 2 vCPU / 2 GB 参考部署达标。详见 `docs/v3.2/qa/PERFORMANCE_REPORT.md`。

已知限制一览见 `docs/v3.2/qa/KNOWN_LIMITATIONS.md`（L1–L13），包括：无模型 Key、主题信号仅词法（`runtime.embedding_status = must_verify_in_M0`、无已验证模型）、
provider 并发/超时/重试仍由 `litsearch.sources` 的环境变量拥有、doc 05 三视口截图未采集、`RETRIEVAL_LOCK` 为既有死代码（未擅自删除）。

## 回滚步骤

本轮改动集中在 `feature/v3.2` 分支的新增路径
（`litsearch/v32/`、`litsearch/contracts/`、`litsearch/jsonio.py`、`docs/v3.2/`、`fixtures/`、`tests/test_v32_*.py`、`tests/test_retrieval_slots_stability.py`、`web/src/v32/`）
与四个既有文件的定点修改（`litsearch/web/api.py` 挂载点、`litsearch/web/__init__.py` 惰性导入、`litsearch/web/repository.py` 再导出、`litsearch/web/jobs.py` 信号量修复）。

- 代码回滚：`git revert <commit>`，或 `git checkout master` 并重新打版本。
- 数据回滚：旧项目文件**不被覆写**；迁移在备份副本上运行并把备份路径写入 `backups/`，`schema_version` 高于本程序支持值时拒绝写入并保留原文件。
- 特性开关：`core_rank_enabled`、`ai_rerank_enabled`，可切回上一稳定排序而不重建数据。
- 产物回滚：`web/dist` 与 `litsearch/web/static` 为构建产物，`npm run build && python scripts/sync_web_assets.py` 可重建任何提交对应的前端。
