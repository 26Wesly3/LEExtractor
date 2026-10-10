# Benchmark 实跑与团队使用说明

本次增加两套可运行流程：公开标注数据的排序对照，以及真实研究方向的来源快照和人工标注。二者的评分口径分别记录。现有 `dataset.sample.json` 仍然只是合成数据自检，不能用来报告比赛成绩。

## 1. 安装和公开数据试跑

在仓库根目录执行，Python 3.10+，建议 3.12；直接复用 Web 版环境也可以。首次需联网下载依赖、SciFact 数据和尚未缓存的 Embedding 模型。

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[web]" -c constraints.txt
.venv\Scripts\python.exe -m scripts.run_public_benchmark --queries 30 --seed 42 --out artifacts/benchmark-scifact
```

默认固定抽取 30 个问题，种子 42，候选库使用全部 5,183 篇文献，不根据相关性筛选候选库。正式扩大试验时使用 `--queries 0` 跑完整 300 个 test 问题，结果另存目录，避免覆盖试跑记录。CPU 首轮向量计算较慢，后续复用独立向量缓存；每 256 个文本块保存一次缓存。不要在 test 标签上挑选权重后再把同一 test 成绩描述为独立验证。

扩大试验同时复用本轮向量可加 `--vector-cache artifacts/benchmark-scifact/model-cache`，只复用论文文本向量，不复用排名或相关性标签。

数据取自 [BEIR 官方 SciFact 下载地址](https://github.com/beir-cellar/beir)，下载压缩包 SHA-256 固定为 `536e14446a0ba56ed1398ab1055f39fe852686ecad24a6306c80c490fa8e0165`。来源说明见 [SciFact 原项目](https://github.com/allenai/scifact)。它测试生物医学科学声明到论文摘要的检索，不代表计算机会议覆盖率。

对照系统：

| 系统 | 实际实现 |
| --- | --- |
| bm25 | 本地 Okapi BM25，k1=1.2、b=0.75，英文停用词，标题重复三次加摘要 |
| lexical | 产品现有 RelevanceFilter：词 TF-IDF、字符 TF-IDF、关键词覆盖 |
| minilm_title | 固定版本的本地多语言 MiniLM，仅标题向量 |
| minilm_title_abstract | 同一模型，标题加摘要，120 token 分块、均值聚合 |
| current_hybrid | 产品实际混合排序，0.60 语义 + 0.15 lexical + 0.25 研究方向概念覆盖 |

后三者使用模型 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，ONNX 仓库版本 `faf4aa4225822f3bc6376869cb1164e8e3feedd0`；这是现有产品模型，不另行训练。测试使用完整冻结语料池向量检索；产品日常流程仍然仅重排上游返回的候选论文。

## 2. 输出和分数

每次输出 `dataset.json`、`systems.json`、`report.json`、`report.md`、`report.csv`。JSON 包含逐题排名和指标，CSV、Markdown 包含按系统宏平均。模型生成的向量缓存位于输出目录的 `model-cache`，不会修改 Web 项目。

指标包括 P@20、nDCG@20、已知相关文献 Recall@50、MRR（本次存储前 50 名，因此为截断 MRR）。系统宏平均读取 `aggregate_by_system`；旧 `aggregate` 只保留作兼容诊断，不用于比较系统强弱。

公开 qrels 是稀疏标注。本试跑明确采用 `unjudged_as_nonrelevant`：未出现在 qrels 的文献按标准检索评测惯例不给相关性收益，但不意味着它们已经被人工判断为无关。所有正例用相等 gain=2，与二元 nDCG 等价，不捏造“核心论文”级别。多数题正例很少，所以 P@20 数值通常较低，优先查看 nDCG 和 Recall。

还输出以问题为抽样单位、种子固定的 2,000 次配对 bootstrap 95% 区间。区间用于探索差异，不是未经多重比较校正的显著性证明。平均值依据逐题保留四位小数的分数计算。耗时分开报告共享建库成本和单题排序成本；只运行一遍，不能据此宣传运行速度优势。

不联网重算已有快照：

```powershell
.venv\Scripts\python.exe -m scripts.run_benchmark --dataset artifacts/benchmark-scifact/dataset.json --snapshot artifacts/benchmark-scifact/systems.json --out artifacts/benchmark-scifact/replay.json
```

这一入口重算分数和宏平均，不重复推理。公开 runner 的模型、采样和 bootstrap 协议保存在原始 `report.json`。

## 3. 真实计算机研究方向与竞品快照

```powershell
.venv\Scripts\python.exe -m scripts.run_topic_benchmark --direction "计算机视觉领域的多智能体合作问题" --out artifacts/benchmark-topic
```

复用产品实际中文翻译和分库查询规则，默认 2020 年至当前年、每个来源最多 30 条、单次分页检索请求预算 5 次、逻辑检索时限 45 秒。OpenReview 的 v2/v1 是分开的检索，最终请求总量保留在来源统计中；不能把调用次数当作 HTTP 请求次数。单个网络请求及服务器 Retry-After 等待可能影响实际耗时，时限不是硬杀进程。可以用 `--english` 输入人工英文译文作另一次独立对照。自动译文不算人工质量标签。

输出 `providers.json` 保存译文、每库实际查询、结果状态、请求计数和耗时；`candidates.json` 保存原始论文元数据；`systems.json` 保存来源原始排序和同一候选池上的产品 lexical / hybrid 排序。来源快照是各数据源 API 的表现，Semantic Scholar API 不等同于其网站交互搜索。

`annotation.csv` 对各系统前 20 名的并集去重后用固定种子打乱顺序，隐藏系统和原排名。CSV 的 label 与 evidence_notes 留空，请真实研究者读标题、摘要，必要时阅读全文后填写：

- `core_relevant`：直接解决研究问题。
- `peripheral_relevant`：相关背景、方法或局部支持。
- `key_review`：重要的相关综述。
- `known_irrelevant`：经过判断不相关。
- `seed`：事先给定种子，排除于发现结果和召回分母。

建议两名组员独立标注，再讨论分歧；不要看系统排名打标签。当前表覆盖 Top20 对照所需的并集，不保证 Top50 全部已标注。如需完整评价 Top50 或扩展发现，将遗漏候选补入标注表。Recall@50 分母是这次已经标注的相关集合，有池化选择偏差，不能描述为全领域召回率。

```powershell
.venv\Scripts\python.exe -m scripts.import_benchmark_labels --dataset artifacts/benchmark-topic/dataset.json --annotations artifacts/benchmark-topic/annotation.csv --reviewer "实际标注者姓名或编号" --out artifacts/benchmark-topic/dataset.labelled.json
.venv\Scripts\python.exe -m scripts.run_benchmark --dataset artifacts/benchmark-topic/dataset.labelled.json --snapshot artifacts/benchmark-topic/systems.json --out artifacts/benchmark-topic/report.labelled.json
```

自建研究方向默认 `require_judged`。TopK 仍有未知标签时，P@K 和 nDCG@K 输出空值；未知候选比例另列。调用失败、限流和缺凭据的系统跳过质量评分，保留状态，不当作“零分竞品”。重复文献按 DOI 别名归一化后去重；对没有公共 ID 的同文献跨源重复，正式标注前还需要人工核对合并。

跨源重复确认后，创建 `identities.confirmed.json`，内容形如 `{"openreview:旧版本ID": "arxiv:最终版本ID"}`；评分命令加 `--aliases identities.confirmed.json`，会同时归并标注分母、候选池和各系统排名，并保留映射。别名环路、同一论文的冲突标签会报错，需先由标注者解决。工具不根据相似标题自动确认论文身份。

## 4. 外部产品比较的后续步骤

第一轮可采用 Semantic Scholar、Google Scholar 作为关键词检索参照；Connected Papers 或 ResearchRabbit 作为种子扩展参照。用同一组研究问题、人工确认的英文检索词、同一日期、年份和最大结果条数保存结果。Google Scholar 当前适配器需要自己的 SerpApi 凭据；无凭据时可按 `providers.json` 中的手动链接检索并导出同格式快照。

Connected Papers、ResearchRabbit 尚未在本次执行自动评分。图谱型产品如果没有明确排序，比较固定预算内新增相关论文数、种子外的已知核心论文覆盖和人工完成筛选的耗时；不要把图上位置伪装成 nDCG 排名。Snowball、BC、CC 的正式实验必须调用真正的扩展流程，不能仅构图后复制同一 lexical 排名。

正式比赛建议至少 20–30 个计算机研究问题，加入准确的 CCF A/B/C 会议已知论文清单和多智能体视觉合作等困难主题；对不同领域另建独立标注集。公开 SciFact 分数只能作为方法试跑记录，不代表已经胜过这些外部产品。
