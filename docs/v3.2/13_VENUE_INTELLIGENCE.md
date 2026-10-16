# 13 期刊与会议信息

## 信息边界

期刊/会议背景帮助理解发表场景，不代替论文质量判断。历史描述中的 CCF 与 Citation Proxy 先经 M0 核验后复用；不得另建并行指标体系。官方 JIF、JCR 分区、中科院分区没有许可且可验证的来源时返回 null。

## 统一数据

VenueMetrics 保存 venue_id、name、venue_type、ISSN 列表与 metrics 数组。每个 VenueMetric 包含 metric_type、value、metric_year、category、edition、source_name、source_url、retrieved_at、verification_status、license_scope、note。类别相关分区必须保留 category，不能把多个学科分区压成一个无来源数字。版本、年份和采集日期各有语义。

metric_type 为 official_jif、jcr_quartile、cas_partition、ccf_rank、citation_proxy、openalex_h_index 或其他已登记类型。verification_status 为 verified、unverified、unavailable。展示许可与真实性分别检查；即使来源真实，许可不允许也不展示受限值。

## 界面与算法

卡片最多显示 1 至 3 个高价值标签；完整来源在详情。Citation Proxy 必须写明“内部引用代理指标”，不能称影响因子。删除旧文档中带具体期刊名和未经核验数字的演示例，使用匿名 fixture。

本轮 CoreRank 不使用期刊指标排序。未来如引入，须单独消融与跨学科验证。出版源不匹配、改名、会议缩写冲突时保留 unknown，不能仅按字符串相似度赋予 CCF A 或 JIF。
