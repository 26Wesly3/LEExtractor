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
