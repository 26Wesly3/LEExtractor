# 试点：查询 "transformer" 能否把 Attention Is All You Need 排到前面

数据快照：2026-10-17，OpenAlex `search=transformer`，前 200 条（相关性排序）+ 被池内文献引用最多的 18 篇（引用扩展）。共 218 篇。

## 结论（当前参数，未改动）

| 检查 | 结果 |
|---|---|
| 关键词前 200 中是否包含 Attention Is All You Need | 否 |
| 池内被引用次数 | 105/218，全池最高 |
| 核心排序中的位置（门槛 0.6） | 109 / 218 |
| 核心排序中的位置（门槛关闭） | 76 / 218 |
| 分项：impact | 第 1（7.96） |
| 分项：graph（池内 PageRank） | 第 3（ImageNet、ResNet 更高） |
| 分项：topic（词法 BM25，标题加摘要） | 第 159 |

## 原因

1. 关键词检索本身召回不到它，需要引用扩展。扩展后它进入候选池。
2. 主题信号是词法的。它的摘要只出现 2 次 "Transformer"，而标题里带 transformer 的综述和变体得分更高。
3. 主题权重 0.6 最大，把它拉低。图信号受池子构成影响：池子以视觉论文为主，ImageNet 和 ResNet 的 PageRank 更高。

## 未验证项

- 语义主题信号未测试：项目固定的 MiniLM 模型在本机不存在，Hugging Face 不可达（HTTP 000）。
- 只有 1 个查询，不能作为效果证据，也不能据此调参。
- OpenAlex 元数据有噪声（例如标题为 "Transformer"、年份 1903、被引 0），未清洗。

## 复现

`python3 run.py`（需要已安装本仓库的 litsearch 包；输入为 pool_all.json）。

## 语义主题信号（本地 MiniLM，2026-10-17 追加）

模型：仓库固定版本 `qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q`，revision `faf4aa4225822f3bc6376869cb1164e8e3feedd0`。从 `LEExtractor_v0.9.11_审查_20261010/.../models/embedding` 复制得到。模型文件 sha256 与缓存 blob 名一致（`model_optimized.onnx` 634d0f…，`tokenizer.json` fa685f…）。原 FastEmbed 缓存里的符号链接损坏，已替换为同内容的真实文件。

| 查询 | Attention 的语义相似排名 | 核心排序位置 | 备注 |
|---|---|---|---|
| `transformer` | 199 / 218 | 133 | 单词查询，与电力变压器、陶瓷变压器等混淆 |
| `transformer neural network architecture` | 64 | 34 | 预先设定的研究上下文 |
| `transformer model based on self-attention for sequence modeling` | 10 | 2 | **事后查询**：写于看到目标论文摘要之后，不是有效证据 |

结论：语义信号可以区分上下文，但裸关键词查询不可用。产品已有研究方向字段（ResearchAnchor 的研究对象、任务、方法），主题查询应使用完整研究意图，而不是单词。是否允许用户输入的研究方向参与主题计算，需要你确认。

复现：`semantic_run.py`、`semantic_ctx.py`（均读取 `pool_all.json`，需已安装的本仓库）。`semantic_topic.json` 为单词查询的语义分数。
