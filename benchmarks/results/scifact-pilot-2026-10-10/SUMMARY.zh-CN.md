# LEExtractor Benchmark 实跑结果与交接

日期：2026-10-10。已实现评分流程并跑出公开真实标注数据的试验分数，同时完成一轮计算机研究方向的来源检索快照。

代码分支：[feature/web-backend-integration](https://github.com/26Wesly3/LEExtractor/tree/feature/web-backend-integration)。运行说明：[benchmarks/PILOT_GUIDE.md](https://github.com/26Wesly3/LEExtractor/blob/feature/web-backend-integration/benchmarks/PILOT_GUIDE.md)。

## 1. 公开标注数据上的实际成绩

使用 BEIR SciFact test 数据，全部 5,183 篇候选文献；从 300 个测试问题中用种子 42 固定抽取 30 个问题，共实际执行 150 次排序。标签直接使用公开 qrels，未生成标签、未训练模型、未根据成绩调参。

| 方法 | nDCG@20 ↑ | 已知相关 Recall@50 ↑ | P@20 ↑ | MRR@50 ↑ |
| --- | ---: | ---: | ---: | ---: |
| 当前混合排序 | 0.6800 | 0.8767 | 0.0467 | 0.6379 |
| BM25 关键词参照 | 0.6631 | 0.8433 | 0.0450 | 0.6367 |
| 现有关键词排序 | 0.5941 | 0.8100 | 0.0450 | 0.5339 |
| 本地向量：仅标题 | 0.5438 | 0.7300 | 0.0350 | 0.5040 |
| 本地向量：标题＋摘要 | 0.4856 | 0.7367 | 0.0383 | 0.4268 |

这些数值是 30 个问题的宏平均。nDCG 评价相关结果是否靠前；Recall 的分母仅为公开 qrels 中已知相关文献。多数问题仅有一到数篇正例，P@20 天然偏低。MRR 使用前 50 名。

### 如何解释这一轮结果

- 当前混合排序相对现有关键词排序的 nDCG@20 均值增加 0.0859，配对 bootstrap 95% 区间为 [0.0080, 0.1773]；这是未作多重比较校正的探索性结果。
- 当前混合排序相对 BM25 的均值只增加 0.0169，区间为 [-0.0612, 0.1015]，跨过零；不能据此宣称稳定优于 BM25。
- 单独标题＋摘要向量的 nDCG@20 为 0.4856，低于仅标题的 0.5438；二者差值区间同样跨零。Recall@50 则从 0.7300 小幅增加到 0.7367。加入摘要并不保证更好的靠前排序。
- 混合排序同时改变了语义、词面和概念覆盖的组合，不能把全部提升都归因于 Embedding。通用多语言模型和摘要分块均值是否适合目标领域，还需要消融和独立计算机领域标注集验证。
- 对整个 5,183 篇池，关键词排序平均约 14.71 秒、混合约 16.96 秒；首轮建库与单题排序分开统计，尚未重复测量稳定性。当前生产流程一般重排较小的上游候选集，不能把此整库耗时当作实际 UI 查询延迟。

该数据集测试生物医学科学声明检索，不能直接证明 CCF A/B/C 论文覆盖、中文翻译质量或对外部竞品的优势。它适合作为第一轮可复现的检索方法对照。

完整逐题结果、固定问题 ID、模型版本、候选集合指纹和配对 bootstrap 区间位于交接包的 public-scifact/report.json；表格另有 Markdown 和 CSV。数据来源：[BEIR](https://github.com/beir-cellar/beir)、[SciFact](https://github.com/allenai/scifact)。

## 2. 真实研究方向试跑

原问题：计算机视觉领域的多智能体合作问题。实际本地译文：`multi-agent collaboration in computer vision`。检索年份为 2020–2026，每个来源最多 30 条。

| 来源 | 本轮状态 | 返回条数 | 实际请求数 |
| --- | --- | ---: | ---: |
| semantic_scholar | rate_limited | 0 | 1 |
| openalex | truncated | 30 | 1 |
| arxiv | truncated | 30 | 1 |
| crossref | truncated | 30 | 1 |
| openreview | truncated | 30 | 2 |
| google_scholar | provider_error | 0 | 0 |

本轮共拿到 120 条按标识去重的记录；各系统 Top20 并集形成 87 行盲标注表。Semantic Scholar 是 429 限流；Google Scholar 尚无 SerpApi 凭据，两者没有被计成竞品零分。OpenAlex 本轮可以返回结果，接口情况与之前的失败截图有变化。

混合排序前列出现协同感知、多智能体视觉推理和协作视觉语言模型等论文；关键词排序也混入泛化会议文集。此观察尚无人工相关性成绩，不能当作已经证明的提升。发现 7 组同名记录，可能为跨库重复或版本差异，已写入 identity-review.json，正式评分前需核对归并，不应按标题自动合并。

## 3. 本次实现与修正

- 新增公开数据试跑脚本，实际对照 BM25、现有词＋字符 TF-IDF、仅标题向量、标题＋摘要向量、现有混合排序。
- 按系统对问题取宏平均，修复旧脚本把所有系统混在一起平均的问题；每项指标记录贡献问题数。
- DOI 别名归一化、去重、排除种子；重复输出不能虚增召回。支持人工确认的跨库标识映射，同时归并标注分母和排名；拒绝环路、冲突标签和重复 case/system 记录。
- 修复空排名被解析为字典字段名的问题。真正失败和缺凭据时保留状态、跳过质量评分。
- 自建方向缺少 TopK 标签时，P@K 与 nDCG@K 留空；公开稀疏 qrels 显式使用标准未标注按无相关收益处理的口径。
- 去掉仅构图后复制关键词排名的伪引文扩展对照。Snowball、BC、CC 没有真实实验就不报分数。
- 新增逐题 JSON、按系统 CSV/Markdown、配对问题 bootstrap 95% 区间、人工标注导入和真实主题快照工具。
- 模型分块向量写入独立缓存并报告进度；补上 wheel 包内 embedding_pins.json 的打包配置。

## 4. 验证证据

本机完整 Python 测试 800 项通过，Ruff 和 git diff 检查通过。150 次真实排序逐一用 sklearn 的独立 nDCG 实现核对，并用集合交集核对 P@20、Recall@50；分数与快照重算一致。另用最新脚本复用完整候选库缓存跑了一题，五个系统的前 50 名和质量分数均与初次排序一致；共享建库的首轮与缓存耗时单独记录。用户原 Web 项目文件 SHA-256 未变化。验证数据见 public-scifact/verification.json。

## 5. 组长拿到材料后如何使用

1. 阅读本文件，查看 public-scifact/report.csv 和 report.md，即可了解试验成绩和限制。
2. 从上面的 GitHub 分支下载项目，按照仓库 README 安装 Web 依赖。
3. 不调用模型、直接复算：`python -m scripts.run_benchmark --dataset public-scifact/dataset.json --snapshot public-scifact/systems.json --out replay.json`。在仓库根目录执行，将交接包的 public-scifact 目录放在根目录。
4. 完整复跑模型：`python -m scripts.run_public_benchmark --queries 30 --seed 42`；用 `--queries 0` 可扩大到全部 300 个问题，另存输出目录。
5. 对 real-topic/annotation.csv 进行真实人工标注，核对 identity-review.json 的同名记录；用 import_benchmark_labels 导入，再运行 run_benchmark。跨库同文献确认后使用 --aliases 同时归并分母和排名。详细命令见 PILOT_GUIDE.md。没有人工标签时，相关性成绩保持空值。
6. 正式竞品比较至少准备 20–30 个计算机领域研究问题和已知 CCF 论文；统一英文查询、日期、年份、候选预算。Google Scholar/Semantic Scholar 作为检索参照，Connected Papers/ResearchRabbit 作为种子扩展参照。

## 6. 交接包内容

- 本文件：实跑结果和团队操作步骤。
- public-scifact/：真实公开标注集、原始排名快照、JSON/CSV/Markdown 报告和独立核对记录。
- real-topic/：真实研究方向、译文、分库查询、来源状态、论文元数据、盲标注 CSV、待核对同名记录。
- PILOT_GUIDE.md：完整复现及标注命令。
- 模型权重和数据库缓存不放进本次小型实验交接包；已有含模型 v0.9.11 发布包继续保留，GitHub 版本通过原启动流程下载模型。

外部产品的人工对照、全量 300 问题评测、CCF 已知论文覆盖测试和真实引文扩展评测尚未完成，不能用本次试跑数字替代。
