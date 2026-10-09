# LEExtractor

> **面向开发与运维的技术 README**（原 `README.md` 的内容，改为本名保留）。
> 项目介绍请见 [`README.md`](README.md)（English）/ [`README.zh-CN.md`](README.zh-CN.md)（简体中文）。

科研文献发现、引文扩展与证据组织工具。当前版本 **v0.9.11**（版本号唯一来源：`litsearch/version.py`；`pyproject.toml` 由测试断言与之一致）。

本文件只描述**当前真实能力**。历史版本改动见 [CHANGES.md](CHANGES.md)，模块与数据流见 [ARCHITECTURE.md](ARCHITECTURE.md)，未完成与后续计划见 [ROADMAP.md](ROADMAP.md)。

## 谷歌学术接入

Google Scholar 自动检索经由 SerpApi 的 `google_scholar` 接口，并非谷歌官方 API。设置页填写「Google Scholar / SerpApi API Key」，或设置 `SERPAPI_API_KEY`；密钥只保存在本机，接口不返回原值。未配置时默认使用其余五库，仍可通过计划中的谷歌学术链接手动检索。配置后默认选中，用户也可取消。请求、分页和缓存计数进入任务预算；账户额度以 SerpApi 为准。检索片段保存在 `search_snippet`，不会冒充 `abstract` 参与摘要编码；会议名称截断时不推断 CCF 等级。

## 本地模型与缓存

翻译采用 `Xenova/opus-mt-zh-en` 的固定修订量化 ONNX，下载文件逐个校验 SHA-256。默认缓存 `%USERPROFILE%/.cache/leextractor`，可通过 `LEEXTRACTOR_MODEL_DIR` 更改。Embedding 采用 FastEmbed 0.8.0 的多语言 MiniLM，固定 ONNX 仓库修订与 mean pooling；默认复用 `%TEMP%/fastembed_cache`（包括已有 TraceRAG 缓存），可通过 `LEEXTRACTOR_EMBEDDING_CACHE` 更改。文本向量写入模型目录的 `embeddings.db`，缓存键包含模型、修订和完整文本。

首次使用需要网络下载；完整缓存后推理可离线执行，在线文献检索仍需要数据库网络访问。Embedding 加载失败会停止并保留原语料，用户可主动选择关键词模式；中文翻译失败可填写英文检索词。工作台不会静默切回宽泛主题。标题＋摘要、长查询均按实际 tokenizer 的 120 词元预算分块，覆盖完整文本，再平均并归一化；来源缺失摘要时只用标题。该路径是候选文献重排，不是全库向量召回。旧 Streamlit 不提供新增的翻译、六库选择和 CCF 控件，请使用 Web 入口。

## 启动

需要 Python 3.10 或以上。发布包已含 Vue / Vuetify 生产构建，运行时不需要 Node.js。双击「启动LEExtractor.bat」（或「启动Web版.bat」），或手动启动：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[web]" -c constraints.txt
.\.venv\Scripts\python.exe scripts\launch_web.py --no-browser
```

访问 http://127.0.0.1:8000 。FastAPI 同时提供页面与 `/api`，API 说明在 `/docs`。项目、任务和导出文件保存在 `.web-data`，按项目分开；多个浏览器标签使用保存版本检测冲突。任务后台执行，进度来自真实阶段与计数；取消会传到数据源，重启后未完成任务显示为中断。相关接口与边界见 [Web 整合说明](docs/web-integration.md)。

开发前端需要 Node.js 20.19+（CI 使用 24）：在 `web` 执行 `npm ci`、`npm run build`，再执行 `python scripts/sync_web_assets.py` 更新 Python 包内静态资源。开发代理由 Vite 转发 `/api` 到本机 8000。

原 Streamlit 入口保留：

发布包 `启动LEExtractor.bat` 默认启动整合 Web 界面。Streamlit 请双击 `启动Streamlit版.bat`，运行在 8501。

已确认服务可用时不重复启动。推荐脚本方式（不自动开浏览器）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]" -c constraints.txt
.\.venv\Scripts\python.exe scripts\launch_gui.py --port 8501 --no-browser
```

`requirements.txt` 会先读 `pyproject.toml` 的范围依赖，再套用 `constraints.txt` 里**在本机验证过的精确版本**。界面地址 http://127.0.0.1:8501 ；`launch_gui.py --help` 可改端口/主机，并可用 `--version` 打印当前版本。

## 使用

1. **研究探索**：填写关键词与研究问题，先做小规模概览或直接检索；「离线演示」加载标注为 **SYNTHETIC DEMO** 的模拟数据，不构成真实科研证据。
2. **文献结果**：按相关度/来源/年份/标题摘要筛选，分页查看论文与发现依据（`explain` 里逐条给出分数构成与发现轨迹）。
3. **引文扩展**：正向引用、参考文献追溯，以及文献耦合 / 共被引；每轮分别统计返回记录、去重新增、保留量与累计新增，并保存检查点，可中断续跑。
4. **证据版图**：当前样本的引文网络、PageRank、社群与有向证据路径；主题/时间/覆盖分析只描述**已检索到的样本**。
5. **研究机会**：由证据推出的候选问题（组合空白、覆盖不足、长期无人跟进的结果），全部标记 `status: hypothesis`，附风险与下一步检索入口。
6. **系统综述（PRISMA）**：仅在勾选后启用；排序信号与筛选决定分离，未标定时只排序不自动排除，全文纳入必须人工判定。
7. **保存与导出**：CSV / JSON / RIS / BibTeX / 会话 JSON / 完整证据包。

中英文界面通过侧栏切换。Web 默认用本地 Marian 翻译研究方向、FastEmbed 多语言 MiniLM 编码标题＋摘要，按语义 60%、词法 15%、方向概念覆盖 25% 混合排序；分数只用于排序。可人工改写英文检索词、选择关键词排序。API 旧调用默认保留词法行为，使用 `translate: true` 与 `ranking_mode: semantic` 启用新路径。

## 数据源与全文获取

- 检索：Semantic Scholar、OpenAlex、arXiv、Crossref、OpenReview、Google Scholar；后两者不提供原生引文扩展，DOI 存在时可走已有 DOI 路由。
- **Crossref**：仅用于 DOI / 元数据解析，不是第四个主检索源。
- 全文：直链 PDF → arXiv → OpenAlex 开放获取 → Unpaywall，均为作者/出版社提供的合法开放版本；不访问 Sci-Hub 等侵权镜像。

全文获取边界（v0.9.1 起，见 `litsearch/downloader.py`）：

- 真正流式下载（`iter_content` → 临时 `.part` → 校验首尾 → 原子替换），不把整份文件读进内存。
- 先看状态码与 `Content-Length`，再按**实际收到的解码字节**累计限制体积，默认上限 100 MiB（`LEEXTRACTOR_MAX_PDF_SIZE` 可改，单位字节）。缺头、伪造头、断流都会得到明确失败原因。
- 初始 URL 与**每一跳重定向**都校验协议与解析到的地址，拒绝 localhost / 私网 / link-local / 保留地址；DNS 失败即拒绝，不发起连接。
- 每次尝试都记录 provenance：`resolver`、`source`、`final_url`、`bytes`、`sha256`、`fetched_at`、`failure_reason`。
- **没下到开放获取全文 ≠ 全文不合格**：状态是「未获取」（`not_retrieved`）并带失败原因，是否排除由人工在全文阶段决定（另见 ROADMAP 中 `prisma.py` 的待办）。
- 已存在的可用 PDF 会被保留；已存在但损坏的文件会重新获取。

可在界面设置 `S2_API_KEY`、`OPENALEX_API_KEY`，或把 `.env.example` 复制为 `.env`；Unpaywall 联系邮箱用 `LEEXTRACTOR_UNPAYWALL_EMAIL`。不要把 `.env` 提交到版本库。

未配置密钥时，限流可能使结果不完整，诊断面板会给出原因。真实接口探测：

```powershell
.\.venv\Scripts\python.exe scripts\provider_probe.py   # 单次请求 ≤12s，结果写入 artifacts/provider_probe.json
```

它只验证连通与解析，不证明召回率或科研结论。

## 会话、导出与安全边界

- **CSV 公式注入**：以 `=` `+` `-` `@`（含 Tab / CR 变体）开头的文本单元格会加 `'` 前缀，电子表格不会执行它；**JSON 导出保留原始文本**。
- **会话 schema**：`litsearch/session_schema.py` 负责版本校验（当前 v4）、已知旧版迁移（v1→v2→v3→v4）与未来版本拒绝，错误是字段级的（`SESSION_SCHEMA_INVALID: search_papers[0].relevance_score: not_finite: …`）；`NaN` / `Infinity` / 越界年份 / 非法枚举都会被拒绝。
- **导入失败保护**：损坏或不合法的会话文件会先备份为 `<文件名>.corrupt-<UTC>.bak`，原文件不被修改，当前会话与自动保存不受影响。
- **多标签隔离**：每个研究项目有稳定 `project_id`、独立保存路径（`<session_dir>/projects/<project_id>/autosave.json`）与写入冲突检测（另一会话保存得更晚 / 属于别的项目 → 不覆盖，写冲突副本）。
- **期刊指标分栏**：官方指标与内部 **Citation Proxy** 是两列、两种标签。硬编码所谓的 JCR 数值一律标为 `needs_source_verification`，以 `NEEDS SOURCE VERIFICATION:` 开头，**不会**被呈现为当前官方影响因子；内部代理永远 `verified=False`。

## 测试

```powershell
$env:PYTHONPATH="."
python -m pip install -e ".[dev,web]" -c constraints.txt
python -m pytest -q -p no:cacheprovider          # 全量（离线）
python -m ruff check litsearch scripts tests app.py
```

测试默认禁止外网（`tests/conftest.py` 的 `no_external_network` 会直接让请求失败），provider 行为一律用假 session 模拟。本版本的实测数字与已知缺口见 `README.md` 与 CI 结果。

## 发布打包

```powershell
python scripts/package_release.py --output deliverables
```

- 版本从 `litsearch/version.py` **用 `ast` 解析**读取，打包过程不导入 `litsearch`（不拉起 streamlit / 网络依赖）。
- 只打包白名单：Python 与 Web 源码、Web 生产构建、必要资源和文档；`.env.local`、项目会话、日志、备份、缓存数据库及其 sidecar、`node_modules`、`__pycache__`、旧 zip 都不会进入包内。
- 产出 `LEExtractor_v<版本>.zip`、包内 `MANIFEST.sha256`（逐文件 SHA-256）与同名 `.sha256`（压缩包自身校验值）。

## MCP

```powershell
.\.venv\Scripts\python.exe server.py     # 安装后入口：litsearch-mcp
```

注册 12 个工具：`literature_review_search`、`snowball_from_seeds`、`find_similar_papers`、`download_paper`、`batch_download`、`plan_search_strategy`、`search_literature`、`explain_paper`、`build_research_landscape`、`candidate_research_questions`、`get_evidence_path`、`export_research_bundle`。

自动筛选默认关闭（`auto_screen=False`）；未标定的阈值只排序不拒绝，全文阶段不提供自动筛选入口。证据包（`AGENT_HANDOFF.md` / `screening.json` / 每个工具的返回值）统一提供 `screening_status`、`threshold_calibrated`、`requires_manual_review`、`full_text_review_completed` 四个字段及其定义。

## 证据包

```text
AGENT_HANDOFF.md      人工/代理阅读入口（含检索式、筛选状态、候选问题、版图小结）
papers.csv            电子表格用（已做公式注入中和）
papers.jsonl          逐篇完整字段 + screening_status
references.ris/.bib   文献管理器导入
discovery_trace.jsonl 每篇的发现轨迹
evidence_graph.json   引文边 + 三种派生关系（source/target/edge_type/score/witness_count/evidence）
landscape.json        主题、时间、覆盖、新颖度（限定当前样本）
questions.json        候选问题（全部 hypothesis）
search_manifest.json  各库实际发出的检索式
screening.json        筛选状态四字段 + 计数 + 定义
session.json          可恢复会话
```

引文边方向为「引用论文 → 被引论文」；没有来源证据的关系不会被补造。图指标只描述当前样本，不能替代质量评估、创新性判断或完整领域分析。
