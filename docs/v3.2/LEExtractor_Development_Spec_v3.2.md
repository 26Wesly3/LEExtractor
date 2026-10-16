# LEExtractor 开发设计总纲 v3.2

本总纲由分模块规范生成。代码AI请先读取 START_HERE.md；字段与默认值以 contracts 为准。

# 00 文档索引与使用方法

版本 3.2。本包将产品愿景、现有资产复用、两条入口、确定性算法、可配置 AI、数据与任务契约，以及分阶段验收统一为一份执行基线。

## 文件用途

| 文件 | 用途 |
|---|---|
| 01_PRODUCT_VISION.md | 产品目标和不能破坏的约束 |
| 02_USER_WORKFLOW.md | 两条入口、无反馈闭环及状态文案 |
| 03_SYSTEM_ARCHITECTURE.md | 服务边界、运行位置和能力协商 |
| 04_ALGORITHM_DESIGN.md | CoreRank、反馈、多样性及解释 |
| 05_UX_UI_SPEC.md | 保留视觉体系、页面和交互规则 |
| 06_REUSE_CHANGE_MAP.md | 仓库核验、复用和迁移清单 |
| 07_DEVELOPMENT_ROADMAP.md | M0 至 M5 的依赖、产物和退出条件 |
| 08_API_DATA_MODEL_SPEC.md | API、身份、字段和错误行为 |
| 09_ACCEPTANCE_BENCHMARK.md | 功能、回归、算法与用户任务验收 |
| 10_SECURITY_PRIVACY.md | 项目隔离、上传、凭据和远程传输 |
| 11_CHINA_ACADEMIC_ACCESS.md | 校园资源及中文数据源能力边界 |
| 12_OPEN_SOURCE_ASSET_POLICY.md | 开源资产评估与 adapter 规范 |
| 13_VENUE_INTELLIGENCE.md | 期刊会议信息与来源核验 |
| 14_JOBS_IDENTITY_MIGRATION.md | 并发、幂等、任务状态与数据迁移 |
| 15_PERFORMANCE_DEPLOYMENT.md | 弱服务器预算、缓存与部署策略 |
| 16_PDF_ZOTERO_INTEGRATIONS.md | PDF 匹配、Zotero 和导出 |
| 17_AI_RERANK_FIELD_INSIGHTS.md | AI 重排、地图、热点及切入点 |
| 18_REQUIREMENTS_TRACEABILITY.md | 用户要求到模块及验收的映射 |
| 19_SOURCES_DECISIONS.md | 来源、工程决策和待核实事实 |
| 20_ZJU_CAS_DEFERRED.md | CARSI与出版社资源跳转及直接SSO延期 |

`contracts/` 提供配置、OpenAPI、JSON Schema 和状态机。`fixtures/` 提供确定性算例与验收场景。`handoff/` 提供实施记录模板。总纲 Markdown 由上述章节拼接；单章修改后必须重新生成总纲与校验清单，避免维护两份不同规范。

## 完成状态

本包已经补齐工程规则及契约，尚未核验当前仓库分支、运行产品测试或标定排序参数。工程实现者必须完成 M0 基线核验。文档检查通过不等于软件无 bug。

## 交付边界

目标功能包含 Zotero 导入与显式保存、个人检索额度、AI BYOK、中文学术访问、期刊指标和研究概览。无许可的中文数据库自动检索、没有权限的官方期刊指标、没有本地伴随端的用户电脑计算不得模拟成功。离线/免费基础模式必须可用。

# 01 产品目标与范围

LEExtractor 为科研早期探索提供证据化文献发现。参考用户是对目标领域缺乏知识的研一学生。完成一次任务后，用户应知道优先阅读的文献、主要研究分支、证据支持的发展脉络、当前检索集内的活跃方向，以及下一步如何核查研究问题。

## 硬约束

1. 提供“从研究方向开始”和“从已有文献开始”两个主入口。后者支持单篇、多篇可提取文本的学术 PDF，不要求手填 DOI。
2. 首轮检索、匹配、去重、排序、有限扩展、初始概览自动完成。人工标记是可选纠偏；没有任何标记也能使用。
3. 保留字体、留白、灰阶、卡片、按钮、抽屉、动效、字标、求是鹰与现有设计 token。信息架构可调整，不以全新主题重做界面。
4. 普通流程不要求选择算法、数据库权重或图参数。界面采用正式、克制、准确的中文，技术细节进入高级设置。
5. 核心文献排序必须考虑主题适配与证据；不把高被引、高 JIF、高 CCF 等级直接等同于核心。
6. 每条领域结论关联论文或统计证据；明确检索范围和缺失来源。未知保持未知。
7. 保留现有检索、扩展、证据、导出及 PRISMA 能力；PRISMA 筛选与探索反馈分开存储。
8. 检索 Key、AI Key、Zotero 授权分别配置。可在无模型 Key 时使用基础流程。
9. 引用关系、相似关系和去重关系不得混用。可视化折叠不删除论文身份或证据。

## 验证目标

“覆盖至少 85% 以学术文献为主要载体的研究生探索工作流”是长期目标，必须先定义工作流样本和评估分母。暂不承诺法学原始法源、历史档案、艺术作品等证据系统的完整覆盖。

相对于通用 AI 的目标是核心文献召回、证据可追溯与可复现发现路径。是否更准确，须以同等时间、预算及任务条件的对照评测支持。文档不预设胜出结果。

## 当前完整交付与延期范围

M1 至 M4 交付双入口、可选反馈、渐进排序、基础地图、证据化概览和候选切入点；M5 补齐 Zotero 读写与校园/期刊访问整合。实际授权接口缺失时，必须能提供明确能力状态及合法访问入口。

延期：自训练 GNN/LTR、全学科专用数据库、默认 OCR、浏览器内大模型、用户电脑伴随计算服务、无限雪球、自动生成并认定创新、未经授权的数据接口。延期功能不得以空按钮冒充可用能力。

# 02 用户流程与自动闭环

## 从研究方向开始

用户输入研究主题，点击“开始发现核心文献”。系统保留原文并产生可编辑的研究意图预览；默认自动继续。字段为研究对象、任务/问题、方法、必要约束、排除项及语言。缺失字段允许为空，不能自动捏造研究对象或结果。

先按原始词、确认的术语与可追溯同义词生成最多 3 个查询变体；有翻译服务则附英语检索式，无翻译时保留原语言检索并给出简短能力状态。候选按来源并发获取、归一去重，先返回快速排序。随后在预算内完成增强排序、最多 1 轮自动扩展和初始概览。所有用户可见列表带排名快照版本。

## 从已有文献开始

用户选取 1 至 10 个 PDF，点击“导入并发现文献”。逐文件验证、解析与匹配。已匹配文件成为种子；缺少 DOI 但有题名或足够文本的文件成为本地种子，以题名/摘要检索继续发现；无法读取且无文本的文件明确失败。

不确定匹配项可以稍后确认，不阻塞其余文件。系统默认将其保持为本地种子，不自动采纳存疑外部实体。多篇种子来自不同主题时保留独立锚点、展示分支；不强迫将全部材料平均成单一狭窄方向。

## 无人工反馈时

系统从首轮可推荐候选中选择最多 5 篇自动扩展种子，优先不同分支，且不能把“系统选择”写为“用户确认核心”。每篇种子最多取 20 条引用和 20 条参考文献；项目本轮总候选上限 300，超出后按确定的来源轮询顺序截断。计数、来源游标和停止原因进入日志。

自动扩展达到预算、1 轮结束、没有新增规范身份或用户取消即停止。已知来源失败时不能称作检索穷尽。概览使用已取得的证据直接生成；数据不足则提供当前文献与下一步检索建议。

## 人工判断

`core` 表示用户确认优先阅读，`relevant` 表示有用，`excluded` 表示从当前推荐中排除，`unjudged` 表示撤销判断。点击后即时显示状态；后台去抖重排。排除默认仅隐藏并禁止自动扩展，不代表主题负样本；只有原因 `not_relevant` 才用于负反馈。`read`、`duplicate`、`not_now`、`unspecified` 均不进入负反馈。

用户可撤销判断、查看已排除记录及恢复；排除记录和证据保留。继续扩展必须显式点击，每次再执行一个有预算的批次，不产生无限循环。普通用户无需手动重算权重。

## 失败与恢复

来源部分失败显示“已获取部分文献，部分来源暂不可用”，保留可用结果。无可用来源与真正零结果分开。AI 失败保留本地结果。取消保留已提交候选和判断；页面刷新从任务 API 恢复。批量 PDF 部分成功时逐项展示状态与重试动作。

## 最终研究概览

显示研究范围、优先阅读、主要分支、代表文献、时间线、检索集内活跃方向、证据不足项和下一步核查建议。潜在切入点必须包含支持论文、最邻近已有研究、提出理由和建议检索式。新手主页面只显示一行覆盖状态，详细诊断可展开。

# 03 系统架构与部署边界

## 分层

现有 Web 前端通过 API 操作项目。业务编排连接发现、规范身份、排序、反馈、图谱、研究概览、访问网关和文献库 adapter。任务队列负责昂贵计算；凭据存储独立于项目内容。数据源、模型、解析器及 Zotero 均通过可替换 adapter 接入。

领域对象包括 Paper、ProjectPaper、ResearchAnchor、Judgement、EvidenceEdge、RankingSnapshot、ImportItem、Job、AccessOption 和 VenueMetric。书目事实属于 Paper；排序、角色推断和判断属于项目上下文，不能写入全局共享 Paper 缓存。

## 首期运行模式

`server_basic`：用户浏览器只负责展示和上传。检索、轻量解析和快速排序运行于服务器；可用向量缓存及可选语义计算运行于服务器；AI 模型调用发生于外部服务。用户电脑不因打开网页而承担服务器 Python 运算。

`desktop_local`：同一业务层在用户机器启动，具备本地模型和可选 Zotero Local API。功能能力在启动时探测，不能按操作系统名称猜测。

`companion_future`：用户电脑运行受认证的伴随服务并回传版本化结果，延期。不得直接在公共网页访问用户 localhost 作为既定实现，也不得将云端处理宣传为“仅本地”。

## 新增接口边界

- ParserAdapter：输入受控文件路径和限额，输出 ParsedPaper 与证据位置，不做项目提交。
- IdentityResolver：输入元数据，输出候选实体、匹配分数及冲突；项目事务负责合并。
- CoreRanker：输入不可变候选与上下文快照，输出纯计算排名；不写数据库、不持有 Key。
- ModelAdapter：接收允许发送的字段，返回经 schema 校验的结果，不执行模型给出的工具调用。
- AccessGateway：生成访问选项，不将用户网页登录当作检索 API。
- LibraryAdapter：导入授权选择、生成写入预览、显式提交，并记录逐项结果。

## 能力协商

`GET /api/capabilities` 返回部署模式、可用 provider、解析能力、语义模型就绪状态、AI 配置状态、Zotero 能力和限制。真实凭据不返回。capability 状态为 available、not_configured、unavailable、external_blocked，包含原因码和检查时间。

一个能力不可用不得阻塞不依赖它的流程。来源全部失败须返回明确失败；依赖缺失不得自动加载大型服务或下载未知模型。

## 兼容

先从实际源码核实旧 API。优先新增字段与 adapter，保留 `relevance_score` 原语义；新增项目级 `core_score`。若新增路径与旧路由冲突，保留旧行为并使用内部版本化路由及兼容映射，更新 OpenAPI 和前端客户端，不允许静默破坏旧调用方。

# 04 CoreRank 与反馈算法

## 输入与输出

输入为 project_id、不可变候选列表、ResearchAnchor、JudgementMap、已观察的引用边和 ranking_context_hash。输出 RankingSnapshot。相同元数据、向量、配置、模型修订号与 as_of_date 必须得到相同本地排序。日期取快照日期，不在循环中读取当前时间。

算法适用于当前候选集，不将候选排名解释为全领域排名。候选召回不足时，排序无法恢复未发现论文。

## 信号定义

Topic：复用实际仓库的词法/语义引擎并在 M0 锁定其版本和分数方向。快速阶段只使用词法和已缓存语义，不等待冷模型。词法特征为题名、摘要、确认术语的匹配；词法 adapter 必须保证数值越大越相关。不同 query 的原始分数不直接比较。无摘要时以题名计算并标记字段缺失。

Impact：已知被引数 C 与合法出版年份 Y 时，计算 `log1p(C) / sqrt(max(1, as_of_year - Y + 1))`。未来年份或缺失年份使该信号缺失；被引未知不是 C=0。该值叫“年龄调整后的引用影响”，不叫“引用速度”。同一文献跨来源被引数不相加，按配置固定来源优先级选择，并保存其余值与采集时间。

Graph：仅使用观察到的真实引用边，方向为引用方到被引方。PageRank damping=0.85、max_iter=100、tol=1e-6。图少于 5 条有效边或不收敛则整项停用。只有存在已观察关联边的节点参与图分位排序，孤立点记图信号缺失。不得将文本相似边当成引用边。共被引/耦合在独立图层记录证据见证者。

Seed：以用户上传或明确确认的锚点为依据，取与各有效种子向量余弦相似度的最大值；没有可比向量则该项停用，不发起全量编码阻塞首屏。保留多种子子群，避免平均向量抹去不同研究方向。

Feedback：按下文更新 q_new，计算论文向量与 q_new 的余弦相似度。无有效判断向量则停用。普通排除不是默认负样本。

## 主题门槛

在未排除、存在主题信号的 N 篇候选中，按 Topic 排名选取前 ceil(0.60*N) 进入主推荐候选；同分跨界一并纳入。显式上传或用户确认的核心锚点也可进入，但缺失主题证据时要标明“用户指定起点”。全零词法且没有可用语义证据的记录不能因高被引进入主推荐。

未过门槛的论文保留在“相关探索”中，不从语料删除。它们可被用户主动指定、用于后续查询改进。门槛是相对候选集的工程启发式，不代表绝对相关度；必须在多学科 benchmark 中校准，不能以未过门槛推断科学上不相关。无合格推荐时返回空推荐及可检查候选，不凭高影响力强行填满。

## RRF 确定规则

配置源为 `contracts/defaults.json`。k=60。默认权重 topic=0.60、impact=0.10、graph=0.10、seed=0.10、feedback=0.10。AI 不新增全局正分项。

每项先在有值候选中按降序做平均并列名次，再将名次映射到 N 个非排除候选尺度：当有值数量 M>1，`r = 1 + (rank_known-1)*(N-1)/(M-1)`；M=1 时 r=(N+1)/2。缺失值同样使用中性名次 (N+1)/2，不当作零或最后一名。某信号没有至少 2 个不同的有效值时，整项停用；不因一个异常值赋予排序优势。

原始融合分 `core_score_raw = sum(w_i/(k+r_i)) / sum(active_w_i)`。展示分 `core_score = (k+1)*core_score_raw`，限制在 [0,1]。该展示分只是固定缩放后的排序分，既不是概率，也不能跨快照比较。无活动信号时两者为 null，采用规范身份升序且显示证据不足。

最终主推荐按主题门槛分组、core_score_raw 降序、Topic 降序、paper_id 升序。判断并列前将浮点值按 12 位小数 round；同分不由网络到达先后决定。展示“推荐顺序”优先于裸数字；分项明细在详情中查看。

core_breakdown 每项记录 raw_value、known_count、available、rank、weight、contribution、missing_reason；实际未知 raw_value 为 null。推荐原因必须由实际满足的条件生成，不把模板句当成观测事实。

## 阅读清单

全局排序与首读清单分开。首读清单最多 10 篇、候选池最多 50 篇，MMR lambda=0.75。MMR 的相关度使用池内最终顺序的线性分位，重复度使用可用向量余弦相似度的 max(0,cosine)，没有向量时使用规范化题名 token Jaccard。只进行多样性选择，不改写全局 core_score。

角色为 foundational、representative、review、methodology、frontier、application、background、peripheral、unknown。可靠书目类型支持 review 标签；其他角色只在有证据或模型推断时赋值并记录 origin，默认 unknown。优先为有证据的 review、foundational、representative、frontier 各选最多 1 篇，再 MMR 填充；角色重叠按该顺序去重。缺少角色不制造配额，不为填满清单扩大到明显不相关论文。

## 反馈

q_initial 为固定原始意图向量；PDF 无文本意图时，以归一化种子向量均值再归一化构成，不覆盖种子子群。每次重算从 q_initial 和完整最新判断集开始，不在旧 q_new 上重复累积。

`v = 1.0*q_initial + 0.60*mean(core) + 0.20*mean(relevant) - 0.10*mean(explicit_not_relevant)`。空集合项取零；各论文先单位归一化；若 v 范数接近零则退回 q_initial。将 v 归一化得到 q_new。若其与 q_initial 的余弦小于 0.80，则对 q_new 与 q_initial 做二分线性插值 30 次，取满足该下限的最大反馈比例并重新归一化。文本原始意图不被反馈静默重写。

标为 excluded 的记录从主列表和自动种子集中移除，仍保留存储和证据。恢复 unjudged 删除其反馈作用。确认核心不等于固定全局置顶，可在“已确认文献”区单独展示。每次反馈只重用论文向量重算查询表示，不再次编码全部论文。

## 校准与限制

参数是明确可执行的起点，效果尚未测量。只允许在训练/开发主题集调整，冻结后在独立测试主题评估。保留 Topic-only、加 Impact、加 Graph、加 Seed、加 Feedback、加 AI 的消融结果。图谱缺失、学科引用差异与新论文低被引均需单独报告。

# 05 界面与交互规范

## 视觉基线

M0 从当前可运行 commit 提取设计 token、字体与字号、间距、圆角、颜色、按钮尺寸、抽屉开关动效、导航过渡、求是鹰及字标。记录 1440×900、1024×768、390×844 三种视口的首页、结果、详情抽屉、图谱截图。基线留在仓库 QA 目录。不能把截图缺失当作换主题的理由；先从源码提取并记录。

允许调整信息层级、页面归属和默认展开状态。不能引入第二套组件主题、随意换字体或关闭既有动效。遵守 prefers-reduced-motion；键盘、焦点恢复、按钮禁用状态和触屏操作保持可用。

## 页面结构

首页首屏展示“从研究方向开始”“从已有文献开始”。最近项目保留。运行概况与项目里程碑并排于后续次要区域，避开主入口；产品介绍与比赛展示不占据核心任务路径。项目指标必须来自真实统计，未部署统计则不显示虚构数字。

工作区四阶段：研究起点、核心文献、研究脉络、研究洞察。导出为页面动作；PRISMA、高级来源、图层、诊断及参数默认折叠。保留内部旧路由兼容映射。

## 文献卡片

默认显示序号、题名、作者/年份/出版源、最多 2 条实际推荐依据、角色（有依据时）、“标记核心”“相关”“排除”动作。core_score 在详情中展示，不能叫“核心概率”。用户确认和算法推荐采用不同标签，避免系统推荐冒充人工确认。

本地快速结果出现后显示“已获得初步结果”；增强阶段显示“正在完善排序”。后台排序完成后显示“排序已更新”，保留用户当前阅读条目的视口锚点，避免点击目标突然移动。打开的抽屉按 paper_id 保持，不按列表位置保持。

## 上传与异常

逐文件显示：等待解析、识别中、已识别、需要确认、作为本地文献继续、无法读取、需要文本识别、等待重试。普通界面不暴露 parser 名称。

部分来源不可用仅显示一行状态及“查看详情”。模型失败显示“已使用基础排序”，不显示堆栈。空候选、无推荐、全部被排除三种页面文案分开。用户可取消、恢复、撤销判断和重试单项。

## 设置

三个凭据区域：检索服务、AI 模型、Zotero。写入 Key 后仅返回已配置状态；不回显明文。AI 提供“基础模式”“AI 增强”，不保留语义未定义的 deep 模式。用户选择“AI 增强”表示明确启用元数据发送；选择基础模式不调用远程模型。

本轮不展示“浙大账号登录”按钮；直接SSO延期，不以普通跳转假装登录成功。“校园资源访问”是独立动作，不依赖 LEExtractor 是否完成校园 SSO。

## 文案

使用“输入研究方向”“上传已有文献”“基于当前文献继续发现”“潜在研究切入点”。避免“AI 认为非常重要”“系统学会了你”“已发现创新点”。证据范围、处理失败等必要状态简洁展示，详细解释集中在详情，避免整页免责声明。

# 06 仓库核验与复用

仓库线索为 https://github.com/26Wesly3/LEExtractor 。本包没有固定代码 commit；历史对话曾提到 master 和未推送分支，不能据此认定开发现场版本。M0 必须确认实际工作目录及基线，随后写入状态文件。不存在的路径不得按文档凭空创建平行架构。

## 候选资产清单

| 历史描述中的路径 | 优先动作 | 核验要求 |
|---|---|---|
| litsearch/retrieval.py 与 stop_reasons.py | 复用 | 零结果、失败、部分完成、取消含义与测试 |
| litsearch/sources.py | adapter 扩展 | provider 超时、并发、分页、个人 Key 作用域 |
| litsearch/identifiers.py | 复用并补 alias | 身份稳定、去重冲突及跨源合并 |
| litsearch/cache.py | 修复后复用 | 只读降级、向量命中、版本化 key |
| litsearch/search.py 与 filters.py | 保留编排与主题引擎 | rerank_corpus 实际签名、慢路径打点 |
| litsearch/snowball.py 与 similar.py | 复用 | 显式种子、断点、重复边、证据见证者 |
| litsearch/evidence.py 与 landscape.py | 增量扩展 | 边方向、孤立点、图层及折叠身份 |
| litsearch/web/repository.py 与 jobs.py | 复用并加原子守卫 | 项目隔离、revision、任务重启 |
| litsearch/persistence.py | 增量迁移 | 旧会话 fixture、备份及恢复 |
| litsearch/export.py 与 downloader.py | 复用 | 证据导出、路径安全、许可状态 |
| litsearch/venues.py 与 ImpactFactorLookup | 核验后统一 | CCF、Citation Proxy 和官方 JIF 的区分 |
| Vue App.vue、styles.css 和 ForceGraph | 调整编排 | 保留 token、抽屉、动效和组件外观 |

上述是核验清单，不是已经通过的能力证明。移除旧文档中的“65%—75% 可复用”等未经测量比例；复用程度按实际差异报告。

## 新增能力的边界

优先在现有包中新增或演进 pdf_import、core_rank、feedback、ai_rerank、credentials、access_gateway、venue_metrics、zotero_adapter；auth/cas_adapter仅为将来延期事项。名称可映射到仓库现有约定，业务职责不变。禁止同一功能出现两套身份解析、缓存和任务状态机。

## M0 核验产物

记录 git commit 与工作树、Python/Node 版本、依赖锁、测试入口、启动命令、当前路由、会话 schema 和 3 份代表性旧会话 fixture。先运行相关现有测试，记录既有失败；不要把修改前失败伪装为新功能导致或通过移除测试掩盖。

构建一次小规模实际检索和一次固定语料回放，查明排序各阶段耗时。项目性能历史数据仅作为排查线索，不能当作当前机器实测。

# 07 分阶段开发路线

本轮目标为 M0 至 M5。每阶段保持可运行、可回退；未通过依赖阶段不能标记后续阶段完整完成。首次文献发现主流程不依赖校园批准或商业数据库授权。

| 阶段 | 主要产物 | 退出条件 |
|---|---|---|
| M0 基线与契约 | 仓库映射、旧数据 fixture、视觉基线、接口和性能打点 | 既有结果可复现；明确实际 schema；规范校验通过 |
| M1 最小发现闭环 | 两入口、PDF 单项状态、身份归一、快速列表、有限扩展、无反馈初始概览 | AC-01 至 AC-05、AC-10、AC-11、AC-18 通过 |
| M2 排序与反馈 | CoreRank、主题门槛、首读清单、可撤销判断、快照和并发控制 | AC-06 至 AC-09、AC-12、AC-13 通过；固定排名算例符合 |
| M3 AI 与个人额度 | 检索 Key 分离、模型 Key、局部 AI 重排、超时降级、缓存 | AC-14 至 AC-17、AC-26 通过；首屏不等待模型 |
| M4 研究概览 | 有证据的分支、时间线、趋势描述、核查型研究切入点 | AC-19、AC-20 通过；来源和证据链接可复核 |
| M5 资源与交付 | Zotero、校园访问、期刊信息、迁移回归与完整报告 | AC-21 至 AC-25、AC-27、AC-28、AC-30 至 AC-32 按真实环境验收；AC-29延期 |

阶段期间始终运行相关回归与评测；避免最后才补 benchmark。M2 前必须存在少量专家核对的开发主题集，完整对照评测在 M4/M5 完成。

## 外部依赖

按用户要求直接校园SSO暂缓，本轮仅保留外部资源跳转；AC-29标记deferred，不要求开发CAS adapter或mock协议。中文正式检索 API、官方期刊指标和 Zotero 写权限同理，区分代码未实现与外部授权未提供。

## 可继续推进的工作

缺少某个外部依赖不应停止整个开发。完成其余基础检索、PDF、排序、反馈、概览和导出，并提供可复用 adapter。需要申请材料、登录或付费时列出具体缺项，由用户处理，不能替用户发送申请或接受合同。

## 迭代纪律

每阶段记录需求 ID、改动范围、实际测试和未解决问题。优先小批次变更；通过相关测试后推进，不重复运行无关昂贵测试。数据库迁移先备份再验证。性能未达门槛不得以缩小检索结果且不说明的方式冒充优化。

后续路线：OCR、用户电脑伴随计算、正式中文元数据 API、强时间序列热点、书籍专利标准与学科特定证据、真实标签支持的学习排序。这些均不阻塞当前已有证据范围内的研究概览。

# 08 API 与数据模型

## 通用约定

OpenAPI 文件为 `contracts/openapi.json`，领域对象在 `contracts/domain.schema.json`。UTF-8 JSON，时间为 UTC RFC3339，年份与出版日期分开。paper_id、project_id、job_id、snapshot_id 为不透明内部 ID，不在前端拼装 DOI 路径。分页使用后端不透明 cursor；快照变化时旧 cursor 返回 409 CURSOR_STALE。

项目内容修改携带 expected_revision，异步命令另携带 Idempotency-Key。身份验证使用既有应用会话或匿名私有项目会话；匿名不意味着全站共享项目。所有资源读写先验证 owner。持久化 Key 不属于项目 DTO。

## 数据分层

Paper 保存稳定内部 ID、规范 DOI/外部标识符 aliases、题名、作者、年份、摘要、文献版本关系和字段 provenance。Paper 的事实来源可多值；冲突记录不能被最后响应静默覆盖。

ProjectPaper 保存项目 paper_id 关联、judgement、exclusion_reason、seed_origin、附件引用和排序 snapshot 关联。core_score、role、推荐依据和 AI 结果属于 RankingSnapshot；为兼容列表 DTO 可平铺返回，但必须附 ranking_snapshot_id。

ResearchAnchor 保存原始用户输入、结构化意图、query_variants、seed_paper_ids 和 context_version。query_variants 必须记录生成规则和模型版本；原始输入不可被模型覆盖。

RankingSnapshot 保存 corpus_version、context_version、algorithm_version、config_hash、model_revision、as_of_date、ranking_stage、排序结果、coverage 及父快照 ID。旧快照允许被读取但不能冒充当前结果。

## 主要接口

| 方法与路径 | 行为 |
|---|---|
| GET /api/session | 建立或恢复匿名私有会话并返回CSRF token，不是校园登录 |
| GET /api/capabilities | 返回部署能力与限制 |
| POST /api/projects | 建立私有项目，返回 revision=1 |
| GET /api/projects/{pid} | 项目、活动任务和当前快照 |
| POST /api/projects/{pid}/discover | 文本或种子启动发现任务，202 |
| POST /api/projects/{pid}/papers/import | multipart 批量 PDF 入队，202 |
| GET /api/projects/{pid}/imports/{import_id} | 逐文件解析和匹配状态 |
| POST /api/projects/{pid}/imports/{import_id}/resolve | 显式确认一个匹配候选或保留本地种子 |
| POST /api/projects/{pid}/imports/{import_id}/retry | 仅重试指定可重试失败项 |
| POST /api/projects/{pid}/judgements | 原子保存单次判断，返回新 revision 与重排 job_id |
| POST /api/projects/{pid}/rerank | 创建重排命令，普通 UI 自动调用 |
| POST /api/projects/{pid}/expand | 用户触发一个有预算的扩展批次 |
| GET /api/projects/{pid}/papers | 按快照分页，包含主推荐/探索/已排除筛选 |
| GET /api/projects/{pid}/jobs/{job_id} | 状态、阶段、逐项结果、错误和快照 |
| POST /api/projects/{pid}/jobs/{job_id}/cancel | 请求取消，保留已提交结果 |
| GET /api/projects/{pid}/jobs/{job_id}/zotero-results | 查询Zotero逐项读写结果 |
| GET /api/projects/{pid}/overview | 当前证据化概览或数据不足状态 |
| GET /api/projects/{pid}/papers/{paper_id}/access-options | 访问入口，不返回已授权的虚假结论 |
| GET /api/projects/{pid}/papers/{paper_id}/venue-metrics | 指标、年份、来源和核验状态 |
| PUT /api/settings/credentials/{provider_id} | 会话级写入凭据，只返回配置状态 |
| GET /api/settings/credentials | 当前会话配置状态 |
| DELETE /api/settings/credentials/{provider_id} | 删除会话凭据，204 |
| POST /api/projects/{pid}/zotero/import | 导入用户选择条目，202 |
| POST /api/projects/{pid}/zotero/export-preview | 生成拟保存条目及目标集合预览 |
| POST /api/projects/{pid}/zotero/export | 根据未过期预览显式提交，202 |

本表是新增业务契约。现有 search、similar、landscape、download、export 等路由保持实际基线兼容；不得编造旧路径。各新增响应 schema 与参数详见 OpenAPI，不以本表代替。

## 并发及错误

revision 冲突统一 409 REVISION_CONFLICT，附 current_revision，不含他人数据。前端读取最新状态后只重试仍然有效的操作；不得盲目循环。重复幂等 key 且请求体相同返回原命令结果；同 key 不同请求体返回 409 IDEMPOTENCY_CONFLICT。

400 请求格式错误；401 会话失效；403 权限不足；404 资源不存在或无权查看；413 上传超限；415 非支持类型；422 业务参数非法；429 限额或队列满；503 能力未配置或暂不可用。错误 envelope 包含 code、message、retryable、details、request_id。details 仅允许安全字段，如当前 revision、失败 item_id 和 retry_after_seconds。

异步请求接受后，某个外部来源失败体现在任务和 coverage 中，而不是将已保存候选回滚。AI 输出非法属于 AI 子阶段失败，保留本地排名。导入每个文件单独事务提交，不要求全部成功。

## 向后兼容

保留 relevance_score 原有含义，空的新字段为 null，不以 0 冒充未知。paper_key 仅作为旧端 alias，在 API 边界解析到 paper_id，存储和新 API 均使用 paper_id。移除旧 alias 前必须有弃用与迁移记录。

## 跨字段约束

DiscoverRequest 必须包含非空 research_text 或非空 seed_paper_ids；种子必须属于项目。非 excluded 判断的 exclusion_reason 必须为 unspecified。ResolveRequest 的 match 必须选择该 item 返回的 candidate_id，keep_local 必须传 null。来源边 citation 为 directed=true，co_citation、bibliographic_coupling、text_similarity 为 false；witness 必须能定位证据。上述条件除 schema 类型校验外还要在服务层校验。

项目 GET 同时返回 ResearchAnchor。Overview 明确返回 branches、timeline、trend_status、trends、recommended_paper_ids 与 statements/leads，空结构使用空数组而非省略字段。分支代表文献必须属于该分支，全部引用必须存在于项目。Zotero 导入沿用 mixed 入口模式。

# 09 验收与效果评测

## 证据要求

每条 AC 用例在 `fixtures/acceptance_cases.json` 有预期结果。实施记录填写代码测试位置、真实命令、退出码、日志路径和状态。文档包内场景是测试规格，不是已运行产品测试。不能仅用被测实现生成所有预期答案。

关键不变量必须以固定输入、手工可核对答案或独立参考实现验证。外部接口采用录制并脱敏的响应与明确错误 fixture；再执行小规模真实联调，二者分别报告。

## 关键工程场景

| ID | 场景 | 必须结果 |
|---|---|---|
| AC-01 | 一句主题，零标记 | 自动获得列表、有限扩展及初始概览 |
| AC-02 | 单篇/多篇 PDF，无手填 ID | 逐项识别并启动发现 |
| AC-03 | 无 DOI、有题名或摘要 | 本地种子通过文本继续检索 |
| AC-04 | 相同 PDF 重复上传/重试 | 不重复建立身份或附件 |
| AC-05 | 批量部分失败 | 成功项保留，失败项可单独重试 |
| AC-06 | 固定 RRF 候选与并列 | 原始分、展示分、顺序符合 fixture |
| AC-07 | 引用、年份、图或摘要缺失 | null 与 0 分开，允许降级，无 NaN |
| AC-08 | 排除、撤销、再次扩展 | 排除不自动复活；撤销清除其反馈作用 |
| AC-09 | 高频判断及倒序任务完成 | 最终结果只对应最新上下文 |
| AC-10 | 限流/超时/来源全部失败 | 部分完成、失败、真实零结果严格区分 |
| AC-11 | 取消、刷新、进程重启 | 保留已提交结果；任务可恢复或明确中断 |
| AC-12 | 旧会话迁移与回滚 | 原判断、证据和附件完整可读 |
| AC-13 | canonical identity 合并 | aliases、附件、判断和证据正确迁移 |
| AC-14 | 无 Key / AI 超时 / 非法 JSON | 本地流程完整有效，AI 不破坏当前快照 |
| AC-15 | AI 重复/未知/遗漏 ID | 严格拒绝不合格结果，不生成新论文 |
| AC-16 | Key 与 PDF 隐私 | 日志、导出、DTO 无秘密，默认不传全文 |
| AC-17 | AI 候选窗口外论文 | 原相对顺序不变，没有虚构 AI 加分 |
| AC-18 | 新手与视觉回归 | 无必要算法配置，核心组件及动效保留 |
| AC-19 | 孤立图与少量文献 | 不凭空生成完整领域结构 |
| AC-20 | 热点和潜在切入点 | 有证据范围及核查建议，无保证创新表述 |
| AC-21 | Zotero 导入 | 仅导入选择项，保留键和库来源 |
| AC-22 | Zotero 部分写入/版本冲突 | 逐项记录，不重复写入，不覆盖他人修改 |
| AC-23 | 校园与数据库访问 | 入口可用状态真实，未声称完成统一认证 |
| AC-24 | 期刊指标缺失/未核验 | 不造 JIF，不阻塞发现，不冒充官方指标 |
| AC-25 | 非授权项目访问 | 拒绝读取附件、任务、项目和凭据 |
| AC-26 | 弱服务器固定语料 | 达到性能预算或明确未达标 |
| AC-27 | PDF 中参考文献 DOI | 不误当成上传论文 DOI；冲突进入确认 |
| AC-28 | 未批准 CAS 的当前版本 | 无“浙大账号登录”可用按钮，不调用 CAS |
| AC-29 | 将来正式 CAS 验证 | 当前 deferred，不纳入本轮开发完成条件 |
| AC-30 | 导出与 PRISMA | 导出有来源；普通判断不改变 PRISMA |
| AC-31 | 图谱近重复折叠 | 仅视图聚合，论文/证据身份不丢失 |
| AC-32 | 预算与自动循环 | 自动最多 1 轮，达到上限明确停止 |

## 核心文献 benchmark

SciFact 继续作为相关度回归参照，不能独自证明核心文献效果。建立 24 个主题的初步评测集，跨至少 4 类学科；按主题划分 8 个开发主题、16 个独立测试主题。这个规模用于项目初步验证，不用于声明覆盖全部学科。

每主题由至少 2 位领域知情评阅者独立标注，争议仲裁并报告一致性。标签为 0 不相关、1 相关补充、2 代表性/重要支持、3 核心必读，另标角色与分支。核心集合必须来自独立综述、课程书单及专家补充，不能只从本系统 Top-N 产生。

分开评测：端到端核心候选 Recall；固定候选池内 nDCG@10/@20、核心 Recall@20/@50、首个核心的 MRR；阅读集分支覆盖和重复度。评估集合不完整时报告 pooled recall，不声称领域绝对召回。MRR 定义为第一个 label=3 文献的倒数排名；core hit rate 为至少命中一个 label=3 的主题占比。

比较旧 hybrid、关键词基线、CoreRank local、CoreRank+AI、通用 AI 检索流程。记录模型、版本、prompt、日期、时间及调用预算。端到端比较允许各系统实际检索；纯重排比较必须用相同候选池。不能混报两类结果。通用 AI 多次重复，记录波动。

## 发布门槛与效果目标

工程门槛为所有当前必需 AC 通过，无数据丢失、身份串用和凭据泄漏；外部依赖单独披露。相关度回归 nDCG@20 的下降超过 0.02 绝对值时必须分析并处理，不能直接改阈值放行。

核心效果初始目标：测试集 CoreRank 相对旧 hybrid 的平均 nDCG@20 至少提高 0.03，核心 Recall@50 不下降超过 0.02。它们是研发目标，未测量前不得写成成绩；未达到时保留可切回旧排序并报告原因。以主题为抽样单位给 bootstrap 置信区间，不因单个显著结果宣称整体优越。

## 新手任务

建议邀请至少 8 位对测试领域不熟悉的研究生，在固定 20 分钟条件下完成检索和概览任务；采用平衡顺序的交叉设计，避免同主题重复学习污染。记录核心文献命中、分支辨识、错误判断和时间。小样本结果按探索性试验呈现。首轮流程成功不依赖人工核心判断。

# 10 安全与隐私

## 项目与会话

所有项目、上传、任务、排名与导出访问绑定 owner_id。匿名私有会话使用服务端生成的高熵标识；不能通过可猜项目 ID 获得数据。Cookie 使用 HttpOnly、Secure 和合适的 SameSite；修改操作校验 Origin 与 CSRF。跨用户缓存只允许合法公共元数据和不含私人文本的模型结果；上传全文向量默认项目隔离。

## 凭据

检索 Key、模型 Key、Zotero token 以 provider_id 加 session_id 作用域存储。公共演示默认只在内存保存，会话闲置 60 分钟清理；重启后重新配置。API 仅返回 configured、provider 和更新时间。Key 不进入项目 JSON、异常堆栈、日志、URL、统计、模型提示或导出。

任务只保存凭据引用，在执行时解析；清除凭据后未开始的调用不得继续使用。日志过滤同时覆盖 Authorization、api_key、token 和第三方错误正文。私有部署如需持久化，应使用系统凭据设施或加密存储及外置主密钥，另行启用。

## 文件与解析

每批最多 10 个 PDF、单文件最多 20 MiB、总计最多 100 MiB、单文件最多 200 页。同时校验签名和实际解析，不只信扩展名/MIME。文件名仅用于展示，内部路径使用随机 ID；解析子进程限制时间和内存，禁止执行附件脚本、嵌入动作或外部请求。

项目删除时清理关联上传与派生缓存；引用计数保护同项目重复文件。上传过期策略可配置并在产品说明中展示，不能自动删掉用户仍在使用的研究资料。临时文件清理与长期项目文件分目录。

## 外部请求

公共部署使用管理员维护的 provider/模型 host allowlist，不允许任意 base_url 访问内网、环回、链路本地和云元数据地址。处理 DNS 重绑定及重定向后再验证目标。私人部署自定义端点为显式高级配置，不能默认扩大公共服务器访问范围。

默认向 AI 发送研究意图、题名、摘要和必要书目信息；上传全文不自动发送。启用远程 AI 前说明发送范围，并通过用户选择 AI 增强启用。文献内容作为不可信数据隔离，不执行其指令。输出只能映射到允许的 schema，不执行模型生成 URL、代码或工具调用。

## 校园与资源

本轮不接入浙大统一认证，不收校园密码。图书馆入口仅普通跳转；数据库订阅权限与应用会话分开。不得复制学校登录界面、代理密码登录、转发校园 cookie 或用既有系统回调冒充自身接入。

## 测试要求

至少覆盖他人 project_id、import_id、job_id、附件 ID、导出下载、恶意文件名、超大文件、模拟注入摘要、恶意模型端点及凭据 redaction。不得使用真实用户密码或 Key 作为测试样例。

# 11 中文学术资源与校园访问

## 本轮决策

按用户最新要求，暂不接入浙大统一身份认证。不开发 CAS 登录，不显示可用校园账号登录按钮，不以认证作为文献发现前置条件。未来说明集中在 `docs/20_ZJU_CAS_DEFERRED.md`，不属于当前验收范围。

保留“校园资源访问”以及 CNKI、万方、维普的官方检索/文献落地页入口。它们是用户主动打开的资源链接，不代表 LEExtractor 已取得数据库程序化检索授权。

## 三类能力

metadata_api 表示有正式可用且允许本产品使用的元数据接口；institution_link 表示学校/数据库的机构访问入口；fulltext_link 或 resolver 表示出版社/开放获取/馆藏解析入口。返回 AccessOption 必须包括 url、provider、kind、auth_required、institution、availability、verified、checked_at。

verified 仅说明链接或元数据来源已核验；availability 可取 unknown、public、requires_institution、entry_only、unavailable。不能将用户选择“浙江大学”当作授权证明，不能根据应用登录状态断定数据库全文可访问。

## 接入与降级

首期 institution adapter 为可配置浙江大学资源目录，官方入口在实现时从图书馆页面核对并记录检查日期；无确定的单篇匹配则提供数据库检索入口并标 `entry_only`。不要凭猜测拼接数据库内部 ID 或未知登录回调。

正式中文检索 adapter 在获得接口文档和许可后启用。未提供接口时配置 `metadata_api=external_blocked`，不影响开放来源检索；代码和 UI 不可将普通网页跳转统计为召回成功。

## 中文归一

保留原始中文与英文题名，Unicode NFKC、空白和标点规范化用于匹配；同一强标识符可合并。仅翻译题名相似不足以自动合并，应结合作者、年份和版本关系。作者不能因拼音同名直接合并。中文词法使用确定的分词或字符 n-gram adapter；跨语种向量必须确实具备模型能力。

## 状态文案

“开放获取”“需要机构权限”“仅提供检索入口”“尚未匹配到全文”。访问失败与文献不存在分开。无需显示 CAS、CARSI 等协议名；具体连接与错误进入诊断。未来认证接入也不得突破数据库版权或许可边界。

# 12 开源资产优先

新增通用能力编码前检索成熟项目或官方 SDK。记录名称、官方仓库、许可证、维护状态、锁定版本、漏洞与依赖、资源开销、adapter 边界、测试及拒绝理由。复用现有已验证能力优先于新增依赖，不能以开源项目存在代替适配测试。

## 候选资产与选择条件

| 能力 | 候选方向 | 选择规则 |
|---|---|---|
| PDF 文本提取 | 现有解析库、pypdf、pdfminer.six、PyMuPDF | 核对许可证及机器资源，轻量方案优先 |
| 结构化论文/参考文献 | GROBID | 作为可选独立服务，不默认塞入小服务器 |
| 图计算 | 现有 NetworkX | 复用基础算法；确认有向边及失败行为 |
| 社区发现 | 现有实现、NetworkX Louvain、igraph/leidenalg | 固定随机种子；避免只为名称引入重依赖 |
| 模型与向量 | 现有 embedding adapter 和缓存 | 固定修订号；不在首屏隐式下载 |
| Zotero | 官方 Web API 与成熟客户端 | 核对分页、速率、逐项失败和版本语义 |
| 前端网络图 | 现有 ForceGraph | 保留视图折叠和真实身份映射 |

GROBID 官方部署文档对全文结构化给出约 4 GB 的内存建议，故不作为当前约 2 GB 服务器的默认同机依赖，参见来源 S05。候选项目不是许可证审核结论，采用版本必须自行核验。

## adapter 要求

核心域模型不暴露第三方类；第三方异常转为统一错误码；接口具有超时和取消；已失效服务能替换而不重建业务数据。锁依赖版本和模型版本，不在生产自动升级主版本。

通用基础能力若自研，必须说明已有资产不能满足的实际原因。LEExtractor 的投入重点为发现工作流、证据归一、可解释排序、反馈以及领域认知组织。

# 13 期刊与会议信息

## 信息边界

期刊/会议背景帮助理解发表场景，不代替论文质量判断。历史描述中的 CCF 与 Citation Proxy 先经 M0 核验后复用；不得另建并行指标体系。官方 JIF、JCR 分区、中科院分区没有许可且可验证的来源时返回 null。

## 统一数据

VenueMetrics 保存 venue_id、name、venue_type、ISSN 列表与 metrics 数组。每个 VenueMetric 包含 metric_type、value、metric_year、category、edition、source_name、source_url、retrieved_at、verification_status、license_scope、note。类别相关分区必须保留 category，不能把多个学科分区压成一个无来源数字。版本、年份和采集日期各有语义。

metric_type 为 official_jif、jcr_quartile、cas_partition、ccf_rank、citation_proxy、openalex_h_index 或其他已登记类型。verification_status 为 verified、unverified、unavailable。展示许可与真实性分别检查；即使来源真实，许可不允许也不展示受限值。

## 界面与算法

卡片最多显示 1 至 3 个高价值标签；完整来源在详情。Citation Proxy 必须写明“内部引用代理指标”，不能称影响因子。删除旧文档中带具体期刊名和未经核验数字的演示例，使用匿名 fixture。

本轮 CoreRank 不使用期刊指标排序。未来如引入，须单独消融与跨学科验证。出版源不匹配、改名、会议缩写冲突时保留 unknown，不能仅按字符串相似度赋予 CCF A 或 JIF。

# 14 任务 身份与迁移

## 项目版本

revision 为持久化项目内容版本，每次判断、意图、导入条目确认或候选批次提交后递增。context_version 在意图、种子、判断、模型选择、排序配置改变时递增；corpus_version 在候选事实或证据图改变时递增。纯任务进度、排名快照提交不改变 revision，使用独立 snapshot_id。

读操作返回当前版本。修改命令在接受时比较 expected_revision；不符合则拒绝，前端获取最新状态并核对该操作是否仍有效后最多重试 1 次。批量导入的文件陆续提交会改变 revision，UI 必须更新，而非继续使用上传前的旧值。

## 排名快照提交

计算只读取冻结的 context_version、corpus_version、config_hash 和 model_revision。提交时在项目锁/事务内逐项比较当前值；任一不一致，结果为 superseded，不覆盖当前快照。AI 同时校验其父级基础排名 snapshot_id；只应用于同一基础快照。

同项目同阶段最多一个可提交排名任务。300 ms 去抖收集连续判断；新判断使旧任务失效，可以取消尚未开始的计算。正在执行的第三方调用若无法取消，返回后也必须丢弃。保持一个最新待运行任务，不无限排队。

## 发现与导入提交

检索来源每个有效批次以规范身份合并到当前项目，不用整个旧项目 JSON 覆盖当前文件。提交使用原子写入或数据库事务，并检查项目仍存在、任务未取消、context_version 仍一致；上下文已改变时停止接纳后续批次，已有结果保留。

PDF 解析结果属于文件，可保留在导入记录；若项目上下文已变，不自动重启旧意图发现。用户主动确认的匹配只能改对应 item，不影响其他文件。取消只阻止后续未提交工作，不回滚已接受判断和候选。

## 状态机

状态为 queued、running、cancel_requested、succeeded、partial、failed、cancelled、superseded、interrupted。允许转换见 `contracts/job_states.json`，终态不可改写。stage 是独立字段，例如 parsing、matching、retrieving、fast_ranking、enhancing、mapping、finished。不得把 stage=finished 误写成全部成功。

进程重启将遗留 running/cancel_requested 标为 interrupted。checkpoint 只保存可复现的游标、已提交 ID、预算和输入版本，不存 Key。恢复创建带 parent_job_id 的新任务，重新检查授权、当前上下文和预算；不自动重放外部写入。

## 幂等

命令键作用域为 owner_id、project_id、endpoint、Idempotency-Key；请求规范化后保存 body_hash 与原响应，保留 24 小时。重试相同键返回同一操作；不同 body 拒绝。命令幂等与文件内容去重分开：文件用 SHA256，在同项目内相同内容重用同 attachment_id；不跨用户暴露文件存在性。

## 规范身份

使用不可变内部 paper_id，外部 DOI/OpenAlex/S2/arXiv ID 为 aliases。无外部 ID 的本地种子仍有内部 ID；后续匹配只加 alias，不直接换 ID。两个既有内部 ID 合并时选创建时间更早者，时间相同按 ID 升序；落下的旧 ID 保留重定向 alias。

仅强标识符一致或达到文献匹配规则才自动合并；预印本与正式发表记录优先 linked_version，不仅凭相似题名合并。冲突 DOI 不自动合并。图自环在合并后移除，重复边按证据来源合并。

判断冲突优先采用最近明确用户操作的服务端时间和事件序号；不能以“核心优先”覆盖后来的排除。自动推断不覆盖人工判断。附件、图边、PRISMA 状态、Zotero 映射分别迁移；PRISMA 冲突保留并提示，不自动裁决。

## 持久化升级

先核实真实旧 schema_version，再确定数值递增目标，写入迁移登记；不能将文档版本 3.2 直接当成旧整数 schema+1。新会话同时记录 contract_version=3.2。每个支持旧版本有一条显式迁移链，未知将来版本拒绝写入并保留原文件。

迁移在备份副本上运行，验证引用完整性，再原子替换。旧 judgement 为空，旧 relevance_score 保留原含义，新排名重算。失败恢复备份。旧代码回滚时读取对应备份，不能让旧程序误写新 schema。迁移须可重复执行且第二次无额外改变。

# 15 性能与运行预算

## 当前约束

参考部署约 2 vCPU、2 GB 内存。历史排序 189.01 秒是用户提供的排查线索，不是本包对当前服务的复测。增加 AI 前先定位 CPU 编码、模型加载、缓存和序列化耗时。

默认单个重计算任务、最多 3 个来源并发、1 个 PDF 解析子进程、1 个 AI 调用。任务队列上限 20，满时 429 并给重试时间。请求线程不能做长时间推理；任务调度采取项目间轮询，单用户不能独占所有执行机会。

## 渐进结果

快速阶段不等待新语义模型、不下载权重、不依赖 AI。用词法、已缓存特征和已有证据返回初步列表。图谱和语义增强预算 8 秒，超过则保留快速结果并记录降级；不能把超时误报为模型加载失败。AI 开始于快排窗口，可不等待冷语义补全。

远程模型只有用户启用 AI 增强才使用。首批结果先返回；AI 结果只更新有效基础快照的候选窗口。用户修改判断时先给本地结果，AI 最多在同一上下文空闲 2 秒后补算一次，避免逐点击调用。

## 缓存契约

论文向量 key 包含文本摘要 SHA256、规范化版本、模型名与 revision、向量维度、最大 token 数及 pooling 策略。意图向量 key 另含意图内容 hash。排名缓存含 corpus_version、context_version、算法与配置 hash、as_of_date。AI 缓存另含 provider/model/prompt_version、候选 ID 及顺序、截断摘要 hash、schema 版本。

变更判断不得使所有论文向量失效；扩展只编码新增或文本变化论文。只读磁盘缓存允许读并降级到有界内存缓存，不当作模型故障。不得每次请求重新加载模型，也不得以匿名全局缓存混入私人 PDF 内容。

## 工程预算

以下为验收目标，未实测。使用固定 300 篇元数据、相同向量与图 fixture，2 vCPU/2 GB、无并发干扰，20 次重复记录 p50/p95/最大值。冷启动单独记录，不混入暖缓存统计。

| 指标 | 目标 | 测量边界 |
|---|---|---|
| 快速排序 p95 | ≤2 s | 已取得候选到初步快照，不含网络 |
| 暖缓存本地增强 p95 | ≤5 s | 全部向量已有，不含 AI |
| 判断后本地重排 p95 | ≤1 s | 300 ms 去抖后至新快照 |
| 元数据缓存命中完整首屏 p95 | ≤5 s | 点击到可用列表 |
| 未缓存远程检索首批目标 | ≤20 s | 第一个成功来源即显示；来源故障单列 |
| 旧项目列表/API p95 | ≤500 ms | 无重计算阻塞 |
| 同机应用总峰值内存 | ≤1400 MiB | Web、worker、模型和解析器合计 |

“首批”不得仅返回空壳 loading。“首个核心文献耗时”另按专家标签测量，不能用首条结果代替。无法达到目标时标记未达标并给测量，不删候选、降质量后不说明。

## 网络与资源

来源连接超时 3 秒、单次总超时 10 秒；可重试错误最多重试 1 次，尊重 Retry-After，并受本轮发现 45 秒总预算限制。超预算停止获取新结果，保留已到数据。AI 总预算 20 秒、最多 1 次有效调用；失败不阻塞本地。导入单文件解析 15 秒上限。

模型无法在 1400 MiB 总预算内运行则 server_basic 不启用该模型，使用快速排序与可选远程 AI；不要自作主张把昂贵计算搬到浏览器。完整全文 GROBID 为独立可选服务，当前不默认启用。

## 交付验证

记录每阶段 wall time、候选数、缓存命中数、provider 错误码、模型版本、峰值 RSS 和停止原因，禁止记录敏感内容。带特性开关 core_rank_enabled、ai_rerank_enabled；回滚需能恢复上一稳定排序与旧数据备份。生产部署前先在隔离环境跑双入口、迁移、隐私和取消用例。

# 16 PDF 与文献库整合

## PDF 解析与匹配

先读取 PDF header 和文本，提取首页/标题区题名、作者、年份、摘要及 DOI 候选，并记录 page/section。参考文献区 DOI 仅作为引用，不自动认定为当前论文 DOI。无文本或加密无法读取分别返回 needs_ocr、encrypted_pdf；当前不默认 OCR，不把失败当成功本地种子。

DOI 规范化去除 URL 前缀、空白和末尾标点，保留原值。DOI 回源后仍与题名核对；明显冲突进入 needs_confirmation。题名规范化采用 Unicode NFKC、casefold、空白和标点归一，拉丁词按 token、中文按字符 bigram。

无强标识符时匹配分为 `0.75*title_similarity + 0.15*author_overlap + 0.10*year_agreement`。title_similarity 使用确定的 token Jaccard；author_overlap 为规范化作者 token 的集合 Jaccard；年份相同得 1、相差 1 年得 0.5、其他得 0。缺失子项记 0，不重新放大权重。

自动匹配必须 score≥0.90 且 title_similarity≥0.90、第一名与第二名分差≥0.10、无强 ID 冲突；0.70≤score<0.90 或分差不足进入 needs_confirmation；其余 local_seed。DOI 一致且题名相似≥0.80 可自动匹配；题名缺失/冲突则确认。以上均为初始工程阈值，需用独立 PDF 样本测误匹配率，不把 match_score 标作概率。

local_seed 至少需要题名或 100 个有效文本字符；否则 failed insufficient_metadata。可从解析参考文献发现外部候选；没有参考文献时以题名/摘要查询回退。不确定匹配可保留为本地种子继续，候选选择后才挂接外部实体。

## 每文件记录

ImportItem 包含 item_id、attachment_id、display_name、content_hash、status、paper_id、match_candidates、match_score、error_code、retryable。API 禁止返回物理路径。上传相同文件复用附件，相同 paper 可有多个不同版本附件。成功与失败逐项保留，重试只针对 retryable 项。

支持状态 uploaded、parsing、matching、matched、needs_confirmation、local_seed、needs_ocr、encrypted_pdf、invalid_pdf、failed。解析进行态与终态在契约中有枚举；needs_confirmation 是等待用户动作，不应重试整个批次。

## Zotero 导入

本轮以官方 Web API adapter 为通用路径；desktop_local 可在能力探测后使用 Local API，能力随安装版本与用户权限变化，不能假定支持写入。公共服务器不得访问用户 127.0.0.1 的 Zotero。

用户选择 library、collection/items 后导入。保留 library_type、library_id、item_key、item_version、collection_key 和来源字段。跳过不支持类型但返回逐项原因；附件只在单独权限和明确选择后下载解析。相同库和 item_key 幂等更新，不能以标题相同覆盖不同条目。

## 保存结果

用户点击“保存至 Zotero”，先生成目标库/集合、拟新增、拟更新、可能重复的预览。提交包含 preview_id 和确认的目标，预览绑定当前用户、项目版本、条目 hash，10 分钟有效。保存只写元数据和用户选择的标签/笔记；全文附件另行明确选择，不能默认上传私人 PDF。

更新使用官方版本前置条件；冲突不覆盖，返回可重新读取/合并的状态。解析每项 successful、unchanged、failed 结果，不能因 HTTP 200 就认定所有项目成功。每个批次持久化操作 ID、外部键与结果，网络超时先核对结果再重试，避免重复新增。具体 SDK 与写入幂等方式按官方文档及采用版本核验，不自行推断 token 有效时长。

## 导出与工具衔接

保留 RIS、BibTeX、CSV（已有能力核验后）、证据包。新证据包 JSON 保存 paper_id/aliases、发现来源、关系见证者、query、排序快照、用户判断、时间和许可说明，不含 Key 或全文。为 Codex/Qoder/WorkBuddy 提供文件交接契约；未提供官方连接协议前不声称已实现直接双向 API。

## 批次边界

文件数量、总字节或单文件字节超限在入队前拒绝整次请求，不留下半个批次；入队后的内容错误按单文件返回。解析子进程内存上限初始256 MiB且受整机总预算约束，超限返回资源错误。成功与待确认并存为partial；全部解析失败且没有可用种子为failed；已解析但全部待确认时也返回partial及操作入口，不无限等待后台任务。

# 17 AI 重排与研究概览

## AI 局部重排

先提供本地快照。仅在用户启用且有模型 Key 时，选择本地主推荐前 30 篇；N=min(30,合格候选数)，N<2 不调用。输入顺序固定于父快照，每条带 paper_id、title、abstract、year、已知 doc_type。摘要每篇最多 2000 字符、输入总预算 12000 tokens；按模型 tokenizer 估计，超限先等比例缩短摘要，最低每篇 256 字符，仍超限缩小连续前 N，N<2 取消。模型不支持 tokenizer 时用保守字节估计并给足 margin。

输入含原始意图、用户判断摘要、父快照 ID 和 rubric。要求输出 schema_version、context_hash、ordered_paper_ids 和 judgments；所有 ID 必须来自输入。模型不得补造论文、引用数、作者、年份或图结构。温度为 provider 支持的最低确定性设置，记录真实值；不能宣称远程模型完全确定。

使用 `contracts/ai_rerank.schema.json` 校验类型、枚举、范围及 additionalProperties。再做语义检查：ordered_paper_ids 与输入 ID 精确构成同一集合；judgments 每个 ID 恰好一次；context_hash 相同；reason 中引用的 evidence_ids 必须来自输入。任一不符合则整批拒绝，保留本地顺序，不自动调用无限修复。

AI 只重排原窗口内的现有位置。窗口外论文保持原相对顺序，core_score_raw 不变，返回 ai_order 与 rank_stage=ai_refined。明确不把 AI 分数加到全局 RRF；这样避免“只有被评估者获额外正分”。模型 role 是 inference，未校准 confidence 不展示为科学概率。

## 角色与解释

模型可输出 priority_score 0–4、direct_relevance 0–4、method_relevance 0–4、role 与 reason。priority 的 4 表示直接支撑该研究问题且有关键方法/综述/奠基证据，3 为强相关代表，2 为有用补充，1 为外围，0 为不支持。模型的 ordered_paper_ids 是局部排序依据，分数用于解释与检查，不与其他模型直接比较。

本地原因采用规则，例如有真实引用入边且图分位达到阈值时才显示“在当前引文网络中具有较高关联度”。模型不得凭题名推断“被多个分支共同引用”。无证据则省略相应结论。

## 领域地图

基础图层保留引用、共被引、书目耦合和文本相似，分别标明来源，不静默混成同一关系。图可视化近重复折叠只改变显示。少于 20 个节点、少于 20 条有效关系或孤立率>0.50 时，不强制生成图社区结论，使用文本聚类或有序文献概览并标记“关系数据不足”。

默认社区算法优先实际仓库实现，经核验符合固定随机种子42及稳定输出；升级可选 NetworkX Louvain，resolution=1.0。社区最少 3 篇，不足归入未分组。社区标签从题名/摘要 c-TF-IDF 提取，最多 3 个关键词；已安装成熟库则复用，否则先使用确定的词频/IDF标签，不为标签临时引入庞大运行时。AI 可润色标签，不可凭空添加新社区。

聚类用合格候选及其明确关系；用户 seed 和已排除记录要有可辨来源。不把无边节点解释成无学术价值。算法名称、版本、图 hash 和孤立率存入快照。

## 时间线与热点

按真实出版年份统计当前候选集，未知年份单列。时间范围限定在“本次检索文献中”。基本活跃度比较最近两个完整年份总数与之前两个完整年份总数，平滑增长率 `(recent+1)/(prior+1)-1`；prior<5 或年份覆盖不足4年不显示增长率排序。当前未结束年份不与完整年份直接比较。

引用速度仅在有年度/时间窗口引用历史时计算，不能用累积被引数/文龄替代。没有独立外部基线时不称领域真实热度；缺少足够历史数据输出“暂不足以判断趋势”及现有时间分布。突发检测延期，不用模型捏造 burst score。

## 潜在切入点

本轮提供可核查线索：从至少两个有证据的分支/概念中找到组合较少的连接，或近期区别性工作。线索包含 claim_type=hypothesis、支持 paper_ids、nearest_work_ids、reason、coverage_scope、next_queries。少量引文不等于研究空白；提交前至少检索一次建议组合并记录结果，无法执行时标 needs_verification。无足够证据时返回空 leads，而不是固定生成 3 个创新点。

## 概览证据契约

每条概览 statement 含 text、type（fact/inference/hypothesis）、paper_ids、evidence_ids、scope 和 limitations。事实必须关联存在的记录/统计；推断须标明推断。默认可用模板和统计生成概览，无 AI Key 时不返回空页面。有 AI 时只在给定证据上写作，输出校验失败回退模板。

## 图与标签实现选择

现有社区实现通过固定图fixture才可作为默认。没有可复用实现时采用NetworkX Louvain；没有可用依赖时返回文本分组降级而非自研社区算法。标签基线按各社区合并文本的归一化词频乘跨社区逆文档频率计算，采用锁定的分词/停用词配置，取前三项并稳定排序。模块版本必须进入快照。

# 18 需求追踪与交付判定

需求依据为本次上传包、共享对话中用户的明确要求，以及本轮对统一认证先暂缓、采用官方资源跳转的修订。早先助手的未经实测百分比和已实现声明不作为产品事实。

| ID | 用户要求或关键稳定性需求 | 规范 | 阶段 | 验收 |
|---|---|---|---|---|
| R01 | 从研究方向开始 | 02 | M1 | AC-01 |
| R02 | 上传单篇或多篇 PDF 即开始 | 16 | M1 | AC-02 AC-03 AC-05 AC-27 |
| R03 | 自动相关度及核心文献排序 | 04 | M2 | AC-06 AC-07 |
| R04 | 扩展后自动重排 | 14 | M2 | AC-09 AC-32 |
| R05 | 新手无需算法知识和核心判断 | 02 05 | M1 | AC-01 AC-18 |
| R06 | 保留 UI 设计语言及书面文案 | 05 | 全程 | AC-18 AC-31 |
| R07 | 研究概况 分支 热点 切入点 | 17 | M4 | AC-19 AC-20 |
| R08 | 人工核心 相关 排除与撤销 | 04 14 | M2 | AC-08 AC-09 |
| R09 | 模型 API Key 与可选 AI 精排 | 10 17 | M3 | AC-14 AC-15 AC-16 AC-17 |
| R10 | 个人检索 Key 与额度 | 08 10 | M3 | AC-10 AC-16 |
| R11 | Zotero 导入和保存筛选结果 | 16 | M5 | AC-21 AC-22 |
| R12 | 校园资源及中文数据库入口 | 11 20 | M5 | AC-23 |
| R13 | 暂不直接接入浙大统一认证 | 20 | 当前硬边界 | AC-28 |
| R14 | CARSI Elsevier 官方访问跳转 | 20 | M5 | AC-23 |
| R15 | 期刊影响因子等背景信息 | 13 | M5 | AC-24 |
| R16 | 已有代码复用与不重复造轮子 | 06 12 | 全程 | M0资产记录 |
| R17 | 节省时间及弱服务器可用 | 15 | 全程 | AC-26 |
| R18 | 可追溯 跨学科 对比通用 AI | 01 09 17 | M4 | 效果评测独立报告 |
| R19 | 旧项目 PRISMA 导出兼容 | 08 14 16 | 全程 | AC-12 AC-30 |
| R20 | 身份合并 取消 重试 并发安全 | 14 16 | 全程 | AC-04 AC-09 AC-11 AC-13 |
| R21 | 隐私 凭据 项目隔离 | 10 | 全程 | AC-16 AC-25 |
| R22 | 文档统一为3.2且可一次交给代码AI | START_HERE与contracts | 本包 | validate_pack.py |

## 完成定义

implemented 表示代码存在并通过相应用例；integration_verified 表示真实外部环境成功；effectiveness_validated 表示独立效果评测达到目标。三者不能互相代替。

deferred 只用于本包明确延期项。external_blocked 必须明确外部缺少的授权或服务；不能用于掩饰尚未实现的 adapter 或测试。所有状态均附证据位置与日期，不默认把生成界面当作完工。

# 19 来源与决策记录

## 需求来源

S00 用户共享对话：https://chatgpt.com/share/6ad24667-6b6c-83e9-ab51-f4d2effb9db2 。于 2026-10-16 读取公开页面中的对话正文；部分历史工具输出被页面隐去，因此其仓库核验不能在本包中当作当前证据。用户要求包括两入口、PDF、自动排序、保留UI、书面语言、Zotero、个人额度、校园/中文资源、期刊指标、跨学科目标和优先复用。

本轮追加：原包版本统一3.2；补充稳定工程契约；浙大直接统一认证暂缓；先采用 CARSI/Elsevier/学校图书馆资源访问跳转。日期按 Asia/Shanghai。

## 方法与官方文档

S01 RRF 原始论文信息：https://research.google/pubs/reciprocal-rank-fusion-outperforms-condorcet-and-individual-rank-learning-methods/ 。证明已有方法存在，不证明本产品效果。

S02 MMR 原始作者资料：https://kilthub.cmu.edu/articles/journal_contribution/The_Use_of_MMR_and_Diversity-Based_Reranking_in_Document_Reranking_and_Summarization/6610814 。用于相关性与冗余权衡，角色配额为本产品额外设计。

S03 相关反馈教材：https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-of-relevance-feedback-strategies-1.html 。少量反馈可能漂移；本包将原始意图保留及偏移限制作为工程措施，尚需评测。

S04 GROBID 接口：https://grobid.readthedocs.io/en/latest/Grobid-service/ 。S05 部署资源：https://grobid.readthedocs.io/en/latest/Grobid-docker/ 。解析器能力与实际采用版本需在实现时冻结。

S06 NetworkX PageRank：https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.link_analysis.pagerank_alg.pagerank.html 。使用有向引用图；超时/不收敛降级为本产品规定。

S07 Zotero Web API：https://www.zotero.org/support/dev/web_api 。写入说明：https://www.zotero.org/support/dev/web_api/v3/write_requests 。S08 Local API：https://www.zotero.org/support/dev/web_api/v3/local_api 。本包不预设用户安装版本的读写能力。

S09 浙江大学应用接入申请：https://itc.zju.edu.cn/tysfrzyyjrsq/list.htm 。详细办理说明：https://itc.zju.edu.cn/_t3018/tysfrzyyjrsq/list.psp 。只证明存在申请流程，不证明学生项目、当前域名或海外服务器已获准。

S10 CAS 协议：https://apereo.github.io/cas/development/protocol/CAS-Protocol-Specification.html 。通用协议不是浙大实施参数，不能据此猜验证端点。

S11 CARSI 官方：https://www.carsi.edu.cn/ 。用户入口：https://ds.carsi.edu.cn/ 。其名称为中国教育和科研计算机网联邦认证与资源共享基础设施，不能在界面擅称“教育部统一账号”。

S12 浙大校外资源指南：https://libweb.zju.edu.cn/2022/0408/c39478a2515130/page.htm 。页面标注更新于2025-04-27，本次访问日期2026-10-16。支持 CARSI 的数据库范围以学校各库导航为准，不承诺所有数据库可用。

S13 浙大 ScienceDirect 导航：https://libdb.zju.edu.cn/s/lib/libtb/show/401 。S14 Elsevier 机构账号说明：https://www.elsevier.support/sciencedirect/answer/how-do-i-add-my-institutionalsso-credentials-to-my-elsevier-account 。这些说明支持用户在资源平台使用机构访问，不授予 LEExtractor 登录回调或 API 权限。

## 决策与待核实项

D01 AI 局部重排而不新增全局加分，避免只对窗口内论文额外奖励。D02 快速首屏不等待冷模型。D03 默认排除不是负样本。D04 排名放在项目快照避免跨研究方向串分。D05 官方资源跳转优先，直接 SSO 延期。

待核实：实际仓库 commit/schema/测试命令；所选模型与许可证；新手实测与效果基线；正式中文检索接口；官方期刊指标许可；用户 Zotero 实际权限；各来源配额；当前主机性能。不得将这些待核实项填成虚构完成记录。

# 20 CARSI 与出版社资源跳转

## 当前实施决定

本版本实施外部访问入口，暂不让 LEExtractor 成为浙大 CAS 客户端或 CARSI 服务提供商。application_sso_enabled=false、federation_sp_enabled=false、external_resource_links_enabled=true。直接 SSO 不纳入 M0至M5，不添加学校密码表单、票据验证接口或仿制登录页。

## 用户路径

论文详情展示“出版社原文”“通过学校访问 CARSI”“图书馆数据库导航”。打开新标签页前保留当前题名和 DOI，并提供复制动作。用户在 CARSI 选择浙江大学并在官方页面登录，再选择已授权资源；Elsevier 论文可打开已有 ScienceDirect 落地页，再使用该平台的机构登录。

若没有可核验单篇落地页，优先使用 DOI 官方解析地址；无法确定出版商时，不将所有论文都送到 Elsevier。CARSI 门户是通用入口，不能保证自动带回特定文章；未取得官方深链规范时不自行拼接身份参数。必要时用户复制题名/DOI继续检索，文案为“进入后可使用题名或 DOI 查找”。

登录后会话属于学校、CARSI 或出版商。LEExtractor 无权读取跨站 cookie，也不从用户切回页面推断“认证成功”或“全文已授权”。本应用保持原有匿名/账户会话。链接打开成功只算访问入口事件，不算全文下载成功。

## 固定注册表

`contracts/access_links.json` 保存本次核对的官方入口、标签、来源、checked_at、类型和粒度。界面只使用注册表及有来源的 paper landing_url。外链允许 HTTPS；不生成含 Key、校园身份或私人研究主题的通用登录链接。外链使用 noopener/noreferrer；不自动跟随执行登录。

默认入口：CARSI 用户门户、浙江大学数据库导航、浙江大学 ScienceDirect 说明页。出版社原文依据具体论文记录。数据库未订购或文章不在授权范围时，由目标平台给出状态；本产品不承诺登录后全部全文可得。

## 与检索 API 的区别

资源跳转不增加服务器召回能力，不把 CARSI 当作统一论文搜索 API，也不将 Elsevier 个人账号当作任意第三方登录 provider。若未来要使用 Elsevier/Scopus/ScienceDirect 的程序化 API，需另行核对官方接口、Key、订阅范围与部署限制，再通过 provider adapter 实现；本轮不以浏览器 cookie 代替 API 授权。

## 后续直接 SSO 的记录

用户给出的 zjuam.zju.edu.cn/cas/login 链接的 service 指向 eta.zju.edu.cn 已有系统。此地址不属于 LEExtractor，不能复制其回调注册。将来如恢复需求，先走学校应用接入申请，确认自己的 HTTPS service 回调、允许域名/部署环境、协议及版本、验证端点、最少身份属性、会话和退出策略。

正式开发时才评估成熟 CAS 客户端并新增独立 adapter。后端必须向已确认的学校端点验证一次性票据与精确 service；未验证不能建会话。防重放、过期票据、错误 service、开放重定向和登录请求绑定均需独立验收。通用 CAS 协议不等于学校当前实际配置。

## 当前验收

点击 CARSI 打开官方门户，保留当前文献；无外链不伪造地址；用户回来后不改变应用登录状态；服务未配置不显示已认证；不产生 CAS 校验请求。直接 SSO 测试 AC-29 标记 deferred。
