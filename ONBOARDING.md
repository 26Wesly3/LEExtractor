# 团队接手说明

当前整合版本 v0.9.7：基于 GitHub master 的 v0.9.5 后端，接入群里 v0.9.6 界面方案。原始桌面附件未修改。

**先读什么**：`README.md`（当前真实能力与启动方式）、`ARCHITECTURE.md`（模块与数据流）、`CHANGES.md`（逐版本改动）、`ROADMAP.md`（未完成与后续计划）。

## 快速接手

1. 双击「启动Web版.bat」启动整合界面，或按 README.dev.md 手动启动本机 FastAPI。Streamlit 的原启动脚本仍可使用。
2. 使用明确标记的离线模拟数据熟悉检索、扩展、证据图和导出。模拟结果不作为论文或比赛的科研证据。
3. 配置自己的数据源密钥，使用实际课题查询，人工核对论文身份、相关性和全文。
4. 在开始新的系统检索前导出旧项目会话。新检索会重建筛选与扩展状态。

依赖以 pyproject.toml 为准。开发验证：`python -m pip install -e ".[dev,web]" -c constraints.txt`，随后运行 `python -m pytest -q` 和 `ruff check .`。接口探测与离线测试分开执行。

## 模块分工

| 模块 | 职责 |
|---|---|
| identifiers / models | 论文身份、跨来源标识和发现记录 |
| sources / cache / diagnostics | 数据源适配、版本化缓存和失败诊断 |
| filters / search | 混合词法排序及共享项目状态 |
| snowball / similar | 引文扩展、BC / CC、预算与恢复 |
| prisma / downloader | 人工筛选、获取状态和开放全文 |
| persistence | 会话与检查点保存 |
| evidence / bundle / export | 样本引文图及证据包 |
| web / litsearch.web | Vue 界面、FastAPI、项目存储与后台任务 |
| app / server | 保留的 Streamlit 与 MCP 入口 |

BC / CC 预算限制的是来源调用次数，单次调用可能分页或回退，不等同于精确 HTTP 请求上限。雪球每轮新增少于 5 篇的停止条件仍是启发式，不能证明领域覆盖完成。旧会话缺失的来源标识和发现依据无法凭空恢复。

新增算法应复用统一身份、发现记录和检索清单，先建立真实课题 benchmark，再说明相对基线的质量与成本。当前没有 embedding 语义检索、创新性自动评分或 Zotero 双向同步；请勿将规划项写成已经交付的功能。

Web 开发、接口与任务边界见 [整合说明](docs/web-integration.md)，本版实测见 [验证记录](VALIDATION_v0.9.7.md)。前端修改后必须构建并同步 Python 包内静态文件。
