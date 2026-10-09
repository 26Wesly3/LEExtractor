/* ============================================================================
   数据层自检：在 Node 里加载 prototype/assets/data.js，验证派生数据自洽。
   运行： node tools/check-data.mjs
   ========================================================================== */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const dataPath = resolve(here, '..', 'prototype', 'assets', 'data.js');
const sandbox = { window: {}, console };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(readFileSync(dataPath, 'utf8'), sandbox, { filename: 'data.js' });

const D = sandbox.window.LED;
let failures = 0;
let checks = 0;

function ok(label, condition, detail) {
  checks += 1;
  if (condition) {
    console.log(`  ok   ${label}${detail ? ' — ' + detail : ''}`);
  } else {
    failures += 1;
    console.log(`  FAIL ${label}${detail ? ' — ' + detail : ''}`);
  }
}
function section(name) { console.log(`\n${name}`); }

section('语料');
ok('30 篇论文', D.papers.length === 30, `${D.papers.length}`);
ok('id 唯一', new Set(D.papers.map((p) => p.id)).size === D.papers.length);

const dangling = [];
D.papers.forEach((p) => (p.reference_ids || []).forEach((r) => { if (!D.byId.has(r)) dangling.push(`${p.id} -> ${r}`); }));
ok('指向样本外的引用被如实记录（不是数据错误）', dangling.length === D.externalReferences.length,
  `${D.externalReferences.length} 条外部引用`);
ok('引文图不包含指向样本外的边', D.danglingGraphEdges.length === 0 && D.graph.citation.links.every((l) => D.byId.has(l.source) && D.byId.has(l.target)),
  `${D.graph.citation.links.length} 条边全部在样本内`);
ok('每条边都是 source→target 的引用方向', D.graph.citation.links.every((l) => {
  const src = D.byId.get(l.source);
  return src && (src.reference_ids || []).includes(l.target);
}));

const noYear = D.papers.filter((p) => p.year == null).length;
ok('年份缺失数为 0（未知组由分面兜底）', noYear === 0, `${noYear}`);

section('评分上下文');
const ctxCount = {};
D.papers.forEach((p) => { ctxCount[p.score_context_id] = (ctxCount[p.score_context_id] || 0) + 1; });
ok('恰好两个评分批次', Object.keys(ctxCount).length === 2, JSON.stringify(ctxCount));
ok('主批次与扩展批次都非空', ctxCount[D.ctx.main] > 0 && ctxCount[D.ctx.snowball] > 0);

section('分面');
const yearSum = D.facets.years.reduce((a, b) => a + b.count, 0);
ok('年份分面计数合计 = 语料数', yearSum === D.papers.length, `${yearSum} vs ${D.papers.length}`);
const sourceSum = D.facets.sources.reduce((a, b) => a + b.count, 0);
ok('来源分面计数合计 = 语料数', sourceSum === D.papers.length, `${sourceSum}`);
const schSum = D.facets.screening.reduce((a, b) => a + b.count, 0);
ok('筛选分面计数合计 = 语料数', schSum === D.papers.length, `${schSum}`);
const retSum = D.facets.retrieval.reduce((a, b) => a + b.count, 0);
ok('全文获取分面合计 = 语料数', retSum === D.papers.length, `${retSum}`);
const ctxSum = D.facets.score_contexts.reduce((a, b) => a + b.count, 0);
ok('评分批次分面合计 = 语料数', ctxSum === D.papers.length, `${ctxSum}`);

section('筛选决定');
const tally = D.helpers.decisionTally();
const tallySum = tally.include + tally.exclude + tally.maybe + tally.undecided;
ok('决定计数合计 = 语料数', tallySum === D.papers.length, JSON.stringify(tally));
ok('已决定数与未决定数一致', D.screening.decided + D.screening.undecided === D.papers.length,
  `${D.screening.decided} + ${D.screening.undecided}`);
ok('include 计数与 session 声明一致', tally.include === D.screening.include, `${tally.include} vs ${D.screening.include}`);
ok('maybe 计数与 session 声明一致', tally.maybe === D.screening.maybe, `${tally.maybe} vs ${D.screening.maybe}`);
ok('exclude 计数与 session 声明一致', tally.exclude === D.screening.exclude, `${tally.exclude} vs ${D.screening.exclude}`);
ok('冲突数一致', D.conflicts.length === D.screening.conflicts, `${D.conflicts.length}`);

const orphanDecisions = Object.keys(D.decisions).filter((k) => !D.byId.has(k));
ok('每条决定都指向存在的文献', orphanDecisions.length === 0, orphanDecisions.join(', ') || '无孤立决定');

section('引文图');
ok('引文边只含 citation 类型', D.graph.citation.links.every((l) => l.type === 'citation'), `${D.graph.citation.links.length} 条`);
ok('PageRank 覆盖全部节点', D.graph.centrality.length === D.papers.length);
const rc = D.helpers.relationCounts();
ok('relationCounts 恰为三类规范名', Object.keys(rc).length === 3, JSON.stringify(rc));
ok('relationCounts 不含历史别名 semantic', !('semantic' in rc));
ok('relationCounts 不含 total 键', !('total' in rc));
ok('三类关系边都非空', rc.bibliographic_coupling > 0 && rc.co_citation > 0 && rc.text_similarity > 0, JSON.stringify(rc));
ok('关系边不与自己相连', ['bibliographic_coupling', 'co_citation', 'text_similarity']
  .every((t) => D.graph.relations[t].every((e) => e.a !== e.b)));

section('PRISMA 账本');
ok('账本四条记录、四库各一', D.prisma.ledger.length === 4, D.prisma.ledger.map((r) => r.source).join(','));
const rawSum = D.prisma.ledger.reduce((a, r) => a + r.raw, 0);
ok('账本 raw 合计 = manifest.raw_count', rawSum === D.manifest.raw_count, `${rawSum} vs ${D.manifest.raw_count}`);
const providerSum = Object.values(D.manifest.provider_counts).reduce((a, b) => a + b, 0);
ok('provider_counts 合计 = raw_count', providerSum === D.manifest.raw_count, `${providerSum} vs ${D.manifest.raw_count}`);
ok('去重后 < 数据源返回（漏斗确实收敛）', D.manifest.unique_before_filters < D.manifest.raw_count);
ok('当前列表 < 去重后', D.papers.length < D.manifest.unique_before_filters);
ok('研究层面合并未实现时 included 为 0', D.prisma.included.studies_included === 0);
ok('研究层面合并标记为未实现', D.prisma.included.study_level_merge_implemented === false);

section('候选问题');
ok('全部 status=hypothesis', D.questions.every((q) => q.status === 'hypothesis'));
ok('三种 kind 都出现', new Set(D.questions.map((q) => q.kind)).size === 3, D.questions.map((q) => q.kind).join(','));
ok('支撑文献都存在于语料', D.questions.every((q) => q.supporting_papers.every((id) => D.byId.has(id))));
ok('evidence_strength 只取 moderate / weak（契约只有两级）',
  D.questions.every((q) => ['moderate', 'weak'].includes(q.evidence_strength)),
  D.questions.map((q) => q.evidence_strength).join(','));
ok('不存在被编造的数字型 strength 字段', D.questions.every((q) => !('strength' in q)));
const KIND_ORDER = { combination_gap: 0, coverage_gap: 1, unfollowed_result: 2 };
ok('排序符合 questions.py 的 (kind, moderate 优先) 规则',
  D.questions.every((q, i) => i === 0 || (KIND_ORDER[D.questions[i - 1].kind] < KIND_ORDER[q.kind]
    || (KIND_ORDER[D.questions[i - 1].kind] === KIND_ORDER[q.kind]
      && (D.questions[i - 1].evidence_strength === 'moderate' || q.evidence_strength === 'weak')))),
  D.questions.map((q) => `${q.kind}/${q.evidence_strength}`).join(' → '));

section('新颖度（逐篇，形状对齐 novelty_scores）');
const nv = D.landscape.novelty;
ok('是 {rows, note} 形状而不是 items', Array.isArray(nv.rows) && typeof nv.note === 'string');
ok('rows 覆盖全部语料', nv.rows.length === D.papers.length, `${nv.rows.length}`);
ok('每行都有契约字段 paper_id/title/year/novelty/most_similar',
  nv.rows.every((r) => 'paper_id' in r && 'title' in r && 'year' in r && 'novelty' in r && 'most_similar' in r));
const nulls = nv.rows.filter((r) => r.novelty === null);
ok('没有更早文献的行 novelty 为 null（不是 0、不是 1.0）', nulls.length > 0, `${nulls.length} 行`);
ok('novelty 为 null 的行都带说明', nulls.every((r) => typeof r.note === 'string' && r.note.length > 0));
ok('null 的行排在最后', (() => {
  const firstNull = nv.rows.findIndex((r) => r.novelty === null);
  return nv.rows.slice(firstNull).every((r) => r.novelty === null);
})());
const scored = nv.rows.filter((r) => r.novelty !== null);
ok('有值的行按 novelty 降序', scored.every((r, i) => i === 0 || scored[i - 1].novelty >= r.novelty));
ok('novelty 落在 [0,1] 且不是全部相同（代理相似度确实有区分度）',
  scored.every((r) => r.novelty >= 0 && r.novelty <= 1) && new Set(scored.map((r) => r.novelty)).size > 3,
  `${new Set(scored.map((r) => r.novelty)).size} 个不同取值`);
ok('most_similar 指向样本内的更早文献', scored.every((r) => {
  const m = r.most_similar;
  const other = m && D.byId.get(m.paper_id);
  return other && other.year < r.year;
}));

section('停止原因与预算');
const STOP = ['saturated', 'no_new_results', 'low_yield', 'max_rounds', 'truncated', 'budget_exhausted', 'canceled', 'api_failure'];
ok('引文扩展的停止原因是合法取值', STOP.includes(D.snowball.stopped_reason), D.snowball.stopped_reason);
ok('未跑完的轮次标了 not_run', D.snowball.rounds.filter((r) => r.not_run).length === 1);
ok('已完成轮次数与 rounds_completed 一致', D.snowball.rounds.filter((r) => !r.not_run).length === D.snowball.rounds_completed);
const hb = D.http_budget;
ok('HttpBudget 含全部计数字段',
  ['requests', 'retries', 'cache_hits', 'rate_limited', 'errors', 'canceled', 'elapsed_seconds', 'by_source']
    .every((k) => k in hb));
const bySourceReq = Object.values(hb.by_source).reduce((a, b) => a + b.requests, 0);
ok('by_source 请求数合计 = requests', bySourceReq === hb.requests, `${bySourceReq} vs ${hb.requests}`);

section('会话');
ok('存在一个损坏会话且带备份', D.sessions.some((x) => x.corrupt && x.backup));
ok('当前项目标记唯一', D.sessions.filter((x) => x.is_current).length === 1);
ok('会话 schema_version 均为 1', D.sessions.every((x) => x.schema_version === 1));

console.log(`\n${failures ? 'FAILED' : 'PASSED'} — ${checks - failures}/${checks} 项通过`);
process.exit(failures ? 1 : 0);
