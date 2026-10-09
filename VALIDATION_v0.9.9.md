# LEExtractor v0.9.9 验收记录

本版在 v0.9.8 的 Web 整合与限流修复基础上，加入研究方向翻译、本地语义排序、OpenReview、Google Scholar（SerpApi）适配器与 CCF 2026 会议／期刊级别筛选。

## 已验证的行为

- 本地量化 Marian 中译英，原文、译文、模型修订与各库实际查询随项目保存。人工英文输入可跳过模型；翻译失败在检索开始前提示，不回退成宽泛主题。
- 复用 TraceRAG 的 FastEmbed 多语言 MiniLM，并固定模型修订。标题与摘要一起编码；长摘要分块覆盖到末尾，向量按文本与模型版本缓存。无摘要记录明确标注只用标题。
- Web 支持六个来源，Google Scholar 配置 SerpApi 密钥后默认选择；未配置时默认其余五库；OpenReview 只取公开投稿论坛，过滤评审与回复；跨 2024 年窗口补查 API v1。投稿状态与会议级别分别呈现。
- CCF 目录来自 CCF 编制的 2026 正式版原始 PDF，由高校图书馆提供镜像；提取 681 项，其中会议 386 项、期刊 295 项。人工核对第 57 页 AI A 类表格。Workshop、Findings、短文等不套用主会议等级。等级表示 venue 分类，不证明单篇论文已正式发表或属于长文。

## 真实查询

主题“深度学习”，方向“计算机视觉领域的多智能体合作问题”，2020—2026：译文为 `multi-agent collaboration in computer vision`，各库查询包含研究方向；真实结果包含 mmCooper 协同感知、S2R-ViT、CVPR/ICCV 的多智能体论文与 OpenReview 投稿。语义排序不保证每条结果都相关，需人工核查。

本机测试时 Semantic Scholar 与 OpenAlex 仍有 429 限流，其他正常来源继续返回；首次 OpenReview 实测发现请求参数不兼容，已按官方 API 改为 `term` 并复测取得 v2/v1 记录。早期 DBLP 适配器在本机遇到 HTML 反爬页面，按用户要求已从检索选项中替换为 Google Scholar。旧 DBLP 标识兼容保留。Google Scholar 的官方帮助不提供批量访问，现通过 SerpApi 接入；本机未配置该密钥，已验证缺失凭据时零网络请求、其余来源继续，以及离线接口契约；不宣称完成谷歌学术真实数据验收。

## 限制

- 检索是上游数据库召回，再对候选语料做本地语义排序，并非对全部互联网论文建立向量索引。上游未返回的论文无法凭 Embedding 自动获得。
- 模型首次使用需要下载（翻译约 116 MB、Embedding 约 252 MB），已有缓存可复用；推理在本机 CPU 执行。模型权重不进入 Git 与源码 ZIP。
- Google Scholar 返回的检索片段不是完整摘要；OpenReview 包含未录用与非主会议记录。CCF 筛选范围取决于实际返回的 venue 元数据，不承诺完整收录。
- 当前没有人工标签集，组合权重为可解释的排序启发式；相关度不是纳入概率，不能据此自动排除文献。
- 用户未授权再次进行浏览器视觉验收；使用真实 HTTP 与前端 DOM 测试检验接口联动，不宣称已完成页面视觉验收。

## 官方依据

- [OpenReview API v2](https://docs.openreview.net/reference/api-v2/openapi-definition)
- [Google Scholar 帮助](https://scholar.google.com/intl/en/scholar/help.html)、[SerpApi Google Scholar API](https://serpapi.com/google-scholar-api)
- [CCF 2026 正式目录公告](https://www.ccf.org.cn/Academic_Evaluation/By_category/)
- [CCF 原始 PDF 的高校图书馆镜像](https://lib.zjgsu.edu.cn/_upload/article/files/f5/b1/f4f7201343b88c0f10564f590ebe/854a3c68-36a6-4698-a359-5184d5e30a0c.pdf)
- [Marian ONNX 模型](https://huggingface.co/Xenova/opus-mt-zh-en)、[FastEmbed 官方说明](https://qdrant.github.io/fastembed/)

## 验收命令与结果

- Python 3.12：`python -m pytest -q`，777 项通过；3 条第三方告警（TestClient 弃用与合成数据聚类数不足）。`ruff check .` 与 `pip check` 通过。
- 前端：`npm run test`，10 项通过；`npm run build` 与静态资源同步完成。DOM 用例验证方向翻译、人工改译、语义模式、六库选择、未配置 Scholar 默认不选与密钥输入清空。
- 完整缓存下本地模型推理成功：用户原句译为 `multi-agent collaboration in computer vision`；另一领域“利用遥感图像估计小麦产量”译为 `Estimated wheat production using remote sensing images`。相关视觉协同论文与不相关医疗论文的纯语义样例分数约为 0.780 / 0.163，仅证明该样例行为，不是检索精度评测。
- 独立验收项目保留 20 篇实际候选记录；本次复测 2 个真实 HTTP 请求、0 次重试、6 次来源缓存命中。CCF A 类筛选取得 7 条记录，详情与列表的摘要可用性和投稿状态一致。Semantic Scholar / OpenAlex 限流仍使任务标记为部分完成，已有结果保留。
- 运行模型不包含在 Git、wheel 或源码 ZIP 中；发布包保留模型固定修订清单和 CCF 目录 JSON。
