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
