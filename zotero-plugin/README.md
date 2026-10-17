# LEExtractor Zotero 插件（v0.2.0）

在 Zotero 的**条目面板**里增加一个 LEExtractor 区块：显示本机服务是否在跑，
并把你选中的条目题名与 DOI 复制到剪贴板、打开检索页。原有面板、菜单和设置一律不改。

## 这一版做了什么（以及为什么变小了）

上一版（0.1.0）想在 Zotero 里直接建项目、跑发现、拉排序结果。改版的原因很具体：
Zotero 进程里的 `fetch` **不共享浏览器的会话 cookie**，而 `/api/v3` 的写接口要
会话 + CSRF，所以在插件里调 `POST /projects` / `POST /discover` 会 403。
0.2.0 只保留确定能跑的两件事：

| 能力 | 实现 | 状态 |
|---|---|---|
| 显示本机服务状态 | 读取公开的 `GET /api/v3/capabilities`（无需会话） | 已实现 |
| 复制题名与 DOI | `Zotero.Utilities.Internal.copyTextToClipboard` | 已实现 |
| 打开检索页 | `Zotero.launchURL('http://127.0.0.1:8000/search')` | 已实现 |
| 在 Zotero 里直接建项目/看排序 | 需要鉴权通道（见下） | 未做 |

要"直接把 Zotero 条目写进项目"，请用**网页侧的「从本机 Zotero 导入」侧栏**——
那边有完整的会话与 CSRF，读的是同一个本地只读接口 `127.0.0.1:23119`。

## 安装（开发）

1. 启动本机服务：仓库根目录 `python -m litsearch.web --port 8000`。
2. `cd zotero-plugin && npm run build` → 生成 `dist/LEExtractor.xpi`。
3. Zotero 7/8/9：工具 → 插件 → 齿轮 → **从文件安装插件…**，选择该 xpi。
4. 选中一条文献，在右侧条目面板里找到 **LEExtractor** 区块。

## 隐私与边界

- 只读：不新建、不修改 Zotero 条目或分类；不读附件和笔记。
- 只发元数据：题名与 DOI（复制时由你决定粘贴到哪）。
- 不读 `zotero.sqlite`：插件用 Zotero 自己的 API；网页侧用本地只读 HTTP 接口。

## 已知限制

- 鉴权通道尚未打通，所以插件内不能直接写项目（这是后续工作，不是匹配失败）。
- 本地化文案只有 `locale/en-US` 与 `locale/zh-CN`，区块标题显示 LEExtractor。
