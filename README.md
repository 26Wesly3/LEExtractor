# LEExtractor × OpenAlex — 静态 UI 原型（阶段一）

> **仓库分支说明**：本分支只包含可运行的静态原型（`prototype/` 与自检脚本 `tools/`）。设计文档 `docs/`、截图 `shots/`、以及 `prototype/vendor/`（v0.3 旧版参考文件，不参与运行）**未包含在本分支**。

日期：2026-10-08。这是**阶段一交付物**：一整套可双击打开的静态多页原型，用来把风格和信息架构定死。阶段二（Vue 3 + Vuetify + FastAPI 真实工程）待你确认风格后开工，见 [需求确认_v1.md](docs/需求确认_v1.md) §11。

---

## 立即体验

有三种方式，任选一种：

**1. 直接双击**（最省事）

打开 `prototype/index.html`。字体、求是鹰字符数据与脚本都在包内，不需要 npm、不需要 API Key、不发起任何网络请求。

**2. 本地 HTTP 服务**（推荐，避免个别浏览器对 `file://` 的限制）

```bash
cd prototype
python start_local.py
# 或： py start_local.py
# 或： python -m http.server 8765 --bind 127.0.0.1
```

然后打开 <http://127.0.0.1:8000>（`start_local.py` 默认 8000）。

**3. 逐页直达**

`prototype/` 下 11 个 HTML 就是 11 个页面，可以单独打开、单独截图送审。

---

## 页面清单

| 文件 | 阶段二路由 | 页面 | 这一页在回答什么 |
|---|---|---|---|
| `index.html` | `/` | 研究探索首页 | 提出研究问题，生成分库检索式 |
| `search.html` | `/projects/:id/search` | 文献搜索 | 快速检索 / 高级条件 / 检索计划 |
| `results.html` | `/projects/:id/papers` | 文献结果 | 分面统计、列表与表格、批量选择 |
| `paper.html` | `/projects/:id/papers/:paperKey` | 论文详情 | 元数据、发现路径、关系、筛选决定 |
| `expand.html` | `/projects/:id/expand` | 引文扩展 | 种子、轮次、预算、真实停止原因 |
| `landscape.html` | `/projects/:id/landscape` | 证据版图 | 引文网络、主题、时间、覆盖 |
| `questions.html` | `/projects/:id/questions` | 研究机会 | 候选研究问题与支撑文献 |
| `review.html` | `/projects/:id/review` | 筛选与 PRISMA | 两阶段人工筛选、账本、流程图 |
| `export.html` | `/projects/:id/export` | 保存与导出 | 导出范围、六种格式、证据包 |
| `settings.html` | `/settings` | 设置与诊断 | 数据源密钥、偏好、真实 HTTP 计数 |
| `projects.html` | `/projects` | 项目列表 | 会话恢复、损坏文件、冲突 |

**详情抽屉**不是独立文件，而是任意结果页/版图/扩展页里点标题就能唤起的共享组件（右侧半屏，Esc 或点遮罩关闭），并可从抽屉「展开为完整详情页」跳到 `paper.html`。

---

## 首页的快速展示面

首页（`index.html`）在封面与检索区之下带一整套比赛展示面，分区节奏对齐 <https://openalex.org/> 首页 hero 以下：

| 分区 | 回答的问题 | 内容来源 |
|---|---|---|
| 当前项目 | 这个项目现在是什么状态 | 示例语料派生 |
| **参赛单位与支持平台** | 谁做的、涉及哪些平台 | 用户提供 |
| **不是又一个搜索框** | 这是什么、凭什么叫解决 | 文案自撰；分层图与编号注解为项目结构 |
| **工作台的规模** | 做到什么程度 | 仓库可核验事实（4 数据源 / 12 MCP 工具 / 6 导出格式 / 288 断言） |
| 六项能力 | 具体能做什么 | 源码功能 |
| 检索来源 | 数据从哪来、各自限制 | 四个 provider 的真实能力与降级 |
| 界面守则 | 哪些规矩不许破 | 四条业务契约不变量 |
| 五个入口 | 从哪进去 | 五个页面 |
| 常见问题 | 最容易被误解的地方 | 六条 Q&A |
| 团队与联系 | 谁做的、去哪找 | 用户提供 |

**深链**：`index.html?view=showcase` 会跳过封面直接进入检索区，并把展示面滚到眼前 —— 评审可以直接打开这一条看展示面。

### 支持平台标识的来源与合规

素材取自用户提供的 `LEExtractor_Official_Logos_20261009.zip`，逐件核对记录保留在 [docs/logos/](docs/logos/)：`CHECK_REPORT.md`、`LOGO_SOURCES.md`、`logo-manifest.json`。落地规则：

- **参赛单位**：浙江大学。用官方**毛体校名标准字** + 官方校徽 PNG 备用。
- **支持平台**：Dify、DeepSeek、Qoder、学在浙大。Dify / DeepSeek / Qoder 用官方 SVG；学在浙大只有 64×64 favicon，因此「图标 + 文字」，不伪造字标。

> **毛体校名是怎么来的**：浙江大学官网没有提供独立的校名 SVG/PNG 资源（`xiaoming.ai` 无法在无 Illustrator 的环境里转换）。官方「校名」页 <https://www.zju.edu.cn/576/> 给出的 `浙江大学校标校名规范.zip` 与一张规范示意图里，校名锁定区在 `y 75..193`，下面 `y 313..357` 是 CMYK/RGB 色值说明。因此取 `y 68..200` 的锁定区，把白底转为透明，填充色用**图里标注的官方 RGB 0,63,136**。形状、比例、字距未做任何改动，只做了「裁出锁定区 + 去白底」这两步必要处理。产出 `assets/logos/zju-wordmark.png`（351×133，透明底）。
- **不改色、不重绘、不拼接**：Dify 品牌规范明确「不得修改颜色」，所以 logo 一律保留原色，**不做灰度处理**（这一点和 OpenAlex 首页的灰度 logo 带不同，是刻意为之）。
- 带下的说明行写明：各标识版权归其所有者，展示不构成任何一方的背书声明。

> 原始套件（`originals/`，含 5 MB 校标规范包与 5 MB `xiaobiao.ai`）留在用户的 ZIP 里没有复制进仓库；仓库只放 `web/` 下实际使用的 3 个文件与 1 个由 favicon 转出的 PNG，加上三份来源核对文档。

---

## 两条硬性约定

### 1. 风格纪律（违反一条就会"看起来不像"）

提炼自 OpenAlex `docs/style`，完整依据见 [openalex-style-notes.md](docs/openalex-style-notes.md)：

1. **强调色只有纯黑** `--btn: #000000`。链接蓝 `--link: #1f6feb` 只用于链接与 info 语义，**绝不能当按钮底色**；纯黑按钮的悬停是更深（`#1a1a1a`）而不是更浅。
2. **卡片永不投影**：一律 1px 发丝描边 + `--r-md`。悬停抬升靠底色 + 描边变深，选中靠 0 模糊 ring（`--btn-ring`），不用模糊投影。
3. **不用左侧强调条**（侧栏选中项、告警、指标块都不用）。
4. **chrome 字重只有 400 / 500 两档**；600 只留给首页 v0.3 的编辑风排版。
5. **表格无外框、无斑马纹、无竖线**，只用行分隔线。
6. 语义色只用于成功 / 警告 / 失败 / 人工决定，不做装饰。

首页是例外：它沿用你上传的 Demo v0.3 视觉（Anton 字标、可旋转 X、雾层、求是鹰、页脚「惟学无际，际于天地」），因此 `body.theme-home` 会把主按钮色覆盖回墨色 `#1b1c20`。

### 2. 业务契约（界面不许违反）

完整依据见 [business-contract-notes.md](docs/business-contract-notes.md) 与 [known-gaps-v095.md](docs/known-gaps-v095.md)。原型里已经落地的部分：

- 各库命中数**绝不相加**当文献总数；漏斗四步逐层收敛。
- 相关度是**单次检索内的词法排序信号**，不写成纳入概率；两个 `score_context_id` 之间显式标注不可比。
- **未获取全文不是排除理由**；零被引且来源缺失时显示「未知」而不是断言零。
- 八个 `stop_reason` 全部可达，并按「是否允许声称检索完成」分组着色。
- 计数单位是 **record / report**；研究层面合并未实现时 `studies_included` 保持 0，并写明这是正确状态。
- `auto_screen=false` 默认；单一类别标定明确标注**不能**形成二分类阈值。
- 三类关系（`bibliographic_coupling` / `co_citation` / `text_similarity`）不混合加权，历史别名 `semantic` 不出现、不重复计数。
- 候选问题的证据强度用契约的 **`evidence_strength`，只有 `moderate` / `weak` 两级**（`questions.py` L201/253/347），不是编造的数字分数；排序按契约的 `(kind, moderate 优先)`。
- 新颖度是**逐篇**结构 `{rows:[{paper_id,title,year,novelty,most_similar}], note}`；样本内没有更早文献可比较时 `novelty` 为 **`null`**（不是 0，也不是讨喜的 1.0），该行排最后。原型用词元 Jaccard 代理 TF-IDF 余弦，并在页面上写明这是代理。

---

## 目录结构

```text
LEExtractor_OpenAlex_Web/
  README.md                      本文件
  docs/
    需求确认_v1.md                开工依据：逐条确认的决策与验收标准
    layout-spec.md                **布局规范**（已核对 OpenAlex 源码与 Vuetify 3）
    known-gaps-v095.md            已核实的 5 条契约缺口（阶段二要修）
    business-contract-notes.md    v0.9.5 业务契约提炼（字段、枚举、计数口径、15 条陷阱）
    openalex-style-notes.md       OpenAlex 风格提炼（token、字阶、SERP 几何、组件、15 条冲突）
  prototype/                      阶段一交付物
    index.html … projects.html    11 个页面
    start_local.py                可选本地静态服务器
    assets/
      tokens.css                  统一设计变量（两套风格收口）
      app.css                     工作台版式 + 组件库 + 动效层
      home.css                    首页 v0.3 视觉
      data.js                     示例语料 + 全部派生数据（唯一真源）
      app.js                      共享外壳：导航面板、详情抽屉、双语、动效工具
      i18n-pages.js               页面级双语文案
      i18n-{landscape,questions,settings,projects}.js  这四个页面的文案
      eagle-motion.js / eagle-grid.js / anton.ttf / Anton-OFL.txt  自 Demo v0.3 复用
  tools/
    check-data.mjs                数据层自洽性自检（57 项）
    check-pages.mjs               页面语法 / id 引用 / 资源引用自检（68 项）
    check-render.mjs              抽屉与 openDrawer 全量渲染冒烟（90 项）
    check-layout.mjs              布局不变量：CSS 变量完整性、断点、列顺序、可访问名称（14 项）
    check-live.mjs                无头 Edge 逐页真机渲染验收（59 项）
    shots.mjs                     逐页整页截图，供肉眼复核版式
  shots/                          截图输出目录（可删，可由 shots.mjs 重新生成）
```

---

## 自检

五个检查器都不需要浏览器，改完代码先跑一遍；第六个用无头 Edge 做真机渲染验收：

```bash
node tools/check-data.mjs      # 57 项：分面、账本、漏斗、图、停止原因、候选问题、新颖度、会话
node tools/check-pages.mjs     # 68 项：内联脚本语法、id 引用、资源引用、跨页一致性
node tools/check-render.mjs    # 90 项：30 篇抽屉渲染 + openDrawer 完整路径 + 双语 key 泄漏 + 导出完整性
node tools/check-layout.mjs    # 14 项：CSS 变量完整性、断点阈值、两栏列顺序、容器一致性、可访问名称
node tools/check-live.mjs      # 59 项：无头 Edge 逐页加载，检查渲染后的 DOM（需先起本地服务）
node tools/shots.mjs           # 12 张：给每页拍整页图，供肉眼复核版式
```

当前状态：**288 条断言全部通过**。

`check-data.mjs` 不是走过场：首版就靠它抓出「声明纳入 11 篇、逐条决定实际 15 篇」和一条悬空引用。现在所有筛选与 PRISMA 计数都**从逐条决定派生**，不允许手写。

`check-render.mjs` 抓出过两类浏览器不会告诉你的问题：语言切换事件根本没派发（`setLang` 只对 `[data-trigger]` 派发且不冒泡，页面上没有该元素时事件发不出去），以及会话级 `screening_status` 会被当成逐条决定渲染成原始 key `sch.not_started`。两条现在都有断言守着。

`check-layout.mjs` 守 `docs/layout-spec.md` 的不变量。它存在的直接原因是一个真实事故：把 token `--facet-w` 改名成 `--rail-w` 时漏改了 4 处引用，于是 `grid-template-columns: var(--facet-w) …` **在计算期整条失效**，栅格静默退化成单列 —— 页面照常打开、控制台无报错、DOM 断言全过，只是两栏塌成一列、页面底部对不齐。现在「所有 `var(--x)` 都必须有定义（或有回退值）」是一条断言。

`check-live.mjs` 是因为静态语法检查查不出「脚本中途抛异常导致半页空白」。`shots.mjs` 是因为**以上全部检查都查不出「两栏高低不齐」「文字压在说明上」「颜色太多」** —— 那类问题只有看图能发现，所以留了一个一条命令出图的工具。

> `shots.mjs` 的一个坑：本机 Edge 的 `--headless=new` 会**静默不写** `--screenshot` 文件、也不报错。脚本因此把 `old` / `new` 两种模式都试一遍，并且**以文件是否真的落盘为准**，不看退出码。截图输出在 `shots/`。

---

## 动效与可访问性

动效原则：**用来解释"状态变了"，不做装饰**。

| 位置 | 动效 | 时长 |
|---|---|---|
| 首页封面 → 检索区 | 280ms 淡出 + 620ms 景深推入（Demo v0.3 原参数） | 900ms |
| 首页字标 X | 随鼠标方向柔和转动（指数衰减插值） | 连续 |
| 首页求是鹰 | 字符层 8fps 低频流动 | 连续 |
| 工作台页入场 | 整页轻微上浮 | 320ms |
| 结果行 / 表格行 | 渐次落入（每条 +18ms，封顶 12/20 条） | 260ms |
| 数字（漏斗、计数、预估） | 滚动到目标值，三次缓出 | 460–620ms |
| 条形（分面、相关度、比例） | 从左 `scaleX` 展开 | 520ms |
| 详情抽屉 | 右滑入 + 遮罩淡入 | 240ms |
| 抽屉七组内容 | 依次落入（每组 +40ms） | 280ms |
| 分面展开 | 内容淡入（`<details>` 无法过渡高度，故不做高度动画） | 200ms |
| 运行中任务 | 状态徽标低频呼吸 | 1.8s 循环 |

**全部可一次性关闭**：`body.reduced`（`tokens.css` 里的全局 `animation:none !important`）与 `prefers-reduced-motion: reduce` 都会关掉 CSS 动效；JS 驱动的数字滚动走 `LE.fx`，内部先查 `LE.motion.reduced()`。

可访问性：全部页面键盘可达、焦点可见；Esc 关抽屉与弹窗；抽屉是 `role="dialog" aria-modal="true"`；结果标题是 `<button>` 而不是 `<div onclick>`；表格横向滚动在自身容器内。

---

## 能力边界（很重要）

- 这是**原型**，不是可运行的研究工具。没有真实检索、没有后端、不读你的 `.env`、不发任何网络请求。
- 页面里 30 篇文献、账本、引文图、候选问题**全部是合成的示例数据**，每一处都标注了「示例数据 / SYNTHETIC DEMO」，不得作为科研证据。
- 阶段二才生效的动作（真正跑检索、保存会话、下载全文、写筛选决定）在界面上要么明确禁用并说明原因，要么在点击后用一句话说清"阶段二才生效"，**不会假装成功**。
- 工作区 `LEExtractor\` 内的任何文件都未被修改；业务契约以该目录的 v0.9.5 源码为准。

## 许可

`prototype/assets/anton.ttf` 来自 Google Fonts，许可见同目录 `Anton-OFL.txt`。求是鹰字符图源自浙江大学求是鹰轮廓，是设计参考，不代表浙江大学官方产品。本原型借鉴 OpenAlex 的视觉与信息架构思路，未分发其源码。
