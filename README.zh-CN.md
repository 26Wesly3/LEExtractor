# LEExtractor

**简体中文** · [English](README.md)

从研究问题，走向可追溯的文献证据。

**v0.9.11** 将 v0.9.6 界面设计与 v0.9.5 算法连接为 Vue 3 / Vuetify + 本机 FastAPI 工作台，保留 Streamlit 和 MCP 入口。

## 开始使用

1. 安装 **Python 3.10+（推荐 3.12，64 位）**，安装时勾选 Add Python to PATH。
2. 从 GitHub 选择 [`feature/web-backend-integration` 分支](https://github.com/26Wesly3/LEExtractor/tree/feature/web-backend-integration)，使用 Code → Download ZIP 下载并完整解压。本版尚未合入 master；默认分支的旧版不包含这些功能。
3. 在解压目录双击 **启动LEExtractor.bat**（或 **启动Web版.bat**）。脚本创建 `.venv`、安装 Python 依赖，检查并自动下载本地翻译与 Embedding 模型，准备好后打开 http://127.0.0.1:8000。首次启动请保持联网和窗口打开，下载耗时取决于网络；后续复用缓存。
4. 界面打开后创建项目，输入研究方向，先查看译文和检索计划，再检索。点击「创建离线演示项目」可先检查界面是否正常。

GitHub 源码与发布包已包含构建后的界面，**运行不需要 Node.js、显卡或推理 API Key**。普通源码包不包含模型权重；另提供「交接包_含模型」，完整解压后启动器自动使用其中的 `models`，无需重新下载模型。首次安装 Python 依赖和真实数据库检索仍需联网。

手动启动（Windows PowerShell，在项目根目录）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[web]" -c constraints.txt
.\.venv\Scripts\python.exe scripts\launch_web.py --port 8000
```

macOS/Linux：创建并激活 Python 虚拟环境，安装相同依赖，运行 `python scripts/launch_web.py --port 8000`。只需界面或手动英文关键词时，可加 `--skip-models`；模型下载失败会明确提示，界面继续启动。首次启动无浏览器弹出时，手动访问上面的地址；端口占用可运行 `启动Web版.bat --port 8001`。关闭启动窗口或按 Ctrl+C 停止服务。

点击「创建离线演示项目」可体验明确标记的合成数据。真实研究请创建普通项目，在设置页或本机环境文件中配置数据源凭据。接口可用性与限流会影响检索覆盖范围。

## 翻译、语义排序与会议覆盖

中文方向由本地 Marian 中译英，检索和排序优先使用完整研究方向；英文检索词可手动编辑。翻译运行文件约 116 MB，Embedding 权重与词元文件合计约 267 MB，后续复用缓存，推理在本机 CPU 运行，无需翻译或推理 API Key。固定版本与 SHA-256 文件清单见 `litsearch/translation_pins.json`、`litsearch/embedding_pins.json`。

默认翻译缓存为用户目录下 `.cache/leextractor`，Embedding 复用系统临时目录下 `fastembed_cache`。可设置 `LEEXTRACTOR_MODEL_DIR` 与 `LEEXTRACTOR_EMBEDDING_CACHE` 指向自己的缓存。含模型交接包自动选择项目内 `models`，已有自定义环境变量优先。具体文件、交付范围、验收与问题处理见 [交接说明](交接说明_v0.9.11.md)。

Embedding 使用 TraceRAG 中相同的多语言 MiniLM：`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`。标题和摘要一起编码，长摘要分块，无摘要记录只编码标题。Web 支持 Semantic Scholar、OpenAlex、arXiv、Crossref、OpenReview、Google Scholar 六个来源，并按 CCF 2026 会议／期刊级别筛选。OpenReview 包含公开未录用投稿，Google Scholar 通过 SerpApi 接入，需要在设置中配置 API Key，配置后默认启用；搜索片段与完整摘要分别标注；级别不证明单篇论文已录用或属于长文。来源限流、访问校验和网络问题会逐库显示，不能承诺完整收录所有 CCF 论文。

## 当前工作流程

1. 输入课题、研究方向和年份范围，点击「翻译并查看检索计划」，核对或编辑英文译文，再发起检索。Web 默认使用标题＋摘要的本地语义排序。
2. 筛选项目中的真实记录，查看文献详情、发现轨迹和相关性评分上下文。
3. 选择种子进行引文扩展或 BC/CC 相似检索，后台任务显示真实阶段、计数、请求预算及取消状态。
4. 查看当前语料的研究版图、关系路径和附带依据的候选研究问题。
5. 人工筛选、添加标定标签，获取可访问的开放全文。
6. 导出 CSV、RIS、BibTeX、会话 JSON、PRISMA 流程数据或证据包；导入会话时创建独立项目。

项目、任务检查点和导出文件保存在本机 `.web-data`。多个浏览器标签通过 revision 检测过期写入。服务默认只监听本机，没有面向公网多人部署的认证和账号隔离。

检索主题及文献标识会发送给文献数据源，全文请求发送给开放获取来源。凭据查询只返回是否已配置。本版本不调用大模型服务。停止服务后移除本机数据目录，即可移除 Web 项目与导出记录。

## 能力边界

相关性分数用于本地语义与关键词混合排序，不能替代筛选判断或研究质量评价。真实引文边与派生相似关系分别计数。失败、取消、预算耗尽与正常数量截断分别记录；全文纳入需要人工确认已经阅读。

本版对数据库返回的候选文献做语义重排；无法找回上游未返回的论文。自动全文综述、创新性评分和 Zotero 同步属于后续计划。聚类和候选问题描述现有语料，不等同于已验证的科研结论。流程计数采用 record/report，尚未归并到独立研究层面。

下图是早期产品概念示意，并非当前真实结果截图：

![产品概念](docs/assets/leextractor-hero-4k.png)

## 开发与验证

- [安装与当前能力](README.dev.md)
- [Web 整合说明与接口边界](docs/web-integration.md)
- [本版验证记录](VALIDATION_v0.9.11.md)
- [架构](ARCHITECTURE.md)、[更新记录](CHANGES.md)、[后续计划](ROADMAP.md)、[团队接手](ONBOARDING.md)

前端开发：进入 `web` 后运行 `npm ci`、`npm run dev`，另行在 8000 端口启动 FastAPI；Vite 将 `/api` 代理到后端。发布前运行 `npm run build`，再从仓库根目录运行 `python scripts/sync_web_assets.py`。

反馈问题请附版本、复现步骤、预期和实际结果，分享日志及会话前移除密钥。

## 贡献者与许可

[GlueGPT](https://github.com/GlueGPT) · [jvligyh](https://github.com/jvligyh)

文献内容与第三方数据遵循各自使用条件。仓库代码许可证尚未确定。
