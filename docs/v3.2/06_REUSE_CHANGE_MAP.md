# 06 仓库核验与复用

仓库线索为 https://github.com/26Wesly3/LEExtractor 。本包没有固定代码 commit；历史对话曾提到 master 和未推送分支，不能据此认定开发现场版本。M0 必须确认实际工作目录及基线，随后写入状态文件。不存在的路径不得按文档凭空创建平行架构。

## 候选资产清单

| 历史描述中的路径 | 优先动作 | 核验要求 |
|---|---|---|
| litsearch/retrieval.py 与 stop_reasons.py | 复用 | 零结果、失败、部分完成、取消含义与测试 |
| litsearch/sources.py | adapter 扩展 | provider 超时、并发、分页、个人 Key 作用域 |
| litsearch/identifiers.py | 复用并补 alias | 身份稳定、去重冲突及跨源合并 |
| litsearch/cache.py | 修复后复用 | 只读降级、向量命中、版本化 key |
| litsearch/search.py 与 filters.py | 保留编排与主题引擎 | rerank_corpus 实际签名、慢路径打点 |
| litsearch/snowball.py 与 similar.py | 复用 | 显式种子、断点、重复边、证据见证者 |
| litsearch/evidence.py 与 landscape.py | 增量扩展 | 边方向、孤立点、图层及折叠身份 |
| litsearch/web/repository.py 与 jobs.py | 复用并加原子守卫 | 项目隔离、revision、任务重启 |
| litsearch/persistence.py | 增量迁移 | 旧会话 fixture、备份及恢复 |
| litsearch/export.py 与 downloader.py | 复用 | 证据导出、路径安全、许可状态 |
| litsearch/venues.py 与 ImpactFactorLookup | 核验后统一 | CCF、Citation Proxy 和官方 JIF 的区分 |
| Vue App.vue、styles.css 和 ForceGraph | 调整编排 | 保留 token、抽屉、动效和组件外观 |

上述是核验清单，不是已经通过的能力证明。移除旧文档中的“65%—75% 可复用”等未经测量比例；复用程度按实际差异报告。

## 新增能力的边界

优先在现有包中新增或演进 pdf_import、core_rank、feedback、ai_rerank、credentials、access_gateway、venue_metrics、zotero_adapter；auth/cas_adapter仅为将来延期事项。名称可映射到仓库现有约定，业务职责不变。禁止同一功能出现两套身份解析、缓存和任务状态机。

## M0 核验产物

记录 git commit 与工作树、Python/Node 版本、依赖锁、测试入口、启动命令、当前路由、会话 schema 和 3 份代表性旧会话 fixture。先运行相关现有测试，记录既有失败；不要把修改前失败伪装为新功能导致或通过移除测试掩盖。

构建一次小规模实际检索和一次固定语料回放，查明排序各阶段耗时。项目性能历史数据仅作为排查线索，不能当作当前机器实测。
