# v0.9.7 整合验证记录

验证日期：2026-10-09。后端基线：master b4ed8be（v0.9.5）；界面基线：群内 v0.9.6 UI 包。

本报告仅描述本版实际运行的验证，不沿用历史版本测试数量。环境：Windows、独立 Python 3.12.4 虚拟环境、Node.js 24.15.0。依赖按 `constraints.txt` 安装，前端按 package-lock 安装。

## 自动验证

| 检查 | 实测结果 |
| --- | --- |
| `python -m pytest -q` | **736 passed**，57.41 秒；3 个非失败警告 |
| `ruff check .` | 通过 |
| `python -m pip check` | 无依赖冲突 |
| `cd web && npm run test` | **7 passed**，含 4 项真实 Vue/Vuetify DOM 与 API 联动 |
| `cd web && npm run build` | 生产构建通过，无构建警告 |
| MCP stdio | 完成握手、列出 12 个工具，成功执行离线查询规划 |
| 真实 HTTP 服务联动 | 本机服务成功启动，生产入口、资源及 SPA 深链返回正常 |
| 项目操作 | 演示项目、服务器筛选、详情、查询计划、后台扩展、检查点、版图/问题、人工筛选全部通过 |
| 导出/恢复 | CSV、RIS、BibTeX、PRISMA、会话及证据包均生成并下载；会话导入为独立项目并恢复停止原因 |
| 运行预算 | 真实检索 max=3 时只发生 3 次网络尝试，限流及预算耗尽显示 partial；回归固定取消/超时/预算不虚假重试 |
| 发布白名单 | 构建资源、字体许可证、源码、启动器及文档齐全；密钥、运行数据和 node_modules 排除；实际 Markdown 本地链接可在包内解析 |
| 脱离源码目录 | 源码 ZIP 与 wheel 分别解压到独立目录；实际导入来自各自解压路径，入口、资源、深链及演示项目均通过；ZIP 逐文件 SHA-256 完整 |

新增测试集中覆盖会话运行字段、种子恢复、失效标定、项目隔离、revision 冲突、排队取消、损坏文件恢复、凭据脱敏、预算及发布资源。前端 DOM 测试覆盖 demo→结果→详情、筛选空结果、带 revision 的检索→任务完成→结果刷新，以及全部页面 DTO 和导出下载链接。

3 个警告来自 Starlette 的 TestClient/httpx 兼容层提示，以及两个刻意重复文本样例的聚类收敛提示。均未导致失败。

## 真实来源探测

探测与离线测试分开，每个来源最多一次传输、单次超时 12 秒。Semantic Scholar 返回 2 条、arXiv 返回 2 条、Crossref 按 DOI 返回 1 条；OpenAlex 返回 429，响应指出共享 IP 免费预算不足。另一次完整检索的 3 次预算均被 Semantic Scholar 的 429 消耗，最终保持 `partial / budget_exhausted`，没有声称获得完整结果。

这些只是当时的接口可达性结果，不能证明全面覆盖、持续可用或真实研究质量。比赛环境仍需配置团队的数据源凭据与人工标签。

## 视觉验收与部署限制

真实浏览器视觉验收**未完成**。电脑控制工具无法可靠识别当前浏览器网址而自动停止，本轮没有继续浏览器输入。DOM 联动与静态构建检查不能替代窄屏、动效、字体及实际浏览器布局验收；应在比赛机器上逐页检查。

本版只面向本机单用户。取消在页面/重试/论文之间生效，不能中途杀断已开始的 HTTP 请求。聚类、候选问题及低产停止仍有启发式限制；语义向量检索、自动全文综述及 Zotero 同步未实现。

## 复现

```sh
python -m pip install -e ".[dev,web]" -c constraints.txt
python -m pytest -q
ruff check .
python scripts/mcp_stdio_check.py
cd web
npm ci
npm run test
npm run build
```

回到仓库根目录运行 `python scripts/sync_web_assets.py`、`python scripts/package_release.py`。GitHub Actions 设置 Python 3.10/3.13 × Ubuntu/Windows，以及 Node 24 前端测试/构建；远端状态以对应提交的 Actions 结果为准。
