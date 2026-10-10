# 数据来源与试验范围

本目录记录 LEExtractor 的首次公开标注数据排序试跑，不是完整 BEIR 成绩或外部产品竞品成绩。

来源：BEIR 的 SciFact test 转换版本，下载地址与校验指纹记录在 `report.json`。候选库为全部 5,183 篇文献，测试问题是种子 42 从 300 个问题中固定抽取的 30 个。`dataset.json` 包含这 30 个科学声明、公开正例 ID 及本项目的标签格式转换；正例统一映射为相等 gain=2，不代表原项目提供了核心/外围分类。`systems.json` 是本项目生成的排名，`report.*` 是本项目计算的分数。

原项目：David Wadden、Shanchuan Lin、Kyle Lo、Lucy Lu Wang、Madeleine van Zuylen、Arman Cohan、Hannaneh Hajishirzi，*Fact or Fiction: Verifying Scientific Claims*。

- [SciFact 原项目与论文](https://github.com/allenai/scifact)
- [SciFact 数据与代码许可](https://github.com/allenai/scifact/blob/master/LICENSE.md)：科学声明和证据标注为 CC BY 4.0，语料摘要为 S2ORC / ODC-By 1.0；原项目代码为 Apache 2.0。
- [BEIR 转换、下载与评测说明](https://github.com/beir-cellar/beir)

本目录不包含论文摘要全文或模型权重。候选池相同，主要衡量排序；稀疏 qrels 采用明确记录的未标注不给相关性收益的常用 IR 口径。不能将此结果解释为计算机会议覆盖率、中文翻译质量、引文扩展能力或胜过 Google Scholar 等外部产品。

复算：在仓库根目录运行 `python -m scripts.run_benchmark --dataset benchmarks/results/scifact-pilot-2026-10-10/dataset.json --snapshot benchmarks/results/scifact-pilot-2026-10-10/systems.json --out artifacts/scifact-replay.json`。模型和网络均不参与此分数复算。
