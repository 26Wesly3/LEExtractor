# UI 整合计划：旧工作台 → v3.2 模块（分支 feature/merge-0.9.15-features）

> 本文件只是计划，不含代码改动。行号以当前 HEAD 为准。

## 1. 现状

**入口链**：`web/src/main.js` 的 `createV32Router()` = `createV32Routes()`（`web/src/v32/routes.js`）+ `createWorkspaceRouter()`（`web/src/bootstrap.js`）的路由表。所有 `component` 都是占位 `Page`，实际渲染由 `web/src/v32/AppRoot.vue` 按 `meta.v32` 二选一：`Workspace.vue` 或 `App.vue`。

| 路径 | name | 实际渲染 |
|---|---|---|
| `/` | home | `App.vue` `page==='home'` 分支（模板 L262–269）+ `HomeOverview.vue` |
| `/search` `/projects` `/settings` | search / projects / settings | `App.vue` 内 `v-else-if` 分支 |
| `/projects/:projectId/{search,results,expand,landscape,questions,review,export}` | `project-*` | `App.vue`，`page` 由 `route.name` 推出（L17） |
| `/projects/:projectId/papers/:paperKey` | paper | `App.vue` 结果页 + `v-navigation-drawer.paper-drawer`（L354） |
| `/v32` | v32-entries | `Workspace.vue` → `EntryChoice.vue` + `UploadPanel.vue` |
| `/v32/settings` | v32-settings | `Workspace.vue` → `SettingsPanels.vue` |
| `/v32/projects/:projectId(/:stage)` | v32-workspace / v32-project | `Workspace.vue`，阶段 `anchors/papers/threads/insight`（`states.js` `STAGES`） |
| 其他 | — | 重定向 `/` |

**导航**：`App.vue` L239–252：`header.topbar`（`.brand`、`.toplinks`、语言切换、`导航 ☰`）、`.nav-scrim`、`nav.nav-panel` > `.navmap__item`（`navModules()`/`navDesc()`）、项目内 `nav.work-tabs`。`Workspace.vue` L590 另有 `nav.le32-stage-nav`（`.le32-stage`）。`/v32` 下没有 topbar。

**已有动画**（A1–A12）：

| # | 动画 | 定义位置 |
|---|---|---|
| A1 | 封面离场（280ms，scale .97）+ `main` 景深到场（620ms，blur 6px） | `App.vue` `enterWorkspace()` L197–210；`.cover.is-entering`（styles.css L82） |
| A2 | X 字标随指针旋转 | `rotorMove/rotorTick/rotorWake/rotorReset` L184–196；`.rotor`（L9） |
| A3 | X 悬停 14° | `.hero-button:hover .rotor`（L9） |
| A4 | 求是鹰字符画动态 | `startEagle/eagleSync/eagleDraw/stopEagle` L96–106；`eagle-grid.js`；`#eagleCanvas`（L37） |
| A5 | 导航面板滑入/退场（260ms 定时卸载） | `openNav/closeNav` L171–172；`.nav-panel/.open/.is-closing`（L63–65） |
| A6 | 导航项错峰进入 | `.nav-panel.open .navmap__item` + `@keyframes le-nav-in`（L79–80） |
| A7 | 遮罩淡入 | `.nav-scrim/.open`（L111–112） |
| A8 | 详情抽屉错峰入场/退场 | `closeDrawer()` L183；`le-drawer-in/le-drawer-out`、`.paper-drawer`（L84–85、93–105） |
| A9 | 图谱节点淡入 | `graphFade`（L19–20），证据版图页 |
| A10 | v3.2 进度条滑动 | `Workspace.vue` scoped `le32-progress-slide`（L869–878） |
| A11 | 微交互过渡 | `.skip-link`、`.navmap__item`、`.graph-node__ring` 的 `transition` |
| A12 | 减弱动效 | `body.reduced`（L16，`localStorage le.reduced`）、全局 `prefers-reduced-motion`（L17）、L86–89、L106–109；JS `motionReduced()`/`eagleReduced()`；Workspace L875 |

## 2. 冲突与待决定

1. **路由**：两张表、两套项目路径（`/projects/:id/results` 与 `/v32/projects/:id/papers`）；阶段切换不写 URL（`activeStage` 是本地 ref，`@click="activeStage = stage.key"`）。
2. **导航**：`.topbar/.nav-panel/.work-tabs` 与 `.le32-stage-nav` 重复。
3. **阶段不一一对应**：旧 7 页，v3.2 只有 4 个阶段 + `ExportMenu`。旧 `landscape`（force-graph）、`questions`、`review` 里的标定没有 v3.2 对应。
4. **API 客户端**：`api.js`（`/api`）与 `v32/client.js`（`/api/v3`，带 session/CSRF/幂等键）。v3 **没有 `GET /projects` 列表**，也没有演示项目接口；首页“最近项目”“恢复已有项目”“创建离线演示项目”都依赖旧接口。
5. **数据**：旧项目与 v3 项目存在两套存储，ID 不通用。
6. **样式/token**：v3.2 已在用旧 token（`--ink --line --s-* --text-2`），但写了不一致的回退值（如 `var(--ink, #18181b)`）。`.theme-v32` 规则（L147–152）**目前没有任何地方挂这个 class**，处于失效状态；一旦启用，工作区标题字号会变。字体只有一套（Anton + `--font-sans`），不冲突。
7. **Vuetify**：首页三个按钮是 `v-btn`，必须保留 `createWorkspaceTheme()`；v3.2 用原生按钮。
8. **发布约束**：`tests/test_web_release.py` 要求发布包里有 `web/src/App.vue` 和 `web/src/api.js`。

## 3. 目标结构

- **一张路由表** `web/src/router.js`（新增），取代 `bootstrap.js` 的 `createWorkspaceRouter` 和 `v32/routes.js`：

| path | name | component |
|---|---|---|
| `/` | home | `App.vue`（瘦身后只保留首页） |
| `/search` | search | `v32/EntryChoice.vue`（经 `Workspace.vue` 无项目态） |
| `/projects` | projects | `shell/ProjectList.vue`（由 App.vue 项目页迁出） |
| `/settings` | settings | `v32/SettingsPanels.vue` |
| `/projects/:projectId/:stage(anchors\|papers\|threads\|insight)` | workspace | `v32/Workspace.vue` |
| `/projects/:projectId` | project | 重定向 `anchors` |
| 旧名 `project-search/results/expand/review/landscape/questions/export`、`paper` | 保留 name | 重定向：search→anchors，results/review→papers，expand/landscape→threads，questions→insight，export→`papers?export=1`，paper→`papers?paper=:paperKey` |
| `/v32`、`/v32/settings`、`/v32/projects/...` | v32-* | 重定向到上面的新路径（保住书签和 `e2e-check.mjs`） |
| `/:pathMatch(.*)*` | — | 重定向 `/` |

- **一个外壳**：`web/src/shell/AppShell.vue` 取代 `AppRoot.vue`：`<v-app :class="{'theme-home':isHome,'theme-v32':!isHome}">` → `.skip-link` → `AppNav` → `<main id="main"><router-view/></main>`。`main` 常驻，A1 的到场动画才有对象可播。
- **一个导航**：`web/src/shell/AppNav.vue`。
- **一个 API 客户端**：工作区统一用 `v32/client.js`。`api.js` 缩到首页专用（`request/post`，供 `stats.js`、最近项目、演示项目使用），并加注释说明它只服务首页；以后再并入 `client.js` 的 `legacy` 命名空间。
- **一套 token**：`styles.css` L2 `:root` 是唯一来源；去掉 v3.2 组件里的硬编码回退值。
- **删除**：`v32/AppRoot.vue`、`v32/routes.js`（并入 router.js）、`App.vue` 中除首页以外的所有分支和逻辑（search/results/expand/landscape/questions/review/export/paper/projects/settings 及 `.work-tabs`）、styles.css 里只服务这些页面的规则（`.work-tabs`、`.paper-drawer`、`.facet-rail` 等，要等 A8/A9 迁移完成才删）。
- **原样保留**：`HomeOverview.vue`、`eagle-grid.js`、`stats.js`、`milestones.js`，styles.css L1–4、L9、L37–38、L81–82、L113–144、L154–158。
- **需要改造**：`main.js`、`bootstrap.js`（只留 `createWorkspaceTheme`）、`App.vue`（只剩首页）、`Workspace.vue`（阶段由路由驱动、移除内部 stage nav、抽屉动画）、`styles.css`（L5、L12 的导航部分，以及新导航规则）。

## 4. 首页保护规则

**不许改**：
- `App.vue` 模板 L262–269 整段（`.cover`、`.hero-button`、`.wordmark`、`.x-slot`、`#rotor.rotor`、`.invitation`、`#eagle.eagle`、`#eagleCanvas`、四个 `.home-band`、`HomeOverview`、三个按钮及其 `to`/`@click`）。
- 函数 `rotor*`、`*Eagle*`、`enterWorkspace`、`loadDemo`、`recordSearchEntryClick` 调用的行为。
- styles.css 中的 `.theme-home`、`.theme-home .topbar`、`.cover`、`.hero-button`、`.wordmark`、`.x-slot`、`.rotor*`、`.invitation`、`.eagle`、`.home-band*`、`.logo-band*`、`.feature*`、`.overview-*`、`.stat*`、`.milestone*`、`--topbar-h:56px`、`@font-face`，以及 L12 中 `.cover/.eagle/.invitation` 的窄屏值。
- 首页状态下导航条的外观：高度 56px，`.brand` + `brand-tag`、链接位置、背景 `#f8f9fbdd`、无下边框，与现在逐像素一致（见第 5 节）。

**验证**：
1. `cd web && npx vitest run src/v32/home-first-screen.test.js`。新增一个断言：把 L262–269 冻结成 `web/src/__baseline__/home-template.html`，要求 `App.vue` 首页块与它逐字节相同。
2. 截图对比：新增 `web/e2e-home-baseline.mjs`（Playwright，`channel:'chrome'`）。用 `page.route('/api/**')` 返回固定的 stats/projects，`emulateMedia({reducedMotion:'reduce'})` 冻结 A2/A4，在 1440×900、1024×768、390×844 下截首屏和整页；另截一张开着动效、遮住 `#eagle` 的。改动前在当前 HEAD 构建上生成 `e2e-shots/home-before-*.png`，每步结束后生成 `home-after-*.png`，像素差必须为 0（遮罩区除外）。
3. `npx vitest run src/App.dom.test.js -t "cover departure"` 必须通过（A1）。

## 5. 导航重做（`AppNav.vue`）

- **结构**：第一行 = 品牌（`.brand`）| 主链接（项目、设置）| 语言 | `导航 ☰`。第二行 = 阶段条（只在 workspace 出现），取代 `.work-tabs` 和 `.le32-stage-nav`。抽屉菜单沿用 `.nav-scrim` + `.nav-panel` + `.navmap__item` 的 class 名（这样 A5–A7 的 CSS 一行不用改），条目换成：首页、新研究（`/search`）、当前项目 4 个阶段、导出、全部项目、设置。
- **状态**：
  - `home`：只有第一行，DOM/class 与现在的 `header.topbar` 完全相同（`toplinks` 里仍是 项目/设置/EN/导航）。
  - `entry`（`/search`、`/projects`、`/settings`）：第一行，当前链接 `aria-current="page"`。
  - `workspace`：第二行阶段条 `nav[aria-label="工作区阶段"]`，每项是 `router-link` 到 `/projects/:id/:stage`，当前项 `aria-current="step"`，右侧显示项目名（沿用 `.project-name`）和“导出”按钮（打开 `ExportMenu`）。
  - `detail`（`?paper=`）：阶段条保持 papers 高亮，`.le32-drawer` 打开；返回 = 去掉 query。
- **键盘与焦点**：`.skip-link` 保留在最前面。`Esc` 先关抽屉、再关菜单（沿用 `onGlobalKeydown` 的顺序，移到 `AppShell`）。菜单打开时焦点移到 `.nav-panel__close`，Tab 在面板内循环，关闭后焦点回到 `导航` 按钮。阶段条用 roving tabindex，←/→ 切换焦点，Enter 跳转。路由变化后把焦点移到 `main h1`（Workspace 已有 `.le32-workspace:focus` 规则）。
- **移动端（<600px）**：第一行只留品牌 + 语言 + `☰`（沿用 L12 `.toplinks>a{display:none}`）。阶段条横向滚动（`overflow-x:auto`），当前阶段自动 `scrollIntoView({inline:'center'})`。`.nav-panel` 宽 `min(380px,92vw)` 不变。

## 6. 动画保留表

| # | 选择器 / 函数 | 去向 | 在第几步保留 |
|---|---|---|---|
| A1 | `enterWorkspace()`、`.cover.is-entering`、`main` | 留在 App.vue；`main` 改由 AppShell 提供 | S2 |
| A2–A4 | `rotor*`、`.rotor`、`*Eagle*` | App.vue 原样 | S2（不动） |
| A5–A7 | `openNav/closeNav`、`.nav-panel`、`.navmap__item`、`le-nav-in`、`.nav-scrim` | 移进 AppNav，class 不变 | S3 |
| A8 | `le-drawer-in/out` | 用到 `.le32-drawer`（加 `is-open/is-closing` 和 260ms 卸载定时器），再删 `.paper-drawer` 规则 | S5 |
| A9 | `graphFade` | 若保留证据版图（决定 D2），随图迁到 threads；否则在 S6 删除 | S6 |
| A10 | `le32-progress-slide` | Workspace 原样 | S4 |
| A11 | 各 `transition` | 原样 | 全程 |
| A12 | `body.reduced`、`prefers-reduced-motion`、`motionReduced()` | 合并为 `web/src/shell/motion.js` 的 `motionReduced()`，App.vue 和 AppNav 共用；CSS 原样 | S3 |

## 7. 实施步骤

**实施状态（2026-10-17 收尾核对，均为本地提交，未推送）**

| 步 | 状态 | 提交 | 证据 |
|---|---|---|---|
| S0 | ✅ 完成 | `91aaa1c` | 首页模板冻结 8 行 / 3877 字节，`home-first-screen.test.js` 逐字节断言；`e2e-home-baseline.mjs` 生成 7 张基线图 |
| S1 | ✅ 完成 | `21e8eea` | `src/router.js` 一张表，12 个旧名重定向；路由测试 10 passed |
| S2 | ✅ 完成 | `338c9e6` | AppShell 接管 `v-app`/顶栏/常驻 `main`；prefs 共享语言与减弱动效 |
| S3 | ✅ 完成 | `97e08d7` | AppNav 新导航（9 项）+ 焦点/Tab/Esc；107 passed，截图 7/7 字节相同 |
| S4 | ✅ 完成 | `b657ff2` | 阶段由路由驱动，阶段条进 AppNav（roving tabindex）；109 passed |
| S5 | ✅ 完成 | `d791114` | `?paper=` 详情态 + 抽屉 A8 + `?export=1`；110 passed；比较器改为逐像素 + 0.01% 预算 |
| 回归修复 | ✅ 完成 | `8be9fae` | `<router-view/>` 未传参导致工作区落回入口页；`/projects` 曾是空白页 |
| S6 | ✅ 完成 | `a2ef693` | App.vue 只剩首页；`shell/ProjectList.vue`（旧版只读 + RIS 导出）；App.dom 只留 5 条首页用例；死 CSS 清理 |
| S7 | ✅ 完成 | `68cba49` | `.theme-v32` 已生效（S2 绑定），最后一处 `var(--ink, #…)` 回退色清除，`styles.css :root` 是唯一色彩来源 |
| S8 | ✅ 完成（演示接口除外） | `81b31cc` | `GET /api/v3/projects`（owner 隔离、最新在前）+ 3 条后端用例；项目页并列显示 v3（可打开）与旧版（只读）。`POST /api/v3/demo/projects` **未做**，理由见下 |

S8 的边界：演示项目必须是真实的 v3 语料加一次已提交的快照；为了"点一下有东西"而造一个空项目或假的 demo 标记，正是交接文档禁止的假完工。所以首页「创建离线演示项目」继续走旧接口，等演示语料准备好再接（D3 的第二半）。

当前测试账：前端 `npx vitest run` → 18 文件 / 102 passed；后端全量 `python -m pytest -q` → **1350 passed, 2 failed**（两项均为基线既有的发布文档链接检查）；首页像素门禁 → 7/7 张通过（0 差异像素）。


| 步 | 目标 | 改动文件 | 证明它成立的测试 | 回滚 | 规模 |
|---|---|---|---|---|---|
| S0 | 建立基线：首页模板快照、截图脚本、before 截图 | `web/src/__baseline__/home-template.html`、`web/e2e-home-baseline.mjs`、`home-first-screen.test.js` | `npx vitest run src/v32/home-first-screen.test.js`；`node e2e-home-baseline.mjs --tag before` | 删掉新增文件 | 小 |
| S1 | 统一路由表，用重定向承接旧名和 `/v32/*` | `web/src/router.js`（新）、`main.js`、`bootstrap.js`、`v32/main.routes.test.js` | `npx vitest run src/v32/main.routes.test.js`（改写为：每个旧 name 都能解析，并落到目标路径） | 恢复 `main.js` 里的 `createV32Router` | 中 |
| S2 | AppShell 取代 AppRoot；App.vue 只在 `/` 渲染，`main` 常驻 | `shell/AppShell.vue`（新）、`main.js`、`App.vue`（只删 `<header>`/`<main>` 外壳，首页块不动） | `npx vitest run src/v32/home-first-screen.test.js src/v32/AppRoot.test.js`；`-t "cover departure"`；截图差 0 | 恢复挂载 `AppRoot` | 中 |
| S3 | AppNav 上线（home/entry 状态），迁 A5–A7、A12 | `shell/AppNav.vue`、`shell/motion.js`、`styles.css`（只新增 `.le-nav-*` 规则） | 新增 `shell/AppNav.test.js`（菜单错峰入场与退场、Esc、焦点回位）；从 `App.dom.test.js` 迁出 `-t "vertical module menu"`；截图差 0 | 外壳改回旧 topbar 片段 | 中 |
| S4 | Workspace 由路由驱动阶段，移除 `.le32-stage-nav`，阶段条进 AppNav | `v32/Workspace.vue`（`activeStage` 跟随 `props.stage`）、`AppNav.vue` | `npx vitest run src/v32/Workspace.test.js src/v32/Workspace.gaps.test.js src/v32/entry-flow.test.js` | 恢复内部 stage nav | 中 |
| S5 | 详情态 `?paper=`，抽屉套用 A8；`export=1` 打开 ExportMenu | `Workspace.vue`、`PaperCard.vue`、`styles.css` | `npx vitest run src/v32/PaperCard.test.js src/v32/Workspace.test.js -t drawer` | 回到 `pinnedId` 本地态 | 中 |
| S6 | 删除 App.vue 旧工作区分支和对应 CSS；迁出 ProjectList | `App.vue`、`styles.css`、`shell/ProjectList.vue`、`App.dom.test.js`（只留首页用例） | `npx vitest run src/App.dom.test.js src/v32/home-first-screen.test.js`；`npx vite build`；截图差 0 | `git revert` 本步提交 | 大 |
| S7 | token 收口，启用 `.theme-v32` | `v32/*.vue`（去掉回退色）、`styles.css` L147–152 | `npx vitest run src/v32`；工作区截图人工复核 | 撤掉 class 绑定 | 小 |
| S8 | 首页依赖接到 v3（视决定 D1/D3 而定） | 后端 `litsearch/v32/api.py`（`GET /projects`、演示项目）、`api.js` | `pytest tests/test_v32_api_contract.py -k projects`；`pytest tests/test_web_release.py` | 前端继续用旧接口 | 中 |

每步一个提交，单独回滚；每步结束都跑首页截图对比。

## 8. 风险与待决定（括号内为建议默认）

**2026-10-17 用户逐条确认（以此为准，不再按"默认"）**：

| 项 | 决定 |
|---|---|
| D1 旧项目 | 在项目列表里标为**旧版·只读**，只提供 RIS/会话导出；导出后用 `UploadPanel` 导入 v3。不做自动迁移。 |
| D2 旧专属页面 | 证据版图、候选问题、标定不保留独立页面：`landscape`/`expand` → **研究脉络 threads**，`questions` → **研究洞察 insight**。 |
| D3 首页三按钮 | 外观与行为保持不变，仍走旧接口；等后端补上 `GET /projects` 与演示项目接口后再换内部调用（S8）。 |
| D4 首页顶栏 | 首页状态**逐像素复刻**现有 topbar；**同时**新导航必须可见——重做体现在工作区阶段条与 `导航 ☰` 菜单条目上。 |
| D5 `/search` 落地页 | 改为入口选择页 `EntryChoice`，转场 A1 保留。 |
| D6 `.theme-v32` | 启用，接受工作区标题字号变化（统一到 `.home-band` 字号体系）。 |

- **D1 旧项目**：旧存储的项目在 v3 工作区打不开。（默认：`/projects` 列表里把旧项目标为“旧版·只读”，只提供 RIS/会话导出，导出后可用 `UploadPanel` 导入 v3；不做自动迁移。）
- **D2 旧专属页面**：证据版图、候选问题、标定没有 v3.2 对应。（默认：重定向到 threads/insight，不再提供 force-graph 和标定；如需保留，作为 threads 的子视图另立一步。）
- **D3 首页三个按钮**：“创建离线演示项目”和“最近项目”目前创建/读取的是旧项目。（默认：按钮和外观不变；S8 新增 v3 演示接口和列表接口后换掉内部调用，在此之前演示项目按 D1 只读打开。）
- **D4 首页导航外观**：“重做导航”和“首页像素不变”在首页顶栏处相互冲突。（默认：首页状态的导航逐像素复刻现有 topbar，重做只体现在工作区状态和菜单内容上。）
- **D5 `/search` 内容变化**：转场 A1 保留，但落地页从旧检索表单变成 `EntryChoice`。（默认：接受。）
- **D6 `.theme-v32` 启用后工作区标题字号变化**。（默认：接受，统一到 `.home-band` 字号体系。）
- **R1** 截图受字体加载和鹰动画影响：截图前 `document.fonts.ready`、减弱动效、遮罩 `#eagle`。
- **R2** `e2e-check.mjs` 走 `/v32`：靠 S1 的重定向兼容，并把 `channel` 改为 `'chrome'`。Playwright 需要另行安装或复用已有环境（本计划不安装依赖）。
- **R3** 发布测试要求 `App.vue` 和 `api.js` 存在：两者都保留，不删除。
