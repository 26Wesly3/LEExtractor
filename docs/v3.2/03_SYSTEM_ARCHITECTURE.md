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
