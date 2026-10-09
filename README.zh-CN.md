# LEExtractor

**简体中文** · [English](README.md)

从研究问题，走向可追溯的文献证据。

**v0.9.7** 将 v0.9.6 界面设计与 v0.9.5 算法连接为 Vue 3 / Vuetify + 本机 FastAPI 工作台，保留 Streamlit 和 MCP 入口。

## 开始使用

Windows 双击 **启动Web版.bat**。脚本创建 Python 环境、安装依赖并打开 http://127.0.0.1:8000。发布包已包含构建后的界面，只有修改前端时才需要 Node.js。

手动启动（Python 3.10+）：

```sh
python -m pip install -e ".[web]" -c constraints.txt
python -m litsearch.web --port 8000
```

点击「创建离线演示项目」可体验明确标记的合成数据。真实研究请创建普通项目，在设置页或本机环境文件中配置数据源凭据。接口可用性与限流会影响检索覆盖范围。

## 当前工作流程

1. 输入课题、研究方向和年份范围，查看后端生成的查询计划，再发起检索。
2. 筛选项目中的真实记录，查看文献详情、发现轨迹和相关性评分上下文。
3. 选择种子进行引文扩展或 BC/CC 相似检索，后台任务显示真实阶段、计数、请求预算及取消状态。
4. 查看当前语料的研究版图、关系路径和附带依据的候选研究问题。
5. 人工筛选、添加标定标签，获取可访问的开放全文。
6. 导出 CSV、RIS、BibTeX、会话 JSON、PRISMA 流程数据或证据包；导入会话时创建独立项目。

项目、任务检查点和导出文件保存在本机 `.web-data`。多个浏览器标签通过 revision 检测过期写入。服务默认只监听本机，没有面向公网多人部署的认证和账号隔离。

检索主题及文献标识会发送给文献数据源，全文请求发送给开放获取来源。凭据查询只返回是否已配置。本版本不调用大模型服务。停止服务后移除本机数据目录，即可移除 Web 项目与导出记录。

## 能力边界

相关性分数用于词法排序，不能替代筛选判断或研究质量评价。真实引文边与派生相似关系分别计数。失败、取消、预算耗尽与正常数量截断分别记录；全文纳入需要人工确认已经阅读。

语义向量检索、自动全文综述、创新性评分和 Zotero 同步属于后续计划。聚类和候选问题描述现有语料，不等同于已验证的科研结论。流程计数采用 record/report，尚未归并到独立研究层面。

下图是早期产品概念示意，并非当前真实结果截图：

![产品概念](docs/assets/leextractor-hero-4k.png)

## 开发与验证

- [安装与当前能力](README.dev.md)
- [Web 整合说明与接口边界](docs/web-integration.md)
- [本版验证记录](VALIDATION_v0.9.7.md)
- [架构](ARCHITECTURE.md)、[更新记录](CHANGES.md)、[后续计划](ROADMAP.md)、[团队接手](ONBOARDING.md)

前端开发：进入 `web` 后运行 `npm ci`、`npm run dev`，另行在 8000 端口启动 FastAPI；Vite 将 `/api` 代理到后端。发布前运行 `npm run build`，再从仓库根目录运行 `python scripts/sync_web_assets.py`。

反馈问题请附版本、复现步骤、预期和实际结果，分享日志及会话前移除密钥。

## 贡献者与许可

[GlueGPT](https://github.com/GlueGPT) · [jvligyh](https://github.com/jvligyh)

文献内容与第三方数据遵循各自使用条件。仓库代码许可证尚未确定。
