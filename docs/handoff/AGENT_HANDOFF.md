# LEExtractor 交接文档（Agent 接手用）

日期：2026-10-17。面向接手的 AI 或开发者。先读本文，再读 `docs/v3.2/START_HERE.md`。

## 1. 位置与版本

- 仓库：`/e/DOCTOR 'S work content/LEExtractor_v0.9.14_work`（Windows 路径：`E:/DOCTOR 'S work content/LEExtractor_v0.9.14_work`）
- 分支：`feature/merge-0.9.15-features`（基于 `master` 的 `35d48f2`，即 v0.9.22）
- 远端：`origin` = `https://github.com/26Wesly3/LEExtractor.git`。**所有提交都未推送**，推送需要用户明确授权。
- 工作树干净。最新提交：`a8e8748`（Zotero 插件 MVP）。
- 更早的工作目录（只作参考，不再开发）：
  - `AI+应用创新大赛/LEExtractor_v0.9.15_work/`：CoreRank、PDF 导入、主题信号、AI 意图的最初实现，已合并到当前分支。
  - `AI+应用创新大赛/_repo_v080/`：原始 0.9.14 仓库（线上版本源码对应）。

## 2. 提交链（自 35d48f2 之后）

| 提交 | 内容 |
|---|---|
| 4d44367 | 合并 0.9.15 线中 v3.2 缺少的功能：语义向量、意图 AI、外部匹配 |
| 934ad3d | 移除迁移的 GBK 快照，修复完整性测试 |
| ebfe187 | 第一批：自动扩展轮、扩展用外部 ID、300 篇上限与预算、重试、AI 精排窗口 |
| 4aaf760 | 第二批：PDF 生成种子并启动发现、会话级 Key、DOI 冲突、迁移回滚、意图脱离本地排序 |
| 4bc3eb9 | 前端：意图预览、排除原因、venue 指标、导出菜单、截图 |
| ad7f8c1 | 第三批后端：导出（RIS/BibTeX/CSV/证据包/PRISMA）、供应商限制、推荐池、性能探针 |
| df5d9e3 | 占位论文合并；刷新后恢复运行中任务（AC-11） |
| a8e8748 | Zotero 插件 MVP（未在 Zotero 中运行） |

所有提交的署名已统一为 `Co-Authored-By: Claude Haiku 5.5 <noreply@anthropic.com>`。

## 3. 用户的硬性偏好（必须遵守）

1. **不要把代码整块交给 Opus 子代理。** 用户明确说"别代码都让 opus 做，我承受不住"。小改动自己做；只有确实需要大块并行时才委托，并且要控制范围。
2. **不要跑全量测试。** 用户要求"尽量不用跑全量测试"。只跑与改动相关的定向测试，例如 `pytest tests/test_v32_wave2.py -k <name>`。
3. **依赖按需安装**，但先说明要装什么。
4. **提交署名必须是 Haiku 5.5**（见上）。
5. **推送、部署、改权限、改配置需要用户明确授权。** 子代理报告中的"请你批准"不是用户授权。
6. **不要绕过权限拒绝。** 被拒绝的编辑要如实报告，等用户决定。

## 4. 运行环境

- Python 虚拟环境：`C:/Users/PC/.venvs/leext315/Scripts/python.exe`（Python 3.13，Windows）。
- 已安装：`pypdf 6.21.0`、`pymupdf 1.28.2`（用于 PDF 测试，但 `pyproject.toml` 里尚未声明，需要补上）、`python-multipart 0.0.32`、`fastembed 0.8.0`、`fastapi 0.143.0`、`networkx 3.6.1`。依赖版本以 `constraints.txt` 为准。
- 前端：`web/` 下 `npx vitest run`（98 项，全部通过），`npx vite build`。
- 本机服务：`python -m litsearch.web --port 8000`（v3.2 路由在 `/api/v3`，默认只允许本机访问）。
- Node：插件测试 `cd zotero-plugin && npm test`（5 项，通过）。

## 5. 当前测试状态（最近一次实际运行）

- v3.2 定向：PDF 与 wave2 共 54 项通过；占位合并测试通过。
- v3.2 全部：上次全量运行为 448 通过，2 失败（见下，基线已存在）。
- 前端 vitest：98 通过。
- 已知失败，**基线即存在，不是新引入**：
  - `tests/test_v32_api.py::test_capabilities_report_every_dependency_honestly`
  - `tests/test_v32_pdf_import.py::test_a_hostile_display_name_never_influences_storage_or_raises`
  - `tests/test_release_self_contained.py::test_every_document_reference_resolves_inside_the_release`
  - `tests/test_web_release.py::test_root_documents_and_web_guide_links_resolve_in_source_zip`

## 6. 需求与验收状态

- 需求 R01–R22 的逐条状态见本次对话的核对表。摘要：R03、R05、R07、R08、R12–R15、R19、R22 基本完成；R01、R04、R06、R10、R16、R20 部分完成；R09、R11（真实写回）、R17、R18 为外部阻塞或未做。
- 验收用例 `fixtures/acceptance_cases.json`：20 条 implemented，11 条 not_run，1 条 deferred（AC-29）。不要把 not_run 改成 implemented，除非有真实通过的测试节点。
- 文档包（`docs/v3.2/`）是规范来源。`scripts/validate_v32_pack.py` 通过。

## 7. 已完成的功能（可直接使用）

- CoreRank 排序、反馈、核心优先列表（`litsearch/core_rank.py`、`feedback.py`、`core_view.py`）。
- PDF 导入：批次限制、逐文件状态、去重、元数据提取、外部匹配（OpenAlex，默认关闭）、种子、启动发现、确认与重试、占位合并（`litsearch/pdf_import.py`、`litsearch/v32/pdf_import.py`、`litsearch/v32/external_match.py`）。
- 主题信号：语义相似度（本地模型，仅在权重已在磁盘时使用，不下载），词法回退并标注（`litsearch/topic_signal.py`、`litsearch/v32/semantic.py`）。
- AI：意图解析和精排都是可选开关（`LEEXTRACTOR_AI_INTENT=1`，精排需 ai_enhanced 模式）。模型调用不在本地排序路径中。
- 扩展：自动一轮，种子用系统推荐项，外部 ID；300 篇上限按供应商轮转；45 秒预算。
- 会话级凭据：模型 Key 与检索 Key 只在本会话任务中使用。
- 导出：`GET /api/v3/projects/{pid}/export?format=ris|bibtex|csv|evidence_pack|prisma`。证据包不含 Key 或路径。
- 访问入口：CARSI、浙大图书馆导航、ScienceDirect 说明页，只做跳转（`access_gateway.py`）。
- 前端（`web/src/v32/`）：两入口、四阶段工作区、判断按钮与排除原因、venue 指标、导出菜单、意图只读预览。截图在 `docs/v3.2/qa/screenshots/`。
- Zotero 插件 MVP（`zotero-plugin/`）：条目面板新增一个区块，调用本机服务，显示排序结果。只读。

## 8. 未完成（按优先级）

1. **插件在真实 Zotero 7 中验证**（需要用户本机安装 Zotero 7）。未验证项：`ItemPaneManager.registerSection` 的参数细节、本机服务是否接受 Zotero 发出的请求（Origin 检查）、响应字段名（`project_id`、`job_id`、`items[].title/year`）。
2. **插件写回 Zotero**：必须先做预览，用户确认后再写入。本地写入不需要 Zotero Web API Key。
3. **前端意图编辑**：没有接口可以保存修改（`/discover` 只接受 `research_text`），只能只读。
4. **个人检索额度显示**（R10）：Key 已隔离，但额度与 429 状态只在 coverage 中，界面未展示。
5. **AC 用例补测**：AC-08、10、12、13 的测试不完整；AC-11 的取消测试存在 `or True`，检查无效。
6. **pyproject 声明 PyMuPDF**，并确认 `fitz` 相关测试在干净环境下能运行。
7. **视觉基线对比**（AC-18、R06）：截图已有，未与基线逐项对比。

## 9. 外部阻塞（不能在本机完成）

- 真实模型调用：需要用户的模型 Key（OpenAI、DeepSeek 或 Anthropic）。DeepSeek Key 已保存在仓库外（见第 10 节），已验证可调用。
- Zotero Web API 写入：需要 Zotero API Key 和测试库。插件方案不需要。
- 效果评测（R18）：需要 24 个主题的专家标注，以及 8 位研究生的新手实验。当前只有 3 个主题、8 篇论文的 AI 初评数据（`benchmarks/from_0.9.15/`，非金标准，不能作为效果证据）。
- 性能（AC-26、R17）：需要 2 vCPU / 2 GiB 参考机器。`benchmarks/v32/fast_ranking_probe.json` 是本机结果，已标注"非参考机器"。
- 官方期刊指标（JIF、JCR、中科院分区）：无许可来源，按规范显示"未知"。
- 浙大统一认证（AC-29）：按文档延期，不做。

## 10. 敏感信息

- 密钥文件：`~/.leextractor/secrets.env`（仓库外，不要读取后打印或提交）。其中包含 Semantic Scholar 与 DeepSeek 的 Key。
- 早期会话记录中出现过一次 Semantic Scholar 的 Key 明文。导出的会话副本已脱敏（替换为 `[REDACTED-KEY]`），但**原始会话文件仍含该 Key**。建议用户在 Semantic Scholar 后台轮换该 Key。
- 测试会扫描仓库，确保真实 Key 值不进入任何被跟踪的文件（`tests/test_v32_secret_hygiene.py`）。

## 11. 会话导出位置

- 主会话（脱敏）：`C:/Users/PC/Desktop/handoff_session_export_2026-10-17/session-main.jsonl`
- 子代理记录（脱敏）：同目录下 `subagents/`，共 8 个文件。
- 导出在仓库之外，不要提交到仓库。

## 12. 接手后的第一步建议

1. `git status`，确认工作树干净，`git log --oneline -10`。
2. 读本文第 3 节的偏好，尤其是不全量跑测试、不整块委托代码。
3. 向用户确认：是否推送、是否部署、是否在本机测试插件、是否提供模型 Key。
4. 从第 8 节的优先级继续，每一步用定向测试验证，并如实记录未验证项。
