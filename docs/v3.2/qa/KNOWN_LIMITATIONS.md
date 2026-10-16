# v3.2 已知限制（KNOWN_LIMITATIONS）

- 日期：2026-10-17
- 分支：`feature/v3.2`
- 规则：每条限制写明**现象**、**原因**、**证据位置**、**关闭条件**。凡本轮没有实测的能力一律写明「未实测 / 外部阻塞」，不用「接近达标」或未实测数字顶替。`implemented`、`integration_verified` 与效果验证状态三者不互相代替（`docs/v3.2/18_REQUIREMENTS_TRACEABILITY.md:32`）。

本轮**没有**任何一项可以声明效果验证状态：`docs/v3.2/09_ACCEPTANCE_BENCHMARK.md:48-52,64` 要求的 24 主题专家标注集（≥2 位评阅者/主题）与 8 位新手 20 分钟任务实测均**未执行**。效果结论因此是 **`not_run`**。

---

## L1 无 AI Provider Key：AI 精排路径被跳过

- **现象**：`ai_refined` 阶段不发生，系统按本地排序返回（`AC-14`）。任何真实模型请求都没有发出，也没有被伪造。
- **原因**：本机不存在任何模型 provider 凭据。已核验（只判断存在性，不读取值）：环境变量 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` / `GEMINI_API_KEY` / `AI_API_KEY` 均不存在；`~/.leextractor/secrets.env` 中只有 Semantic Scholar 检索 Key，没有任何模型 Key。`handoff/IMPLEMENTATION_STATUS.md:87` 记为 **`external_blocked`**。
- **证据**：`tests/test_v32_ai_rerank.py` 文件头声明「every AI path is driven by an injected adapter, and the real-provider path is reported as blocked rather than faked」；`tests/test_v32_overview.py` 声明「The AI path is exercised with injected stub adapters only」。因此 `AC-14/AC-15/AC-17` 由**注入式 adapter** 覆盖，**不是**真实模型联调。
- **关闭条件**：提供一个模型 provider Key 并获得联网授权，再对一个真实模型跑一次窗口重排与拒绝路径，单独记录 provider / model / prompt 版本与日期。

## L2 主题信号只有词法证据（无语义模型）

- **现象**：`topic` 信号来自查询与题名/摘要的词法重合（`queries` recall），不是语义相似度。没有语义分被虚构为 0——缺失就是 `null`（`docs/v3.2/04_ALGORITHM_DESIGN.md`、`litsearch/v32/pipeline.py:273-296`）。
- **原因**：`contracts.runtime_defaults().embedding_status == "must_verify_in_M0"`，且 `embedding_revision` 为 `null`；本机没有经过核验的语义模型，`V32State.vectors` 在真实运行中为 `None`。
- **证据**：`litsearch/v32/pipeline.py:273-296`（`topic_scores` 的 docstring：「no semantic score is invented when no model is loaded」）；`contracts/defaults.json` → `runtime.embedding_status`。
- **关闭条件**：M0 核验一个语义模型，记录 `embedding_revision`，把论文向量灌入 `V32State.vectors`，然后把主题信号切换为语义口径并重测。

## L3 provider 并发/超时/重试旋钮仍归 `litsearch.sources` 所有并由环境变量驱动

- **现象**：契约 `discovery` 定义了 `provider_concurrency: 3`、`provider_connect_timeout_seconds: 3`、`provider_total_timeout_seconds: 10`、`provider_retries: 1`，但 **v3.2 层没有强制这四个值**；实际生效的是 `litsearch.sources` 的默认值与 `LEEXTRACTOR_*` 环境变量。
- **原因**：v3.2 pipeline 只消费了 `query_variants`、`candidate_cap`、`round_budget_seconds`、`provider_priority`、`references_per_seed`、`citations_per_seed`、`seeds_per_round`（`litsearch/v32/pipeline.py:246,690-691,695,742-744,891`）。`provider_concurrency` 只在 `litsearch/sources.py:66,2531` 使用（`DEFAULT_PROVIDER_CONCURRENCY = 5`，上限 16，env `LEEXTRACTOR_PROVIDER_CONCURRENCY`）；超时在 `litsearch/sources.py:83-102`（默认 connect 5.0 s / read 12.0 s，env `LEEXTRACTOR_HTTP_CONNECT_TIMEOUT` / `LEEXTRACTOR_HTTP_READ_TIMEOUT`）。只有 Zotero 适配器读了契约的超时/重试（`litsearch/v32/zotero.py:223-225`）。
- **影响**：契约里的 3 并发 / 3 s 连接 / 10 s 总超时 / 1 次重试**没有被本轮实测也未由 v3.2 层强制**；这是复用旧轮子的直接后果（`R16`），不是隐藏缺陷——按规范照实披露。
- **关闭条件**：由 pipeline 在检索前按契约构造 `SourceManager`（或让 `sources` 读契约），并为「契约值与生效值一致」补一条测试。

## L4 未缓存远程检索首批预算（≤20 s）未实测

- **现象**：`performance_targets.network_first_result_target_seconds = 20` 未测。
- **原因**：本次会话没有获得联网授权，`scripts/v32_perf_probe.py` 确定不打开 socket（输出 `"network_used": false`）。本机**确实**有 Semantic Scholar Key（`~/.leextractor/secrets.env` 中存在 `SEMANTIC_SCHOLAR_API_KEY`），所以这是**授权**缺口而不是凭据缺口。
- **状态**：`external_blocked`（见 `PERFORMANCE_REPORT.md` §2.5）。
- **关闭条件**：用户明确批准联网后运行 `scripts/v32_provider_probe.py`，记录耗时、返回条数与错误码。

## L5 S2 的真实联调结论来自 M0 记录，不是本轮复测

- **现象**：`handoff/IMPLEMENTATION_STATUS.md:85` 记录 Semantic Scholar 检索为 **`integration_verified`**（`scripts/v32_provider_probe.py` 退出码 0，1.954 s 返回 5 篇，含 DOI/摘要/被引数；`get_citations` 返回 3）。**本轮工作项没有重跑该探测**（原因同 L4）。
- **同时观察到的限制**：同一次探测对所选论文 `get_references` 返回 **0 条**——该记录的参考文献未被索引。这是真实观察，不等于「引用图完整」，也不记为失败（`handoff/IMPLEMENTATION_STATUS.md:86`，状态 `observed_limitation`）。
- **关闭条件**：联网复测，并换一篇参考文献被索引的论文重测 `get_references`。

## L6 3 视口截图未采集（首页首屏一项已由浏览器实测关闭，其余仍未采集）

- **现象**：`docs/v3.2/05_UX_UI_SPEC.md:5` 要求首页、结果、详情抽屉、图谱在 **1440×900 / 1024×768 / 390×844** 三个视口下的截图。
  **首页首屏要求已实测满足**（见下）；**其余页面在三个视口下的截图仍未采集**。
- **原因**：`docs/v3.2/qa/VISUAL_BASELINE.md` 的「Screenshot clause」与 §9 第 1 条明确写 **NOT VERIFIED — not captured**：捕捉截图需要运行本地服务加浏览器会话，本包的工作项没有分配该动作；同一句规范也说缺截图不是改主题的理由，因此设计令牌与动效的源码提取仍然成立。
- **部分关闭（2026-10-17）**：`docs/v3.2/05_UX_UI_SPEC.md`「页面结构」要求首页首屏展示两个主入口。该要求在真实浏览器中**实测达成**：
  Playwright 驱动系统 Edge（headless Chrome 在本机因 WinError 14001 不可用，且 `--window-size` 会把窄窗口钳到约 492px，故必须用 Playwright），
  在 1440×900 / 1024×768 / 390×844 三个精确视口、`scrollY=0` 下读取真实 `getBoundingClientRect()`：
  两个入口按钮底边分别为 **707/707 ≤ 900**、**679/679 ≤ 768**、**640/797 ≤ 844**，均无需滚动；390×844 余量 47px。
  证据形式为尺寸断言（`web/src/v32/home-first-screen.test.js` 固定了使能规则）＋测量时的人工截图核对，**截图本身未入库**。
- **测量口径注意**：字标的 flex 盒跨满 cover，因此「字标 vs 求是鹰」的盒模型测试在 1440/1024 会报告重叠，属**测量假象**；
  截图显示两者并排，无视觉碰撞。这是口径说明，不是缺陷。
- **影响（剩余）**：`AC-18`（新手与视觉回归）在**结果页、详情抽屉、图谱**三个页面仍只有源码级证据，没有渲染级证据。
- **关闭条件**：起本地服务，对结果页、详情抽屉与图谱在三个视口各截一张，附到交付记录。

## L7 没有直接浙大统一认证（本轮固定延期）

- **现象**：界面没有「浙大账号登录」入口，代码里没有 CAS 客户端、没有票据校验、没有校园身份请求。`access_gateway` 把 `application_sso` 能力报为 `external_blocked` / `reason_code: deferred_this_round`（`litsearch/v32/access_gateway.py:194-218`）。
- **原因**：按 `AC-29`（`required_this_release: false`）与 `docs/v3.2/20_ZJU_CAS_DEFERRED.md` 延期；将来需要独立授权与真环境验证。`handoff/IMPLEMENTATION_STATUS.md:90` 记为 `deferred`。
- **关闭条件**：取得 CAS/CARSI 服务提供方授权与测试账号，在真实环境验证登录、票据与退出，并重新评估隐私影响。

## L8 没有官方 JIF / JCR / 中科院分区来源

- **现象**：官方影响因子、JCR 分区、中科院分区一律返回 `unavailable`，不展示数值，也不阻塞排序（`AC-24`）。
- **原因**：没有**许可且可核验**的来源；按 `docs/v3.2/13_VENUE_INTELLIGENCE.md` 不得造数。`litsearch/v32/venue_metrics.py:72` 的理由文案：「无可验证的许可来源：按规范不展示官方 JIF、JCR 分区或中科院分区数值。」`handoff/IMPLEMENTATION_STATUS.md:88` 记为 `external_blocked`。
- **关闭条件**：接入一个有许可、可核验的期刊指标来源；在此之前保持 `unavailable`。

## L9 Zotero 读写未做真实联调

- **现象**：Zotero 导入/导出只有适配器契约级证据，没有真实库往返。
- **原因**：没有用户 Zotero 授权、库 ID 与写权限。`tests/test_v32_zotero.py` 文件头声明「The live Zotero integration is `external_blocked` in this environment ... no test here claims a real round trip」。`handoff/IMPLEMENTATION_STATUS.md:89` 记为 `external_blocked`。
- **关闭条件**：提供 Zotero API 凭据与一个可写的测试库（或 fake server 之外的沙箱库），验证选择项导入、逐项结果与版本冲突不覆盖（`AC-21/AC-22`）。

## L10 效果评测未执行

- **现象**：没有 `docs/v3.2/09_ACCEPTANCE_BENCHMARK.md` 要求的核心文献效果结论（CoreRank 相对旧 hybrid 的 nDCG@20 增益、核心 Recall@50 变化、MRR、分支覆盖、重复度），也没有 8 位新手任务实测。
- **原因**：需要 24 个主题（≥4 学科、8 开发 + 16 独立测试）、每主题 ≥2 位领域知情评阅者独立标注并仲裁一致性；本轮未组织。
- **状态**：`not_run`。因此任何交付物都**不得**声明效果验证状态；`ACCEPTANCE_MATRIX.md` 中所有 AC 的状态都不会是该值，`tests/test_v32_acceptance_matrix.py` 会因出现该状态而失败。
- **关闭条件**：执行该评测并按主题给出 bootstrap 置信区间，同时允许切回旧排序。

## L11 性能报告只覆盖单进程本地预算

- **现象**：`AC-26` 的本地排序/反馈/缓存读取预算已实测达标，但**应用合计峰值内存 ≤1400 MiB** 与 **HTTP 端到端 p95** 未测。
- **原因**：`litsearch/contracts/defaults.json` 的 `app_total_peak_rss_mib` 测量边界是「Web、worker、模型和解析器**合计**」（`docs/v3.2/15_PERFORMANCE_DEPLOYMENT.md:33`），离线单进程探针只能给出下界（58.56 MiB）；HTTP 传输层未测，因为本机 `NO_PROXY` 含字面量 `[::1]`，`httpx.Client()` 构造即抛 `httpx.InvalidURL: Invalid port: ':1]'`（已实测复现，见 L12），FastAPI `TestClient` 因此也构造失败。
- **关闭条件**：在真实多进程部署上采样合计 RSS；清理 `NO_PROXY` 中的 `[::1]` 条目后经 HTTP 客户端压测。

## L12 `NO_PROXY` 含 `[::1]` 使 httpx 客户端无法构造（已实测；此前记录的 MCP 失败本轮未复现）

- **现象（已复现）**：本机 `NO_PROXY` / `no_proxy` 值为 `10.195.152.43,.local,localhost,127.0.0.1,::1,[::1]`。构造 httpx 客户端时立即失败：

  ```
  httpx.InvalidURL: Invalid port: ':1]'
    File "httpx/_client.py", line 698, in __init__  ->  URLPattern(key)
  ```

  复现命令：`python -c "import httpx; httpx.Client()"`。这是因为 httpx 把 `[::1]` 当作带端口的 URL 模式解析。任何依赖 httpx 构造客户端的代码（含 FastAPI `TestClient`）在本机默认环境下无法运行——`L11` 的 HTTP 传输层未测即由此而来。
- **现象（未复现）**：早先记录「`tests/test_mcp_host_compat.py` 3 个用例因同一根因失败」。本工作项**两次**运行该模块均为 **11 passed / 退出码 0**，**未复现**任何失败：

  ```
  $env:PYTHONUTF8="1"; python -m pytest tests/test_mcp_host_compat.py -q --tb=line
  11 passed in 23.47s        (exit 0)
  ```

  同机 `NO_PROXY` 未变，`HTTP_PROXY`/`HTTPS_PROXY` 指向本机代理。本工作项**未修改**该模块、相关环境变量或任何代码，因此该模块当前是否稳定通过仍未知（MCP 传输走 stdio，未必经过 httpx 客户端构造）。状态记为 **not reproduced**，既不算通过也不算失败。
- **关闭条件**：清理 `NO_PROXY` 中的 `[::1]` 条目，然后在干净环境下重跑 `tests/test_mcp_host_compat.py` 与任何 httpx 依赖路径。

## L13 两个既有发布文档链接检查失败（与 v3.2 无关）

- **现象**：`tests/test_release_self_contained.py::test_every_document_reference_resolves_inside_the_release` 与 `tests/test_web_release.py::test_root_documents_and_web_guide_links_resolve_in_source_zip` 失败。
- **原因**：`README.md` 引用 `LICENSE` / `DEPLOYMENT.md` / `docs/demo/*.gif` / `docs/devlog/` / `download/*.zip` 等，发布白名单未收录（`handoff/IMPLEMENTATION_STATUS.md:34-35`）。基线即存在，与本轮 v3.2 功能无关。
- **关闭条件**：修正发布白名单或移除失效引用；本轮不擅自改阈值或删测试。

---

## 明确不是限制的项（本轮已实现且有通过用例）

- 本地 CoreRank RRF 排序、缺失数据降级、主题门控、起始文献选择：`tests/test_v32_core_rank.py`、`tests/test_v32_feedback.py`。
- PDF 逐文件导入、无 DOI 文本种子、内容哈希去重、参考文献区 DOI 误识别防护、批次边界：`tests/test_v32_pdf_import.py`。
- AI 窗口校验的严格拒绝路径（重复/未知/遗漏 ID、上下文 hash、越界分值、未知角色、多余属性）：`tests/test_v32_ai_rerank.py`（注入式 adapter）。
- 图谱稀疏降级、时间线/趋势证据、概览证据契约：`tests/test_v32_mapping.py`、`tests/test_v32_overview.py`。
- 任务状态机、supersede、取消保留、身份合并、快照：`tests/test_v32_jobs.py`、`tests/test_v32_store.py`。
- HTTP 契约面（25 个操作、错误信封、owner 隔离、游标 409、凭据不回显、无 CAS/SSO 路由）：`tests/test_v32_api.py`、`tests/test_v32_api_contract.py`。
- 校园资源入口只走注册表、期刊指标不造数、Zotero 适配器契约：`tests/test_v32_access_gateway.py`、`tests/test_v32_venue_metrics.py`、`tests/test_v32_zotero.py`。
- 凭据卫生与仓库完整性：`tests/test_v32_credentials.py`、`tests/test_v32_secret_hygiene.py`、`tests/test_v32_repo_integrity.py`。

以上「有通过用例」只等于 `implemented`（代码存在且用例通过），**不等于** `integration_verified`（真实外部服务应答）；唯一的 `integration_verified` 是 L5 所述的 Semantic Scholar，且其证据来自 M0 记录而非本轮复测。
