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
