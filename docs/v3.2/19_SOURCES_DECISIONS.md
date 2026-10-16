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
