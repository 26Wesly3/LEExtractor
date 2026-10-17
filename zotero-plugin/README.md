# LEExtractor Zotero 插件（MVP）

在 Zotero 7 的条目面板里增加一个"LEExtractor"区块，点击"找核心文献"，把所选条目的题名和 DOI 发给本机的 LEExtractor 服务，显示排序结果。原有面板、菜单和设置不改动。

## 状态（请先读这一节）

| 项目 | 状态 |
|---|---|
| 纯逻辑（元数据裁剪、幂等键、CSRF 调用、错误处理） | 已通过 Node 单元测试（5 项） |
| 调用的接口（/session、/projects、/discover、/jobs、/papers） | 已对照 `litsearch/v32/api.py` 核实存在 |
| Zotero 7 中的 `ItemPaneManager.registerSection`、`startup`/`shutdown` 用法 | **未核实**：官方文档站点本次无法访问，需对照 Zotero 7 开发文档和 zotero-plugin-template |
| 插件在真实 Zotero 中运行 | **未验证**：本环境没有 Zotero，需要你在本机安装后测试 |
| 本机服务接受 Zotero 发出的请求（origin 检查） | **未验证**：服务有 Host/Origin 检查，Zotero 的请求来源可能被拒绝 |
| 响应字段名（project_id、job_id、items[].title/year） | **未逐字段核对**：按接口代码推断，需用真实响应确认 |
| 写回 Zotero（导入候选） | **未做**：下一步，先做预览再确认 |

## 安装（开发）

1. 启动本机服务（默认 `127.0.0.1:8000`），例如在仓库根目录运行 `python -m litsearch.web --port 8000`。
2. `npm run build` 生成 `dist/LEExtractor.xpi`。
3. Zotero 7：工具 → 附加组件 → 齿轮 → 从文件安装，选择该 xpi。
4. 选中一条文献，在条目面板找到"LEExtractor"区块。

## 隐私

只发送题名、DOI、年份和摘要（最多 1500 字符）。不读取附件、笔记和本地文件路径，不上传 PDF。

## 测试

`npm test`（Node 内置测试，无依赖）。
