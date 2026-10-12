# ADR-002：向量缓存不可写时降级，绝不因此让检索失败

- 状态：**Accepted**（2026-10-12 已实施）
- 相关：`litsearch/local_models.py`、`litsearch/web/api.py`、`tests/test_model_discovery.py`

## Context

用户可见故障：

```
任务：失败 · 本地模型加载失败
本地 Embedding 未完成；请检查模型缓存或网络，也可切换到关键词排序。
```

实测故障链（完整链路，防止再次把症状当根因）：

```
UI: local_model_failure
  ↓ 实际调用
embedding 模型已成功加载（fastembed 构造成功）
  ↓ LocalEmbedding.vectors()
SQLite 向量缓存写入（~/.cache/leextractor/embeddings.db）
  ↓
OperationalError: attempt to write a readonly database
  ↓ 原实现
except Exception → 统一包装为"本地 Embedding 未完成/模型加载失败"
```

即：**模型是好的、网络是好的，坏的是缓存目录的写权限**。同类环境很常见：受限安装目录（Program Files）、服务账号、容器只读挂载、受管沙箱。

## Decision

按"数据 vs 缓存"原则分级处理：

| 类别 | 例子 | 失败处理 |
|---|---|---|
| 核心数据 | 项目 JSON、会话、导出 | 失败必须**明确报告** |
| 优化数据 | 向量缓存、按源 HTTP 缓存 | 失败**降级**，不阻断 |

具体：

1. **缓存位置候选链**：`LEEXTRACTOR_VECTOR_CACHE` → `models_root()/embeddings.db` → 系统临时目录 `leextractor/embeddings.db`，选第一个**可写**者（写探针实测，不靠假设）；
2. 全部不可写 → **纯内存计算**，本轮照常返回向量，仅不做缓存；
3. 缓存库损坏/不可读 → 视为无缓存（不抛异常），继续计算；
4. **错误信息保留真实异常**：`本地 Embedding 未完成（OperationalError: ...）`；
5. `/api/health` 暴露 `vector_cache` 实际路径，便于用户/支持定位。

## Alternatives

| 方案 | 否决理由 |
|---|---|
| 缓存不可写就报错并要求用户修权限 | 把"优化不可用"升级成"功能不可用"，与降级原则冲突 |
| 静默忽略所有异常（保持原状） | 正是造成误报的原因；用户拿到错误提示却指向错误方向 |
| 总是把缓存写到临时目录 | 重启即失效，失去缓存意义；且多进程/多实例争用未解决 |
| 引入独立缓存服务 | 与本地优先、单机交付形态冲突 |

## Why

缓存只影响**速度**，不影响**正确性**；而错误提示的正确性直接影响可支持性（用户按提示去查模型与网络，方向全错）。降级 + 真实错误两者同时做，才能既保住可用性又保住可诊断性。

## Consequences

- 正面：只读环境下语义排序可用（内存计算）；错误提示与根因一致；health 可直接看到缓存落点；
- 负面：临时目录回退会导致重启后向量重算（速度损失，已在 Known Issues 记录）；
- 观测点：`model_diagnostics()["vector_cache"]`；测试覆盖只读/损坏/复用三种情形（`tests/test_model_discovery.py`）。

## Rollback

- 删除 `vector_cache_candidates/is_writable_directory/vector_cache_path` 三处并恢复单一路径即可回到旧行为；
- 无数据迁移风险：向量缓存可随时删除重建。
