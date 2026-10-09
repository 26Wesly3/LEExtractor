# 更新日志

倒序记录。v0.9.0 及更早的说明保留在下方，未删改（其中的测试数量与「结果可信」等结论属于当时版本，不构成当前版本的验收证据）。

## v0.9.7（2026-10-09）

- 接入 Vue 3 / Vuetify 工作台与本机 FastAPI，11 个页面使用真实项目接口，保留原 Streamlit / MCP 入口。
- 复用现有查询规划、系统检索、雪球扩展、BC/CC、研究版图、候选问题、PRISMA、标定、下载和证据包算法。用户选择的年份与种子传入领域流程。
- 后台任务支持真实阶段计数、全程 HTTP 预算、协作取消、检查点及重启中断恢复。项目独立存储，revision 防止旧标签覆盖新状态。
- 补全 schema v4 可选运行元数据：stop_reason、HTTP 计数、运行历史、评分/标定上下文、雪球待重试队列与全文获取失败事实。旧会话保持兼容，未知事实不补造。
- 修正 Windows 零时长超时边界；修复真实 Session 传输被两层重复计数的问题。
- 发布包包含前端构建文件和 Web 启动批处理；Python wheel 同样包含静态资源。文档与 CI 更新为实际整合路径。
- 本版实测与限制见 [VALIDATION_v0.9.7.md](VALIDATION_v0.9.7.md)。历史测试数量仅属于对应旧版本。

## v0.9.5（2026-10-08）

在 v0.9.4 包基础上收口**上一轮复核记录在案的三处遗留问题**。不新增功能（无 Semantic Retrieval / RRF / Zotero / GROBID / REST）。

**1. `find_similar` 不再要求 finder 提供额外状态（曾被列为"建议但不阻塞"）**
- 问题：`search.py` 直接读 `finder.incomplete` / `finder.stop_reason` / `finder.retrieval_results`。真实 `SimilarPaperFinder` 三个属性都有，所以生产路径安全；但只实现 `find_similar()` 的替身（v0.9.4 之前工作流用到的全部接口）会抛
  `AttributeError: 'Finder' object has no attribute 'incomplete'`——而且是在**检索已经跑完之后**才抛，整段工作白费。
- 修法：改为 `getattr(..., 默认值)` 读取。默认值取"没有报告不完整"——**一个无法报告不完整的实现，绝不能被当作已经报告了不完整**。
- 修复前后都用测试固定：新增 `tests/test_v095_finder_contract.py`（4 项），把生产代码临时改回裸属性访问可复现上述 `AttributeError`（2 项失败），改回 `getattr` 后转绿。同时确保**真实报告不完整时仍被采纳**（`incomplete=True` / `stop_reason` / `retrieval_results` 照常写入 run history）。

**2. 发布白名单按版本写死，导致当前版本的验收报告可能漏出包外**
- 问题：`WHITELIST_ROOT_FILES` 逐字列出各版本的报告文件名。每次升版都必须手改白名单，忘记改就会**静默**把当前版本的验收报告排除在包外——而 `README.md` 等多个文档正引用它。
- 修法：改为按前缀匹配（本版报告与历史报告），历史各版本一并保留，升版不再需要维护。

**3. 文档引用悬空：被要求"先读"的文件不在包里，且自称是验收依据**
- 问题：`ONBOARDING.md` 让新读者**先读**一份历史说明，而该文件与代码评审文档都不在发布白名单里，因此**每个包里都缺**——读者要么按不了指引，要么以为包不完整。更麻烦的是它写的是旧版本（v0.6.0）的交付说明，却写着"交付版本 0.6.0、验收日期 2026-10-05"，容易被当成当前验收依据。`ONBOARDING.md` 还指引双击「启动LEExtractor.bat」，而包里只有工作名 `启动LEExtractor_收到后改回bat.bat`。
- 修法：
  - 两个历史文件纳入包，并在开头加**醒目的历史横幅**，指明"不是当前验收依据"，指向 `README.md`。
  - `ONBOARDING.md` 的开篇改为指向当前文档（README / VALIDATION / ARCHITECTURE / CHANGES / ROADMAP），并明确那两个文件是 v0.6.0 / v0.3.0 历史材料。
  - 打包时把启动脚本按**文档承诺的名字** `启动LEExtractor.bat` 发出（`RELEASE_RENAMES`），`MANIFEST.sha256` 同步记录发出后的名字。
  - 新增 `tests/test_release_self_contained.py`（5 项）：把"已发布文档中的每个文件名引用都能在包内解析"变成不变量，并校验历史横幅、启动脚本名字、manifest 与包内容一致。

## v0.9.4（2026-10-08）

correctness freeze 收口。继续修复 v0.9.3 独立复核发现的跨模块不变量，不新增 Semantic Retrieval、RRF、Zotero、GROBID 等功能。

**1. Calibration 与 whole corpus 完全一致**
- 新增/统一 `current_scored_corpus(state)`：Search、Snowball、Similar、PRISMA 只定义一份当前可比较语料；Scoping 仅在主语料为空时回退。
- UI 标定、标定相关比例、自动初筛、screening domain、session restore 全部使用同一 scored corpus，不再以 `state.search_papers` 代替扩展后的完整候选集。
- 校准有效性现在同时校验当前 `ALGORITHM_VERSION`、`SCORE_VERSION` 与 `score_context_id`；修复“拿 calibration 自己的 algorithm_version 和自己比较”的恒真校验。

**2. Score context 指纹覆盖真正的排序输入**
- 保留 identity `corpus_hash`，新增 `ranking_input_hash`（canonical id + title + abstract + topics，顺序无关、空白/大小写归一）。
- `build_score_context` 同时绑定 query hash、identity corpus hash、ranking-input hash、algorithm version、score version；同一论文 ID 在元数据补全后会得到新 context，旧 calibration 自动失效。

**3. RetrievalResult 真正贯穿 Scoping / Snowball / Similar**
- `SourceManager` 新增 `get_references_result` / `get_citations_result`，relation lookup 不再先压扁为 `list[Paper]`。
- Snowball 对 PARTIAL/失败保留已到达论文并把 seed 留在待重试队列；provider failure 不再变成 `no_new_results/saturated`；取消与预算耗尽保持独立 stop_reason。
- Similar 优先消费 result API，记录 `retrieval_results`、`incomplete` 与 stop_reason；失败不再仅表现为“0 个相似论文”。
- `scope_topic` 与 systematic search 共用 `_retrieve()`，Scoping 也能显式记录 provider failure。

**4. TRUNCATED 与 PARTIAL 语义拆分**
- `RetrievalStatus.TRUNCATED`：provider 正常工作，但调用方 limit 截断；结果可用、`complete=False`、不是 provider failure。
- `RetrievalStatus.PARTIAL`：异常中断后只拿到部分结果；保留论文但 `ok=False`，不得作为覆盖证明。
- 新增 `StopReason.TRUNCATED`，避免把正常 limit 截断伪装成 `api_failure`。

**5. 新增回归测试**
- `tests/test_v094_hardening.py` 覆盖：whole-corpus calibration、metadata-only score-context 变化、旧 algorithm calibration 失效、relation failure 传播、Snowball failure≠saturation、Similar incomplete、Scoping result API、TRUNCATED/PARTIAL 区分。

**6. 完整依赖环境下的本机复核（2026-10-08，本机补齐审计容器无法执行的项）**
- 审计容器缺 `streamlit` / `fastmcp` / `ruff`，只跑到 507 项就停止，且**从未执行** `tests/test_audit_hardening.py`。本机全量实跑后**发现并修复 2 个问题**，两处都不在 v0.9.4 的 4 组修复范围内：
  - **测试回归**：`tests/test_audit_hardening.py::test_similar_ledger_does_not_report_every_candidate_as_a_duplicate` 失败（`AttributeError: 'Finder' object has no attribute 'incomplete'`，`search.py:856`）。第 3 组修复让 `find_similar` 开始读取 finder 的完整性状态（`incomplete` / `stop_reason` / `retrieval_results`），但该测试替身只实现了 `find_similar()`。**生产路径不受影响**（真实 `SimilarPaperFinder.__init__` 三个属性都有），已按真实接口补齐替身。
  - **Ruff 不通过**：`tests/test_v094_hardening.py` 有 `F401`（未使用 `ALGORITHM_VERSION`）与 `I001`（import 未排序）。早期报告把 Ruff 列为"未执行"，同时"后续建议"又把它当作冻结前提——未执行的项不能充当通过条件。已修正。
- 本机全量：**644 passed / 0 failed / 0 skipped**；`ruff check . --no-cache` **All checks passed!**；AppTest **19 passed**；MCP **12 工具**，原始 stdio `initialize` 3.52s / `tools/list` / `tools/call` 全部应答，按注册配置启动 3.18s。

**7. 浏览器端用户视角测试发现并修复的 UI 缺陷（2026-10-08）**
- **加载离线演示后出现黄色警告**：点击「加载模拟演示」后页面顶部出现
  `The widget with key "topic_input" was created with a default value but also had its value set via the Session State API.`
  根因：`explore()` 的两个输入框同时传了 `value=`（默认值）与 `key=`，而 `load_demo()` / 会话恢复又把**同一个 key** 写进 session state。Streamlit 在这种情况下保留默认值并打印警告，用户看到的就是这个黄条。
  修法：输入框不再传 `value=`，`key` 作为唯一事实源；新增 `seed_query_inputs(state)`，在**三条**采用状态的路径（离线演示、autosave 恢复、恢复会话文件）统一播种这两个 key。新增 3 项回归测试：`test_query_widgets_do_not_set_a_default_and_a_key_together`、`test_every_state_adoption_path_seeds_the_query_inputs`、`test_a_restored_session_seeds_the_query_inputs`。
- 本机全量更新为 **647 passed**，`ruff check . --no-cache` 仍 **All checks passed!**。


## v0.9.3（2026-10-08）

correctness / architecture hardening。**不新增功能**：本版只收口 v0.9.x 的正确性、接口与 domain 边界。

**Task 1 — 引用身份归一化收敛为唯一实现**
- 新增 `identifiers.reference_witness()` / `paper_reference_witnesses()`：有效 identifier 归一化为 key，`None` / `""` / 空白 / 非字符串 → `""`，**调用方必须丢弃空 key**。
- `similar.py` 此前自带一套归一化：`None` 会抛 `AttributeError`、空串会成为 witness 造成**伪共享参考文献**。现已与 `evidence.py` 共用同一规则。
- 回归：`test_similar_none_reference_does_not_crash`、`test_two_blank_references_do_not_create_fake_overlap`、`test_similar_and_evidence_use_the_same_reference_identity` 等。

**Task 2 — 未标定不再显示「相关率」**
- 移除 UI 中 `relevant_count / count` 的百分比列：该值在固定阈值移除后恒为 0，等于断言"没有任何论文相关"，而运行其实没有做过相关判定。
- 新增 `app.calibrated_relevant_share()`：仅当标定可用**且能证明仍适用于当前 query / corpus / ranker 版本**时才显示「标定相关比例」；否则显示观测计数并说明原因。未重新引入任何 magic threshold。
- 修掉一个自身缺陷：该函数内 `ranking_query` 未 import，`NameError` 被 `except Exception` 吞掉，使任何合法标定都被判无效。异常吞噬已移除。

**Task 3 — 统一 content-addressed score context**
- `rerank_corpus` 成为唯一事实源；新增 `build_score_context()` = `sha256(query)[:12] : corpus_hash : SCORE_VERSION`。
- `systematic_search` 不再自造 `search:{topic}:{years}:{count}`。该 id 在 v0.9.2 实测**会碰撞**：不同语料、相同篇数 ⇒ 相同 context ⇒ 不同尺度被当作可比。
- 新增 `prune_corpus()`：新检索替换候选集后，清理 snowball / similar / PRISMA 中的旧语料残留。
- `stage` 只作溯源，不进入 context id。
- 不变量：query 变 ⇒ context 变；任一论文变 ⇒ context 变；ranker 版本变 ⇒ context 变；**仅顺序变 ⇒ context 不变**。
- 标定改为 property，赋值时记录 `calibration_context_id`；重打分时只有「记录 context 与当前一致」或「`is_valid_for` 严格通过」才保留，**无法自证即丢弃**。

**Task 4 — ProviderAdapter → RetrievalResult**
- 新增 `litsearch/retrieval.py`：`RetrievalStatus`（8 值）、`RequestStats`、`RetrievalResult`（`ok` / `genuine_empty` / `failed` / `to_dict()`）、`STATUS_TO_STOP_REASON`。
- 不变量在 `__post_init__` 结构化强制；`BaseSource` 的内部结果 API 为 `@abstractmethod`，只实现 list 的 provider **无法实例化**。
- 四家 provider（S2 / OpenAlex / arXiv / Crossref）的 search / references / citations 全部内部返回 `RetrievalResult`；公开 `list[Paper]` 为薄包装。
- `SourceManager.search_all_sources_result()` 逐 provider 隔离；某家抛异常 ⇒ 该家 `PROVIDER_ERROR`，不再塌成"0 篇"。
- cache：S2 / arXiv / Crossref 分页缓存改走完整性 envelope，partial 不得按完整成功缓存。
- `search.py` 新增 `_retrieve()`：**只有 genuine empty 允许静默通过**，其余写入 `stop_reason=api_failure` 与 manifest 的 `retrieval_failure` / `provider_results`；未实现结果 API 的替身回退旧接口，既有测试无需改动。

**Task 5 — screening domain 从 MCP server 下沉**
- 新增 `litsearch/screening.py`（原样搬迁）：模块级 import 为空，不 import `fastmcp`、不 import `litsearch.server`。
- `server.py` 改为再导出，只保留 MCP 适配职责；`bundle.py` / `app.py` 改从 domain 取 screening 真值，删除"因 fastmcp 依赖才存在"的懒透传。
- v0.9.2 状态机语义逐字保留（PENDING 与 MAYBE 均 actionable）；MCP 仍 **12 工具**，名称与 schema 不变。
- 依赖方向由 AST 测试（含嵌套 import、相对 import）与全新解释器运行时测试双向证明。

## v0.9.2（2026-10-08）

响应独立代码审计（`LEExtractor_v0.9.1_代码审计与后续开发报告.docx`）的正确性加固版。

**跨批次评分一致性（审计 P0-01 / P0-02）**
- 新增 `LiteratureReviewWorkflow.rerank_corpus()`：语料发生任何增删后，合并 search / snowball / similar / PRISMA 记录并去重，对**完整语料统一重打分**，生成唯一的 `score_context_id`（绑定 query + corpus hash + ranker 版本）。
- `systematic_search` 现在会把 `score_context_id` 写入 `ReviewState`（此前从未赋值）。
- 重打分同步 `PRISMARecord` 内复制的 `relevance_score` / `score_context_id`，并**作废 calibration**（旧尺度上的阈值在新尺度上无意义）。

**Snowball（审计 P0-04 + 本轮额外发现）**
- 修复 PRISMA ledger 的 `unique_records` / `cross_source_duplicates` 语义反置；恒等式曾成立而字段含义相反，因此回归测试改为断言**字段语义**而非仅总数守恒。
- 修复新增计数恒为 0 的缺陷：`checkpoint` 回调先整批注册导致调用方计数读空；计数改为在注册处累加。
- `run_snowballing(..., seeds=[...])` 支持显式种子；**没有任何种子时显式抛错**，不再静默空转后报告 `no_new_results` / `completed=True`。
- 修复引擎种子缺陷：`SnowballResult.seed_ids` 记录首轮种子；首轮用调用方种子，后续轮次用上一轮真正发现的论文（此前第 2 轮会重复查询第 1 轮种子）。
- 中途取消归类为 `CANCELED`，不再误记为 `api_failure`（审计 P1-04）。
- 删除扩展循环中基于固定 `0.15` 的「相关率」（审计 P1-03）。

**筛选状态机（审计 P0-05）**
- `screening_facts` 改为单调不变量：任何前置阶段仍有可处理项（PENDING 或 MAYBE）时，整体阶段一律不完成。
- 修复「两篇仍 pending 却报 `final_included` + `full_text_review_completed=True`」；`MAYBE` 明确定义为 actionable。

**Provider 传输与分页（审计 P1-01 / P1-02）**
- 8 处绕过统一 transport 的裸 `self._session.get/request` 全部改走 `_transport_request`，HTTP budget / retry / cancel / diagnostics 不再漏计。
- OpenAlex `get_references` 的 `per_page` 从 200 降到不超过 100，并按页上限切块。
- 新增 AST 级回归测试：任何 `BaseSource` 子类出现裸 session 调用即失败。

**证据图（审计 P1-06）**
- `identifier_key` 对 `None` / 空值 / 非字符串返回 `""` 而非抛异常（此前 `None` 会**导致图构建崩溃**）。
- 空或非法 reference 不再产生 BC / CC witness（此前两个空引用会被判为"共享参考文献"，把所有带空项的论文连成虚假团）。

**MCP 与 AI 工具接入（本轮独立验证）**
- 修复冷启动：`litsearch/__init__.py` 改为 PEP 562 懒加载。`import litsearch.version` 由 **6.14s → 0.02s**，MCP `initialize` 握手 **6.5s → 3.4s**（宿主启动超时不再有风险）。公开 API 与 `from litsearch import X` 行为不变。
- 新增 `tests/test_mcp_host_compat.py`（11 项）：真实 stdio 握手 / 12 工具 / schema 合法性 / 一次真实工具调用 + 懒加载与冷启动预算。
- 新增验证脚本：`mcp_raw_probe.py`、`mcp_handshake_timing.py`、`mcp_registered_check.py`、`mcp_startup_profile.py`；`mcp_smoke.py` 支持 `--stdio`。
- 已注册进 Codex 配置 `[mcp_servers.leextractor]`，并用该配置中的确切命令复验通过。

**会话（上一轮遗留的集成缺口）**
- `persistence.load_state` 接入 `session_schema` 校验：损坏文件备份并以字段级错误拒绝、未来版本拒绝、已知旧版迁移；拒绝导入不影响邻近 autosave。
- 新增 `tests/test_session_wiring.py`（8 项）走真实加载入口验证。

## v0.9.1（2026-10-08）

正确性收口与发布工程化。本版新增回归测试全部先复现失败再修复。

**单一版本来源**
- 新增 `litsearch/version.py`（`__version__`、`APP_NAME`、`version_tag()`、`package_name()`），`litsearch/__init__.py` 重新导出；`pyproject.toml` 由测试断言与之一致。
- `scripts/package_release.py` 不再硬编码任何 `v0.6.0` 字样：版本用 `ast` 解析源码读取，打包过程不导入 `litsearch`（不拉起 streamlit/网络依赖），并新增测试用「毒化 import」子进程证明这一点。

**PDF 获取边界**
- `resp.content`（整包读入内存）改为真正的 `iter_content` 流式：写入同目录 `.part` 临时文件 → 首尾（magic / `%%EOF`）校验 → `os.replace` 原子替换；任何失败都删除临时文件并关闭响应，已有可用 PDF 不被破坏。
- 体积限制：先查状态码与 `Content-Length`（缺失/非数字/伪造都不再绕过），再按**实际收到的解码字节**累计到 `config.max_pdf_size()`（默认 100 MiB，`LEEXTRACTOR_MAX_PDF_SIZE` 覆盖）；总耗时也有上限。
- SSRF 边界：初始 URL 与**每一跳重定向**都校验协议与解析地址（`allow_redirects=False` 手动跟随），拒绝 localhost / 私网 / link-local / 保留 / 组播地址；DNS 失败即拒绝且不发起连接。
- provenance 每次尝试都记录：`resolver`、`source`、`final_url`、`bytes`、`sha256`、`fetched_at`、`failure_reason`；失败原因是稳定 token（如 `size_limit_exceeded`、`non_public_address`、`not_a_pdf`、`truncated_stream`）。
- 空作者名回退 `Unknown`；Windows 保留字（CON/PRN/AUX/NUL/COM0-9/LPT0-9）、非法字符、文件名与路径长度、同名文件都有处理。
- 未获取到开放获取全文保持「未获取」（`status="not_retrieved"`），下载失败不会自动成为全文排除理由（`prisma.py` 侧的历史行为见 ROADMAP）。

**导出与指标**
- CSV 文本单元格以 `=` `+` `-` `@`（含 Tab/CR）开头时加 `'` 前缀，电子表格不执行；JSON 导出保留原始文本。
- 期刊指标改名并分栏：非官方数值统一为 **Citation Proxy**（带来源、年份、公式、`verified=False`），硬编码所谓的 JCR 数值标 `needs_source_verification`，以 `NEEDS SOURCE VERIFICATION:` 开头；官方指标与内部代理是两列。`sources.get_if_display()` 不再输出 `IF: …`。
- 导出与 `sources.citation_proxy()` 字段对齐（`value/kind/source/year/formula/verified/needs_source_verification/display/note`），并有跨模块一致性测试防止两边对同一个数字给出不同说法。

**会话与恢复**
- 新增 `litsearch/session_schema.py`：会话 schema 校验（当前 v4）、已知旧版迁移（v1→v2→v3→v4）、**未来版本拒绝而非降级**，错误为字段级（`SESSION_SCHEMA_INVALID: <field>: <code>: <message>`），覆盖列表/数值/标识符/枚举/大小/NaN/Inf。
- 导入失败先备份 `<文件名>.corrupt-<UTC>.bak`，原文件不修改，当前会话与自动保存不受影响。
- 多标签共用一个 autosave 的问题：新增 `project_id`、按项目独立保存路径与写入冲突检测（更晚保存 / 不同项目 / 文件不可读都会报冲突，不覆盖）。

**发布与文档**
- `scripts/package_release.py` 改为白名单打包（源码/文档），`.env.local`、会话、日志、备份、缓存数据库及其 sidecar、`__pycache__`、旧 zip 一律不进包；产出包内 `MANIFEST.sha256` 与同名 `.sha256` 校验文件。
- 新增 `constraints.txt`（在本机验证过的精确版本）、`pyproject.toml` 声明 `requires-python = ">=3.10"` 与 3.10–3.13 classifiers。
- 文档各负其责：`README.md`（当前真实能力）、`ARCHITECTURE.md`（模块与不变量）、`ROADMAP.md`（未完成项）；不再用 v0.6/0.7/0.8 的测试数量作为当前验收结论。
- `scripts/launch_gui.py` 增加 `--version`，启动时打印版本来源。

## v0.9.0（2026-10-07）

按《LEExtractor 算法演进、版本融合与未来开发报告 v2.0》清点剩余项后，补上其中最后一个可离线实现的 Intelligence 能力。

**新增：候选研究问题（报告第 22 节 / Step 7）**
- 新模块 `litsearch/questions.py`，三个生成器，全部离线：
  - `combination_gap`——两个概念各有若干文献却几乎不同时出现（组合空白）；
  - `coverage_gap`——某主题占比远低于均值（检索不足的信号）；
  - `unfollowed_result`——近两年、文本差异大、且语料内零被引的结果。
- 每条都带 question / rationale / supporting papers / nearest existing work / coverage gap / risks / suggested next search，并**一律标记 `status: hypothesis`**——仅凭元数据无法判定一个方向是否真的没人做过。
- 少于 6 篇文献不生成任何问题（共现关系无法与偶然区分），返回说明而不是硬凑。
- 接入：MCP 新增 `candidate_research_questions`（共 12 个工具）；界面新增「研究机会」页；证据包新增 `questions.json` 与 `AGENT_HANDOFF.md` 对应章节（风险说明随问题一起导出，避免被单独摘出去当作创新性结论）。

**报告其他项的核对结果**
- 已满足 DoD 的：主题可链到底层论文集合（6）、新颖度给出最近似已有工作而非绝对判定（8）、citation 与推导/语义边严格区分（5）。
- 仍未做：Semantic Retrieval（sentence-transformers，需新增约 2GB 依赖）、Benchmark 冻结基线、Zotero / ZJU / GROBID、Screening 与 PRISMA 完全解耦。

**验证**：离线测试 129 项通过（新增 10 项）、ruff 通过、MCP 12 个工具、新页面 AppTest 渲染无异常。

## v0.8.1（2026-10-07）

**自包含启动脚本**：`启动LEExtractor.bat` 不再依赖 `scripts/`，运行时在临时目录生成一次性助手；先轮询健康接口确认服务可用，再自动打开浏览器（带一次性参数规避缓存）。踩坑已处理：`.bat` 必须 CRLF；UTF-8 中文注释行在 `chcp 65001` 下会被拆断，注释改 ASCII；缩进用变量保存。

**界面问题清点**（依据《已有UI出现的问题》，已修过的未重复）：文献结果页加收敛漏斗与「先看这几篇」；扩展页轮次表加相关率与边际变化并给判断句；saturated 改中文人话；两页都加「下一步」；BC/CC 与引文追溯的关系写明互补非替代；扩展页给请求量估算；侧栏加快速/标准/深入预设；字号放大。

## v0.8.0（2026-10-07）

**研究意图结构化 + 分库检索式**：`litsearch/intent.py` 规则式拆 object/method/task/scenario，带 confidence、unparsed 与 warnings，槽位不填猜测；S2/OpenAlex/arXiv/Crossref 各用自己的语法（arXiv 仅在有 ≥2 组真实概念时才做字段限定，否则退回 `all:()`）；语言不一致时不接管检索。各库实际发出的检索式写入 manifest，证据包新增「Search strategy (per database)」供 PRISMA 报告。

# v0.6.0 本轮优化

以群里 v0.5.0 为基础，统一跨来源身份与发现记录，修正缓存、年份过滤、扩展恢复、BC / CC 预算、下载与 PRISMA 计数，重整中英文 Streamlit 导航和筛选交互；新增有依据的引文图、证据包与 MCP 探索接口。修正 setuptools 包发现，避免安装时误收测试产物目录。

以下为原有 v0.5.0 改动说明，保留作历史记录，其「结果可信」等结论不替代本轮验证，也不构成科研质量 benchmark。

# 改动说明：从最初版本到 v0.5.0

> **基线说明**：本文以 `git` 提交 `8c1a5fb`（v0.3.0，"Rebuild to 4-phase systematic review workflow"）为「最初版本」，  
> 即本次改造开始前的状态。git 历史中还有一个更早的提交 `692ade3`，那是作者自己在 v0.3.0 中重构掉的老版本，  
> 不在本文对比范围内。
>
> 一句话概括：**最初版本能跑起来，但四个核心功能里有三个静默返回空结果**——不报错、不崩溃，  
> 只是结果恒为 0。本次改造修掉了这些，并把「能跑」推进到「结果可信、可追溯、可复现」。

---

## 一、最重要的结论

最初版本最大的问题不是缺少功能，而是**失败时没有声音**。以下四个功能在最初版本里都在正常执行，  
但结果永远是空的：

| 功能                | 最初版本表现       | 根本原因                                      | 现在         |
| ----------------- | ------------ | ----------------------------------------- | ---------- |
| 引文追溯（snowballing） | 恒发现 0 篇      | S2 端点要求 `DOI:10.xxx` 前缀，代码传裸 DOI → 全部 404 | 实测发现 118 篇 |
| OpenAlex 检索       | 整条链路失败、摘要全空  | 请求里带了 `abstract` 字段，该字段已从 API 移除 → 400    | 恢复正常       |
| 自动筛选              | 100% 判为「不相关」 | `add_papers` 没传递 `relevance_score`，分数全是 0 | 正常筛选       |
| 相似文献              | 恒为空          | 依赖 `reference_ids`，但 S2 检索不返回该字段          | 实测返回 10 条  |

这四处都属于「静默失败」：程序不报错，日志也不明显，使用时会以为是自己检索词没写好。  
**本次改造最核心的价值就是把这几处变成可见的**。

---

## 二、致命缺陷修复（第一轮）

### P0-1 · 引文追溯端点拼错

`get_citations` / `get_references` 直接把 `Paper.id`（通常是裸 DOI）拼进  
`/paper/{id}/citations`。Semantic Scholar 要求 `DOI:10.xxxx` 形式，裸 DOI 一律 404。  
现在统一走 `s2_paper_id()` 做规范化。

### P0-2 · OpenAlex 字段失效

`select` 参数里的 `abstract` 已被 OpenAlex 移除（只保留 `abstract_inverted_index`），  
带它整个请求 400。现在改为请求 `abstract_inverted_index`，再用 `_reconstruct_abstract()` 还原成文本。

### P0-3 · 相关性分数丢失

`PRISMATracker.add_papers` 不复制 `paper.relevance_score`，导致 `auto_screen` 时所有分数为 0，  
全部被判为不相关。现在分数正确透传，并建立 id/doi 双索引。

### P0-4 · 相似文献依赖缺失字段

`similar.py` 依赖 `reference_ids` 计算文献耦合，但 S2 检索接口不返回该字段。  
改为按需补拉 references，并加了 API 预算控制。

### P0-5 · MCP 服务启动即崩

`FastMCP(name, description=...)` 在 fastmcp 4.x 下抛 TypeError。改为 `instructions=` 参数。

### P0-6 · 年份硬编码

6 处写死 2026。抽到 `litsearch/config.py` 的 `current_year()` 统一处理。

### P0-7 · 入口点指向不存在的函数

`pyproject.toml` 的 `litsearch-mcp = litsearch.server:main` 无法导入（`server.py` 在包外）。  
已将 `server.py` 移入包内，根目录保留 shim。

---

## 三、合规处理

最初版本的 PDF 下载器第一优先源是 **Sci-Hub**（`downloader.py` 开头就写着  
`PDF downloader with multi-source fallback: Sci-Hub → OA → arXiv`，内置 sci-hub.se / .st / .ru 域名列表）。

这属于在多数司法辖区构成版权侵权的行为，且镜像站极不稳定。**已整层移除**，  
替换为纯合法来源：arXiv → OpenAlex OA → Unpaywall（需配置邮箱）。  
拿不到全文时明确报告「没有」，而不是去影子图书馆抓。

---

## 四、第二轮：三个架构级问题

### 4.1 PRISMA 拆成两阶段 ✅

最初版本把「标题摘要排除」和「全文评估」混为一谈，`full_text_excluded` 恒为 0，  
只能算"PRISMA-shaped"，达不到投稿要求。

现在引入 `ScreeningStage` 枚举（TITLE_ABSTRACT / FULL_TEXT），两阶段各有独立的决策字段，  
补齐了 PRISMA 2020 真正要求的 `reports_sought`、`reports_not_retrieved`、  
`full_text_assessed`、`full_text_excluded` 及排除原因分布，Mermaid 流程图重画为标准形状。

未做过全文决策时自动退回旧行为，不破坏既有流程。

### 4.2 S2 限流治理 ✅

最初版本无 key 时共享配额极紧，首次检索就大量 429。当时代码里是  
`if 429: sleep(5); continue`，在持续限流时会空转。

现在所有 S2 请求走统一的 `_request()`：按配额自适应限速（有 key 0.2s / 无 key 1.2s），  
429 时优先读 `Retry-After`，否则指数退避（3→6→12s）最多 3 次。  
支持 `.env` 配置，GUI 侧边栏可直接粘贴保存 `S2_API_KEY`，被限流时页面弹黄条警告。

### 4.3 状态持久化 ✅

最初版本进度只存在内存里，浏览器刷新即丢，而一次 3 轮 snowball 要几分钟。

新增 `litsearch/persistence.py`，把整个 `ReviewState` 序列化为 JSON 原子写入  
`.sessions/autosave.json`。每完成一个阶段、每做一次筛选决策都自动存盘，  
下次打开自动恢复。snowball 增加 `on_round` 回调，每轮存盘并显示进度。

---

## 五、第三轮：清理、打分、错误分级

### 5.1 legacy 清理 ✅

删除 `path_miner.py`(307 行)、`engine.py`(205 行)、`LiteratureExplorer` 兼容层，  
共约 580 行死代码（删除前逐一确认无引用）。连带移除 4 个已无使用者的依赖：  
`networkx`、`pyvis`、`lxml`、`beautifulsoup4`。

### 5.2 相关性打分升级 ✅

最初版本是单一 TF-IDF 余弦，对短查询区分度差，且阈值 0.15 没有任何标定依据。

改为三路混合：词级 TF-IDF(0.45) + 字符 3-5gram(0.30) + 查询词覆盖率(0.25)，  
归一化后加权融合，失效信号自动降权。新增 `calibrate_threshold()`：  
给已知结论文献打标，自动算出有依据的阈值。GUI 增加「标定筛选阈值」面板。

### 5.3 错误分级 ✅

最初版本 sources 层遍布 `except Exception: logger.warning(...)`，  
把 400（API 契约错误）和网络抖动同等对待——**这正是上述 P0 缺陷长期没被发现的根本原因**。

新增 `litsearch/diagnostics.py`，按「重试有没有用」分类：  
429→限流 / 5xx+超时→可重试 / 400,401,403,422→参数错误（走 `logger.error`）/ 404→预期内 / 解析失败→我方 bug。  
契约类错误现在会在 GUI 顶部弹红色横幅并列出具体请求。

### 5.4 CI 与 lint ✅

最初版本 `pyproject` 里写了 pytest 依赖但**一个测试用例都没有**，也没有任何 CI。  
现在有 34 个离线测试 + `.github/workflows/ci.yml`（Python 3.10/3.13 × Ubuntu/Windows，ruff + pytest）。

---

## 六、文件级改动清单

### 新增文件

```
litsearch/config.py          环境变量、.env 加载、年份与路径助手
litsearch/persistence.py     会话存盘与恢复
litsearch/diagnostics.py     数据源错误分级与运行诊断
litsearch/prisma.py          （v0.3.0 已有，本次重写）
litsearch/snowball.py        （v0.3.0 已有，本次增强）
litsearch/similar.py         （v0.3.0 已有，本次修复）
tests/test_offline.py        34 个离线回归用例
scripts/smoke.py             真实联网端到端冒烟
.github/workflows/ci.yml     CI 配置
ONBOARDING.md                新手上手指南
```

### 删除文件

```
litsearch/path_miner.py      307 行，已被新流程取代
litsearch/engine.py          205 行，无任何引用
```

### 修改文件（行数变化）

```
app.py                        +575 / 大幅重写（两阶段筛选、诊断面板、阈值标定）
litsearch/sources.py          +438 / S2 DOI 修复、OpenAlex 重建、限流、错误分级
litsearch/prisma.py           +293 / 两阶段重写
litsearch/filters.py          +289 / 打分算法重写
litsearch/search.py           +149 / 兼容层删除、年份动态化
litsearch/downloader.py       +150 / 移除 Sci-Hub、PDF 魔数校验
litsearch/__init__.py          +36 / 导出清理
pyproject.toml                 +34 / 依赖精简、ruff 与 pytest 配置
```

---

## 七、数字对比

| 指标                   | 最初版本（v0.3.0） | 现在（v0.5.0）                 |
| -------------------- | ------------ | -------------------------- |
| 测试数量                 | **0**        | **34**（全部通过）               |
| lint                 | 无配置          | ruff 全绿                    |
| CI                   | 无            | 3.10/3.13 × Ubuntu/Windows |
| 引文追溯发现量              | **0**        | 118（1 轮）                   |
| 相似文献                 | **0**        | 10                         |
| `full_text_excluded` | 恒为 **0**     | 5（含原因分布）                   |
| 刷新页面                 | 进度丢失         | 自动恢复                       |
| 无 key 时 429          | 首次检索即退化      | 自动退避重试，仍可完成                |
| PDF 来源               | Sci-Hub 优先   | 仅合法 OA 来源                  |
| 依赖数量                 | 9 个          | 6 个                        |

---

## 八、已知的剩余局限

诚实地说，以下问题**尚未解决**：

1. **相关性打分是词汇法**：抓不到「语义相关但字面不重合」的文献。  
   实测「Vision transformers for crop trait estimation」因只命中一个查询词而排在第 4 位。  
   要突破需换成 embedding 相似度（如 sentence-transformers），会引入新依赖和模型下载。
2. **打分是相对值**：分数只在同一次检索内可比，不同检索之间 0.3 的含义不同。  
   所以才需要阈值标定流程，而不是固定阈值。
3. **无 S2 key 时仍会限流**：47 次请求里 10 次 429。配 key 是唯一根治办法。
4. **PDF 只下开放获取**：付费文献不会去影子图书馆拿，会直接报告「没有」。

---

## 九、如何验证改动

```bash
# 离线测试（不联网，秒级）
python -m pytest tests -q          # → 34 passed

# lint
ruff check .                       # → All checks passed

# 真实联网端到端（约 1-2 分钟）
python scripts/smoke.py "plant phenotyping deep learning" --max-papers 20 --rounds 1

# GUI
streamlit run app.py
```
